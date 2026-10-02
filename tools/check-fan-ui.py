#!/usr/bin/env python3
"""Check fan settings with in-memory fixtures only; optionally render PNGs."""
from pathlib import Path
import argparse
import importlib.util
import os
import sys
import threading
import time
from unittest.mock import patch

if sys.platform == "darwin":
    os.environ.setdefault("GDK_BACKEND", "x11")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
spec = importlib.util.spec_from_file_location("preview", Path(__file__).with_name("preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
from typix_launcher import app as module
from typix_launcher.app import GLib, Gtk
from typix_launcher.fan import DEFAULT_CURVE, FanController, FanState
from typix_launcher.fan_panel import FanPanel


def pump_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("GTK fixture timed out")


def settle():
    end = time.monotonic() + .15
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class ControlledFan(preview.PreviewFanController):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.hold = False
        self.release = threading.Event()
        self.entered = threading.Event()
        self.failure = ""

    def apply(self, mode, percent=50, *, curve=None):
        self.calls.append((mode, percent))
        self.entered.set()
        if self.hold and not self.release.wait(3):
            raise RuntimeError("fixture release timed out")
        if self.failure:
            return FanState(available=True, mode=self.current.mode,
                            requested_percent=self.current.requested_percent,
                            percent=self.current.percent, rpm=self.current.rpm,
                            temperature=self.current.temperature,
                            error="authorization", message=self.failure)
        return super().apply(mode, percent, curve=curve)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, help="Folder for isolated 600px-wide PNG fixtures")
    args = parser.parse_args()
    preview.require_display()
    windows, panels = [], []

    def render(widget, name, height=260):
        offscreen = Gtk.OffscreenWindow()
        if isinstance(widget, Gtk.Notebook):
            offscreen.get_style_context().add_class('launcher-settings')
        windows.append(offscreen)
        offscreen.add(widget)
        offscreen.set_size_request(600, height)
        offscreen.show_all()
        pump_until(lambda: offscreen.get_pixbuf() is not None)
        pump_until(lambda: not isinstance(widget, FanPanel) or widget.initialized)
        settle()
        minimum, _ = widget.get_preferred_width()
        assert minimum <= 600, f"fan settings overflow: {minimum}"
        widget.get_preferred_height()
        settle()  # CSS and notebook page measurement may queue a second layout.
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            offscreen.get_pixbuf().savev(str(args.screenshots / f"{name}.png"), "png", [], [])
        return offscreen

    def no_device(*_args, **_kwargs):
        raise AssertionError("preview tried to access real fan/session interfaces")

    # Catch regressions that accidentally replace the fixture with a real
    # controller or system command. These checks never touch hardware/config.
    with patch.object(FanController, "state", no_device), \
         patch.object(FanController, "apply", no_device), \
         patch.object(module.subprocess, "run", no_device), \
         patch.object(module.subprocess, "Popen", no_device):
        app = preview.Preview()
        app.register(None)
        app.load_css()
        try:
            control = ControlledFan()
            panel = FanPanel(control)
            panels.append(panel)
            panel.start()
            pump_until(lambda: panel.initialized)
            assert panel.mode.get_active_id() == "auto"
            assert "1750 RPM" in panel.readings.get_text()
            render(panel, "fan-auto")
            panel.mode.set_active_id("manual")
            panel.scale.set_value(70)
            assert control.calls == [], "changing draft applied settings"
            assert panel.manual_row.get_visible()
            settle()
            assert panel.scale.get_mapped(), 'manual slider hidden inside visible row'
            assert panel.percent_input.get_mapped(), 'manual numeric input hidden'
            assert panel.scale.get_allocated_width() > 100
            assert panel.scale.get_allocated_height() >= 20
            panel.percent_input.set_value(65)
            assert panel.scale.get_value() == 65
            panel.scale.set_value(70)
            assert panel.scale.get_adjustment().get_lower() == 30
            assert panel.scale.get_adjustment().get_upper() == 100
            control.hold = True
            panel.apply_button.clicked()
            pump_until(control.entered.is_set)
            assert panel.busy and not panel.apply_button.get_sensitive()
            assert not panel.mode.get_sensitive() and not panel.scale.get_sensitive()
            assert "已应用" not in panel.message.get_text()
            ticks = []
            GLib.idle_add(lambda: (ticks.append(True), False)[1])
            pump_until(lambda: bool(ticks))
            assert panel.busy, "worker unexpectedly completed before fixture release"
            control.release.set()
            pump_until(lambda: not panel.busy)
            assert control.calls == [("manual", 70)]
            assert panel.message.get_text() == "演示 · 手动 70%"
            assert "输出 70%" in panel.readings.get_text()
            render_window = windows[-1]
            if args.screenshots:
                pump_until(lambda: bool(panel.get_allocation().width))
                settle()
                render_window.get_pixbuf().savev(str(args.screenshots / "fan-manual.png"), "png", [], [])

            control.hold = False
            panel.mode.set_active_id('auto')
            panel.curve_inputs[0][0].set_value(40)
            panel.curve_inputs[1][1].set_value(50)
            panel.apply_button.clicked()
            pump_until(lambda: not panel.busy)
            assert control.current.curve == ((40, 30), (55, 50), (65, 65), (75, 100))
            assert panel.message.get_text() == '演示 · 温控曲线'
            assert not panel.dirty
            panel.curve_inputs[0][0].set_value(60)
            assert not panel.apply_button.get_sensitive(), 'unordered curve allowed'
            assert '递增' in panel.message.get_text()
            panel.refresh()
            pump_until(lambda: not panel.reading)
            assert '递增' in panel.message.get_text(), 'refresh erased invalid-draft explanation'
            panel.on_reset_curve(None)
            assert panel.curve_values() == DEFAULT_CURVE
            assert panel.apply_button.get_sensitive()
            panel.mode.set_active_id('manual')
            panel.apply_button.clicked()
            pump_until(lambda: not panel.busy)

            control.hold = False
            control.failure = "授权已取消，未修改设置"
            panel.scale.set_value(85)
            panel.apply_button.clicked()
            pump_until(lambda: not panel.busy)
            assert panel.message.get_text() == control.failure
            assert "输出 70%" in panel.readings.get_text(), "draft was rendered as applied"
            control.failure = "授权被拒绝，未修改设置"
            panel.apply_button.clicked()
            pump_until(lambda: not panel.busy)
            assert panel.message.get_text() == control.failure
            assert panel.apply_button.get_sensitive(), "failed authorization prevented retry"

            panel.feedback = True
            panel.message.set_text("旧的成功提示")
            control.current = FanState(available=True, mode="manual", percent=100,
                                       rpm=0, error="temperature", message="温度读取失败，已进入保护")
            panel.refresh()
            pump_until(lambda: not panel.reading)
            assert panel.message.get_text() == control.current.message
            assert "0 RPM" in panel.readings.get_text()
            assert "温度 —" in panel.readings.get_text()

            control.current = FanState(message="需启用 EMC2301 驱动")
            panel.refresh()
            pump_until(lambda: not panel.reading)
            assert not panel.apply_button.get_sensitive()
            assert not panel.mode.get_sensitive()
            assert "EMC2301" in panel.message.get_text()
            if args.screenshots:
                settle()
                render_window.get_pixbuf().savev(str(args.screenshots / "fan-unavailable.png"), "png", [], [])
            panel.close()
            assert panel.closed and panel.timer == 0

            # A closed settings page must not terminate an authorized helper or
            # update its destroyed widgets when that helper eventually returns.
            pending = ControlledFan()
            pending.hold = True
            closing = FanPanel(pending)
            panels.append(closing)
            closing.start()
            pump_until(lambda: closing.initialized)
            closing.apply_button.clicked()
            pump_until(pending.entered.is_set)
            closing.close()
            assert closing.timer == 0
            pending.release.set()
            pump_until(lambda: pending.current.message == "演示设置，未修改设备")

            # Render the actual existing settings contents and verify that both
            # runtime/autostart and fan controls fit a scrollable 600px viewport.
            with patch.object(module, "StatusStrip", preview.PreviewStatus), \
                 patch.object(module.desktop, "load_apps", return_value=[]), \
                 patch.object(Gtk.ApplicationWindow, "show_all"):
                app.do_activate()

            def settings_run(dialog):
                assert app.fan_panel is not None
                pump_until(lambda: app.fan_panel.initialized)
                content = dialog.get_content_area()
                notebook = content.get_children()[0]
                assert isinstance(notebook, Gtk.Notebook)
                assert notebook.get_n_pages() == 2
                notebook.show_all()
                notebook.set_current_page(1)
                content.remove(notebook)
                render(notebook, "settings-fan", height=500)
                assert app.fan_panel.curve_area.get_mapped()
                assert app.fan_panel.apply_button.get_mapped()
                assert app.fan_panel.get_preferred_height()[0] <= 450, 'fan editor needs unnecessary scrolling'
                return Gtk.ResponseType.CLOSE

            with patch.object(Gtk.Dialog, "show_all"), patch.object(Gtk.Dialog, "run", settings_run):
                app.show_settings()
            assert app.fan_panel is None and app.settings_dialog is None
            menu = Gtk.Menu()
            item = Gtk.MenuItem(label='手动 · 固定转速')
            menu.append(item)
            menu.show_all()
            def luminance(color):
                values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                          for v in (color.red, color.green, color.blue)]
                return sum(v * w for v, w in zip(values, (.2126, .7152, .0722)))
            for state in (Gtk.StateFlags.NORMAL, Gtk.StateFlags.PRELIGHT):
                background = item.get_style_context().get_background_color(state)
                foreground = item.get_child().get_style_context().get_color(state)
                light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
                assert (light + .05) / (dark + .05) >= 4.5, 'dropdown text contrast too low'
            menu.destroy()
            print("PASS: visible manual slider/input, curve editing/validation/readback, dropdown contrast, GTK responsiveness, authorization failure, protected-state feedback, closed-page lifecycle and 600px tabbed settings")
        finally:
            for panel in panels:
                panel.close()
            if app.fan_panel is not None:
                app.fan_panel.close()
            if app.settings_dialog is not None:
                app.settings_dialog.destroy()
            if app.status_strip is not None and not app.status_strip.closed:
                app.status_strip.close()
            for window in windows:
                window.destroy()
            if app.window is not None:
                app.window.destroy()


if __name__ == "__main__":
    main()
