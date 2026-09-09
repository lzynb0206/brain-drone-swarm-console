"""Select a trained FB-EEGNet model while retaining real FBCCA fallback."""

from __future__ import annotations

import time

from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.predictor import FBEEGNetPredictor


class HybridIntentDecoder:
    def __init__(self, predictor: FBEEGNetPredictor, fallback: FBCCADecoder) -> None:
        self.predictor, self.fallback = predictor, fallback

    @property
    def backend_status(self) -> str:
        return "FB-EEGNet" if self.predictor.enabled else f"FBCCA（模型不可用：{self.predictor.error}）"

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
