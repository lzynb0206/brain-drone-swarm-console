"""Preprocessing shared by FBCCA and future offline evaluation tools."""

from __future__ import annotations

from typing import Iterable

import numpy as np


FILTER_BANK_LOW_EDGES_HZ = (6.0, 10.0, 14.0, 18.0)
FILTER_BANK_WEIGHTS = (1.0, 0.25, 0.11, 0.06)


def prepare_ssvep_channels(samples, channels_1based: Iterable[int]):
    """Validate/select channels and return standardized data plus quality metadata."""

    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] < 100:
        raise ValueError("EEG window must have shape (channels, samples) with at least 100 samples")
    if not np.isfinite(data).all():
        raise ValueError("EEG contains NaN or Inf")
    requested = tuple(int(ch) for ch in channels_1based if 1 <= int(ch) <= data.shape[0])
    if not requested:
        requested = tuple(range(1, data.shape[0] + 1))
    selected = data[[channel - 1 for channel in requested], :]
    selected = selected - selected.mean(axis=1, keepdims=True)
    standard_deviation = selected.std(axis=1, keepdims=True)
    usable = standard_deviation[:, 0] > 1e-12
    if not np.any(usable):
        raise ValueError("All selected EEG channels are flat")
    used_channels = tuple(channel for channel, ok in zip(requested, usable) if ok)
    excluded_channels = tuple(channel for channel, ok in zip(requested, usable) if not ok)
    standardized = selected[usable] / standard_deviation[usable]
    quality = {
        "status": "good" if not excluded_channels else "partial",
        "requested_channels": list(requested),
        "used_channels": list(used_channels),
        "excluded_flat_channels": list(excluded_channels),
        "usable_channel_count": len(used_channels),
        "total_channel_count": int(data.shape[0]),
    }
    return standardized, used_channels, quality


def fft_bandpass(data: np.ndarray, sample_rate_hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    sample_count = data.shape[1]
    frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    spectrum = np.fft.rfft(data, axis=1)
    mask = (frequencies >= low_hz) & (frequencies <= high_hz)
    spectrum[:, ~mask] = 0
    return np.fft.irfft(spectrum, n=sample_count, axis=1)


def filter_bank(data: np.ndarray, sample_rate_hz: float):
    high_hz = min(45.0, sample_rate_hz * 0.48)
    for low_hz, weight in zip(FILTER_BANK_LOW_EDGES_HZ, FILTER_BANK_WEIGHTS):
        if low_hz < high_hz:
            yield fft_bandpass(data, sample_rate_hz, low_hz, high_hz), weight
