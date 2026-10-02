"""Root-only package hooks preserving an active fan service across upgrades."""
from __future__ import annotations

import os
import subprocess
import sys
import syslog

from .fan import RUNTIME, FanError, read_root_json, secure_directory
from .fan_helper import systemctl
from .fan_service import atomic_root_json, root_lock

MARKER = RUNTIME / 'restart-on-upgrade.json'


def pending_restart() -> bool:
    try:
        value = read_root_json(MARKER)
    except FileNotFoundError:
        return False
    if (set(value) != {'version', 'restart'} or type(value['version']) is not int
            or value['version'] != 1 or value['restart'] is not True):
        raise FanError('风扇升级恢复记录无效')
    return True


def maintain(action: str) -> None:
    if action not in {'pre-upgrade', 'post-configure', 'pre-remove'}:
        raise FanError('风扇包维护参数错误')
    if action == 'pre-remove' and not RUNTIME.exists():
        stopped = systemctl('stop').returncode == 0
        disabled = systemctl('disable').returncode == 0
        if not stopped or not disabled:
            raise FanError('风扇服务无法停止')
        return
    if action == 'post-configure' and not RUNTIME.exists():
        return  # First installation never starts a service.
    secure_directory(RUNTIME, create=action == 'pre-upgrade')
    lock = root_lock(RUNTIME / 'apply.lock')
    try:
        if action == 'pre-remove':
            stopped = systemctl('stop').returncode == 0
            disabled = systemctl('disable').returncode == 0
            if not stopped or not disabled:
                raise FanError('风扇服务无法停止')
            # A removed opt-in must not be resurrected on a later reinstall.
            if pending_restart():
                MARKER.unlink()
        elif action == 'pre-upgrade':
            active = systemctl('is-active', '--quiet').returncode == 0
            if active and not pending_restart():
                # Checkpoint before stop. Low storage must not stop a service
                # whose former state could no longer be recovered.
                atomic_root_json(MARKER, {'version': 1, 'restart': True})
            if active and systemctl('stop').returncode:
                raise FanError('风扇服务无法安全停止')
        elif pending_restart():
            if systemctl('start').returncode:
                raise FanError('风扇升级后无法恢复；保留恢复记录')
            MARKER.unlink()
        syslog.syslog(syslog.LOG_NOTICE, 'fan package maintenance: ' + action)
    finally:
        os.close(lock)


def main() -> int:
    try:
        if os.geteuid() != 0 or not sys.flags.isolated or len(sys.argv) != 2:
            raise FanError('风扇包维护只能由系统包管理器调用')
        maintain(sys.argv[1])
        return 0
    except (OSError, ValueError, FanError, subprocess.SubprocessError) as error:
        syslog.syslog(syslog.LOG_ERR, 'fan package maintenance failed: ' + str(error))
        print('风扇服务维护未完成，请检查系统日志。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
