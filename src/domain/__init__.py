"""Domain data shared by UI, services, and device adapters."""

from src.domain.models import (
    DeviceStatus,
    EEGFrame,
    RecognitionResult,
    Position,
    Role,
    SoldierVitals,
    SwarmCommand,
    SystemMetrics,
    UAVTelemetry,
    User,
)

__all__ = [
    "DeviceStatus",
    "EEGFrame",
    "RecognitionResult",
    "Position",
    "Role",
    "SoldierVitals",
    "SwarmCommand",
    "SystemMetrics",
    "UAVTelemetry",
    "User",
]
