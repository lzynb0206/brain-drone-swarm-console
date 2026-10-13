"""Honest no-hardware gateway used when Crazyflie is not configured."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain import SwarmCommand, SwarmStatus, UAVTelemetry


class DisabledSwarmGateway:
    """Report an unavailable interface instead of inventing UAV telemetry."""

    def __init__(self, reason: str = "未配置 Crazyflie 实机接口") -> None:
        self.reason = reason

    def connect(self) -> None:
        raise RuntimeError(self.reason)

    def disconnect(self) -> None:
        return None

    def status(self) -> SwarmStatus:
        return SwarmStatus("disabled", "disabled", False, False, False, self.reason)

    def arm(self) -> None:
        raise RuntimeError(self.reason)

    def disarm(self) -> None:
        return None

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        return ()

    def send_command(self, command: SwarmCommand) -> None:
        raise RuntimeError(self.reason)

    def stop_motion(self) -> None:
        raise RuntimeError(self.reason)

    def stop_motors(self) -> None:
        raise RuntimeError(self.reason)

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        if any(abs(float(value)) > 1e-12 for value in (vertical, forward, lateral)):
            raise RuntimeError(self.reason)

    def takeoff_height_m(self) -> float:
        return 0.4

    def set_takeoff_height(self, height_m: float) -> None:
        raise RuntimeError(self.reason)

    def emergency_stop(self) -> None:
        raise RuntimeError(self.reason)

    def reset_after_emergency_reboot(self) -> None:
        return None

    def configure_uris(self, uris) -> None:
        raise RuntimeError("当前未启用 Crazyflie 网关，不能配置 URI")

    def add_event_listener(self, listener) -> None:
        return None


    def add_command_listener(self, listener) -> None:
        return None
