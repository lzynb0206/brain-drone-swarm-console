"""Runtime-selectable intent decoder without silent model fallback."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from src.ports import IntentDecoder


class RuntimeIntentDecoder:
    def __init__(self, decoders: Mapping[str, IntentDecoder], initial: str = "FBCCA") -> None:
        if not decoders:
            raise ValueError("At least one decoder is required")
        self._decoders = dict(decoders)
        self._lock = threading.RLock()
        self._active_name = ""
        self.select(initial)

    @property
    def active_name(self) -> str:
        with self._lock:
            return self._active_name

    @property
    def available_algorithms(self) -> tuple[str, ...]:
        return tuple(self._decoders)

    @property
    def backend_status(self) -> str:
        with self._lock:
            decoder = self._decoders[self._active_name]
            return str(getattr(decoder, "backend_status", self._active_name))

    def algorithm_statuses(self) -> dict[str, dict[str, object]]:
        statuses = {}
        for name, decoder in self._decoders.items():
            available = bool(getattr(decoder, "available", True))
            statuses[name] = {
                "available": available,
                "status": str(getattr(decoder, "backend_status", "可用")),
                "active": name == self.active_name,
            }
        return statuses

    def select(self, name: str) -> str:
        normalized = str(name).strip()
        if normalized not in self._decoders:
            raise ValueError(f"未知解码算法：{normalized}")
        decoder = self._decoders[normalized]
        if not bool(getattr(decoder, "available", True)):
            reason = str(getattr(decoder, "backend_status", "模型不可用"))
            raise RuntimeError(f"无法启用 {normalized}：{reason}")
        reset = getattr(decoder, "reset", None)
        if callable(reset):
            reset()
        with self._lock:
            self._active_name = normalized
        return f"运行时解码器已切换为 {normalized}"

    def decode(self, samples, sample_rate_hz: float):
        with self._lock:
            decoder = self._decoders[self._active_name]
        return decoder.decode(samples, sample_rate_hz)

    def reset(self) -> None:
        with self._lock:
            decoder = self._decoders[self._active_name]
        reset = getattr(decoder, "reset", None)
        if callable(reset):
            reset()

    def begin_idle_calibration(self) -> None:
        with self._lock:
            decoder = self._decoders[self._active_name]
        callback = getattr(decoder, "begin_idle_calibration", None)
        if callable(callback):
            callback()

    def observe_idle(self, result) -> None:
        with self._lock:
            decoder = self._decoders[self._active_name]
        callback = getattr(decoder, "observe_idle", None)
        if callable(callback):
            callback(result.confidence, result.margin)

    def idle_calibration_summary(self) -> str:
        with self._lock:
            decoder = self._decoders[self._active_name]
        callback = getattr(decoder, "idle_calibration_summary", None)
        if callable(callback):
            return str(callback())
        return "当前解码器不需要空闲阈值校准"
