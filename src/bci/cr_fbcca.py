"""Calibration-free contrast-regularized filter-bank CCA.

CR-FBCCA keeps conventional FBCCA as the dominant decision signal and adds a
small, bounded correction from an equal-dimensional local-frequency contrast.
It contains no fitted subject parameters or labelled templates.

This module exposes score extraction only.  Runtime command thresholds have
not yet been calibrated for the fused score and must remain on the production
FBCCA decoder until a prospective online safety study is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import _canonical_correlation
from src.bci.preprocessing import fft_bandpass


CR_WEIGHT = 0.15
LOCAL_OFFSET_FRACTIONS = (-0.8, -0.6, -0.4, 0.4, 0.6, 0.8)


@dataclass(frozen=True)
class CRFBCCAScores:
    """Auditable score components for one EEG window."""

    fused: np.ndarray
    fbcca: np.ndarray
    neighborhood_ratio: np.ndarray
    correction_weight: float
    equal_harmonics: int
    local_offsets_hz: tuple[float, ...]

    @property
    def prediction_index(self) -> int:
        return int(np.argmax(self.fused))


def _unit_evidence(scores: np.ndarray) -> np.ndarray:
    centered = np.asarray(scores, dtype=np.float64) - float(np.mean(scores))
    return centered / max(float(np.linalg.norm(centered)), 1e-12)


def _references(
    frequencies_hz: Sequence[float],
    sample_rate_hz: float,
    sample_count: int,
    harmonics: int,
    high_hz: float,
) -> tuple[np.ndarray, ...]:
    timeline = np.arange(sample_count, dtype=np.float64) / float(sample_rate_hz)
    result = []
    for frequency in frequencies_hz:
        rows = []
        for harmonic in range(1, int(harmonics) + 1):
            if harmonic * float(frequency) > float(high_hz):
                break
            phase = 2.0 * np.pi * harmonic * float(frequency) * timeline
            rows.extend((np.sin(phase), np.cos(phase)))
        if not rows:
            raise ValueError(f"No usable harmonic for {frequency:g} Hz below {high_hz:g} Hz")
        result.append(np.asarray(rows, dtype=np.float64))
    return tuple(result)


def _fbcca(
    data: np.ndarray,
    sample_rate_hz: float,
    frequencies_hz: Sequence[float],
    *,
    harmonics: int,
    high_hz: float,
) -> np.ndarray:
    references = _references(
        frequencies_hz,
        sample_rate_hz,
        data.shape[1],
        harmonics,
        high_hz,
    )
    scores = np.zeros(len(frequencies_hz), dtype=np.float64)
    for bank_index, low_hz in enumerate((6.0, 14.0, 22.0), start=1):
        if low_hz >= high_hz:
            continue
        filtered = fft_bandpass(data, sample_rate_hz, low_hz, high_hz)
        weight = bank_index ** -1.25 + 0.25
        for index, reference in enumerate(references):
            rho = _canonical_correlation(filtered, reference)
            scores[index] += weight * rho * rho
    return scores


def contrast_regularized_fbcca_scores(
    samples: np.ndarray,
    sample_rate_hz: float,
    *,
    frequencies_hz: Sequence[float] = TARGET_FREQUENCIES_HZ,
    high_hz: float = 50.0,
    correction_weight: float = CR_WEIGHT,
) -> CRFBCCAScores:
    """Return zero-training CR-FBCCA evidence for a preprocessed EEG window.

    ``samples`` must already be channel-quality checked, detrended, mains
    notched, and standardized, matching the existing FBCCA preprocessing.
    The local reference offsets are fractions of the minimum target spacing;
    no labels or previous trials enter the calculation.
    """

    data = np.asarray(samples, dtype=np.float64)
    fs = float(sample_rate_hz)
    frequencies = tuple(float(value) for value in frequencies_hz)
    alpha = float(correction_weight)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 4:
        raise ValueError("CR-FBCCA expects at least two channels and four samples")
    if not np.isfinite(data).all():
        raise ValueError("CR-FBCCA input contains NaN/Inf")
    if fs <= 0.0 or not 0.0 < high_hz < fs / 2.0:
        raise ValueError("Invalid sample rate or high-frequency edge")
    ordered = tuple(sorted(frequencies))
    if len(ordered) < 2 or any(right <= left for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Candidate frequencies must be unique")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("Correction weight must be between zero and one")

    spacing = min(right - left for left, right in zip(ordered, ordered[1:]))
    offsets = tuple(float(spacing * value) for value in LOCAL_OFFSET_FRACTIONS)
    base = _fbcca(
        data,
        fs,
        frequencies,
        harmonics=4,
        high_hz=high_hz,
    )

    # Every candidate receives the same reference rank.  With 8--13 Hz and a
    # 50-Hz upper edge this is three harmonics, avoiding the conventional
    # 13-Hz disadvantage where its fourth harmonic lies above the passband.
    equal_harmonics = min(4, int(high_hz // max(frequencies)))
    equal_harmonics = max(1, equal_harmonics)
    target = _fbcca(
        data,
        fs,
        frequencies,
        harmonics=equal_harmonics,
        high_hz=high_hz,
    )
    neighbors = np.stack([
        _fbcca(
            data,
            fs,
            tuple(frequency + offset for frequency in frequencies),
            harmonics=equal_harmonics,
            high_hz=high_hz,
        )
        for offset in offsets
    ])
    background = np.median(neighbors, axis=0)
    ratio = target / np.maximum(background, 1e-12)
    fused = (1.0 - alpha) * _unit_evidence(base) + alpha * _unit_evidence(ratio)
    return CRFBCCAScores(
        fused=fused,
        fbcca=base,
        neighborhood_ratio=ratio,
        correction_weight=alpha,
        equal_harmonics=equal_harmonics,
        local_offsets_hz=offsets,
    )


__all__ = [
    "CR_WEIGHT",
    "LOCAL_OFFSET_FRACTIONS",
    "CRFBCCAScores",
    "contrast_regularized_fbcca_scores",
]
