"""Launcher UI supervisor with exit-while-app-runs semantics."""
from __future__ import annotations

import configparser
import contextlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import desktop
from .fullscreen import FullscreenSession
from .handoff import SingleHandoff, ENV


class Supervisor:
    """Run UI, exit it before a foreground app, and restore it when app exits.

    The UI writes a desktop-entry request and calls ``Gtk.Application.quit``. The
    supervisor therefore has no launcher GUI process resident while the requested
    application owns the screen, then starts a fresh UI after the child exits.
    """

    def __init__(
        self,
        run_ui: Callable[[], int] | None = None,
        run_app: Callable[[Path], int] | None = None,
    ) -> None:
        self.run_ui = run_ui or self.default_run_ui
        self.run_app = run_app or self.default_run_app

    def default_run_ui(self) -> int:
        return subprocess.run([sys.executable, "-m", "typix_launcher", "--ui"], check=False).returncode

    def default_run_app(self, path: Path, *, single_handoff: bool = True) -> int:
        while True:
            command, working_dir, terminal = desktop.expand_exec(path)
            if terminal:
                terminal_command = shutil.which("x-terminal-emulator") or shutil.which("lxterminal")
                if terminal_command:
                    command = [terminal_command, "-e", *command]
            # The next foreground app starts only after the current app exits.
            # No Launcher UI is recreated between Store and its selected app.
            owner = SingleHandoff() if single_handoff else contextlib.nullcontext(None)
            with owner as handoff, FullscreenSession(path) as session:
                env = dict(os.environ)
                if handoff is not None:
                    env[ENV] = str(handoff.path)
                else:
                    env.pop(ENV, None)
                child = subprocess.Popen(command, cwd=str(working_dir) if working_dir else None, env=env)
                code = session.wait(child)
                selected = handoff.selected() if handoff is not None else None
            if selected is None:
                return code
            path = selected

    def run_once(self) -> bool:
        destination = desktop.request_path()
        destination.unlink(missing_ok=True)
        self.run_ui()
        if not destination.exists():
            return False
        requested = Path(destination.read_text(encoding="utf-8").strip())
        destination.unlink(missing_ok=True)
        self.run_app(requested)
        return True

    def run_forever(self) -> int:
        while True:
            try:
                launched = self.run_once()
                # Preserve the old launcher's calm-down period after a UI-only exit.
                time.sleep(1.0 if not launched else 0.1)
            except KeyboardInterrupt:
                return 0
            except (OSError, ValueError, configparser.Error, subprocess.SubprocessError) as exc:
                print(f"TypixDeck launcher: {exc}", file=sys.stderr, flush=True)
                time.sleep(2)
