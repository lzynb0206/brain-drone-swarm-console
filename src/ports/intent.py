from __future__ import annotations

from typing import Protocol

from src.domain import RecognitionResult


class IntentDecoder(Protocol):
    """Decode an EEG window without executing a device command."""

    def decode(self, samples, sample_rate_hz: float) -> RecognitionResult:
        ...
