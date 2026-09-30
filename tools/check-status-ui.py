#!/usr/bin/env python3
"""Render an isolated GTK preview with public fixture tiles, never user inventory.

Run on a GTK desktop: python3 tools/check-status-ui.py /tmp/status-preview.png
The screenshot is a layout fixture, not measured device telemetry.
"""
import sys
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from typix_launcher.app import Launcher
from typix_launcher.desktop import DesktopEntry
from typix_launcher.status import battery_indicator, wifi_indicator


def main():
    app = Launcher()
    app.set_application_id('ai.typixdeck.launcher.LayoutCheck')
    app.register(None)
    rows = [('Copilot', '固件', 'applications-system'), ('Store', '应用', 'system-software-install'),
            ('Reader', '阅读', 'accessories-text-editor'), ('MyAI', '工具', 'face-smile'),
            ('微信', '网络', 'internet-chat'), ('Pixel Relay', '游戏', 'applications-games')]
    fixtures = [DesktopEntry(Path('/nonexistent') / f'{i}.desktop', name, '', category, icon, i)
                for i, (name, category, icon) in enumerate(rows)]
    # Build the real widget tree without mapping an ApplicationWindow or
    # altering the running launcher's window/session/desktop shortcuts.
    with patch('typix_launcher.app.desktop.load_apps', return_value=fixtures), \
         patch.object(Launcher, 'watch_desktop_dirs'), \
         patch.object(Gtk.ApplicationWindow, 'show_all'):
        app.do_activate()
    root = app.window.get_child()
    app.window.remove(root)
    offscreen = Gtk.OffscreenWindow()
    offscreen.add(root)
    offscreen.set_default_size(800, 600)
    offscreen.set_size_request(800, 600)
    app.status_strip.apply(battery_indicator(68, estimated=True, source='CW2015', mv=3850),
                           wifi_indicator('wifi:connected', 'enabled', '*:82'))
    offscreen.show_all()
    child = None
    try:
        end = time.monotonic() + .6
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        minimum, _ = root.get_preferred_width()
        assert minimum <= 800, f'header too wide: {minimum}'
        assert all(isinstance(item, Gtk.Image) and not item.get_can_focus() for item in app.status_strip.items)
        with patch.object(app, 'focused_index', return_value=None):
            assert app.on_key_press(app.window, SimpleNamespace(keyval=Gdk.KEY_Return)) is False
        pixbuf = offscreen.get_pixbuf()
        scale = offscreen.get_scale_factor()
        assert pixbuf is not None, 'no GTK offscreen render'
        assert (pixbuf.get_width(), pixbuf.get_height()) == (800 * scale, 600 * scale), (pixbuf.get_width(), pixbuf.get_height(), scale)
        pixbuf.savev(sys.argv[1], 'png', [], [])
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        app.status_strip.child = child
        app.status_strip.close()
        assert child.poll() is not None, 'background reader survived launcher shutdown'
        print('800x600 layout, non-interactive status icons and reader shutdown: PASS')
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()
        if not app.status_strip.closed:
            app.status_strip.close()
        app.status_strip.child = None
        offscreen.destroy()
        app.window.destroy()


if __name__ == '__main__':
    main()
