"""Framework-independent application models.

These classes deliberately do not import Qt, LSL, Crazyflie, or database code.
That keeps the same data contract usable by mock and real integrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class Role(str, Enum):
    SOLDIER = "soldier"
    COMMANDER = "commander"
    RESEARCHER = "researcher"
    ENGINEER = "engineer"
    ADMIN = "admin"

    @property
    def label(self) -> str:
        return {
            Role.SOLDIER: "作战操作员",
            Role.COMMANDER: "指挥官",
            Role.RESEARCHER: "科研人员",
            Role.ENGINEER: "系统工程师",
            Role.ADMIN: "系统管理员",
        }[self]


class DeviceStatus(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: Role
    uid: str | None = None
    email: str | None = None

    @property
    def role_label(self) -> str:
        return self.role.label


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class UAVTelemetry:
    uav_id: str
    battery: float
    speed_mps: float
    altitude_m: float
    task: str
    status: DeviceStatus
    position: Position
    battery_voltage: float | None = None
    positioning_ok: bool = False
    last_update_timestamp: float = 0.0


@dataclass(frozen=True)
class SwarmStatus:
    backend: str
    state: str
    connected: bool
    armed: bool
    control_enabled: bool
    message: str = ""


@dataclass(frozen=True)
class EEGFrame:
    timestamp: float
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    samples: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class IntentResult:
    """Unified decoder output; it carries evidence but cannot execute a device action."""

    timestamp: float
    label: str
    confidence: float
    margin: float
    accepted: bool
    reason: str
    frequency_hz: float
    scores: Mapping[str, float]
    sample_rate_hz: float
    sample_count: int
    used_channels_1based: tuple[int, ...]
    signal_quality: Mapping[str, object] = field(default_factory=dict)

    @property
    def prediction(self) -> str | None:
        return self.label if self.accepted else None

    @property
    def best_score(self) -> float:
        return self.confidence


RecognitionResult = IntentResult


@dataclass(frozen=True)
class SoldierVitals:
    soldier_id: str
    attention: float
    fatigue: float
    stress: float
    heart_rate_bpm: float


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    gpu_percent: float
    memory_percent: float
    latency_ms: float
    throughput_mbps: float


@dataclass(frozen=True)
class SwarmCommand:
    action: str
    uav_id: str | None = None
    parameters: Mapping[str, float | str] = field(default_factory=dict)
    command_id: str = ""
