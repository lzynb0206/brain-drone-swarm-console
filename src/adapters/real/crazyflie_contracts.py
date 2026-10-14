"""Configuration and internal hardware contract for the Crazyflie adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


TelemetryCallback = Callable[[str, float, dict[str, float]], None]
EventCallback = Callable[[str, str], None]


class CrazyflieBackend(Protocol):
    def connect(self, uris: tuple[str, ...], telemetry: TelemetryCallback, event: EventCallback) -> None: ...
    def disconnect(self) -> None: ...
    def arm_all(self) -> bool | None: ...
    def arm_one(self, uri: str) -> bool | None: ...
    def disarm_all(self) -> None: ...
    def disarm_one(self, uri: str) -> None: ...
    def takeoff_all(self, height_m: float, duration_s: float) -> None: ...
    def takeoff_one(self, uri: str, height_m: float, duration_s: float) -> None: ...
    def land_all(self, duration_s: float) -> None: ...
    def land_one(self, uri: str, duration_s: float) -> None: ...
    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def hover_all(self, duration_s: float) -> None: ...
    def hover_one(self, uri: str, duration_s: float) -> None: ...
    def stop_motion_all(self) -> None: ...
    def stop_motion_one(self, uri: str) -> None: ...
    def stop_motors_all(self) -> None: ...
    def stop_motors_one(self, uri: str) -> None: ...
    def start_manual_control(self, target_height_m: float) -> None: ...
    def start_manual_control_one(self, uri: str, target_height_m: float) -> None: ...
    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None: ...
    def set_manual_input_one(self, uri: str, vertical: float, forward: float, lateral: float) -> None: ...
    def emergency_stop_all(self) -> None: ...
    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def start_lighthouse_hold(self, targets: Mapping[str, tuple[float, float, float, float]]) -> None: ...
    def set_lighthouse_targets(self, targets: Mapping[str, tuple[float, float, float, float]]) -> None: ...
    def move_all_absolute(self, targets: Mapping[str, tuple[float, float, float, float]], duration_s: float) -> None: ...
    def move_one_absolute(self, uri: str, x: float, y: float, z: float, yaw: float, duration_s: float) -> None: ...
    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None: ...


@dataclass(frozen=True)
class CrazyflieSwarmConfig:
    uris: tuple[str, ...]
    control_enabled: bool = False
    default_height_m: float = 0.4
    move_distance_m: float = 0.05
    move_speed_mps: float = 0.15
    minimum_battery_voltage: float = 3.2
    battery_low_confirm_s: float = 1.0
    telemetry_timeout_s: float = 1.5
    flow_failure_samples: int = 3
    cache_dir: Path = Path(".cache/cflib")
    flow_min_height_m: float = 0.2
    flow_max_height_m: float = 2.0
    flow_max_horizontal_speed_mps: float = 0.5
    flow_max_vertical_speed_mps: float = 0.5
    flow_setpoint_rate_hz: float = 5.0
    formation_spacing_m: float = 0.4

    @classmethod
    def from_environment(cls) -> "CrazyflieSwarmConfig":
        uris = tuple(
            value.strip()
            for value in os.getenv("BDS_CRAZYFLIE_URIS", "").split(",")
            if value.strip()
        )
        return cls(
            uris=uris,
            control_enabled=os.getenv("BDS_HARDWARE_CONTROL_ENABLED", "false").strip().lower()
            in {"1", "true", "yes"},
            default_height_m=float(os.getenv("BDS_SWARM_TAKEOFF_HEIGHT", "0.4")),
            move_distance_m=float(os.getenv("BDS_SWARM_MOVE_DISTANCE", "0.05")),
            move_speed_mps=float(os.getenv("BDS_SWARM_MOVE_SPEED", "0.15")),
            minimum_battery_voltage=float(os.getenv("BDS_SWARM_MIN_VBAT", "3.2")),
            battery_low_confirm_s=float(os.getenv("BDS_SWARM_LOW_VOLTAGE_CONFIRM_S", "1.0")),
            flow_failure_samples=int(os.getenv("BDS_FLOW_FAILURE_SAMPLES", "3")),
            cache_dir=Path(os.getenv("BDS_CFLIB_CACHE", ".cache/cflib")).expanduser(),
            flow_min_height_m=float(os.getenv("BDS_FLOW_MIN_HEIGHT", "0.2")),
            flow_max_height_m=float(os.getenv("BDS_FLOW_MAX_HEIGHT", "2.0")),
            flow_max_horizontal_speed_mps=float(os.getenv("BDS_FLOW_MAX_XY_SPEED", "0.5")),
            flow_max_vertical_speed_mps=float(os.getenv("BDS_FLOW_MAX_Z_SPEED", "0.5")),
            flow_setpoint_rate_hz=float(os.getenv("BDS_FLOW_SETPOINT_RATE", "5")),
            formation_spacing_m=float(os.getenv("BDS_FORMATION_SPACING", "0.4")),
        )


__all__ = [
    "CrazyflieBackend", "CrazyflieSwarmConfig", "EventCallback", "TelemetryCallback",
]
