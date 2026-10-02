"""Fan telemetry labels, independent of GTK and hardware access."""
from .fan import FanState


def readings_label(state: FanState) -> str:
    temperature = f"{state.temperature:.1f} °C" if state.temperature is not None else "—"
    rpm = f"{state.rpm} RPM" if state.rpm is not None else "—"
    output = f"{state.percent}%" if state.percent is not None else "—"
    return f"温度 {temperature}    转速 {rpm}    输出 {output}"
