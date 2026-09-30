"""Launch-scoped fullscreen via the compositor's foreign-toplevel protocol.

Only exact application IDs from the selected desktop entry are eligible.  The
version-3 parent event excludes transient dialogs, and set_fullscreen is
idempotent (unlike Labwc's ToggleFullscreen action).  No extra daemon, toolkit,
root permission, executable, or persistent window inventory is needed.

Wire definitions: wlr-protocols/unstable/
wlr-foreign-toplevel-management-unstable-v1.xml (version 3), and wayland.xml.
This small client binds no interfaces carrying file descriptors.
"""
from __future__ import annotations

import argparse
import json
import os
import select
import shlex
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .desktop import read_desktop

MANAGER = "zwlr_foreign_toplevel_manager_v1"
FULLSCREEN = 3
ACTIVATED = 2
_UINT = struct.Struct("=I")
_HEADER = struct.Struct("=II")
_GENERIC_COMMANDS = {
    "env", "sh", "bash", "dash", "python", "python3", "run.sh",
    "gtk-launch", "gio", "flatpak", "snap", "x-terminal-emulator",
}


def application_ids(path: Path) -> frozenset[str]:
    """Exact IDs only; wrappers can declare X-TypixDeck-FullscreenAppId."""
    loaded = read_desktop(path)
    if loaded is None:
        return frozenset()
    resolved, section = loaded
    explicit = section.get("X-TypixDeck-FullscreenAppId", "").strip()
    if explicit:
        return frozenset(value.strip().casefold() for value in explicit.split(";") if value.strip())
    values = {resolved.stem, section.get("StartupWMClass", "").strip()}
    try:
        command = shlex.split(section.get("Exec", ""))
    except ValueError:
        command = []
    if command:
        executable = Path(command[0]).name
        if executable not in _GENERIC_COMMANDS and not executable.startswith("python"):
            values.add(executable)
    return frozenset(value.casefold() for value in values if value)


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return _UINT.pack(len(encoded)) + encoded + b"\0" * (-len(encoded) % 4)


def _read_string(payload: bytes, offset: int = 0) -> tuple[str, int]:
    length = _UINT.unpack_from(payload, offset)[0]
    start = offset + 4
    end = start + length
    if length == 0 or end > len(payload) or payload[end - 1] != 0:
        raise ValueError("Malformed Wayland string")
    return payload[start:end - 1].decode("utf-8"), start + ((length + 3) & ~3)


@dataclass
class Toplevel:
    identifier: int
    app_id: str = ""
    title: str = ""
    parent: int = 0
    states: set[int] = field(default_factory=set)
    ready: bool = False


class ForeignToplevelClient:
    """Minimal bounded Wayland transport, also usable for read-only snapshots."""

    def __init__(self, connection: socket.socket | None = None) -> None:
        self.connection = connection
        self.windows: dict[int, Toplevel] = {}
        self.manager: int | None = None
        self.seat: int | None = None
        self._registry = 2
        self._next_id = 3
        self._callbacks: set[int] = set()
        self._pending = bytearray()
        self.on_done: Any = None
        self.on_closed: Any = None

    def _allocate(self) -> int:
        result = self._next_id
        self._next_id += 1
        return result

    def _send(self, identifier: int, opcode: int, payload: bytes = b"") -> None:
        if self.connection is None:
            raise OSError("Wayland connection is closed")
        self.connection.sendall(_HEADER.pack(identifier, ((len(payload) + 8) << 16) | opcode) + payload)

    def connect(self, timeout: float = 1.0) -> None:
        if self.connection is None:
            display = os.environ.get("WAYLAND_DISPLAY")
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if not display:
                raise OSError("No Wayland display")
            address = Path(display)
            if not address.is_absolute():
                if not runtime:
                    raise OSError("No Wayland runtime directory")
                address = Path(runtime) / address
            self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.connection.settimeout(timeout)
            self.connection.connect(str(address))
        self.connection.settimeout(timeout)
        self._send(1, 1, _UINT.pack(self._registry))  # wl_display.get_registry
        self._roundtrip(timeout)
        if self.manager is None:
            raise OSError("Compositor lacks foreign-toplevel version 3")
        self._roundtrip(timeout)  # Include every existing handle before launch.

    def _roundtrip(self, timeout: float) -> None:
        callback = self._allocate()
        self._callbacks.add(callback)
        self._send(1, 0, _UINT.pack(callback))
        deadline = time.monotonic() + timeout
        while callback in self._callbacks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Wayland snapshot timed out")
            self.poll(remaining)

    def poll(self, timeout: float = 0.1) -> None:
        if self.connection is None:
            raise OSError("Wayland connection is closed")
        if not select.select([self.connection], [], [], timeout)[0]:
            return
        chunk = self.connection.recv(65536)
        if not chunk:
            raise OSError("Compositor disconnected")
        self._pending.extend(chunk)
        while len(self._pending) >= 8:
            identifier, header = _HEADER.unpack_from(self._pending)
            size, opcode = header >> 16, header & 0xFFFF
            if size < 8 or size % 4:
                raise ValueError("Malformed Wayland message")
            if len(self._pending) < size:
                break
            payload = bytes(self._pending[8:size])
            del self._pending[:size]
            self._event(identifier, opcode, payload)

    def _event(self, identifier: int, opcode: int, payload: bytes) -> None:
        if identifier == 1:
            if opcode == 0:
                raise OSError("Compositor rejected foreign-toplevel request")
            return  # wl_display.delete_id; this bounded client never reuses IDs.
        if identifier in self._callbacks:
            self._callbacks.discard(identifier)
            return
        if identifier == self._registry:
            if opcode == 0:
                name = _UINT.unpack_from(payload)[0]
                interface, end = _read_string(payload, 4)
                version = _UINT.unpack_from(payload, end)[0]
                if interface == MANAGER and version >= 3 and self.manager is None:
                    self.manager = self._allocate()
                    self._send(self._registry, 0, _UINT.pack(name) + _string(MANAGER)
                               + struct.pack("=II", 3, self.manager))
                elif interface == "wl_seat" and self.seat is None:
                    self.seat = self._allocate()
                    self._send(self._registry, 0, _UINT.pack(name) + _string("wl_seat")
                               + struct.pack("=II", 1, self.seat))
            return
        if identifier == self.manager:
            if opcode == 0:
                handle = _UINT.unpack_from(payload)[0]
                self.windows[handle] = Toplevel(handle)
            elif opcode == 1:
                raise OSError("Compositor stopped foreign-toplevel observation")
            return
        window = self.windows.get(identifier)
        if window is None:
            return
        if opcode == 0:
            window.title, _ = _read_string(payload)
        elif opcode == 1:
            window.app_id, _ = _read_string(payload)
        elif opcode == 4:
            length = _UINT.unpack_from(payload)[0]
            if length % 4 or length + 4 != len(payload):
                raise ValueError("Malformed Wayland state array")
            window.states = set(struct.unpack(f"={length // 4}I", payload[4:]))
        elif opcode == 5:
            window.ready = True
            if self.on_done is not None:
                self.on_done(window)
        elif opcode == 6:
            self.windows.pop(identifier, None)
            if self.on_closed is not None:
                self.on_closed(window)
            self._send(identifier, 7)  # handle.destroy, after closed.
        elif opcode == 7:
            window.parent = _UINT.unpack_from(payload)[0]

    def set_fullscreen(self, window: Toplevel) -> None:
        self._send(window.identifier, 8, _UINT.pack(0))  # compositor selects output

    def activate(self, window: Toplevel) -> None:
        if self.seat is None:
            raise OSError("No compositor seat for activation")
        self._send(window.identifier, 3)  # unset_minimized
        self._send(window.identifier, 4, _UINT.pack(self.seat))

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {"app_id": item.app_id, "title": item.title, "parent": item.parent,
             "fullscreen": FULLSCREEN in item.states}
            for item in self.windows.values() if item.ready
        ]

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None


def activate_existing(path: Path) -> str:
    """Return activated/unavailable/none/unsupported without spawning a process.

    A known existing window is never reported as absent merely because the
    compositor denies focus; callers can avoid accidentally duplicating it.
    """
    ids = application_ids(path)
    if not ids or not os.environ.get("WAYLAND_DISPLAY"):
        return "unsupported"
    client = ForeignToplevelClient()
    found = False
    try:
        client.connect(timeout=0.7)
        matches = [item for item in client.windows.values()
                   if item.ready and not item.parent and item.app_id.casefold() in ids]
        if not matches:
            return "none"
        found = True
        if len(matches) != 1:
            # App IDs do not identify a particular document/window. Let the
            # user choose with Alt+Tab instead of focusing an arbitrary one.
            return "unavailable"
        window = matches[0]
        client.activate(window)
        client.set_fullscreen(window)
        client._roundtrip(0.7)
        return "activated" if ACTIVATED in window.states else "unavailable"
    except (OSError, ValueError, UnicodeError, struct.error):
        return "unavailable" if found else "unsupported"
    finally:
        client.close()


class FullscreenSession:
    """Snapshot before spawning, fullscreen on map, then wait for app closure.

    An exact match that becomes active may reuse a pre-existing window.  Other
    pre-existing windows are never changed.  Unconfirmed fullscreen requests
    are retried during startup; after confirmation the user can leave normally.
    """

    def __init__(self, path: Path, startup_timeout: float = 10.0) -> None:
        self.app_ids = application_ids(path)
        self.startup_timeout = startup_timeout
        self.client: ForeignToplevelClient | None = None
        self._baseline: set[int] = set()
        self._targets: set[int] = set()
        self._confirmed: set[int] = set()
        self._last_request: dict[int, float] = {}
        self._ever_matched = False
        self._deadline = 0.0

    def __enter__(self) -> FullscreenSession:
        if not self.app_ids or not os.environ.get("WAYLAND_DISPLAY"):
            return self
        candidate = ForeignToplevelClient()
        try:
            candidate.connect()
            self._baseline = set(candidate.windows)
            candidate.on_done = self._consider
            candidate.on_closed = self._closed
            self.client = candidate
        except (OSError, ValueError, UnicodeError, struct.error) as exc:
            candidate.close()
            self._diagnostic(exc)
        return self

    @staticmethod
    def _diagnostic(exc: Exception) -> None:
        print(f"TypixDeck fullscreen unavailable: {exc}", file=sys.stderr, flush=True)

    def _consider(self, window: Toplevel) -> None:
        now = time.monotonic()
        if (now > self._deadline or window.parent
                or window.app_id.casefold() not in self.app_ids
                or window.identifier in self._confirmed):
            return
        # A DBus/single-instance launch may activate its already-open window.
        if (window.identifier in self._baseline and window.identifier not in self._targets
                and ACTIVATED not in window.states):
            return
        self._targets.add(window.identifier)
        self._ever_matched = True
        if FULLSCREEN in window.states:
            self._confirmed.add(window.identifier)
            self._last_request.pop(window.identifier, None)
            return
        previous = self._last_request.get(window.identifier)
        if self.client is not None and (previous is None or now - previous >= 0.5):
            self.client.set_fullscreen(window)
            self._last_request[window.identifier] = now

    def _closed(self, window: Toplevel) -> None:
        # The server may reuse a destroyed handle ID for a replacement window.
        self._targets.discard(window.identifier)
        self._confirmed.discard(window.identifier)
        self._last_request.pop(window.identifier, None)
        self._baseline.discard(window.identifier)

    def wait(self, child: Any) -> int:
        if self.client is None:
            return child.wait()
        self._deadline = time.monotonic() + self.startup_timeout
        empty_since: float | None = None
        try:
            while True:
                code = child.poll()
                now = time.monotonic()
                active = self._targets.intersection(self.client.windows)
                if active:
                    empty_since = None
                if code is not None and not active:
                    if code != 0 or now >= self._deadline:
                        return code
                    if self._ever_matched:
                        # Splash and main windows can have a short unmapped gap
                        # after a successful daemonizing wrapper has exited.
                        # This grace never extends the startup deadline.
                        if empty_since is None:
                            empty_since = now
                        if now - empty_since >= 0.75:
                            return code
                if now >= self._deadline and not self._ever_matched:
                    # No matching window: release observation and retain the
                    # established child-process lifecycle fallback.
                    self.client.close()
                    return child.wait()
                # Labwc can ignore a request while an output is asleep. Retry
                # only until confirmation/deadline; never wake an output or
                # override the user's later decision to leave fullscreen.
                if now < self._deadline:
                    for identifier in self._targets - self._confirmed:
                        window = self.client.windows.get(identifier)
                        if window is not None:
                            self._consider(window)
                self.client.poll(0.1)
        except (OSError, ValueError, UnicodeError, struct.error) as exc:
            self._diagnostic(exc)
            self.client.close()
            return child.wait()

    def __exit__(self, *_exc: Any) -> None:
        if self.client is not None:
            self.client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only compositor window snapshot (JSON)")
    parser.add_argument("--snapshot", action="store_true", required=True)
    parser.parse_args()
    client = ForeignToplevelClient()
    try:
        client.connect()
        print(json.dumps(client.snapshot(), ensure_ascii=False))
        return 0
    except (OSError, ValueError, UnicodeError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
