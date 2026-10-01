import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from typix_launcher import desktop, handoff
from typix_launcher.supervisor import Supervisor
from typix_launcher.runtime import external_launch


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root)}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
        self.entry = self.root / "test.desktop"
        self.entry.write_text("[Desktop Entry]\nType=Application\nName=Test\nExec=/bin/true\n")
        self.apps = patch.object(desktop, "default_desktop_dirs", return_value=(self.root,))
        self.apps.start(); self.addCleanup(self.apps.stop)

    def test_private_nonce_request_is_consumed_after_parent_and_cleaned(self):
        with handoff.SingleHandoff() as owner:
            path = owner.path
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(owner.selected())
            with patch.dict(os.environ, {handoff.ENV: str(path)}):
                self.assertTrue(handoff.queue("test.desktop"))
                with self.assertRaisesRegex(ValueError, "已有"):
                    handoff.queue("test.desktop")
            self.assertEqual(owner.selected(), self.entry)
        self.assertFalse(path.exists())

    def test_api_does_not_expand_discovery_to_all_system_applications(self):
        self.apps.stop()
        with patch.object(desktop, "default_desktop_dirs", return_value=()):
            with self.assertRaises(ValueError):
                handoff.launch_entry("test.desktop")
        for name in ("../test.desktop", "/usr/share/applications/test.desktop", "x\n.desktop", "--option"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                handoff.launch_entry(name)

    def test_wrong_token_symlink_or_unprivate_request_is_rejected(self):
        with handoff.SingleHandoff() as owner:
            raw = json.loads(owner.path.read_bytes())
            raw["token"] = "0" * 32
            owner.path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError): owner.selected()
            owner.path.chmod(0o644)
            with self.assertRaises(ValueError): owner.selected()
        outside = self.root / "outside.json"; outside.write_text("{}")
        with patch.dict(os.environ, {handoff.ENV: str(outside)}), self.assertRaises(ValueError):
            handoff.queue("test.desktop")

    def test_supervisor_launches_selected_app_without_reopening_ui_between(self):
        events = []
        store = self.root / "store.desktop"
        store.write_text("[Desktop Entry]\nType=Application\nName=Store\nExec=/bin/store\n")
        def spawn(command, **kwargs):
            events.append(command[0] + "-start")
            if command[0] == "/bin/store":
                with patch.dict(os.environ, kwargs["env"]): handoff.queue("test.desktop")
            return SimpleNamespace(command=command)
        class Session:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def wait(self, child): events.append(child.command[0] + "-exit"); return 0
        with patch("typix_launcher.supervisor.subprocess.Popen", side_effect=spawn), \
             patch("typix_launcher.supervisor.FullscreenSession", Session):
            self.assertEqual(Supervisor().default_run_app(store), 0)
        self.assertEqual(events, ["/bin/store-start", "/bin/store-exit", "/bin/true-start", "/bin/true-exit"])
        self.assertEqual(list(handoff.directory().iterdir()), [])

    def test_resident_app_has_no_single_mode_handoff_and_stays_in_appjobs(self):
        class Session:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def wait(self, child): return 0
        with patch.dict(os.environ, {handoff.ENV: "/private/stale"}), \
             patch("typix_launcher.supervisor.FullscreenSession", Session), \
             patch("typix_launcher.supervisor.subprocess.Popen") as spawn:
            Supervisor().default_run_app(self.entry, single_handoff=False)
        self.assertNotIn(handoff.ENV, spawn.call_args.kwargs["env"])

    def test_external_request_in_single_mode_cannot_submit_atomic_request_or_quit(self):
        jobs = Mock(); jobs.contains.return_value = False
        activate, launch = Mock(), Mock()
        with patch.object(desktop, "atomic_request") as submit:
            with self.assertRaisesRegex(ValueError, "先从 Launcher 打开 Store"):
                external_launch(self.entry, mode="single", jobs=jobs, activate=activate, launch=launch)
        activate.assert_not_called(); launch.assert_not_called(); submit.assert_not_called()
        # A mode change does not prevent focusing an already-owned running app.
        jobs.contains.return_value = True
        self.assertTrue(external_launch(self.entry, mode="single", jobs=jobs, activate=activate, launch=launch))
        activate.assert_called_once_with(self.entry); launch.assert_not_called()
        jobs.contains.return_value = False
        launch.return_value = True
        self.assertTrue(external_launch(self.entry, mode="resident", jobs=jobs, activate=activate, launch=launch))
        launch.assert_called_once_with(self.entry)

    def test_installed_api_checks_exact_package_status_owner_and_fixed_commands(self):
        def owned(_path):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_size=512)
        with patch.object(Path, "lstat", owned), patch.object(desktop, "read_desktop", return_value=(self.entry, {"Type": "Application", "Exec": "/bin/true"})), \
             patch.object(handoff.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0, "install ok installed"),
                 subprocess.CompletedProcess([], 0, "typix-test:arm64: /usr/share/applications/test.desktop\n")]) as run:
            self.assertEqual(handoff.launch_entry("test.desktop", "typix-test"), Path("/usr/share/applications/test.desktop"))
            self.assertEqual(run.call_args_list[1].args[0], ["dpkg-query", "--search", "/usr/share/applications/test.desktop"])
            self.assertTrue(all(call.kwargs["timeout"] <= 5 for call in run.call_args_list))
        for state, owner in (("install ok unpacked", "typix-test"), ("install ok installed", "typix-other")):
            with patch.object(Path, "lstat", owned), patch.object(handoff.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, state), subprocess.CompletedProcess([], 0, owner + ": /usr/share/applications/test.desktop\n")]), self.assertRaises(ValueError):
                handoff.launch_entry("test.desktop", "typix-test")

    def test_installed_api_rejects_source_symlink_and_package_injection(self):
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)), self.assertRaises(ValueError):
            handoff.launch_entry("test.desktop", "typix-test")
        for package in ("--all", "pi;reboot", "bad/path"):
            with self.assertRaises(ValueError): handoff.launch_entry("test.desktop", package)

    def test_handoff_validation_timeout_restores_ui_and_never_replays_request(self):
        events = []
        def ui():
            events.append("ui")
            if len(events) > 1:
                raise KeyboardInterrupt
            desktop.atomic_request(self.entry)
            return 0
        app = Mock(side_effect=subprocess.TimeoutExpired("dpkg-query", 5))
        supervisor = Supervisor(run_ui=ui, run_app=app)
        with patch("typix_launcher.supervisor.time.sleep"):
            self.assertEqual(supervisor.run_forever(), 0)
        self.assertEqual(events, ["ui", "ui"])
        app.assert_called_once_with(self.entry)
        self.assertFalse(desktop.request_path().exists())
