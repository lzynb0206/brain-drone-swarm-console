"""Intent-decoder adapter for the separate Lzy personal algorithm."""

from __future__ import annotations

import time

import numpy as np

from src.bci.config import COMMANDS, SSVEP_CHANNELS_1BASED
from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.lzy_personal_model import LzyPersonalModel


class LzyPersonalDecoder:
    """Decode a full five-second window with the Lzy personal calibration."""

    idle_calibration_seconds = 0.0

    def __init__(self, model: LzyPersonalModel) -> None:
        self.model = model

    @property
    def available(self) -> bool:
        return self.model.enabled

    @property
    def hardware_control_allowed(self) -> bool:
        return self.model.production_approved

    @property
    def evaluation_accuracy(self) -> float | None:
        evaluation = self.model.metadata.get("evaluation", {})
        value = evaluation.get("block_held_out_accuracy") if isinstance(evaluation, dict) else None
        return float(value) if isinstance(value, (float, int)) else None

    @property
    def required_window_seconds(self) -> float:
        if self.model.sample_rate_hz <= 0.0:
            return 5.0
        return self.model.sample_count / self.model.sample_rate_hz

    @property
    def backend_status(self) -> str:
        if not self.available:
            return self.model.error
        accuracy = self.evaluation_accuracy
        score = f"；整块留一 {accuracy * 100:.1f}%" if accuracy is not None else ""
        permission = "允许实机" if self.hardware_control_allowed else "仅科研分析，禁止实机"
        return (
            "Lzy重叠窗FBCCA＋个人非目标基线已就绪"
            f"（完整{self.required_window_seconds:g}秒{score}；{permission}）"
        )

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.available:
            raise RuntimeError(self.model.error or "个人算法-Lzy模型不可用")
        posterior = np.asarray(samples, dtype=np.float64)[:len(SSVEP_CHANNELS_1BASED)]
        quality = assess_signal_quality(
            posterior,
            sample_rate_hz,
            deduplicate_channels=SSVEP_CHANNELS_1BASED,
        )
        prediction = self.model.predict(posterior, sample_rate_hz)
        usable_count = int(quality.get("usable_channel_count", 0))
        accepted = usable_count >= 2
        reason = (
            "Lzy个人证据最大类通过科研分析质量门"
            if accepted else "少于2个质量合格枕区通道，拒绝输出"
        )
        quality.update({
            "raw_segment_median_fbcca_scores": prediction["raw_fbcca_scores"],
            "lzy_calibrated_evidence": prediction["calibrated_evidence"],
            "segment_starts_seconds": list(prediction["segment_starts_seconds"]),
            "feature_pipeline": (
                "完整5秒→去漂移→50Hz陷波→0/0.5/1秒起点的三个4秒FBCCA"
                "→分数中位数→Lzy非目标基线校准"
            ),
            "quality_gate_applied": True,
            "required_usable_channel_count": 2,
            "production_approved": self.hardware_control_allowed,
        })
        scores = dict(prediction["scores"])
        return IntentResult(
            timestamp=time.time(),
            label=str(prediction["label"]),
            confidence=float(prediction["confidence"]),
            margin=float(prediction["margin"]),
            accepted=accepted,
            reason=reason,
            frequency_hz=float(prediction["frequency_hz"]),
            scores=scores,
            sample_rate_hz=float(sample_rate_hz),
            sample_count=int(prediction["sample_count"]),
            used_channels_1based=tuple(
                index + 1
                for index, usable in enumerate(quality.get("usable_mask", []))
                if bool(usable)
            ),
            signal_quality=quality,
            safety_verified=False,
        )

    def reset(self) -> None:
        """The personal score calibration has no temporal state."""

    def reload(self) -> bool:
        return self.model.load()

    def begin_idle_calibration(self) -> None:
        """The persisted Lzy model does not alter itself from online idle data."""

    def observe_idle(self, confidence: float, margin: float, scores=None) -> None:
        """Keep the trained parameters immutable during evaluation."""

    def idle_calibration_summary(self) -> str:
        return "个人算法-Lzy使用本人训练集的固定非目标基线；本轮在线空闲不改写模型"


__all__ = ["LzyPersonalDecoder"]
