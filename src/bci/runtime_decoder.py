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

    @property
    def idle_calibration_seconds(self) -> float:
        with self._lock:
            decoder = self._decoders[self._active_name]
        return float(getattr(decoder, "idle_calibration_seconds", 12.0))

    @property
    def hardware_control_allowed(self) -> bool:
        with self._lock:
            decoder = self._decoders[self._active_name]
        return bool(getattr(decoder, "hardware_control_allowed", True))

    @property
    def required_window_seconds(self) -> float:
        """Window required by the active decoder, if it has a fixed contract."""

        with self._lock:
            decoder = self._decoders[self._active_name]
        return float(getattr(decoder, "required_window_seconds", 0.0))

    def algorithm_statuses(self) -> dict[str, dict[str, object]]:
        statuses = {}
        for name, decoder in self._decoders.items():
            available = bool(getattr(decoder, "available", True))
            statuses[name] = {
                "available": available,
                "status": str(getattr(decoder, "backend_status", "可用")),
                "active": name == self.active_name,
                "hardware_control_allowed": bool(
                    getattr(decoder, "hardware_control_allowed", True)
                ),
                "evaluation_accuracy": getattr(decoder, "evaluation_accuracy", None),
            }
        return statuses

    def select(self, name: str) -> str:
        normalized = str(name).strip()
        if normalized not in self._decoders:
            raise ValueError(f"未知解码算法：{normalized}")
        decoder = self._decoders[normalized]
        if not bool(getattr(decoder, "available", True)):
            # "加载算法" may be clicked after a trainer has written a model
            # while the application is already open. Give decoders with a
            # validated reload hook one chance to discover the new artifact.
            reload_model = getattr(decoder, "reload", None)
            if callable(reload_model):
                reload_model()
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

    def reload(self, name: str) -> str:
        normalized = str(name).strip()
        if normalized not in self._decoders:
            raise ValueError(f"未知解码算法：{normalized}")
        decoder = self._decoders[normalized]
        callback = getattr(decoder, "reload", None)
        if not callable(callback):
            return f"{normalized} 不需要重新加载"
        if callback() is False:
            reason = str(getattr(decoder, "backend_status", "重新加载失败"))
            raise RuntimeError(reason)
        return f"{normalized} 已重新加载"

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
            callback(result.confidence, result.margin, scores=result.scores)

    def idle_calibration_summary(self) -> str:
        with self._lock:
            decoder = self._decoders[self._active_name]
        callback = getattr(decoder, "idle_calibration_summary", None)
        if callable(callback):
            return str(callback())
        return "当前解码器不需要空闲阈值校准"
