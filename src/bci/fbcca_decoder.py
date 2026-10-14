"""Pure NumPy filter-bank canonical-correlation SSVEP decoder."""

from __future__ import annotations

import threading
import time
from typing import Iterable, Sequence

import numpy as np

from src.bci.config import (
    COMMANDS,
    FBCCA_AUTO_MAX_MARGIN,
    FBCCA_AUTO_MAX_SCORE,
    SSVEP_CHANNELS_1BASED,
    TARGET_FREQUENCIES_HZ,
)
from src.bci.preprocessing import filter_bank, prepare_ssvep_channels
from src.domain import IntentResult
from src.services.intent_safety_service import IntentSafetyService


def _orthonormal_basis(values: np.ndarray) -> np.ndarray:
    centered = values - values.mean(axis=0, keepdims=True)
    u, singular_values, _ = np.linalg.svd(centered, full_matrices=False)
    if not singular_values.size:
        return np.empty((centered.shape[0], 0))
    tolerance = max(centered.shape) * np.finfo(float).eps * singular_values[0]
    return u[:, singular_values > tolerance]


def _canonical_correlation(x: np.ndarray, y: np.ndarray) -> float:
    qx = _orthonormal_basis(x.T)
    qy = _orthonormal_basis(y.T)
    if qx.shape[1] == 0 or qy.shape[1] == 0:
        return 0.0
    cross_basis = np.einsum("ni,nj->ij", qx, qy, optimize=False)
    value = float(np.linalg.svd(cross_basis, compute_uv=False)[0])
    return max(0.0, min(1.0, value))


class FBCCADecoder:
    """Decode an 8×1000 SSVEP window into a side-effect-free IntentResult."""

    def __init__(
        self,
        frequencies_hz: Sequence[float] = TARGET_FREQUENCIES_HZ,
        labels: Sequence[str] = COMMANDS,
        channels_1based: Iterable[int] = SSVEP_CHANNELS_1BASED,
        harmonics: int = 2,
        safety_service: IntentSafetyService | None = None,
        min_score: float | None = None,
        min_margin: float | None = None,
    ) -> None:
        if len(frequencies_hz) != len(labels) or len(labels) < 2:
            raise ValueError("FBCCA frequencies and labels must have the same length (at least two)")
        self.frequencies_hz = tuple(float(value) for value in frequencies_hz)
        self.labels = tuple(str(value) for value in labels)
        self.channels_1based = tuple(int(value) for value in channels_1based)
        self.harmonics = max(1, int(harmonics))
        if safety_service is None:
            kwargs = {}
            if min_score is not None:
                kwargs["min_confidence"] = min_score
            if min_margin is not None:
                kwargs["min_margin"] = min_margin
            safety_service = IntentSafetyService(**kwargs)
        self.safety_service = safety_service
        self._threshold_lock = threading.RLock()
        self._base_min_score = float(self.safety_service.min_confidence)
        self._base_min_margin = float(self.safety_service.min_margin)
        self._idle_scores: list[float] = []
        self._idle_margins: list[float] = []

    def decode(self, samples, sample_rate_hz: float) -> IntentResult:
        fs = float(sample_rate_hz)
        if fs <= 0:
            raise ValueError("EEG sample rate must be positive")
        data, used_channels, quality = prepare_ssvep_channels(samples, self.channels_1based, fs)
        references = self._reference_signals(fs, data.shape[1])
        raw_scores = np.zeros(len(self.labels), dtype=float)
        for filtered, weight in filter_bank(data, fs):
            for index, reference in enumerate(references):
                correlation = _canonical_correlation(filtered, reference)
                raw_scores[index] += weight * correlation * correlation

        order = np.argsort(raw_scores)[::-1]
        best_index = int(order[0])
        confidence = float(raw_scores[best_index])
        margin = confidence - float(raw_scores[int(order[1])])
        with self._threshold_lock:
            decision = self.safety_service.evaluate(confidence, margin)
        return IntentResult(
            timestamp=time.time(),
            label=self.labels[best_index],
            confidence=confidence,
            margin=margin,
            accepted=decision.accepted,
            reason=decision.reason,
            frequency_hz=self.frequencies_hz[best_index],
            scores={label: float(raw_scores[index]) for index, label in enumerate(self.labels)},
            sample_rate_hz=fs,
            sample_count=int(data.shape[1]),
            used_channels_1based=used_channels,
            signal_quality=quality,
        )

    def reset(self) -> None:
        """FBCCA is stateless between windows; keep session calibration intact."""

    def begin_idle_calibration(self) -> None:
        """Start a fresh, non-blocking idle calibration for this control session."""

        with self._threshold_lock:
            self._idle_scores.clear()
            self._idle_margins.clear()
            self.safety_service.min_confidence = self._base_min_score
            self.safety_service.min_margin = self._base_min_margin

    def observe_idle(self, confidence: float, margin: float) -> None:
        """Adapt rejection thresholds from an explicitly dark/idle interval.

        Only the upper quartile is used and both thresholds have modest caps.
        Calibration therefore reduces obvious false positives without turning a
        noisy three-second baseline into an impossible command threshold.
        """

        score = float(confidence)
        gap = max(0.0, float(margin))
        if not np.isfinite(score) or not np.isfinite(gap):
            return
        with self._threshold_lock:
            self._idle_scores.append(score)
            self._idle_margins.append(gap)
            # Two windows are enough for a best-effort update.  If fewer arrive,
            # the original permissive defaults remain active.
            if len(self._idle_scores) < 2:
                return
            score_q75 = float(np.percentile(self._idle_scores, 75.0))
            margin_q75 = float(np.percentile(self._idle_margins, 75.0))
            self.safety_service.min_confidence = min(
                max(self._base_min_score, score_q75 + 0.005),
                max(self._base_min_score, FBCCA_AUTO_MAX_SCORE),
            )
            self.safety_service.min_margin = min(
                max(self._base_min_margin, margin_q75 + 0.003),
                max(self._base_min_margin, FBCCA_AUTO_MAX_MARGIN),
            )

    def idle_calibration_summary(self) -> str:
        with self._threshold_lock:
            count = len(self._idle_scores)
            score = float(self.safety_service.min_confidence)
            margin = float(self.safety_service.min_margin)
        if count < 2:
            return (
                "FBCCA 空闲校准样本不足，继续使用宽松默认阈值："
                f"分数 {score:.3f}、差值 {margin:.3f}"
            )
        return (
            f"FBCCA 已按当前操作者的 {count} 个空闲窗口轻量校准："
            f"分数阈值 {score:.3f}、差值阈值 {margin:.3f}"
        )

    def _reference_signals(self, sample_rate_hz: float, sample_count: int) -> list[np.ndarray]:
        timeline = np.arange(sample_count, dtype=float) / sample_rate_hz
        references = []
        for frequency in self.frequencies_hz:
            components = []
            for harmonic in range(1, self.harmonics + 1):
                phase = 2.0 * np.pi * harmonic * frequency * timeline
                components.extend((np.sin(phase), np.cos(phase)))
            references.append(np.asarray(components))
        return references
