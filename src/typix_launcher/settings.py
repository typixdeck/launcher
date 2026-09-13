"""Launcher settings, including the user-level systemd autostart switch."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

SERVICE = "typix-launcher.service"
CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True)
class AutostartState:
    enabled: bool
    raw: str
    available: bool


def enable_command(service: str = SERVICE) -> list[str]:
    return ["systemctl", "--user", "enable", service]


def disable_command(service: str = SERVICE) -> list[str]:
    return ["systemctl", "--user", "disable", service]


def query_command(service: str = SERVICE) -> list[str]:
    return ["systemctl", "--user", "is-enabled", service]


class AutostartController:
    def __init__(self, runner: CommandRunner = subprocess.run) -> None:
        self.runner = runner

    def state(self, service: str = SERVICE) -> AutostartState:
        try:
            result = self.runner(query_command(service), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except OSError:
            return AutostartState(False, "systemctl-unavailable", False)
        raw = result.stdout.decode(errors="replace").strip() or result.stderr.decode(errors="replace").strip()
        enabled = result.returncode == 0 and raw.splitlines()[0] in {"enabled", "enabled-runtime"}
        return AutostartState(enabled, raw, True)

    def set_enabled(self, value: bool, service: str = SERVICE) -> AutostartState:
        command = enable_command(service) if value else disable_command(service)
        result = self.runner(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip()
            raise RuntimeError(detail or "systemctl 返回失败")
        return self.state(service)
