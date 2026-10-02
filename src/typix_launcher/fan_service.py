"""Small system fan controller. Runs only after an authorized opt-in."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
import syslog
import time
import uuid

from .fan import (CONFIG, RUNTIME, STATUS, SYS, FanDevice, FanError, discover,
                  read_root_json, readings, secure_directory, validate_config, verify_device,
                  config_curve, curve_percent)


def atomic_root_json(path: Path, value: dict) -> None:
    secure_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_root_json(path)  # Refuse unsafe existing files, never follow them.
    temporary = path.parent / ('.fan-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        payload = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode('ascii')
        os.fchmod(descriptor, 0o644)
        position = 0
        while position < len(payload):
            written = os.write(descriptor, payload[position:])
            if written <= 0:
                raise OSError('风扇配置无法完整写入')
            position += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def root_lock(path: Path):
    secure_directory(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022 or item.st_nlink != 1:
            raise FanError('风扇锁文件不安全')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_percent(device: FanDevice, percent: int, sys_root: Path = SYS) -> int:
    if type(percent) is not int or not 30 <= percent <= 100:
        raise FanError('风扇输出超出安全范围')
    resolved = device.pwm.resolve(strict=True)
    if resolved != device.pwm or not (sys_root.resolve() / 'devices') in resolved.parents:
        raise FanError('PWM 文件路径不可信')
    descriptor = os.open(resolved, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(descriptor)
        if item.st_uid != 0 or item.st_mode & 0o022 or not stat.S_ISREG(item.st_mode):
            raise FanError('PWM 文件权限不安全')
        os.write(descriptor, (str(round(percent * 255 / 100)) + '\n').encode('ascii'))
    finally:
        os.close(descriptor)
    # Kernel cooling-state quantization may differ from the requested duty.
    actual, _, _ = readings(device, sys_root)
    if actual is None or actual < 30 or actual < percent - 11 or (percent == 100 and actual != 100):
        raise FanError('PWM 写入后读回异常')
    return actual


class FanPolicy:
    """Temperature tiers with 2 C hysteresis and a tachometer fail-safe."""
    def __init__(self) -> None:
        self.level = 0
        self.zero_count = 0
        self.curve_signature = None
        self.held_temperature = None

    def choose(self, config: dict, temperature: float | None, rpm: int | None) -> tuple[int, str]:
        validate_config(config)
        if rpm == 0:
            self.zero_count += 1
        else:
            self.zero_count = 0
        if temperature is None:
            return 100, 'CPU 温度不可用，已保持全速保护'
        if rpm is None:
            return 100, '转速反馈读取失败，已保持全速保护'
        if self.zero_count >= 3:
            return 100, '持续未收到转速反馈，已保持全速；请检查 CN5 接线'
        if rpm == 0:
            return 100, '正在等待转速反馈，保持全速启动'
        if temperature >= 80:
            return 100, 'CPU 温度达到 80°C，已保持全速保护'
        if config['mode'] == 'manual':
            self.curve_signature = None
            return config['percent'], ''
        if config['version'] == 2:
            points = config_curve(config)
            if points != self.curve_signature or self.held_temperature is None:
                self.curve_signature = points
                self.held_temperature = temperature
            elif temperature >= self.held_temperature:
                self.held_temperature = temperature
            elif temperature < self.held_temperature - 2:
                self.held_temperature = temperature + 2
            return curve_percent(points, self.held_temperature), ''
        self.curve_signature = None
        thresholds = (55, 65, 75)
        target = sum(temperature >= item for item in thresholds)
        if target >= self.level:
            self.level = target
        else:
            while self.level > target and temperature < thresholds[self.level - 1] - 2:
                self.level -= 1
        return (30, 45, 65, 100)[self.level], ''


def failsafe(sys_root: Path = SYS, previous: FanDevice | None = None) -> None:
    """Raise only a verified CN5 output; safe even when thermal owns it."""
    try:
        if previous is not None:
            # Revalidate the same target, without letting a new unrelated
            # controller prevent a full-speed recovery of the previous one.
            device = verify_device(previous.hwmon, sys_root, allow_thermal=True)
            if device.client != previous.client:
                raise FanError('恢复目标与原设备不一致')
        else:
            candidates = []
            for entry in (sys_root / 'class/hwmon').glob('hwmon*'):
                try:
                    candidates.append(verify_device(entry, sys_root, allow_thermal=True))
                except (OSError, ValueError, UnicodeError, FanError):
                    continue
            if len(candidates) != 1:
                raise FanError('不能唯一确认板载风扇恢复目标')
            device = candidates[0]
        write_percent(device, 100, sys_root)
    except (OSError, ValueError, UnicodeError, FanError) as error:
        syslog.syslog(syslog.LOG_ERR, 'fan full-speed fallback unavailable: ' + str(error))


def run() -> int:
    if os.geteuid() != 0:
        raise FanError('风扇服务必须由系统服务启动')
    secure_directory(RUNTIME, create=True)
    descriptor = root_lock(RUNTIME / 'service.lock')
    stop = False

    def stopped(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    policy = FanPolicy()
    device = None
    previous_message = None
    try:
        device = discover()
        write_percent(device, 100)
        # A short full-speed startup kick avoids a stalled low-duty start.
        time.sleep(2)
        while not stop:
            detected = discover()
            if detected.client != device.client:
                raise FanError('风扇目标设备发生变化')
            device = detected
            config = validate_config(read_root_json(CONFIG))
            _, rpm, temperature = readings(device)
            percent, message = policy.choose(config, temperature, rpm)
            actual = write_percent(device, percent)
            status = {'version': config['version'], 'mode': config['mode'], 'requested_percent': config['percent'],
                                     'percent': actual, 'rpm': rpm, 'temperature': temperature,
                                     'updated': time.monotonic(), 'error': 'protection' if message else '', 'message': message}
            if config['version'] == 2:
                status['curve'] = config['curve']
            atomic_root_json(STATUS, status)
            if message != previous_message:
                syslog.syslog(syslog.LOG_WARNING if message else syslog.LOG_INFO, message or 'fan normal control restored')
                previous_message = message
            time.sleep(2)
        return 0
    finally:
        failsafe(previous=device)
        os.close(descriptor)


def main() -> int:
    syslog.openlog('typix-launcher-fan', syslog.LOG_PID, syslog.LOG_DAEMON)
    try:
        if sys.argv[1:] == ['--failsafe'] and os.geteuid() == 0:
            failsafe()
            return 0
        if sys.argv[1:]:
            raise FanError('不支持的风扇服务参数')
        return run()
    except (OSError, ValueError, UnicodeError, FanError) as error:
        syslog.syslog(syslog.LOG_ERR, 'fan service failed: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
