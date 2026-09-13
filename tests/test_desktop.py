import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from typix_launcher import desktop


class DesktopDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous_runtime = os.environ.get("XDG_RUNTIME_DIR")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / "local"
        self.system = self.root / "system"
        self.local.mkdir()
        self.system.mkdir()
        self.directories = patch.object(desktop, "default_desktop_dirs", return_value=(self.local, self.system))
        self.directories.start()
        self.addCleanup(self.directories.stop)
        os.environ["XDG_RUNTIME_DIR"] = str(self.root / "runtime")
        os.environ["LANG"] = "zh_CN.UTF-8"

    def tearDown(self):
        for key, value in (
            ("XDG_RUNTIME_DIR", self.previous_runtime),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_discovers_and_prioritizes_visible_entries(self):
        local = self.write(
            self.local,
            "local.desktop",
            "[Desktop Entry]\nType=Application\nName=Local\nExec=/usr/bin/local\nCategories=Game;\n",
        )
        self.write(
            self.system,
            "system.desktop",
            "[Desktop Entry]\nType=Application\nName=System\nExec=/usr/bin/system\n",
        )
        self.write(
            self.system,
            "hidden.desktop",
            "[Desktop Entry]\nType=Application\nName=Hidden\nExec=/usr/bin/hidden\nHidden=true\n",
        )
        apps = desktop.load_apps()
        self.assertEqual([app.name for app in apps], ["Local", "System"])
        self.assertEqual(apps[0].path, local)
        self.assertEqual(apps[0].category, "游戏")

    def test_expands_exec_and_writes_atomic_request(self):
        path = self.write(
            self.local,
            "app.desktop",
            '[Desktop Entry]\nType=Application\nName=My App\nExec=/usr/bin/app --name %c %F\nTerminal=false\n',
        )
        command, working_dir, terminal = desktop.expand_exec(path)
        self.assertEqual(command, ["/usr/bin/app", "--name", "My App"])
        self.assertIsNone(working_dir)
        self.assertFalse(terminal)
        desktop.atomic_request(path)
        request = desktop.request_path()
        self.assertEqual(request.read_text(encoding="utf-8"), str(path))
        self.assertFalse(request.with_suffix(".tmp").exists())

    def test_default_discovery_does_not_include_installed_applications(self):
        self.directories.stop()
        visible = self.root / "Desktop"
        installed = self.root / ".local/share/applications"
        visible.mkdir()
        installed.mkdir(parents=True)
        body = "[Desktop Entry]\nType=Application\nName=Example\nExec=/bin/true\n"
        target = self.write(installed, "example.desktop", body)
        with patch.object(Path, "home", return_value=self.root), patch.object(
            desktop.subprocess, "run", side_effect=FileNotFoundError
        ):
            self.assertEqual(desktop.load_apps(), [])
            shortcut = visible / "example.desktop"
            shortcut.symlink_to(target)
            self.assertEqual([app.name for app in desktop.load_apps()], ["Example"])
            shortcut.unlink()
            self.assertEqual(desktop.load_apps(), [])

    def test_localized_desktop_and_file_link(self):
        self.directories.stop()
        visible = self.root / "桌面"
        visible.mkdir()
        target = self.write(self.system, "my app.desktop", "[Desktop Entry]\nType=Application\nName=Linked\nExec=/bin/true\n")
        self.write(visible, "shortcut.desktop", f"[Desktop Entry]\nType=Link\nURL={target.as_uri()}\n")
        result = desktop.subprocess.CompletedProcess([], 0, str(visible) + "\n", "")
        with patch.object(desktop.subprocess, "run", return_value=result):
            self.assertEqual([app.path for app in desktop.load_apps()], [target])


if __name__ == "__main__":
    unittest.main()
