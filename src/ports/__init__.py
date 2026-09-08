"""Interfaces that isolate the UI from hardware and infrastructure."""

from src.ports.auth import AuthError, AuthGateway
from src.ports.eeg import EEGStream
from src.ports.intent import IntentDecoder
from src.ports.engineering import EngineerDataProvider, ModuleRecord, StreamStatus
from src.ports.monitoring import SystemMonitor
from src.ports.swarm import SwarmGateway

__all__ = [
    "AuthError", "AuthGateway", "EEGStream", "IntentDecoder", "EngineerDataProvider", "ModuleRecord",
    "StreamStatus", "SwarmGateway", "SystemMonitor",
]
