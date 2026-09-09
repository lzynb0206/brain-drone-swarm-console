"""Unavailable EEG boundary used instead of generated mock samples."""

from __future__ import annotations

from src.domain import EEGFrame


class UnavailableEEGStream:
    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.source_name = "EEG 未配置"
        self.sample_rate_hz = 0.0
        self.connected = False

    def connect(self) -> None:
        raise RuntimeError(self.reason)

    def disconnect(self) -> None:
        self.connected = False

    def read_frame(self) -> EEGFrame:
        raise RuntimeError(self.reason)

    def flush(self) -> int:
        return 0
