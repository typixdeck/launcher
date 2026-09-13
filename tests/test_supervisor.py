import os
import tempfile
import unittest
from pathlib import Path

from typix_launcher.supervisor import Supervisor


class SupervisorTests(unittest.TestCase):
    def test_launcher_exits_then_returns_after_application(self):
        events = []
        app_done = False
        previous_runtime = os.environ.get("XDG_RUNTIME_DIR")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "typix-launcher-request"
            entry = root / "game.desktop"
            entry.write_text(
                "[Desktop Entry]\nType=Application\nName=Game\nExec=/usr/bin/game\n",
                encoding="utf-8",
            )

            ui_runs = 0

            def run_ui() -> int:
                nonlocal ui_runs
                ui_runs += 1
                events.append("ui-start")
                if ui_runs == 1:
                    temporary_file = request.with_suffix(".tmp")
                    temporary_file.write_text(str(entry), encoding="utf-8")
                    temporary_file.replace(request)
                    events.append("ui-quit")
                elif app_done:
                    raise KeyboardInterrupt
                return 0

            def run_app(path: Path) -> int:
                self.assertEqual(path, entry)
                self.assertIn("ui-quit", events)
                events.append("app-run")
                nonlocal app_done
                app_done = True
                return 0

            os.environ["XDG_RUNTIME_DIR"] = str(root)
            try:
                supervisor = Supervisor(run_ui=run_ui, run_app=run_app)
                self.assertEqual(supervisor.run_forever(), 0)
            finally:
                if previous_runtime is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = previous_runtime

        self.assertEqual(events[:4], ["ui-start", "ui-quit", "app-run", "ui-start"])
        self.assertFalse(request.exists())


if __name__ == "__main__":
    unittest.main()
