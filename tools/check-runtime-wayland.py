#!/usr/bin/env python3
"""Opt-in live Wayland smoke test using only temporary launcher/app windows.

Temporarily maps fixture windows, restores prior focus and closes all test apps.
It does not send power commands, stop services or open real user applications.
"""
from pathlib import Path
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from typix_launcher import app as module
from typix_launcher.app import Launcher, GLib
from typix_launcher.desktop import DesktopEntry
from typix_launcher.fullscreen import ForeignToplevelClient, ACTIVATED
from typix_launcher.runtime import RuntimePreferences

spec = importlib.util.spec_from_file_location("preview", Path(__file__).with_name("preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

LAUNCHER_ID = "ai.typixdeck.launcher.RuntimeSwitchCheck"
APP_ID = "ai.typixdeck.launcher.RuntimeFixture"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-fixtures", action="store_true", required=True)
    parser.parse_args()
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("Run inside a Wayland desktop session")
    GLib.set_prgname(LAUNCHER_ID)
    client = ForeignToplevelClient()
    client.connect()
    previous = next((w for w in client.windows.values() if ACTIVATED in w.states), None)
    children = []
    original_popen = subprocess.Popen
    outcome = {"phase": 0, "error": "", "passed": False}
    with tempfile.TemporaryDirectory(prefix="typix-mode-fixtures-") as directory:
        root = Path(directory)
        fixture_py = root / "fixture.py"
        fixture_py.write_text('''import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
GLib.set_prgname("ai.typixdeck.launcher.RuntimeFixture")
app = Gtk.Application(application_id="ai.typixdeck.launcher.RuntimeFixture")
def activate(application):
    window = Gtk.ApplicationWindow(application=application)
    window.set_title("Launcher test application")
    window.add(Gtk.Label(label="临时验证窗口 · 即将自动关闭"))
    window.show_all()
app.connect("activate", activate)
GLib.timeout_add_seconds(20, lambda: (app.quit(), False)[1])
app.run([])
''')
        desktop = root / "fixture.desktop"
        desktop.write_text("[Desktop Entry]\nType=Application\nName=测试应用\n"
                           f"Exec={sys.executable} {fixture_py}\n"
                           f"X-TypixDeck-FullscreenAppId={APP_ID}\n")
        entry = DesktopEntry(desktop, "测试应用", "", "临时验证", "applications-system", 0)
        app = preview.Preview()
        app.set_application_id(LAUNCHER_ID)
        app.runtime = RuntimePreferences(root / "runtime.json")
        app.runtime.save("resident")
        # Use actual routing and supervisor/fullscreen code, not Preview.launch.
        app.launch = lambda button, path: Launcher.launch(app, button, path)
        started = time.monotonic()

        def spawn(command, **kwargs):
            assert command == [sys.executable, str(fixture_py)], "unexpected application command"
            child = original_popen(command, **kwargs)
            children.append(child)
            return child

        def tick():
            try:
                assert time.monotonic() - started < 15, f"fixture switch test timed out at phase {outcome['phase']}"
                client.poll(0)
                windows = {w.app_id: w for w in client.windows.values() if w.ready and not w.parent}
                launcher = windows.get(LAUNCHER_ID)
                target = windows.get(APP_ID)
                phase = outcome["phase"]
                if phase == 0 and launcher:
                    app.launch(None, desktop)
                    outcome["phase"] = 1
                elif phase == 1 and launcher and target:
                    assert app.app_jobs.count() == 1
                    client.activate(launcher)
                    outcome["phase"] = 2
                elif phase == 2 and launcher and ACTIVATED in launcher.states:
                    app.launch(None, desktop)
                    assert len(children) == 1, "duplicate app spawned"
                    outcome["phase"] = 3
                elif phase == 3 and target and ACTIVATED in target.states:
                    assert len(children) == 1
                    children[0].terminate()  # Only this test's disposable app.
                    outcome["phase"] = 4
                elif phase == 4 and not target and app.app_jobs.count() == 0:
                    assert launcher is not None, "resident launcher disappeared"
                    app.runtime.save("single")
                    with patch.object(module.desktop, "atomic_request") as request:
                        app.launch(None, desktop)
                        request.assert_called_once_with(desktop)
                    outcome["passed"] = True
                    return GLib.SOURCE_REMOVE
            except Exception as exc:
                outcome["error"] = str(exc)
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        source = GLib.timeout_add(100, tick)
        try:
            with patch.object(module, "StatusStrip", preview.PreviewStatus), \
                 patch.object(module.desktop, "load_apps", return_value=[entry]), \
                 patch.object(module.subprocess, "Popen", side_effect=spawn):
                app.run([sys.argv[0]])
        finally:
            if not outcome["passed"] and not outcome["error"]:
                GLib.source_remove(source)
            for child in children:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            try:
                client.poll(0)
                if previous and previous.identifier in client.windows:
                    client.activate(previous)
                    client._roundtrip(.7)
            finally:
                client.close()
        if not outcome["passed"]:
            raise SystemExit(outcome["error"] or "Window closed before test completed")
        print("PASS: resident launcher/app coexist and activate both ways; repeated tile launches only once; single mode quits UI")
        print("Window activation tested through compositor; no synthetic Alt+Tab key was sent.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
