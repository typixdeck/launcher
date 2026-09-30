#!/usr/bin/env python3
"""Exercise GTK lifecycle routing/settings against fakes, with no mapped windows."""
from pathlib import Path
import importlib.util
import sys
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from typix_launcher import app as module
from typix_launcher.app import Gio, GLib, Gtk, Launcher
from typix_launcher.desktop import DesktopEntry
from typix_launcher.runtime import RuntimePreferences

spec = importlib.util.spec_from_file_location("preview", Path(__file__).with_name("preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


def main():
    with tempfile.TemporaryDirectory(prefix="launcher-runtime-check-") as folder:
        prefs = RuntimePreferences(Path(folder) / "runtime.json")
        fixture = DesktopEntry(Path(folder) / "example.desktop", "示例", "", "测试", "applications-system", 0)
        app = Launcher()
        app.runtime = prefs
        app.autostart = preview.PreviewSettings()
        app.set_application_id("ai.typixdeck.launcher.RuntimeCheck")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        try:
            with patch.object(module, "StatusStrip", preview.PreviewStatus), \
                 patch.object(module.desktop, "load_apps", return_value=[fixture]), \
                 patch.object(app, "watch_desktop_dirs"), \
                 patch.object(Gtk.ApplicationWindow, "show_all"):
                app.do_activate()
            jobs = app.app_jobs = Mock()
            jobs.contains.return_value = False
            jobs.count.return_value = 0
            with patch.object(module.desktop, "atomic_request") as request, \
                 patch.object(app, "quit") as quit_ui:
                prefs.save("single")
                app.launch(None, fixture.path)
                request.assert_called_once_with(fixture.path)
                quit_ui.assert_called_once()
                jobs.start.assert_not_called()
                request.reset_mock()
                quit_ui.reset_mock()
                prefs.save("resident")
                app.launch(None, fixture.path)
                jobs.start.assert_called_once()
                request.assert_not_called()
                quit_ui.assert_not_called()
                jobs.contains.return_value = True
                with patch.object(app, "activate_running") as activate:
                    app.launch(None, fixture.path)
                    activate.assert_called_once_with(fixture.path)
                assert jobs.start.call_count == 1, "duplicate start"
                jobs.contains.return_value = False
                jobs.count.return_value = 1
                prefs.save("single")
                with patch.object(app, "show_error") as error:
                    app.launch(None, fixture.path)
                    assert error.called
                    assert app.on_delete() is True
                request.assert_not_called()
                quit_ui.assert_not_called()

            jobs.count.return_value = 0

            # Different .desktop entries must preserve their initial Exec,
            # even when the compositor already has a shared executable app ID.
            entries = [Path(folder) / "project-a.desktop", Path(folder) / "project-b.desktop"]
            for index, entry in enumerate(entries):
                entry.write_text(f"[Desktop Entry]\nType=Application\nName=Project {index}\nExec=editor /tmp/project-{index}.txt\n")
            with patch.object(module, "activate_existing", return_value="activated") as focus, \
                 patch.object(module, "Supervisor") as supervisor:
                supervisor.return_value.default_run_app.return_value = 0
                for entry in entries:
                    assert app.run_resident_app(entry) == 0
                assert [call.args[0] for call in supervisor.return_value.default_run_app.call_args_list] == entries
                focus.assert_not_called()

            def exercise_settings(dialog):
                combos = [w for w in walk(dialog) if isinstance(w, Gtk.ComboBoxText)]
                assert len(combos) == 1
                combos[0].set_active_id("resident")
                assert RuntimePreferences(prefs.path).effective() == "resident"
                combos[0].set_active_id("single")
                assert RuntimePreferences(prefs.path).effective() == "single"
                return Gtk.ResponseType.CLOSE

            with patch.object(Gtk.Dialog, "show_all"), patch.object(Gtk.Dialog, "run", exercise_settings):
                app.show_settings()
            print("GTK: single releases UI; resident retains UI; duplicates suppressed; mode changes persist; active apps preserved: PASS")
        finally:
            if app.status_strip and not app.status_strip.closed:
                app.status_strip.close()
            if app.window:
                app.window.destroy()


if __name__ == "__main__":
    main()
