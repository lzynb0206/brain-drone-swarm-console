"""Stateful paper-baseline gate for consecutive SSVEP decisions."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable

from src.domain import IntentResult
from src.ports import IntentDecoder


class ConsecutiveWindowGate:
    """Require confidence and the same label in consecutive windows.

    This reproduces the paper's command-admission baseline.  It deliberately
    remains separate from the neural decoder so a future overlap-aware method
    can replace it without retraining the Transformer.
    """

    def __init__(
        self,
        decoder: IntentDecoder,
        min_confidence: float = 0.60,
        required_windows: int = 2,
        cooldown_seconds: float = 0.50,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 <= float(min_confidence) <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if int(required_windows) < 1:
            raise ValueError("required_windows must be positive")
        if float(cooldown_seconds) < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        self.decoder = decoder
        self.min_confidence = float(min_confidence)
        self.required_windows = int(required_windows)
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self.reset()

    @property
    def available(self) -> bool:
        return bool(getattr(self.decoder, "available", True))

    @property
    def backend_status(self) -> str:
        return str(getattr(self.decoder, "backend_status", type(self.decoder).__name__))

    @property
    def hardware_control_allowed(self) -> bool:
        return bool(getattr(self.decoder, "hardware_control_allowed", True))

    def reset(self) -> None:
        self._candidate_label = ""
        self._candidate_count = 0
        self._cooldown_until = 0.0

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        result = self.decoder.decode(samples, sample_rate_hz)
        now = float(self._clock())
        if result.label == "空闲" or not result.accepted or result.confidence < self.min_confidence:
            self._candidate_label = ""
            self._candidate_count = 0
            reason = "识别为空闲" if result.label == "空闲" else (
                f"置信度 {result.confidence:.3f} 低于阈值 {self.min_confidence:.3f}"
            )
            return replace(result, accepted=False, reason=reason, safety_verified=False)
        if now < self._cooldown_until:
            self._candidate_label = ""
            self._candidate_count = 0
            remaining = self._cooldown_until - now
            return replace(
                result,
                accepted=False,
                reason=f"安全冷却中（剩余 {remaining:.3f}s）",
                safety_verified=False,
            )

        if result.label == self._candidate_label:
            self._candidate_count += 1
        else:
            self._candidate_label = result.label
            self._candidate_count = 1
        if self._candidate_count < self.required_windows:
            return replace(
                result,
                accepted=False,
                reason=(
                    f"等待连续窗口确认 {self._candidate_count}/{self.required_windows}"
                ),
                safety_verified=False,
            )

        self._candidate_label = ""
        self._candidate_count = 0
        self._cooldown_until = now + self.cooldown_seconds
        return replace(
            result,
            accepted=True,
            reason=(
                f"置信度阈值通过且连续 {self.required_windows} 个窗口一致；"
                f"进入 {self.cooldown_seconds:.3f}s 冷却"
            ),
            safety_verified=True,
        )
