"""Common decoder adapter for the subject-specific dual-score classifier."""

from __future__ import annotations

import threading
import time

import numpy as np

from src.bci.config import COMMANDS, SSVEP_CHANNELS_1BASED
from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ml.personal_score_calibration import PersonalScoreCalibrationModel


class PersonalScoreCalibrationDecoder:
    """Use two FBCCA evidence vectors, then a personal logistic classifier."""

    idle_calibration_seconds = 12.0

    def __init__(
        self,
        model: PersonalScoreCalibrationModel,
        *,
        algorithm_name: str = "个人校准融合",
    ) -> None:
        self.model = model
        self.algorithm_name = str(algorithm_name)
        self._lock = threading.RLock()
        self._idle_probabilities: list[np.ndarray] = []
        self._idle_margins: list[float] = []
        self._class_thresholds = np.full(len(COMMANDS), self.model.confidence_threshold)
        self._margin_threshold = self.model.margin_threshold

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
        score = f"；整块留一 {float(accuracy) * 100:.1f}%" if isinstance(accuracy, (float, int)) else ""
        permission = "允许实机" if self.hardware_control_allowed else "仅科研测试，禁止实机"
        return (
            f"{self.algorithm_name}：双FBCCA分数＋个人LogReg已就绪"
            f"（完整{self.required_window_seconds:g}秒{score}；{permission}）"
        )

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        if not self.available:
            raise RuntimeError(self.model.error or f"{self.algorithm_name}模型不可用")
        posterior_samples = np.asarray(samples, dtype=np.float64)[:len(SSVEP_CHANNELS_1BASED)]
        quality = assess_signal_quality(
            posterior_samples,
            sample_rate_hz,
            deduplicate_channels=SSVEP_CHANNELS_1BASED,
        )
        prediction = self.model.predict(posterior_samples, sample_rate_hz)
        probabilities = np.asarray(
            [float(prediction["scores"][label]) for label in COMMANDS], dtype=np.float64,
        )
        order = np.argsort(probabilities)[::-1]
        best = int(order[0])
        confidence = float(probabilities[best])
        margin = float(confidence - probabilities[int(order[1])])
        usable_count = int(quality.get("usable_channel_count", 0))
        with self._lock:
            class_threshold = float(self._class_thresholds[best])
            margin_threshold = float(self._margin_threshold)
        quality_passed = usable_count >= 2
        confidence_passed = confidence >= class_threshold
        margin_passed = margin >= margin_threshold
        accepted = bool(quality_passed and confidence_passed and margin_passed)
        if not quality_passed:
            reason = "少于2个质量合格枕区通道，拒绝输出"
        elif not confidence_passed:
            reason = "个人分类概率未超过该指令空闲门槛"
        elif not margin_passed:
            reason = "个人分类前两类过于接近"
        else:
            reason = "双FBCCA证据经个人逻辑回归校准后通过"
        quality.update({
            "score_threshold": class_threshold,
            "margin_threshold": margin_threshold,
            "standard50_fbcca_scores": prediction["standard50_scores"],
            "equal46_fbcca_scores": prediction["equal46_scores"],
            "feature_pipeline": "50Hz陷波→[6/14/22–50标准FBCCA, 6/14/22–46等权FBCCA]→StandardScaler→LogReg",
            "quality_gate_applied": True,
            "required_usable_channel_count": 2,
            "production_approved": self.hardware_control_allowed,
        })
        return IntentResult(
            timestamp=time.time(),
            label=COMMANDS[best],
            confidence=confidence,
            margin=margin,
            accepted=accepted,
            reason=reason,
            frequency_hz=float(prediction["frequency_hz"]),
            scores=dict(prediction["scores"]),
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
        """The classifier has no temporal state; keep this session's idle gate."""

    def reload(self) -> bool:
        loaded = self.model.load()
        if loaded:
            self.begin_idle_calibration()
        return loaded

    def begin_idle_calibration(self) -> None:
        with self._lock:
            self._idle_probabilities.clear()
            self._idle_margins.clear()
            self._class_thresholds.fill(self.model.confidence_threshold)
            self._margin_threshold = self.model.margin_threshold

    def observe_idle(self, confidence: float, margin: float, scores=None) -> None:
        if scores is None or not all(label in scores for label in COMMANDS):
            return
        vector = np.asarray([scores[label] for label in COMMANDS], dtype=np.float64)
        gap = max(0.0, float(margin))
        if not np.isfinite(vector).all() or not np.isfinite(gap):
            return
        with self._lock:
            self._idle_probabilities.append(vector)
            self._idle_margins.append(gap)
            if len(self._idle_probabilities) < 8:
                return
            matrix = np.asarray(self._idle_probabilities)
            self._class_thresholds = np.maximum(
                self.model.confidence_threshold,
                np.percentile(matrix, 99.0, axis=0) + 0.03,
            )
            self._margin_threshold = max(
                self.model.margin_threshold,
                float(np.percentile(self._idle_margins, 99.0)) + 0.02,
            )

    def idle_calibration_summary(self) -> str:
        with self._lock:
            count = len(self._idle_probabilities)
            thresholds = self._class_thresholds.copy()
            margin = self._margin_threshold
        if count < 8:
            return f"{self.algorithm_name}空闲门槛样本不足：{count}/8"
        values = "、".join(
            f"{label}≥{threshold:.2f}" for label, threshold in zip(COMMANDS, thresholds)
        )
        return f"{self.algorithm_name}空闲门槛已校准（{count}窗）：{values}，差值≥{margin:.2f}"


__all__ = ["PersonalScoreCalibrationDecoder"]
