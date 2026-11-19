"""Self-contrast-normalized filter-bank CCA (SCN-FBCCA).

This is a standalone, calibration-free FBCCA score.  It does not fuse FBCCA
with a classifier or with a second decoder.  Instead, each candidate's
three-harmonic filter-bank CCA score is divided by a robust local CCA floor
estimated from six DFT-bin-aligned flank references in the same EEG window.

The idea is related to normalized CCA (NCCA); the research contribution being
tested here is its robust, equal-rank, filter-bank formulation.  It must not be
described as wholly unprecedented without a complete literature review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import _canonical_correlation
from src.bci.preprocessing import (
    FILTER_BANK_HIGH_EDGE_HZ,
    FILTER_BANK_LOW_EDGES_HZ,
    FILTER_BANK_WEIGHTS,
    fft_bandpass,
)


FLANK_BIN_DISTANCES = (2, 3, 4)


@dataclass(frozen=True)
class SCNFBCCAScores:
    """Auditable score components for one preprocessed EEG window."""

    normalized: np.ndarray
    target: np.ndarray
    local_background: np.ndarray
    flank_scores: np.ndarray
    harmonics: int
    frequency_resolution_hz: float
    flank_offsets_hz: tuple[float, ...]

    @property
    def prediction_index(self) -> int:
        return int(np.argmax(self.normalized))


def _references(
    frequencies_hz: Sequence[float],
    sample_rate_hz: float,
    sample_count: int,
    harmonics: int,
) -> tuple[np.ndarray, ...]:
    timeline = np.arange(sample_count, dtype=np.float64) / float(sample_rate_hz)
    result = []
    for frequency in frequencies_hz:
        rows = []
        for harmonic in range(1, int(harmonics) + 1):
            phase = 2.0 * np.pi * harmonic * float(frequency) * timeline
            rows.extend((np.sin(phase), np.cos(phase)))
        result.append(np.asarray(rows, dtype=np.float64))
    return tuple(result)


def _equal_rank_fbcca(
    data: np.ndarray,
    sample_rate_hz: float,
    frequencies_hz: Sequence[float],
    harmonics: int,
    high_hz: float,
) -> np.ndarray:
    references = _references(
        frequencies_hz,
        sample_rate_hz,
        data.shape[1],
        harmonics,
    )
    scores = np.zeros(len(frequencies_hz), dtype=np.float64)
    for low_hz, weight in zip(FILTER_BANK_LOW_EDGES_HZ, FILTER_BANK_WEIGHTS):
        if float(low_hz) >= float(high_hz):
            continue
        filtered = fft_bandpass(data, sample_rate_hz, low_hz, high_hz)
        for index, reference in enumerate(references):
            rho = _canonical_correlation(filtered, reference)
            scores[index] += float(weight) * rho * rho
    return scores


def self_contrast_normalized_fbcca_scores(
    samples: np.ndarray,
    sample_rate_hz: float,
    *,
    frequencies_hz: Sequence[float] = TARGET_FREQUENCIES_HZ,
    high_hz: float = FILTER_BANK_HIGH_EDGE_HZ,
    flank_bin_distances: Sequence[int] = FLANK_BIN_DISTANCES,
) -> SCNFBCCAScores:
    """Calculate a zero-training, robust local-contrast FBCCA score.

    ``samples`` must already have passed channel quality checks and undergone
    detrending, mains removal, and per-channel standardization.  No label,
    previous trial, fitted coefficient, or subject template is consumed.
    """

    data = np.asarray(samples, dtype=np.float64)
    fs = float(sample_rate_hz)
    frequencies = tuple(float(value) for value in frequencies_hz)
    distances = tuple(sorted({int(value) for value in flank_bin_distances}))
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 4:
        raise ValueError("SCN-FBCCA expects at least two channels and four samples")
    if not np.isfinite(data).all():
        raise ValueError("SCN-FBCCA input contains NaN/Inf")
    if fs <= 0.0 or not 0.0 < float(high_hz) < fs / 2.0:
        raise ValueError("Invalid sample rate or high-frequency edge")
    if len(frequencies) < 2 or len(set(frequencies)) != len(frequencies):
        raise ValueError("Candidate frequencies must be unique")
    if not distances or distances[0] < 2:
        raise ValueError("Flank distances must start at least two DFT bins from the target")

    # Equal reference rank avoids giving 8--12 Hz an extra CCA dimension when
    # the fourth harmonic of the highest candidate lies above 50 Hz.
    harmonics = max(1, min(4, int(float(high_hz) // max(frequencies))))
    target = _equal_rank_fbcca(data, fs, frequencies, harmonics, float(high_hz))
    resolution = fs / data.shape[1]
    offsets = tuple(
        sign * distance * resolution
        for distance in reversed(distances)
        for sign in (-1.0,)
    ) + tuple(
        distance * resolution
        for distance in distances
    )
    flank_scores = np.stack([
        _equal_rank_fbcca(
            data,
            fs,
            tuple(frequency + offset for frequency in frequencies),
            harmonics,
            float(high_hz),
        )
        for offset in offsets
    ])
    background = np.median(flank_scores, axis=0)
    normalized = target / np.maximum(background, 1e-12)
    return SCNFBCCAScores(
        normalized=normalized,
        target=target,
        local_background=background,
        flank_scores=flank_scores,
        harmonics=harmonics,
        frequency_resolution_hz=float(resolution),
        flank_offsets_hz=tuple(float(value) for value in offsets),
    )


__all__ = [
    "FLANK_BIN_DISTANCES",
    "SCNFBCCAScores",
    "self_contrast_normalized_fbcca_scores",
]
