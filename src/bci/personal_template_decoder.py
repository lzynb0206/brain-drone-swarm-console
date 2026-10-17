"""Adapt the subject-specific template model to the common decoder contract."""

from __future__ import annotations

import time

from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.personal_template_model import PersonalTemplateModel


class PersonalTemplateSSVEPDecoder:
    """Six-class personal matching used for isolated online command testing."""

    # The model derives conservative score/margin gates from leakage-free
    # leave-one-trial calibration checks.  The test/control layer still adds a
    # separate two-consecutive-result confirmation.
    idle_calibration_seconds = 0.0

    def __init__(self, model: PersonalTemplateModel) -> None:
        self.model = model

    @property
    def available(self) -> bool:
        return self.model.enabled

    @property
    def backend_status(self) -> str:
        if not self.available:
            return self.model.error
        count = int(self.model.metadata.get("trial_count", len(self.model.trial_labels)))
        return f"个人模板已就绪（{count}段，未做独立准确率评估）"

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.available:
            raise RuntimeError(self.model.error or "个人模板不可用")
        prediction = self.model.predict(samples, sample_rate_hz)
        quality = assess_signal_quality(samples, sample_rate_hz)
        stable_channels = tuple(index + 1 for index in self.model.channel_indices)
        usable_mask = tuple(bool(value) for value in quality.get("usable_mask", []))
        usable_stable_channels = tuple(
            channel for channel in stable_channels
            if channel - 1 < len(usable_mask) and usable_mask[channel - 1]
        )
        score_passed = float(prediction["confidence"]) >= self.model.confidence_threshold
        margin_passed = float(prediction["margin"]) >= self.model.margin_threshold
        quality_passed = bool(usable_stable_channels)
        accepted = bool(prediction["accepted"] and quality_passed)
        quality.update({
            "score_threshold": self.model.confidence_threshold,
            "margin_threshold": self.model.margin_threshold,
            "template_raw_similarities": prediction["raw_similarities"],
            "template_trial_count": len(self.model.trial_labels),
            "template_stable_channels": list(stable_channels),
            "template_usable_stable_channels": list(usable_stable_channels),
            "quality_gate_applied": True,
            "quality_warning": (
                "个人模板依赖的稳定通道当前不可用。"
                if not quality_passed else ""
            ),
        })
        label = str(prediction["label"])
        if not quality_passed:
            reason = "个人模板稳定通道当前不可用，拒绝输出"
        elif not score_passed:
            reason = "个人模板相似度不足，拒绝输出"
        elif not margin_passed:
            reason = "前两类过于接近，拒绝输出"
        else:
            reason = "个人模板通过质量、相似度和差值门限；等待连续结果确认"
        return IntentResult(
            timestamp=time.time(),
            label=label,
            confidence=float(prediction["confidence"]),
            margin=float(prediction["margin"]),
            accepted=accepted,
            reason=reason,
            frequency_hz=float(prediction["frequency_hz"]),
            scores=prediction["scores"],
            sample_rate_hz=float(sample_rate_hz),
            sample_count=int(prediction["sample_count"]),
            used_channels_1based=usable_stable_channels,
            signal_quality=quality,
            safety_verified=False,
        )

    def reset(self) -> None:
        """The template matcher has no rolling internal state."""

    def reload(self) -> bool:
        return self.model.load()

    def begin_idle_calibration(self) -> None:
        """No-op: do not alter a model built from saved labelled trials."""

    def observe_idle(self, *_args, **_kwargs) -> None:
        """No-op: the personal model never trains on unlabelled test data."""

    def idle_calibration_summary(self) -> str:
        count = int(self.model.metadata.get("trial_count", len(self.model.trial_labels)))
        return f"个人模板已加载 {count} 段校准 EEG；测试数据不会回写模型"


__all__ = ["PersonalTemplateSSVEPDecoder"]
