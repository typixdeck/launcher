"""Passive CN5 fan discovery and the narrowly scoped authorization client.

Only a kernel-registered EMC2301 on the documented CM I2C0 controller is
eligible. No I2C scans, GPIO writes, overlay changes, or client creation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time

SYS = Path('/sys')
CONFIG = Path('/etc/typix-launcher/fan.json')
RUNTIME = Path('/run/typix-launcher-fan')
STATUS = RUNTIME / 'status.json'
HELPER = '/usr/libexec/typix-launcher-fan'
SERVICE = 'typix-launcher-fan.service'


class FanError(RuntimeError):
    pass


@dataclass(frozen=True)
class FanState:
    available: bool = False
    mode: str = 'unavailable'
    requested_percent: int = 50
    percent: int | None = None
    rpm: int | None = None
    temperature: float | None = None
    error: str = ''
    message: str = ''


@dataclass(frozen=True)
class FanDevice:
    hwmon: Path
    client: Path

    @property
    def pwm(self) -> Path:
        return self.hwmon / 'pwm1'


def _text(path: Path, limit: int = 4096) -> str:
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise FanError('设备属性过长')
    return data.decode('ascii').strip()


def _integer(path: Path, low: int, high: int) -> int:
    value = _text(path)
    if not re.fullmatch(r'-?[0-9]{1,9}', value):
        raise FanError('设备读数无效')
    result = int(value)
    if not low <= result <= high:
        raise FanError('设备读数超出范围')
    return result


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def verify_device(hwmon: Path, sys_root: Path = SYS, *, allow_thermal: bool = False) -> FanDevice:
    """Validate a specific kernel path, including during full-speed recovery."""
    root = sys_root.resolve(strict=True)
    hwmon = hwmon.resolve(strict=True)
    if _text(hwmon / 'name') != 'emc2305':
        raise FanError('不是 EMC2301 内核设备')
    client = (hwmon / 'device').resolve(strict=True) if (hwmon / 'device').exists() else hwmon.parent.parent
    if not _within(hwmon, root / 'devices') or not _within(client, root / 'devices'):
        raise FanError('风扇设备路径不可信')
    if not re.fullmatch(r'[0-9]+-002f', client.name):
        raise FanError('不是板载 CN5 控制器地址')
    if (client / 'driver').resolve(strict=True).name != 'emc2305':
        raise FanError('风扇未绑定 EMC2301 内核驱动')
    # Locate the adapter through this client's ancestry, never by i2c-N.
    adapter = client.parent
    if not re.fullmatch(r'i2c-[0-9]+', adapter.name):
        raise FanError('风扇 I2C 控制器路径不匹配')
    of_node = (adapter / 'of_node').resolve(strict=True)
    if not _within(of_node, root / 'firmware/devicetree/base') or not str(of_node).endswith('/soc/i2c@7e205000'):
        raise FanError('风扇不在已验证的 CM_SDA0 / CM_SCL0 控制器上')
    compatible = _text(of_node / 'compatible').split('\0')
    if 'brcm,bcm2835-i2c' not in compatible:
        raise FanError('板载 I2C 控制器类型不匹配')
    device_node = (client / 'of_node').resolve(strict=True)
    if not _within(device_node, of_node) or 'microchip,emc2301' not in _text(device_node / 'compatible').split('\0'):
        raise FanError('设备树不是板载 EMC2301')
    # EMC2301 has exactly one physical channel. Reject EMC2302/3/5 aliases.
    if (hwmon / 'pwm2').exists() or not (hwmon / 'pwm1').is_file() or not (hwmon / 'fan1_input').is_file():
        raise FanError('风扇通道与板载 EMC2301 不一致')
    if not allow_thermal:
        for zone in (root / 'class/thermal').glob('thermal_zone*'):
            for link in zone.glob('cdev[0-9]*'):
                if not link.is_symlink():
                    continue
                target = link.resolve(strict=True)
                if _text(target / 'type').startswith('emc2305'):
                    raise FanError('内核温控已管理风扇，不能同时启用 Launcher 调速')
    return FanDevice(hwmon, client)


def discover(sys_root: Path = SYS, *, allow_thermal: bool = False) -> FanDevice:
    """Resolve indices at runtime; reject ambiguity and a kernel-owned fan."""
    root = sys_root.resolve(strict=True)
    candidates = []
    for entry in (root / 'class/hwmon').glob('hwmon*'):
        try:
            if _text(entry / 'name') == 'emc2305':
                candidates.append(entry.resolve(strict=True))
        except (OSError, ValueError, UnicodeError):
            continue
    if len(candidates) != 1:
        raise FanError('未检测到已配置的 CN5 风扇驱动' if not candidates else '检测到多个风扇控制器，拒绝猜测目标')
    return verify_device(candidates[0], root, allow_thermal=allow_thermal)


def readings(device: FanDevice, sys_root: Path = SYS) -> tuple[int | None, int | None, float | None]:
    try:
        percent = round(_integer(device.pwm, 0, 255) * 100 / 255)
    except (OSError, ValueError, UnicodeError, FanError):
        percent = None
    try:
        rpm = _integer(device.hwmon / 'fan1_input', 0, 100000)
        if (device.hwmon / 'fan1_fault').exists() and _integer(device.hwmon / 'fan1_fault', 0, 1):
            rpm = None
    except (OSError, ValueError, UnicodeError, FanError):
        rpm = None
    temperatures = []
    for zone in (sys_root / 'class/thermal').glob('thermal_zone*'):
        try:
            if _text(zone / 'type') in {'cpu-thermal', 'cpu_thermal', 'bcm2835_thermal'}:
                temperatures.append(_integer(zone / 'temp', -10000, 125000) / 1000)
        except (OSError, ValueError, UnicodeError, FanError):
            temperatures.append(None)
    temperature = temperatures[0] if len(temperatures) == 1 else None
    return percent, rpm, temperature


def secure_directory(path: Path, *, create: bool = False) -> None:
    """No writable or symlinked ancestors are permitted for root state."""
    for parent in reversed((path, *path.parents)):
        try:
            item = parent.lstat()
        except FileNotFoundError:
            if not create or parent != path:
                raise FanError('风扇配置目录不存在')
            parent.mkdir(mode=0o755)
            item = parent.lstat()
        if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022:
            raise FanError('风扇配置目录权限不安全')


def read_root_json(path: Path) -> dict:
    secure_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022 or item.st_size > 4096:
            raise FanError('风扇配置文件权限或大小不安全')
        data = os.read(descriptor, 4097)
        result = json.loads(data)
        if not isinstance(result, dict):
            raise FanError('风扇配置格式错误')
        return result
    finally:
        os.close(descriptor)


def validate_config(value: dict) -> dict:
    if set(value) != {'version', 'mode', 'percent'} or type(value['version']) is not int or value['version'] != 1:
        raise FanError('风扇配置版本或字段错误')
    if type(value['mode']) is not str or value['mode'] not in {'auto', 'manual'} or type(value['percent']) is not int or not 30 <= value['percent'] <= 100:
        raise FanError('手动风速仅支持 30–100%')
    return value


def validate_status(value: dict) -> dict:
    required = {'version', 'mode', 'requested_percent', 'percent', 'rpm', 'temperature', 'updated', 'error', 'message'}
    if set(value) != required:
        raise FanError('风扇服务状态字段错误')
    validate_config({'version': value['version'], 'mode': value['mode'], 'percent': value['requested_percent']})
    for name, low, high in (('percent', 0, 100), ('rpm', 0, 100000)):
        if value[name] is not None and (type(value[name]) is not int or not low <= value[name] <= high):
            raise FanError('风扇服务读数无效')
    temperature = value['temperature']
    if temperature is not None and (type(temperature) not in {int, float} or not -10 <= temperature <= 125):
        raise FanError('风扇服务温度无效')
    if type(value['updated']) not in {int, float} or not math.isfinite(value['updated']) or value['updated'] < 0:
        raise FanError('风扇服务时间无效')
    if any(type(value[name]) is not str or len(value[name]) > 512 for name in ('error', 'message')):
        raise FanError('风扇服务说明无效')
    return value


class FanController:
    def __init__(self, runner=subprocess.run) -> None:
        self.runner = runner

    def state(self) -> FanState:
        try:
            device = discover()
            percent, rpm, temperature = readings(device)
        except (OSError, ValueError, UnicodeError, FanError) as error:
            # Kernel-owned fans are still readable. Preserve genuine RPM and
            # PWM telemetry while disabling our competing write controls.
            try:
                device = discover(allow_thermal=True)
                percent, rpm, temperature = readings(device)
                return FanState(percent=percent, rpm=rpm, temperature=temperature,
                                error='unavailable', message=str(error))
            except (OSError, ValueError, UnicodeError, FanError):
                pass
            return FanState(error='unavailable', message=str(error))
        base = FanState(True, 'off', percent=percent, rpm=rpm, temperature=temperature, message='尚未启用风扇控制')
        try:
            config = validate_config(read_root_json(CONFIG))
            runtime = validate_status(read_root_json(STATUS))
            age = time.monotonic() - runtime['updated']
            if type(runtime['updated']) not in {int, float} or not 0 <= age <= 8 or runtime.get('mode') not in {'auto', 'manual'}:
                return replace(base, message='风扇服务未运行，请重新应用设置')
            if runtime.get('mode') != config['mode'] or runtime.get('requested_percent') != config['percent']:
                return replace(base, message='正在应用风扇设置')
            return replace(base, mode=config['mode'], requested_percent=config['percent'], error=runtime.get('error', ''), message=runtime.get('message', ''))
        except FileNotFoundError:
            return base
        except (OSError, KeyError, TypeError, ValueError, FanError):
            return replace(base, error='configuration', message='风扇配置或服务状态无法读取')

    def apply(self, mode: str, percent: int = 50) -> FanState:
        try:
            validate_config({'version': 1, 'mode': mode, 'percent': percent})
            current = self.state()
            if not current.available:
                return current
            command = ['/usr/bin/pkexec', HELPER, mode]
            if mode == 'manual':
                command.append(str(percent))
            # Do not impose a timeout on an interactive Polkit authentication.
            result = self.runner(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if result.returncode:
                if result.returncode == 70:
                    return replace(self.state(), error='recovery',
                                   message='风扇设置未能安全恢复，需要管理员检查配置和系统服务')
                cancelled = result.returncode in {126, 127}
                return replace(self.state(), error='cancelled' if cancelled else 'apply', message='已取消风扇设置授权' if cancelled else '风扇设置未确认；请检查风扇服务和系统日志')
            return self.state()
        except (OSError, ValueError, FanError):
            return replace(self.state(), error='apply', message='无法应用风扇设置；手动风速仅支持 30–100%')
