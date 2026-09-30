#!/usr/bin/env python3
"""Preview checkout code with fixture tiles and no device/session mutations."""
from pathlib import Path
import argparse
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from typix_launcher import app as module
from typix_launcher.app import Gdk, Gio, GLib, Gtk, Launcher
from typix_launcher.desktop import DesktopEntry
from typix_launcher.settings import AutostartState
from typix_launcher.status import battery_indicator, wifi_indicator
from typix_launcher.status_ui import StatusStrip


class PreviewStatus(StatusStrip):
    def refresh(self):
        self.apply(battery_indicator(68, estimated=True, source="Preview", mv=3850),
                   wifi_indicator("wifi:connected", "enabled", "*:82"))
        return GLib.SOURCE_CONTINUE

    def set_active(self, _active):
        # Keep production drawing, without starting the serial/network sampler.
        pass


class PreviewSettings:
    enabled = False

    def state(self):
        return AutostartState(self.enabled, "preview", True)

    def set_enabled(self, value):
        self.enabled = value
        return self.state()


class Preview(Launcher):
    def __init__(self):
        super().__init__()
        self.set_application_id("ai.typixdeck.launcher.Preview")
        self.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        self.autostart = PreviewSettings()

    def watch_desktop_dirs(self):
        pass

    def reload(self):
        super().reload()
        self.count_label.set_text("源码预览 · 演示数据 · Esc 退出")

    def launch(self, _button, _path):
        self.notice("应用入口预览", "这里使用演示快捷方式。")

    def notice(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.CLOSE, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def confirm_power(self, _button, _action):
        self.notice("电源按钮预览", "预览模式不会重启或关闭设备。")

    def screen_off(self, _button):
        self.notice("息屏按钮预览", "预览模式保持屏幕亮起。")

    def on_key_press(self, window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.quit()
            return True
        return super().on_key_press(window, event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path, help="Offscreen PNG; use GDK_BACKEND=x11")
    parser.add_argument("--smoke-seconds", type=int, default=0)
    args = parser.parse_args()
    rows = [("Copilot", "固件", "applications-system"), ("Store", "应用", "system-software-install"),
            ("Reader", "阅读", "accessories-text-editor"), ("时钟", "工具", "preferences-system-time"),
            ("MIDI", "应用", "audio-x-generic"), ("设置", "工具", "preferences-system")]
    fixtures = [DesktopEntry(Path("/nonexistent") / f"{i}.desktop", name, "", category, icon, i)
                for i, (name, category, icon) in enumerate(rows)]
    with patch.object(module, "StatusStrip", PreviewStatus), \
         patch.object(module.desktop, "load_apps", return_value=fixtures):
        app = Preview()
        offscreen = None
        try:
            if not args.screenshot:
                if args.smoke_seconds > 0:
                    GLib.timeout_add_seconds(args.smoke_seconds, lambda: (app.quit(), False)[1])
                return app.run([sys.argv[0]])
            app.register(None)
            with patch.object(Gtk.ApplicationWindow, "show_all"):
                app.do_activate()
            root = app.window.get_child()
            app.window.remove(root)
            offscreen = Gtk.OffscreenWindow()
            offscreen.add(root)
            offscreen.set_size_request(800, 600)
            offscreen.show_all()
            end = time.monotonic() + .5
            while time.monotonic() < end:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            pixbuf = offscreen.get_pixbuf()
            if pixbuf is None:
                raise RuntimeError("No offscreen image; run with GDK_BACKEND=x11")
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            pixbuf.savev(str(args.screenshot), "png", [], [])
            print(args.screenshot)
            return 0
        finally:
            if app.status_strip is not None and not app.status_strip.closed:
                app.status_strip.close()
            if offscreen is not None:
                offscreen.destroy()
            if app.window is not None:
                app.window.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
