"""Pure NumPy filter-bank canonical-correlation SSVEP decoder.

This module is intentionally independent from Qt, LSL and the swarm gateway.
Its only output is a :class:`RecognitionResult`.
"""

from __future__ import annotations

import time
from typing import Iterable, Sequence

import numpy as np

from src.bci.config import (
    COMMANDS,
    FBCCA_MIN_MARGIN,
    FBCCA_MIN_SCORE,
    SSVEP_CHANNELS_1BASED,
    TARGET_FREQUENCIES_HZ,
)
from src.domain import RecognitionResult


def _orthonormal_basis(values: np.ndarray) -> np.ndarray:
    centered = values - values.mean(axis=0, keepdims=True)
    u, singular_values, _ = np.linalg.svd(centered, full_matrices=False)
    if not singular_values.size:
        return np.empty((centered.shape[0], 0))
    tolerance = max(centered.shape) * np.finfo(float).eps * singular_values[0]
    return u[:, singular_values > tolerance]


def _canonical_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Largest canonical correlation via principal angles (no sklearn model)."""

    qx = _orthonormal_basis(x.T)
    qy = _orthonormal_basis(y.T)
    if qx.shape[1] == 0 or qy.shape[1] == 0:
        return 0.0
    # einsum avoids a noisy Accelerate/BLAS matmul warning seen on some macOS
    # NumPy builds while computing the same small cross-basis matrix.
    cross_basis = np.einsum("ni,nj->ij", qx, qy, optimize=False)
    value = float(np.linalg.svd(cross_basis, compute_uv=False)[0])
    return max(0.0, min(1.0, value))


def _fft_bandpass(data: np.ndarray, sample_rate_hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    """Zero-phase windowed FFT band-pass suitable for a completed trial window."""

    sample_count = data.shape[1]
    frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    spectrum = np.fft.rfft(data, axis=1)
    mask = (frequencies >= low_hz) & (frequencies <= high_hz)
    spectrum[:, ~mask] = 0
    return np.fft.irfft(spectrum, n=sample_count, axis=1)


class FBCCADecoder:
    """Five-class SSVEP FBCCA decoder inherited from the lab configuration."""

    def __init__(
        self,
        frequencies_hz: Sequence[float] = TARGET_FREQUENCIES_HZ,
        labels: Sequence[str] = COMMANDS,
        channels_1based: Iterable[int] = SSVEP_CHANNELS_1BASED,
        harmonics: int = 2,
        min_score: float = FBCCA_MIN_SCORE,
        min_margin: float = FBCCA_MIN_MARGIN,
    ) -> None:
        if len(frequencies_hz) != len(labels) or len(labels) < 2:
            raise ValueError("FBCCA frequencies and labels must have the same length (at least two)")
        self.frequencies_hz = tuple(float(value) for value in frequencies_hz)
        self.labels = tuple(str(value) for value in labels)
        self.channels_1based = tuple(int(value) for value in channels_1based)
        self.harmonics = max(1, int(harmonics))
        self.min_score = float(min_score)
        self.min_margin = float(min_margin)

    def decode(self, samples, sample_rate_hz: float) -> RecognitionResult:
        data = np.asarray(samples, dtype=float)
        fs = float(sample_rate_hz)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] < 100:
            raise ValueError("EEG window must have shape (channels, samples) with at least 100 samples")
        if fs <= 0 or not np.isfinite(data).all():
            raise ValueError("EEG sample rate and sample values must be finite")

        selected = tuple(ch for ch in self.channels_1based if ch <= data.shape[0])
        if not selected:
            selected = tuple(range(1, data.shape[0] + 1))
        data = data[[channel - 1 for channel in selected], :]
        data = data - data.mean(axis=1, keepdims=True)
        standard_deviation = data.std(axis=1, keepdims=True)
        usable = standard_deviation[:, 0] > 1e-12
        if not np.any(usable):
            raise ValueError("All selected EEG channels are flat")
        data = data[usable] / standard_deviation[usable]
        used_channels = tuple(channel for channel, ok in zip(selected, usable) if ok)

        references = self._reference_signals(fs, data.shape[1])
        high_hz = min(45.0, fs * 0.48)
        low_edges = tuple(edge for edge in (6.0, 10.0, 14.0, 18.0) if edge < high_hz)
        weights = (1.0, 0.25, 0.11, 0.06)[: len(low_edges)]
        raw_scores = np.zeros(len(self.labels), dtype=float)
        for low_hz, weight in zip(low_edges, weights):
            filtered = _fft_bandpass(data, fs, low_hz, high_hz)
            for index, reference in enumerate(references):
                correlation = _canonical_correlation(filtered, reference)
                raw_scores[index] += weight * correlation * correlation

        order = np.argsort(raw_scores)[::-1]
        best_index = int(order[0])
        best_score = float(raw_scores[best_index])
        second_score = float(raw_scores[int(order[1])])
        margin = best_score - second_score
        accepted = best_score >= self.min_score and margin >= self.min_margin
        if best_score < self.min_score:
            reason = "置信度不足"
        elif margin < self.min_margin:
            reason = "区分度不足"
        else:
            reason = "识别成功"
        scores = {label: float(raw_scores[index]) for index, label in enumerate(self.labels)}
        return RecognitionResult(
            timestamp=time.time(),
            prediction=self.labels[best_index] if accepted else None,
            frequency_hz=self.frequencies_hz[best_index] if accepted else None,
            scores=scores,
            best_score=best_score,
            margin=margin,
            accepted=accepted,
            reason=reason,
            sample_rate_hz=fs,
            sample_count=int(data.shape[1]),
            used_channels_1based=used_channels,
        )

    def _reference_signals(self, sample_rate_hz: float, sample_count: int) -> list[np.ndarray]:
        timeline = np.arange(sample_count, dtype=float) / sample_rate_hz
        references: list[np.ndarray] = []
        for frequency in self.frequencies_hz:
            components = []
            for harmonic in range(1, self.harmonics + 1):
                phase = 2.0 * np.pi * harmonic * frequency * timeline
                components.extend((np.sin(phase), np.cos(phase)))
            references.append(np.asarray(components))
        return references
