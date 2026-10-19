"""Select a trained FB-EEGNet model while retaining real FBCCA fallback."""

from __future__ import annotations

import time

from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.predictor import FBEEGNetPredictor
from src.ports import IntentDecoder


class HybridIntentDecoder:
    def __init__(self, predictor: FBEEGNetPredictor, fallback: IntentDecoder) -> None:
        self.predictor, self.fallback = predictor, fallback

    @property
    def backend_status(self) -> str:
        if not self.predictor.enabled:
            return f"FBCCA（模型不可用：{self.predictor.error}）"
        suffix = "允许实机" if self.hardware_control_allowed else "仅科研测试，禁止实机"
        return f"FB-EEGNet（{suffix}）"

    @property
    def hardware_control_allowed(self) -> bool:
        if not self.predictor.enabled:
            return True
        return bool(self.predictor.metadata.get("production_approved", False))

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.predictor.enabled:
            return self.fallback.decode(samples, sample_rate_hz)
        prediction = self.predictor.predict(samples, sample_rate_hz)
        quality = assess_signal_quality(samples, sample_rate_hz)
        used = tuple(index + 1 for index, value in enumerate(prediction["quality_mask"]) if value)
        label = str(prediction["label"])
        accepted = bool(prediction["accepted"]) and label != "空闲"
        reason = "FB-EEGNet安全阈值通过" if accepted else "FB-EEGNet输出空闲或未通过阈值"
        return IntentResult(
            timestamp=time.time(), label=label, confidence=float(prediction["confidence"]),
            margin=float(prediction["margin"]), accepted=accepted, reason=reason,
            frequency_hz=0.0, scores=prediction["scores"], sample_rate_hz=float(sample_rate_hz),
            sample_count=len(samples[0]), used_channels_1based=used, signal_quality=quality,
        )
