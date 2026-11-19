"""Feature extraction used by the dual-evidence personal score calibrator.

This deliberately mirrors the fixed configuration selected in the October-24
offline audit.  It does not run FFT merely for plotting: each FFT mask is an
ideal zero-phase sub-band and the resulting waveforms enter CCA.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import _canonical_correlation
from src.bci.preprocessing import fft_bandpass, notch_mains, remove_linear_drift
from src.ml.personal_score_contract import FEATURE_NAMES


@dataclass(frozen=True)
class DualFBCCAFeatureResult:
    values: np.ndarray
    standard50_scores: np.ndarray
    equal46_scores: np.ndarray


def _references(
    sample_rate_hz: float,
    sample_count: int,
    high_hz: float,
    harmonics: int = 4,
) -> tuple[np.ndarray, ...]:
    timeline = np.arange(sample_count, dtype=np.float64) / float(sample_rate_hz)
    references = []
    for frequency in TARGET_FREQUENCIES_HZ:
        rows = []
        for harmonic in range(1, harmonics + 1):
            if harmonic * float(frequency) > float(high_hz):
                break
            phase = 2.0 * np.pi * harmonic * float(frequency) * timeline
            rows.extend((np.sin(phase), np.cos(phase)))
        references.append(np.asarray(rows, dtype=np.float64))
    return tuple(references)


def _branch_scores(
    data: np.ndarray,
    sample_rate_hz: float,
    *,
    high_hz: float,
    equal_weights: bool,
) -> np.ndarray:
    references = _references(sample_rate_hz, data.shape[1], high_hz)
    scores = np.zeros(len(TARGET_FREQUENCIES_HZ), dtype=np.float64)
    for bank_index, low_hz in enumerate((6.0, 14.0, 22.0), start=1):
        if low_hz >= high_hz:
            continue
        filtered = fft_bandpass(data, sample_rate_hz, low_hz, high_hz)
        weight = 1.0 if equal_weights else bank_index ** -1.25 + 0.25
        for class_index, reference in enumerate(references):
            correlation = _canonical_correlation(filtered, reference)
            scores[class_index] += weight * correlation * correlation
    return scores


def extract_dual_fbcca_features(
    samples,
    sample_rate_hz: float,
    *,
    sample_count: int,
    channel_count: int = 8,
) -> DualFBCCAFeatureResult:
    """Return the fixed 12-score feature vector expected by the model.

    The full requested interval is used.  No leading/trailing samples are
    discarded.  A longer input is cropped to its most recent complete window;
    a shorter input is rejected instead of being padded or silently resized.
    """

    fs = float(sample_rate_hz)
    data = np.asarray(samples, dtype=np.float64)
    if fs <= 0.0:
        raise ValueError("EEG sample rate must be positive")
    if data.ndim != 2 or data.shape[0] < int(channel_count):
        raise ValueError(f"个人校准融合需要前 {channel_count} 个枕区 EEG 通道")
    if data.shape[1] < int(sample_count):
        seconds = int(sample_count) / fs
        raise ValueError(f"个人校准融合需要完整 {seconds:g} 秒 EEG 窗口")
    data = data[:int(channel_count), -int(sample_count):].copy()
    if not np.isfinite(data).all():
        raise ValueError("EEG 含 NaN/Inf，不能提取个人校准特征")
    data = remove_linear_drift(data)
    data = notch_mains(data, fs, 50.0, 30.0)
    scale = data.std(axis=1, keepdims=True)
    if np.any(scale[:, 0] <= 1e-12):
        raise ValueError("EEG 存在平坦通道，不能提取个人校准特征")
    data /= scale
    standard = _branch_scores(data, fs, high_hz=50.0, equal_weights=False)
    equal = _branch_scores(data, fs, high_hz=46.0, equal_weights=True)
    values = np.concatenate((standard, equal))
    if values.shape != (len(FEATURE_NAMES),):
        raise RuntimeError("个人校准特征维数与模型契约不一致")
    return DualFBCCAFeatureResult(values, standard, equal)


__all__ = ["DualFBCCAFeatureResult", "extract_dual_fbcca_features"]
