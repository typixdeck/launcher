"""Private one-shot foreground handoff, consumed only after the parent exits."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile

from . import desktop

ENV = "TYPIX_LAUNCHER_HANDOFF"
NAME = re.compile(r"handoff-([0-9a-f]{32})\.json")


def launch_entry(name: str, package: str | None = None) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,155}\.desktop", name):
        raise ValueError("应用入口名称无效")
    if package is not None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,79}", package):
            raise ValueError("应用软件包名称无效")
        path = Path("/usr/share/applications") / name
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("已安装入口必须由系统软件包管理")
            if item == path and (not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024):
                raise ValueError("已安装入口必须是有界的普通文件")
        state = subprocess.run(["dpkg-query", "--show", "--showformat=${Status}", package],
                               capture_output=True, text=True, timeout=5, check=False)
        owner = subprocess.run(["dpkg-query", "--search", str(path)], capture_output=True,
                               text=True, timeout=5, check=False)
        lines = owner.stdout.strip().splitlines()
        if (state.returncode != 0 or state.stdout != "install ok installed" or owner.returncode != 0
                or len(lines) != 1 or lines[0].rsplit(": ", 1)[-1] != str(path)
                or lines[0].rsplit(": ", 1)[0].split(":", 1)[0] != package):
            raise ValueError("无法确认此已安装应用入口属于所选软件包")
        entry = desktop.read_desktop(path, resolve_link=False)
        if not entry or entry[1].get("Type") != "Application" or not entry[1].get("Exec"):
            raise ValueError("已安装应用入口无效")
        return path
    for app in desktop.load_apps():
        if app.path.name == name:
            return app.path
    raise ValueError("桌面没有此应用入口，请重新打开 Store 自动检查快捷方式")


def directory() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
    if not base.is_absolute():
        raise ValueError("启动请求目录必须是绝对路径")
    root = base / ("typix-launcher-handoff-" + str(os.getuid()))
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("启动请求目录不可用")
    return root


def read_request(path: Path) -> dict:
    if path.parent != directory() or not NAME.fullmatch(path.name):
        raise ValueError("启动请求路径无效")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 2048:
            raise ValueError("启动请求权限无效")
        data = json.loads(stream.read(2049))
    if (not isinstance(data, dict) or set(data) != {"schema", "token", "desktop", "package"}
            or type(data["schema"]) is not int or data["schema"] != 1 or data["token"] != NAME.fullmatch(path.name)[1]
            or data["desktop"] is not None and not isinstance(data["desktop"], str)
            or data["package"] is not None and not isinstance(data["package"], str)):
        raise ValueError("启动请求内容无效")
    return data


def queue(name: str, package: str | None = None) -> bool:
    """Return False without a single-mode parent; never invent a new owner."""
    value = os.environ.get(ENV)
    if not value:
        return False
    launch_entry(name, package)
    path = Path(value)
    data = read_request(path)
    if data["desktop"] is not None:
        raise ValueError("已有待启动应用，请等待当前界面退出")
    if package == "typix-launcher":
        return True  # Closing Store restores the parent's UI directly.
    data["desktop"] = name
    data["package"] = package
    fd, temporary = tempfile.mkstemp(prefix=".request-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


class SingleHandoff:
    def __enter__(self):
        token = secrets.token_hex(16)
        self.path = directory() / ("handoff-" + token + ".json")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"schema": 1, "token": token, "desktop": None, "package": None}, stream)
        return self

    def selected(self) -> Path | None:
        data = read_request(self.path)
        return launch_entry(data["desktop"], data["package"]) if data["desktop"] is not None else None

    def __exit__(self, *_args):
        self.path.unlink(missing_ok=True)
