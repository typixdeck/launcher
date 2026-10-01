"""User-selected app lifecycle, with no machine-model assumptions."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable

MODES = ("auto", "single", "resident")
RESIDENT_MIN_KIB = 1536 * 1024


def external_launch(path: Path, *, mode: str, jobs, activate, launch) -> bool:
    """No-handoff requests may focus existing jobs or start in resident mode.

    A single-mode child must instead use its supervisor's nonce handoff, so an
    unrelated Store cannot quit Launcher and restore it over the selected app.
    """
    if jobs.contains(path):
        activate(path)
        return True
    if mode != "resident":
        raise ValueError("请先从 Launcher 打开 Store，再启动此应用")
    return bool(launch(path))


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "typix-launcher/runtime.json"


def detected_mode(meminfo: Path = Path("/proc/meminfo")) -> str:
    try:
        for line in meminfo.read_text().splitlines():
            fields = line.split()
            if fields and fields[0] == "MemTotal:":
                return "resident" if int(fields[1]) >= RESIDENT_MIN_KIB else "single"
    except (OSError, ValueError, IndexError):
        pass
    return "single"


class RuntimePreferences:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else default_config_path()

    def read(self) -> str:
        try:
            with self.path.open() as stream:
                value = json.loads(stream.read(4096))
            mode = value.get("mode") if isinstance(value, dict) else None
            return mode if mode in MODES else "auto"
        except (OSError, ValueError, UnicodeError):
            return "auto"

    def effective(self) -> str:
        mode = self.read()
        return detected_mode() if mode == "auto" else mode

    def save(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("Unknown runtime mode")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"mode": mode}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class AppJobs:
    """Keep one launch per desktop path; GTK is notified by the caller's bridge.

    No force termination or process inventory. Each worker uses the existing
    foreground lifecycle, including daemonized-window tracking.
    """
    def __init__(self, runner: Callable[[Path], int]):
        self.runner = runner
        self.lock = threading.Lock()
        self.jobs: set[Path] = set()

    def contains(self, path: Path) -> bool:
        with self.lock:
            return path.resolve() in self.jobs

    def count(self) -> int:
        with self.lock:
            return len(self.jobs)

    def start(self, path: Path, finished: Callable[[str], None]) -> bool:
        path = path.resolve()
        with self.lock:
            if path in self.jobs:
                return False
            self.jobs.add(path)

        def run():
            error = ""
            try:
                result = self.runner(path)
                if result:
                    error = f"应用已退出（状态 {result}）"
            except Exception as exc:
                error = f"无法启动应用：{exc}"
            finally:
                with self.lock:
                    self.jobs.discard(path)
                finished(error)

        try:
            threading.Thread(target=run, name="launcher-app", daemon=True).start()
        except RuntimeError:
            with self.lock:
                self.jobs.discard(path)
            raise
        return True
