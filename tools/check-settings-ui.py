#!/usr/bin/env python3
"""Check the complete settings dialog under the selected GTK theme, using fixtures."""
from pathlib import Path
import argparse
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import preview
from typix_launcher import app as module
from typix_launcher.app import Gtk, Gdk
from typix_launcher.fan import FanController


def settle(predicate=lambda: True):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if predicate():
            time.sleep(.05)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            return
        time.sleep(.01)
    raise AssertionError("Settings layout timed out")


def children(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from children(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    preview.require_display()
    app = preview.Preview()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Settings preview accessed hardware or the user session")

    def check(dialog):
        dialog.move(60, 10)
        dialog.show_all()
        settle(lambda: app.fan_panel.initialized)
        header = dialog.get_titlebar()
        assert header.get_title() == "设置"
        notebook = next(w for w in children(dialog.get_content_area()) if isinstance(w, Gtk.Notebook))
        for state in (Gtk.StateFlags.NORMAL, Gtk.StateFlags.BACKDROP):
            color = header.get_style_context().get_background_color(state)
            assert color.alpha > .99 and color.red < .15 and color.green < .25, "Light system header leaked"
            foreground = header.get_style_context().get_color(state)
            assert min(foreground.red, foreground.green, foreground.blue) > .8
        for page, name in ((0, "settings-general"), (1, "settings-fan")):
            notebook.set_current_page(page)
            settle()
            width, height = dialog.get_size()
            assert width <= 800 and height <= 600, f"Settings exceed display: {width}x{height}"
            assert dialog.get_widget_for_response(Gtk.ResponseType.CLOSE).get_mapped()
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                root = Gdk.get_default_root_window()
                shot = Gdk.pixbuf_get_from_window(root, 0, 0, 800, 600)
                assert shot is not None
                shot.savev(str(args.screenshots / (name + ".png")), "png", [], [])
        return Gtk.ResponseType.CLOSE

    with patch.object(module.desktop, "load_apps", return_value=[]), \
            patch.object(module, "StatusStrip", preview.PreviewStatus), \
            patch.object(FanController, "state", forbidden), \
            patch.object(FanController, "apply", forbidden), \
            patch.object(module.subprocess, "run", forbidden), \
            patch.object(module.subprocess, "Popen", forbidden):
        try:
            app.register(None)
            app.do_activate()
            with patch.object(Gtk.Dialog, "run", check):
                app.show_settings()
            assert app.settings_dialog is None and app.fan_panel is None
            print("PASS: complete settings dialog, dark active/inactive header, Close action, 800x600 bounds; no hardware writes")
        finally:
            if app.status_strip is not None:
                app.status_strip.close()
            if app.window is not None:
                app.window.destroy()
            app.quit()


if __name__ == "__main__":
    main()
