"""Short-lived, rootless CW2015 diagnostic reader for a Copilot-bound board.

Shares the root-owned Copilot lock; never resets or switches the ESP/display.
Only a strict battery record is returned. Raw CDC bytes are never persisted.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import select
import stat
import termios
import time
import tty
from contextlib import contextmanager

FIELDS = 'stc_err mode ctrl counter soc_raw mv current_raw ocv_raw cc vm ram_err ram_ok seeded cw_err cw_mv cw_soc cw_mode'.split()
RECORD = re.compile(rb'TD_BATT v=2 ' + b' '.join(k.encode() + rb'=(-?[0-9]{1,6})' for k in FIELDS))


def parse_battery(line: bytes) -> dict | None:
    match = RECORD.fullmatch(line)
    if not match:
        return None
    data = dict(zip(FIELDS, map(int, match.groups())))
    # STC raw registers alone do not prove calibrated/fresh SOC. Leave their
    # interpretation to the firmware; report CW2015 explicitly as a reference.
    if (data['cw_err'] != 0 or not 2500 <= data['cw_mv'] <= 4500 or
            not 0 <= data['cw_soc'] <= 100 or not 0 <= data['cw_mode'] <= 255 or
            data['cw_mode'] & 0xc0):
        return None
    return {'percent': data['cw_soc'], 'mv': data['cw_mv'], 'source': 'CW2015', 'estimated': True}


@contextmanager
def shared_writer_lock(path=Path('/run/lock/typix-copilot-write.lock')):
    # Missing/inaccessible lock means unavailable. Never create or replace it
    # as a desktop user. tmpfiles installs it with a read-only user permission.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError('unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def query_battery() -> dict | None:
    # Optional integration: no Copilot/profile/permissions => unknown, not a
    # fallback to the first ttyACM or another USB device.
    from typix_copilot.device import Board, load_profile
    from typix_copilot.writer import port_in_use
    with shared_writer_lock():
        board = Board(load_profile())
        endpoint = board.probe()
        if endpoint.mode != 'runtime' or port_in_use(endpoint):
            return None
        fd = os.open(endpoint.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_rdev != endpoint.device_number:
                return None
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            board.same(endpoint)
            if port_in_use(endpoint):
                return None
            attributes = termios.tcgetattr(fd)
            attributes[2] &= ~termios.HUPCL
            attributes[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attributes)
            tty.setraw(fd, termios.TCSANOW)
            # No DTR/RTS toggles, baud tricks, reset, MUX or register writes.
            # Drain at most 64 KiB of old logs first; never accept stale data.
            drained = 0
            while select.select([fd], [], [], 0)[0]:
                chunk = os.read(fd, 1024)
                if not chunk:
                    return None
                drained += len(chunk)
                if drained >= 65536:
                    return None
            command = b'\nBATTERY_STATUS\n'
            if os.write(fd, command) != len(command):
                return None
            deadline = time.monotonic() + 2
            buffer = bytearray()
            discard = False
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                    break
                data = os.read(fd, 1024)
                if not data:
                    break
                for byte in data:
                    if byte in (10, 13):
                        result = None if discard else parse_battery(bytes(buffer))
                        buffer.clear()
                        discard = False
                        if result is not None:
                            board.same(endpoint)
                            return result
                    elif not discard:
                        if 32 <= byte <= 126 and len(buffer) < 383:
                            buffer.append(byte)
                        else:
                            buffer.clear()
                            discard = True
            return None
        finally:
            os.close(fd)


def main():
    try:
        result = query_battery()
    except Exception:
        # No tracebacks containing device paths/stream contents in GUI logs.
        result = None
    print(json.dumps(result))


if __name__ == '__main__':
    main()
