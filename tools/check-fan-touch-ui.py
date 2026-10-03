#!/usr/bin/env python3
"""Inject GTK touch/mouse events into the isolated fan preview; no hardware writes."""
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import preview
from typix_launcher.app import Gtk, Gdk
from typix_launcher.fan import FanController, validate_curve
from typix_launcher.fan_panel import FanPanel


def settle():
    end = time.monotonic() + .08
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(.005)


def main():
    preview.require_display()
    application = preview.Preview()
    application.register(None)
    application.load_css()
    control = preview.PreviewFanController()
    window = Gtk.Window()
    panel = FanPanel(control)
    scroll = Gtk.ScrolledWindow()
    scroll.set_kinetic_scrolling(True)
    scroll.set_capture_button_press(False)
    scroll.add(panel)
    window.add(scroll)
    window.set_default_size(600, 430)
    panel.render_state(control.state())
    window.show_all()
    settle()
    area = panel.curve_area
    pointer = Gdk.Display.get_default().get_default_seat().get_pointer()

    def event(kind, x, y):
        packet = Gdk.Event.new(kind)
        packet.window = area.get_window()
        packet.send_event = True
        packet.time = Gdk.CURRENT_TIME
        packet.x, packet.y = x, y
        packet.set_device(pointer)
        if kind in (Gdk.EventType.TOUCH_BEGIN, Gdk.EventType.TOUCH_UPDATE,
                    Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            # Primary touch with pointer emulation, as exposed by GTK3 touchscreens.
            packet.emulating_pointer = True
        else:
            packet.state = Gdk.ModifierType.BUTTON1_MASK if kind != Gdk.EventType.BUTTON_PRESS else Gdk.ModifierType(0)
            if kind != Gdk.EventType.MOTION_NOTIFY:
                packet.button = 1
        Gtk.main_do_event(packet)
        settle()

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Touch editing must not read/write real fan hardware')

    try:
        with patch.object(FanController, 'state', forbidden), patch.object(FanController, 'apply', forbidden), \
             patch.object(control, 'apply', forbidden):
            before = panel.curve_values()
            x, y = panel.curve_position(*before[1])
            event(Gdk.EventType.TOUCH_BEGIN, x + 16, y + 8)
            assert panel.curve_drag is not None and panel.curve_selection == 1
            assert panel.curve_values() == before, 'off-center touch made the point jump'
            page_position = scroll.get_vadjustment().get_value()
            event(Gdk.EventType.TOUCH_UPDATE, x + 36, y - 7)
            assert panel.curve_values()[1][0] > before[1][0]
            assert panel.curve_values()[1][1] > before[1][1]
            assert scroll.get_vadjustment().get_value() == page_position, 'page stole the point drag'
            event(Gdk.EventType.TOUCH_UPDATE, x + 10000, y - 10000)
            assert panel.curve_values()[1] == (before[2][0] - 1, before[2][1])
            event(Gdk.EventType.TOUCH_END, x + 10000, y - 10000)
            assert panel.curve_drag is None and panel.dirty
            validate_curve(panel.curve_values())
            edited = panel.curve_values()
            panel.render_state(control.state())
            assert panel.curve_values() == edited, 'telemetry refresh replaced the draft'

            # A real TOUCH_CANCEL from GTK must restore the pre-drag draft.
            x, y = panel.curve_position(*edited[1])
            event(Gdk.EventType.TOUCH_BEGIN, x, y)
            event(Gdk.EventType.TOUCH_UPDATE, x - 30, y + 20)
            assert panel.curve_values() != edited
            event(Gdk.EventType.TOUCH_CANCEL, x - 30, y + 20)
            assert panel.curve_values() == edited, 'cancelled touch retained changed values'
            assert panel.curve_drag is None

            # Mouse input uses the same GTK gesture and preserves the 100% endpoint.
            x, y = panel.curve_position(*panel.curve_values()[3])
            event(Gdk.EventType.BUTTON_PRESS, x, y)
            event(Gdk.EventType.MOTION_NOTIFY, x - 20, y + 80)
            event(Gdk.EventType.BUTTON_RELEASE, x - 20, y + 80)
            assert panel.curve_values()[3][1] == 100
            validate_curve(panel.curve_values())

            unchanged = panel.curve_values()
            event(Gdk.EventType.TOUCH_BEGIN, 1, 1)
            assert panel.curve_drag is None
            event(Gdk.EventType.TOUCH_END, 1, 1)
            assert panel.curve_values() == unchanged
            panel.busy = True
            panel.update_controls()
            x, y = panel.curve_position(*unchanged[0])
            event(Gdk.EventType.TOUCH_BEGIN, x, y)
            event(Gdk.EventType.TOUCH_UPDATE, x + 20, y - 20)
            event(Gdk.EventType.TOUCH_END, x + 20, y - 20)
            assert panel.curve_values() == unchanged and panel.curve_drag is None
            panel.busy = False
            panel.update_controls()

            panel.curve_selection = 0
            key = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            key.state = Gdk.ModifierType(0)
            key.keyval = Gdk.KEY_Right
            assert panel.on_curve_key(area, key)
            assert panel.curve_values()[0][0] == unchanged[0][0] + 1
            key.keyval = Gdk.KEY_Tab
            assert panel.on_curve_key(area, key) and panel.curve_selection == 1
            panel.curve_selection = 3
            assert not panel.on_curve_key(area, key), 'Tab cannot leave the chart'
            panel.close()
            key.keyval = Gdk.KEY_Up
            assert not panel.on_curve_key(area, key)
        print('PASS: GTK touch drag, large hit target, no jump, no scroll steal, constrained points, cancel rollback, mouse, keyboard, busy guard, draft-only edits')
    finally:
        panel.close()
        window.destroy()


if __name__ == '__main__':
    main()
