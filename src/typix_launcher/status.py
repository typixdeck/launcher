"""Read-only status sources; no GTK, hardware writes, scans or saved identifiers."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class Indicator:
    text: str
    icon: str
    detail: str


BATTERY_UNKNOWN = Indicator("--", "battery-missing-symbolic", "暂无可用的电池读数")
WIFI_UNKNOWN = Indicator("--", "network-wireless-offline-symbolic", "无法读取 Wi-Fi 状态")


def battery_indicator(percent: int, *, estimated=False, charging=False, source="系统电池", mv=None):
    if not 0 <= percent <= 100:
        return BATTERY_UNKNOWN
    level = "empty" if percent < 10 else "caution" if percent < 20 else "low" if percent < 40 else "good" if percent < 80 else "full"
    icon = f"battery-{level}{'-charging' if charging else ''}-symbolic"
    detail = f"{source} · {'约 ' if estimated else ''}{percent}%"
    if mv is not None:
        detail += f" · {mv / 1000:.2f} V"
    if charging:
        detail += " · 充电中"
    if estimated:
        detail += "\nCW2015 参考读数，尚非校准后的主电量计读数"
    return Indicator(f"{'≈' if estimated else ''}{percent}%", icon, detail)


def system_battery(root=Path('/sys/class/power_supply')) -> Indicator | None:
    """Only system batteries, never a wireless mouse/keyboard battery."""
    try:
        supplies = sorted(root.iterdir())
    except OSError:
        return None
    for supply in supplies:
        try:
            if (supply / 'type').read_text().strip() != 'Battery':
                continue
            if (supply / 'scope').exists() and (supply / 'scope').read_text().strip() == 'Device':
                continue
            if (supply / 'present').exists() and (supply / 'present').read_text().strip() != '1':
                continue
            percent = int((supply / 'capacity').read_text().strip())
            if not 0 <= percent <= 100:
                continue
            state = (supply / 'status').read_text().strip() if (supply / 'status').exists() else ''
            return battery_indicator(percent, charging=state == 'Charging')
        except (OSError, ValueError, UnicodeError):
            continue
    return None


def wifi_indicator(devices: str, radio: str, access_points: str) -> Indicator:
    wireless = [row.split(':') for row in devices.splitlines() if row.startswith('wifi:')]
    if not wireless:
        return Indicator("无网卡", "network-wireless-offline-symbolic", "未检测到 Wi-Fi 网卡")
    if radio.strip() == 'disabled':
        return Indicator("已关闭", "network-wireless-disabled-symbolic", "Wi-Fi 已关闭")
    if not any(row[1:] == ['connected'] for row in wireless):
        return Indicator("未连接", "network-wireless-offline-symbolic", "Wi-Fi 未连接")
    strengths = []
    for row in access_points.splitlines():
        if row.startswith('*:'):
            try:
                value = int(row[2:])
                if 0 <= value <= 100:
                    strengths.append(value)
            except ValueError:
                pass
    if not strengths:
        return Indicator("已连接", "network-wireless-connected-symbolic", "Wi-Fi 已连接，信号强度暂不可用")
    signal = max(strengths)
    level = 'excellent' if signal >= 75 else 'good' if signal >= 50 else 'ok' if signal >= 25 else 'weak'
    return Indicator(f"{signal}%", f"network-wireless-signal-{level}-symbolic", f"Wi-Fi 信号强度 {signal}%（不是网速）")


def read_wifi(runner=subprocess.run) -> Indicator:
    def query(*args):
        result = runner(['/usr/bin/nmcli', '--wait', '2', '-t', *args],
                        capture_output=True, text=True, timeout=3, check=False,
                        env={**os.environ, 'LC_ALL': 'C'})
        if result.returncode:
            raise ValueError('network status unavailable')
        return result.stdout
    try:
        devices = query('-f', 'TYPE,STATE', 'device', 'status')
        radio = query('radio', 'wifi')
        # Never scan, ask for credentials, or collect network names/addresses.
        points = query('-f', 'IN-USE,SIGNAL', 'device', 'wifi', 'list', '--rescan', 'no')
        return wifi_indicator(devices, radio, points)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return WIFI_UNKNOWN
