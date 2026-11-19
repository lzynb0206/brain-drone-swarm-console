"""BN-FBCCA correction; relative margin is an index, not a probability.

The existing three filter banks are unchanged. Method A preserves ranking;
method B subtracts per-class means of explicitly observed idle windows.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading

import numpy as np

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ, FBCCA_MIN_SCORE, FBCCA_MIN_MARGIN
from src.bci.fbcca_decoder import FBCCADecoder


@dataclass(frozen=True)
class BNScores:
    normalized: np.ndarray
    prediction: int
    margin: float
    formula_confidence: float | None
    confidence: float


def normalize_scores(raw_scores, mode="a", background=None) -> BNScores:
    raw = np.asarray(raw_scores, dtype=float)
    if raw.ndim != 1 or raw.size < 2 or not np.isfinite(raw).all():
        raise ValueError("Expected a finite class-score vector")
    if mode == "a":
        values = raw - (raw.sum() - raw) / (raw.size - 1)
    elif mode == "b":
        baseline = np.asarray(background, dtype=float)
        if baseline.shape != raw.shape or not np.isfinite(baseline).all():
            raise ValueError("Method B requires a finite per-class idle background")
        values = raw - baseline
    else:
        raise ValueError("BN mode must be a or b")
    order = np.argsort(-values, kind="stable")
    top, second = values[order[:2]]
    gap = float(top - second)
    formula = float(gap / top) if abs(top) > 1e-12 else None
    confidence = float(np.clip(gap / top, 0., 1.)) if top > 1e-12 else 0.
    return BNScores(values, int(order[0]), gap, formula, confidence)


def gate_scores(result: BNScores, raw, threshold: float | None,
                min_raw_score=FBCCA_MIN_SCORE, min_margin=FBCCA_MIN_MARGIN) -> bool:
    return bool(threshold is not None and result.normalized[result.prediction] > 1e-12
                and result.confidence >= threshold
                and raw[result.prediction] >= min_raw_score and result.margin >= min_margin)


class BNFBCCADecoder:
    """Selectable streaming research decoder; no diffusion runs online."""

    hardware_control_allowed = False

    def __init__(self, mode="b", *, confidence_threshold=0.70, min_idle_windows=8,
                 max_idle_windows=256, calibration_path: Path | None = None,
                 channels_1based=None, filter_method="fft", harmonics=4,
                 min_score=FBCCA_MIN_SCORE, min_margin=FBCCA_MIN_MARGIN):
        if mode not in ("a", "b"):
            raise ValueError("BN mode must be a or b")
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("Confidence threshold must be in [0, 1]")
        if not 1 <= min_idle_windows <= max_idle_windows:
            raise ValueError("Invalid idle window limits")
        self.mode = mode
        self.confidence_threshold = float(confidence_threshold)
        self.min_idle_windows = int(min_idle_windows)
        self.max_idle_windows = int(max_idle_windows)
        self.min_score, self.min_margin = float(min_score), float(min_margin)
        self._fbcca = FBCCADecoder(filter_method=filter_method, harmonics=harmonics,
            min_score=min_score, min_margin=min_margin,
            **({"channels_1based": channels_1based} if channels_1based is not None else {}))
        if calibration_path is not None and filter_method != "fft":
            raise ValueError("Existing BN gate files require the original FFT filter")
        if calibration_path is not None and channels_1based is not None and tuple(channels_1based) != tuple(range(1, 9)):
            raise ValueError("Existing BN gate files are calibrated for the legacy eight-channel montage")
        self._lock = threading.RLock()
        self._idle: list[np.ndarray] = []
        self._contract: tuple[float, int] | None = None
        self._gate_contract: tuple[float, int] | None = None
        self._gate_source = "unfitted_configured_threshold"
        self.calibration_path = Path(calibration_path) if calibration_path else None
        if self.calibration_path and self.calibration_path.exists():
            self.reload()

    @property
    def idle_calibration_seconds(self):
        return 12.0 if self.mode == "b" else 0.0

    @property
    def backend_status(self):
        return f"BN-FBCCA-{self.mode.upper()} · 实时研究解码 · 门控 {self.confidence_threshold}"

    def reset(self):
        self.begin_idle_calibration()

    def begin_idle_calibration(self):
        with self._lock:
            self._idle.clear()
            self._contract = None

    def observe_idle_result(self, result):
        """Only the explicit idle phase calls this; ordinary decode never learns."""
        if self.mode != "b" or len(result.used_channels_1based) < 2:
            return
        if result.signal_quality.get("bn_mode") != self.mode:
            return
        raw = np.asarray([result.scores[label] for label in COMMANDS], dtype=float)
        if not np.isfinite(raw).all():
            return
        contract = (float(result.sample_rate_hz), int(result.sample_count))
        with self._lock:
            if contract != self._contract:
                self._idle.clear()
                self._contract = contract
            self._idle.append(raw.copy())
            self._idle = self._idle[-self.max_idle_windows:]

    def idle_calibration_summary(self):
        if self.mode == "a":
            return "BN-FBCCA-A 无需空闲校准；该变换不改变六类排序"
        with self._lock:
            count = len(self._idle)
        return f"BN-FBCCA-B 空闲窗口 {count}/{self.min_idle_windows}；门控来源 {self._gate_source}"

    def reload(self):
        if not self.calibration_path:
            raise ValueError("No BN gate artifact configured")
        payload = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        if (payload.get("schema_version") != 1 or payload.get("mode") != self.mode
                or tuple(payload.get("frequencies_hz", [])) != TARGET_FREQUENCIES_HZ
                or payload.get("confidence_definition") != "positive_top_clipped_relative_margin_v1"
                or payload.get("background_policy") != ("none" if self.mode == "a" else "session_prefix_idle_mean")):
            raise ValueError("BN gate artifact does not match decoder contract")
        threshold = payload.get("threshold")
        if threshold is not None and not 0 <= float(threshold) <= 1:
            raise ValueError("Invalid BN threshold")
        fs, count = float(payload["sample_rate_hz"]), int(payload["sample_count"])
        if fs <= 0 or count < 100 or payload.get("min_idle_windows") != self.min_idle_windows:
            raise ValueError("Invalid BN window/calibration contract")
        if payload.get("min_raw_score") != FBCCA_MIN_SCORE or payload.get("min_margin") != FBCCA_MIN_MARGIN:
            raise ValueError("BN raw-score/margin thresholds differ from calibration")
        with self._lock:
            self.confidence_threshold = None if threshold is None else float(threshold)
            self._gate_contract = (fs, count)
            self._gate_source = str(self.calibration_path)
        return True

    def decode(self, samples, sample_rate_hz):
        original = self._fbcca.decode(samples, sample_rate_hz)
        raw = np.asarray([original.scores[label] for label in COMMANDS])
        contract = (float(sample_rate_hz), original.sample_count)
        with self._lock:
            if contract != self._contract:
                self._idle.clear()
                self._contract = contract
            count = len(self._idle)
            ready = self.mode == "a" or count >= self.min_idle_windows
            background = np.mean(self._idle, axis=0) if self._idle else np.zeros(len(COMMANDS))
            result = normalize_scores(raw, self.mode, background)
            matching_gate = self._gate_contract is None or contract == self._gate_contract
            accepted = (ready and matching_gate and len(original.used_channels_1based) >= 2
                        and gate_scores(result, raw, self.confidence_threshold, self.min_score, self.min_margin))
            reason = ("空闲校准不足" if not ready else "门控标定窗口不匹配" if not matching_gate
                      else "通过 BN 门控" if accepted else "未通过 BN 置信度/原始分数/差值门控")
            quality = {**original.signal_quality,
                       "bn_mode": self.mode, "bn_idle_windows": count,
                       "bn_background": background.tolist() if self.mode == "b" else None,
                       "background_corrected_scores": dict(zip(COMMANDS, result.normalized.tolist())),
                       "p_ssvep": result.confidence, "p_ssvep_unclipped": result.formula_confidence,
                       "confidence_is_probability": False,
                       "bn_confidence_threshold": self.confidence_threshold,
                       "bn_gate_source": self._gate_source,
                       "raw_winner_score": float(raw[result.prediction])}
        return replace(original, label=COMMANDS[result.prediction],
                       frequency_hz=TARGET_FREQUENCIES_HZ[result.prediction],
                       confidence=result.confidence, margin=result.margin,
                       accepted=bool(accepted), reason=reason, signal_quality=quality)
