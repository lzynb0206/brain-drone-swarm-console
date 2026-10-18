"""Adapt the subject-specific template model to the common decoder contract."""

from __future__ import annotations

import time

import numpy as np

from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.personal_template_model import PersonalTemplateModel


class PersonalTemplateSSVEPDecoder:
    """Six-class personal matching used for isolated online command testing."""

    # The model derives conservative score/margin gates from leakage-free
    # leave-one-trial calibration checks.  The test/control layer still adds a
    # separate two-consecutive-result confirmation.
    idle_calibration_seconds = 0.0

    def __init__(
        self,
        model: PersonalTemplateModel,
        fbcca: FBCCADecoder | None = None,
    ) -> None:
        self.model = model
        self._fallback_fbcca = fbcca
        self.fbcca = self._build_fbcca()

    def _build_fbcca(self) -> FBCCADecoder:
        if self.model.experimental_override and self.model.channel_indices:
            return FBCCADecoder(
                channels_1based=tuple(index + 1 for index in self.model.channel_indices),
            )
        return self._fallback_fbcca or FBCCADecoder()

    @property
    def available(self) -> bool:
        return self.model.enabled

    @property
    def hardware_control_allowed(self) -> bool:
        """Only independently approved metadata may drive real aircraft."""

        return bool(self.model.metadata.get("production_approved", False))

    @property
    def backend_status(self) -> str:
        if not self.available:
            return self.model.error
        count = int(self.model.metadata.get("trial_count", len(self.model.trial_labels)))
        if self.model.experimental_override:
            channels = "/".join(
                str(value) for value in self.model.metadata.get("channel_names", [])
            )
            suffix = "允许实机" if self.hardware_control_allowed else "仅科研测试，禁止实机"
            return f"实验个人模型已就绪（{count}段；{channels}；门槛已放宽；{suffix}）"
        return f"个人模板＋FBCCA已就绪（{count}段，未做独立准确率评估）"

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.available:
            raise RuntimeError(self.model.error or "个人模板不可用")
        stable_channels = tuple(index + 1 for index in self.model.channel_indices)
        prediction = self.model.predict(samples, sample_rate_hz)
        fbcca_result = self.fbcca.decode(samples, sample_rate_hz)
        # Only compare/deduplicate the electrodes this model actually uses.
        # An unused but highly correlated lead must not invalidate Oz/O2.
        quality = assess_signal_quality(
            samples, sample_rate_hz, deduplicate_channels=stable_channels,
        )
        usable_mask = tuple(bool(value) for value in quality.get("usable_mask", []))
        usable_stable_channels = tuple(
            channel for channel in stable_channels
            if channel - 1 < len(usable_mask) and usable_mask[channel - 1]
        )
        score_passed = float(prediction["confidence"]) >= self.model.confidence_threshold
        margin_passed = float(prediction["margin"]) >= self.model.margin_threshold
        quality_passed = bool(usable_stable_channels)
        label = str(prediction["label"])
        confidence = float(prediction["confidence"])
        margin = float(prediction["margin"])
        result_scores = dict(prediction["scores"])
        frequency_hz = float(prediction["frequency_hz"])
        if self.model.experimental_override:
            labels = self.model.labels
            template_probabilities = np.asarray(
                [float(prediction["scores"][value]) for value in labels], dtype=np.float64,
            )
            fbcca_scores = np.asarray(
                [float(fbcca_result.scores[value]) for value in labels], dtype=np.float64,
            )
            logits = (
                fbcca_scores - np.max(fbcca_scores)
            ) / self.model.fbcca_temperature
            fbcca_probabilities = np.exp(np.clip(logits, -60.0, 0.0))
            fbcca_probabilities /= np.sum(fbcca_probabilities)
            fused = (
                self.model.fusion_template_weight * template_probabilities
                + (1.0 - self.model.fusion_template_weight) * fbcca_probabilities
            )
            order = np.argsort(fused)[::-1]
            best, second = int(order[0]), int(order[1])
            label = labels[best]
            confidence = float(fused[best])
            margin = float(fused[best] - fused[second])
            frequency_hz = float(self.model.frequencies_hz[best])
            result_scores = {
                value: float(fused[index]) for index, value in enumerate(labels)
            }
            score_passed = confidence >= self.model.confidence_threshold
            margin_passed = margin >= self.model.margin_threshold
            fbcca_agreed = fbcca_result.label == label
            accepted = bool(score_passed and margin_passed and quality_passed)
        else:
            fbcca_agreed = fbcca_result.label == label
            accepted = bool(prediction["accepted"] and quality_passed and fbcca_agreed)
        quality.update({
            "score_threshold": self.model.confidence_threshold,
            "margin_threshold": self.model.margin_threshold,
            "template_raw_similarities": prediction["raw_similarities"],
            "template_trial_count": len(self.model.trial_labels),
            "template_stable_channels": list(stable_channels),
            "template_usable_stable_channels": list(usable_stable_channels),
            "fbcca_consensus_label": fbcca_result.label,
            "fbcca_consensus_agreed": fbcca_agreed,
            "fbcca_consensus_accepted": fbcca_result.accepted,
            "fbcca_scores": dict(fbcca_result.scores),
            "experimental_override": self.model.experimental_override,
            "fusion_template_weight": self.model.fusion_template_weight,
            "fused_scores": result_scores,
            "quality_gate_applied": True,
            "quality_warning": (
                "个人模板依赖的稳定通道当前不可用。"
                if not quality_passed else ""
            ),
        })
        if not quality_passed:
            reason = "个人模板稳定通道当前不可用，拒绝输出"
        elif not score_passed:
            reason = "个人模板相似度不足，拒绝输出"
        elif not margin_passed:
            reason = "前两类过于接近，拒绝输出"
        elif not self.model.experimental_override and not fbcca_agreed:
            reason = f"个人模板为{label}、FBCCA为{fbcca_result.label}，两种证据不一致"
        elif self.model.experimental_override:
            reason = "实验个人模型已通过宽松门槛；等待连续结果确认"
        else:
            reason = "个人模板与FBCCA指向同一频率；等待连续结果确认"
        return IntentResult(
            timestamp=time.time(),
            label=label,
            confidence=confidence,
            margin=margin,
            accepted=accepted,
            reason=reason,
            frequency_hz=frequency_hz,
            scores=result_scores,
            sample_rate_hz=float(sample_rate_hz),
            sample_count=int(prediction["sample_count"]),
            used_channels_1based=usable_stable_channels,
            signal_quality=quality,
            safety_verified=False,
        )

    def reset(self) -> None:
        """Reset the FBCCA companion; the template matcher is stateless."""

        self.fbcca.reset()

    def reload(self) -> bool:
        loaded = self.model.load()
        if loaded:
            self.fbcca = self._build_fbcca()
        return loaded

    def begin_idle_calibration(self) -> None:
        """No-op: do not alter a model built from saved labelled trials."""

    def observe_idle(self, *_args, **_kwargs) -> None:
        """No-op: the personal model never trains on unlabelled test data."""

    def idle_calibration_summary(self) -> str:
        count = int(self.model.metadata.get("trial_count", len(self.model.trial_labels)))
        return f"个人模板已加载 {count} 段校准 EEG；测试数据不会回写模型"


__all__ = ["PersonalTemplateSSVEPDecoder"]
