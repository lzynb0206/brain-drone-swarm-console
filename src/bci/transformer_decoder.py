"""Adapt Transformer-SSVEP probabilities to the common IntentResult contract."""

from __future__ import annotations

import time

from src.bci.config import LABEL_TO_FREQUENCY
from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.transformer_predictor import TransformerSSVEPPredictor


class TransformerIntentDecoder:
    def __init__(self, predictor: TransformerSSVEPPredictor) -> None:
        self.predictor = predictor

    @property
    def available(self) -> bool:
        return self.predictor.enabled

    @property
    def hardware_control_allowed(self) -> bool:
        return bool(self.predictor.metadata.get("production_approved", False))

    @property
    def backend_status(self) -> str:
        return "Transformer-SSVEP" if self.available else self.predictor.error

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.available:
            raise RuntimeError(self.predictor.error or "Transformer-SSVEP 模型不可用")
        prediction = self.predictor.predict(samples, sample_rate_hz)
        quality = assess_signal_quality(samples, sample_rate_hz)
        used = tuple(
            index + 1
            for index, value in enumerate(prediction["quality_mask"])
            if value
        )
        label = str(prediction["label"])
        accepted = bool(prediction["accepted"])
        reason = "最大置信度阈值通过" if accepted else (
            "识别为空闲" if label == "空闲" else "最大置信度低于阈值"
        )
        return IntentResult(
            timestamp=time.time(),
            label=label,
            confidence=float(prediction["confidence"]),
            margin=float(prediction["margin"]),
            accepted=accepted,
            reason=reason,
            frequency_hz=float(LABEL_TO_FREQUENCY.get(label, 0.0) or 0.0),
            scores=prediction["scores"],
            sample_rate_hz=float(sample_rate_hz),
            sample_count=int(prediction["sample_count"]),
            used_channels_1based=used,
            signal_quality=quality,
        )
