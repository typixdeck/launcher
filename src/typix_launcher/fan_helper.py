"""Fixed-argument, short-lived root helper called by Polkit.

The GUI cannot supply paths, units, commands, environments or a fan target.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import syslog
import time

from .fan import (CONFIG, RUNTIME, SERVICE, STATUS, FanError, discover,
                  read_root_json, secure_directory, validate_config, validate_status)
from .fan_service import atomic_root_json, failsafe, root_lock


class RecoveryError(FanError):
    """Preferences or service state could not be restored safely."""


def rollback(old: dict | None, active: bool, enabled: bool, changed: bool, prior_checked: bool, device) -> None:
    """Try every cleanup independently, with a full-speed request last."""
    errors = []
    restored = prior_checked
    try:
        if changed:
            try:
                if old is None:
                    CONFIG.unlink(missing_ok=True)
                    if CONFIG.exists() or CONFIG.is_symlink():
                        raise FanError('未能移除失败的首次风扇配置')
                else:
                    atomic_root_json(CONFIG, old)
                    if validate_config(read_root_json(CONFIG)) != old:
                        raise FanError('原风扇配置恢复后核对失败')
                restored = True
            except BaseException as error:
                restored = False
                errors.append('configuration restore: ' + str(error))
        # If the former preference cannot be verified, do not allow the
        # failed new preference to keep running or return on the next boot.
        if not active or not restored:
            try:
                if systemctl('stop').returncode:
                    raise FanError('系统未确认停止风扇服务')
            except BaseException as error:
                errors.append('service stop: ' + str(error))
        if not enabled or not restored:
            try:
                if systemctl('disable').returncode:
                    raise FanError('系统未确认禁用风扇服务')
            except BaseException as error:
                errors.append('service disable: ' + str(error))
    finally:
        # This also runs when a preceding restore/stop/disable raises.
        failsafe(previous=device)
    if errors or not restored:
        syslog.syslog(syslog.LOG_CRIT, 'fan rollback incomplete; administrator recovery required; requested full speed; ' + '; '.join(errors))
        raise RecoveryError('风扇设置未能安全恢复，需要管理员检查配置和系统服务')
    syslog.syslog(syslog.LOG_ERR, 'fan setting failed; previous preference retained; requested full speed')


def arguments(argv: list[str]) -> dict:
    if argv == ['auto']:
        return {'version': 1, 'mode': 'auto', 'percent': 50}
    if len(argv) == 2 and argv[0] == 'manual' and re.fullmatch(r'[0-9]{2,3}', argv[1]):
        return validate_config({'version': 1, 'mode': 'manual', 'percent': int(argv[1])})
    raise FanError('只接受 auto 或 manual 30–100')


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['/usr/bin/systemctl', *args, SERVICE], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})


def apply_config(config: dict) -> None:
    validate_config(config)
    device = discover()  # Refuse wrong device or thermal conflicts before writing.
    secure_directory(CONFIG.parent, create=True)
    secure_directory(RUNTIME, create=True)
    descriptor = root_lock(RUNTIME / 'apply.lock')
    old = None
    active = False
    enabled = False
    changed = False
    prior_checked = False
    try:
        try:
            old = validate_config(read_root_json(CONFIG))
        except FileNotFoundError:
            pass
        prior_checked = True
        active = systemctl('is-active', '--quiet').returncode == 0
        enabled = systemctl('is-enabled', '--quiet').returncode == 0
        changed = True
        # Directory fsync can fail after the rename has already committed.
        # Rollback must be attempted even when the atomic writer raises.
        atomic_root_json(CONFIG, config)
        result = systemctl('enable', '--now')
        if result.returncode:
            raise FanError('系统无法启动风扇服务')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                status = validate_status(read_root_json(STATUS))
                if (status.get('mode') == config['mode'] and status.get('requested_percent') == config['percent']
                        and 0 <= time.monotonic() - status.get('updated', -999) <= 5):
                    syslog.syslog(syslog.LOG_NOTICE, 'fan authorized setting: mode=%s percent=%d' % (config['mode'], config['percent']))
                    return
            except (OSError, ValueError, TypeError, FanError):
                pass
            time.sleep(0.2)
        raise FanError('风扇服务未确认设置')
    except BaseException:
        rollback(old, active, enabled, changed, prior_checked, device)
        raise
    finally:
        os.close(descriptor)


def main() -> int:
    syslog.openlog('typix-launcher-fan', syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    try:
        config = arguments(sys.argv[1:])
        if os.geteuid() != 0:
            raise FanError('需要系统授权')
        apply_config(config)
        return 0
    except RecoveryError as error:
        syslog.syslog(syslog.LOG_CRIT, str(error))
        print(str(error), file=sys.stderr)
        return 70
    except (OSError, ValueError, UnicodeError, FanError, subprocess.SubprocessError) as error:
        syslog.syslog(syslog.LOG_ERR, 'fan authorized helper failed: ' + str(error))
        print('无法应用风扇设置；请检查风扇服务和系统日志。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
