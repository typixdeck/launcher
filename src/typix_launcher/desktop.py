"""FreeDesktop desktop-entry discovery and launch-request helpers."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")


def default_desktop_dirs() -> tuple[Path, ...]:
    """Show only shortcuts on the user's desktop, including localized desktops."""
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
            check=False, timeout=2,
        )
        directory = Path(result.stdout.strip())
        if result.returncode == 0 and directory.is_absolute():
            if directory == Path.home():
                return ()  # XDG uses HOME to indicate a disabled desktop.
            return (directory,)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return (Path.home() / "Desktop",)


def is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def language_tag() -> str:
    language = os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LANG", "")
    return language.split(":", 1)[0].split(".", 1)[0]


def localized(section: configparser.SectionProxy, key: str) -> str:
    language = language_tag()
    candidates: list[str] = []
    if language:
        candidates.append(f"{key}[{language}]")
        if "_" in language:
            candidates.append(f"{key}[{language.split('_', 1)[0]}]")
    candidates.append(key)
    for candidate in candidates:
        value = section.get(candidate, "").strip()
        if value:
            return value
    return ""


def read_desktop(path: Path, resolve_link: bool = True) -> tuple[Path, configparser.SectionProxy] | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
        section = parser["Desktop Entry"]
    except (OSError, UnicodeError, KeyError, configparser.Error):
        return None
    if resolve_link and section.get("Type", "Application") == "Link":
        target = section.get("URL", "")
        parsed = urlsplit(target)
        if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
            target_path = Path(unquote(parsed.path))
        elif not parsed.scheme:
            target_path = Path(target)
        else:
            return None
        if target_path.is_absolute() and target_path.suffix == ".desktop":
            return read_desktop(target_path, resolve_link=False)
    return path, section


def category_name(categories: str) -> str:
    values = set(categories.split(";"))
    if values & {"Game", "Emulator"}:
        return "游戏"
    if values & {"AudioVideo", "Audio", "Video", "Player"}:
        return "影音"
    if values & {"Network", "WebBrowser"}:
        return "网络"
    if values & {"Settings", "System"}:
        return "系统"
    if values & {"Utility", "Office"}:
        return "工具"
    return "应用"


def integrated_icon(path: Path, icon: str) -> str:
    """Use Store's bundled logo only when the shortcut has a generic icon."""
    if icon not in {"", "DesktopIcon", "application-x-executable", "applications-other",
                    "accessories-text-editor", "applications-games", "audio-input-microphone",
                    "system-software-install", "preferences-system", "system-run", "internet-chat",
                    "web-browser", "applications-system"}:
        return icon
    root = Path("/usr/share/typix-store/catalog-icons")
    try:
        manifest = root / "manifest.json"
        if manifest.stat().st_size > 64 * 1024:
            return icon
        data = json.loads(manifest.read_bytes())
        item = data["icons"][path.resolve().stem]
        name = item["filename"]
        size = item["bytes"]
        if (type(data["schema"]) is not int or data["schema"] != 1 or type(size) is not int
                or not 0 < size <= 2 * 1024 * 1024 or not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]+\.(png|svg)", name)):
            return icon
        logo = root / name
        if (logo.is_file() and not logo.is_symlink() and logo.stat().st_size == size
                and hashlib.sha256(logo.read_bytes()).hexdigest() == item["sha256"]):
            return str(logo)
        return icon
    except (OSError, ValueError, KeyError, TypeError):
        return icon


@dataclass(frozen=True)
class DesktopEntry:
    path: Path
    name: str
    comment: str
    category: str
    icon: str
    priority: int


def load_apps() -> list[DesktopEntry]:
    apps: list[DesktopEntry] = []
    seen: set[str] = set()
    for priority, directory in enumerate(default_desktop_dirs()):
        try:
            paths = sorted(directory.glob("*.desktop"), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for shortcut in paths:
            loaded = read_desktop(shortcut)
            if loaded is None:
                continue
            path, section = loaded
            identity = path.name.casefold()
            if identity in seen:
                continue
            if section.get("Type", "Application") != "Application":
                continue
            if is_true(section.get("Hidden", "false")) or is_true(section.get("NoDisplay", "false")):
                continue
            if is_true(section.get("X-TypixNode-Exclude", "false")) or is_true(section.get("X-TypixDeck-Exclude", "false")):
                continue
            command = section.get("Exec", "").strip()
            name = localized(section, "Name")
            if not command or not name:
                continue
            try_exec = section.get("TryExec", "").strip()
            if try_exec and not (Path(try_exec).exists() or shutil.which(try_exec)):
                continue
            seen.add(identity)
            category = category_name(section.get("Categories", ""))
            apps.append(
                DesktopEntry(
                    path=path,
                    name=name,
                    comment=localized(section, "Comment") or category,
                    category=category,
                    icon=integrated_icon(path, section.get("Icon", "application-x-executable").strip()),
                    priority=priority,
                )
            )
    apps.sort(key=lambda app: (app.priority, app.name.casefold()))
    return apps


def expand_exec(path: Path) -> tuple[list[str], Path | None, bool]:
    loaded = read_desktop(path, resolve_link=False)
    if loaded is None:
        raise ValueError(f"无法读取启动项：{path}")
    _, section = loaded
    name = localized(section, "Name")
    icon = integrated_icon(path, section.get("Icon", "").strip())
    command: list[str] = []
    for token in shlex.split(section.get("Exec", "")):
        if token in {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m"}:
            continue
        if token == "%i":
            if icon:
                command.extend(("--icon", icon))
            continue
        token = token.replace("%%", "\0")
        token = token.replace("%c", name).replace("%k", str(path))
        token = FIELD_CODE.sub("", token).replace("\0", "%")
        if token:
            command.append(token)
    if not command:
        raise ValueError(f"启动项没有可执行命令：{path}")
    working_dir = section.get("Path", "").strip()
    return command, Path(working_dir) if working_dir else None, is_true(section.get("Terminal", "false"))


def request_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "typix-launcher-request"


def atomic_request(path: Path) -> None:
    destination = request_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(str(path), encoding="utf-8")
    os.replace(temporary, destination)
