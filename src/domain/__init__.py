"""Domain data shared by UI, services, and device adapters."""

from src.domain.models import (
    DeviceStatus,
    EEGFrame,
    IntentResult,
    RecognitionResult,
    Position,
    Role,
    SoldierVitals,
    SwarmCommand,
    SwarmStatus,
    SystemMetrics,
    UAVTelemetry,
    User,
)

__all__ = [
    "DeviceStatus",
    "EEGFrame",
    "IntentResult",
    "RecognitionResult",
    "Position",
    "Role",
    "SoldierVitals",
    "SwarmCommand",
    "SwarmStatus",
    "SystemMetrics",
    "UAVTelemetry",
    "User",
]
