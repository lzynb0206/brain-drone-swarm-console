"""Preprocessing shared by FBCCA and future offline evaluation tools."""

from __future__ import annotations

from typing import Iterable

import numpy as np


FILTER_BANK_LOW_EDGES_HZ = (6.0, 10.0, 14.0, 18.0)
FILTER_BANK_WEIGHTS = (1.0, 0.25, 0.11, 0.06)


def assess_signal_quality(samples, sample_rate_hz: float) -> dict[str, object]:
    """Measure per-channel amplitude, variance and 50 Hz interference."""

    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[1] < 32:
        raise ValueError("EEG quality input must have shape (channels, samples)")
    finite = np.isfinite(data).all(axis=1)
    centered = data - np.nanmean(data, axis=1, keepdims=True)
    std_uv = np.nanstd(centered, axis=1)
    max_amplitude_uv = np.nanmax(np.abs(centered), axis=1)
    filtered = fft_bandpass(centered, sample_rate_hz, 5.0, min(45.0, sample_rate_hz * 0.48))
    filtered_variance = np.nanvar(filtered, axis=1)

    frequencies = np.fft.rfftfreq(data.shape[1], d=1.0 / float(sample_rate_hz))
    power = np.abs(np.fft.rfft(np.nan_to_num(centered), axis=1)) ** 2
    total_band = (frequencies >= 1.0) & (frequencies <= min(80.0, sample_rate_hz * 0.48))
    mains_band = (frequencies >= 49.0) & (frequencies <= 51.0)
    total_power = np.sum(power[:, total_band], axis=1)
    mains_power = np.sum(power[:, mains_band], axis=1) if np.any(mains_band) else np.zeros(data.shape[0])
    mains_ratio = np.divide(mains_power, total_power, out=np.zeros_like(mains_power), where=total_power > 0)

    usable = finite & (std_uv >= 0.5) & (max_amplitude_uv <= 2500.0) & (mains_ratio <= 0.5)
    channels = []
    for index in range(data.shape[0]):
        issues = []
        if not finite[index]:
            issues.append("NaN/Inf")
        if std_uv[index] < 0.5:
            issues.append("平坦/方差过低")
        if max_amplitude_uv[index] > 2500.0:
            issues.append("振幅过大")
        if mains_ratio[index] > 0.5:
            issues.append("50Hz工频占比过高")
        channels.append({
            "channel": index + 1,
            "usable": bool(usable[index]),
            "std_uv": float(std_uv[index]),
            "max_centered_amplitude_uv": float(max_amplitude_uv[index]),
            "filtered_variance_uv2": float(filtered_variance[index]),
            "mains_50hz_ratio": float(mains_ratio[index]),
            "issues": issues,
        })
    return {
        "status": "good" if bool(np.all(usable)) else ("partial" if bool(np.any(usable)) else "bad"),
        "usable_channel_count": int(np.sum(usable)),
        "total_channel_count": int(data.shape[0]),
        "usable_mask": usable.tolist(),
        "channels": channels,
    }


def prepare_ssvep_channels(samples, channels_1based: Iterable[int], sample_rate_hz: float = 250.0):
    """Validate/select channels and return standardized data plus quality metadata."""

    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] < 100:
        raise ValueError("EEG window must have shape (channels, samples) with at least 100 samples")
    quality = assess_signal_quality(data, sample_rate_hz)
    requested = tuple(int(ch) for ch in channels_1based if 1 <= int(ch) <= data.shape[0])
    if not requested:
        requested = tuple(range(1, data.shape[0] + 1))
    selected = data[[channel - 1 for channel in requested], :]
    selected = selected - selected.mean(axis=1, keepdims=True)
    standard_deviation = selected.std(axis=1, keepdims=True)
    global_mask = np.asarray(quality["usable_mask"], dtype=bool)
    usable = (standard_deviation[:, 0] > 1e-12) & global_mask[[channel - 1 for channel in requested]]
    if not np.any(usable):
        raise ValueError("All selected EEG channels are flat")
    used_channels = tuple(channel for channel, ok in zip(requested, usable) if ok)
    excluded_channels = tuple(channel for channel, ok in zip(requested, usable) if not ok)
    standardized = selected[usable] / standard_deviation[usable]
    quality.update({
        "status": "good" if not excluded_channels else "partial",
        "requested_channels": list(requested),
        "used_channels": list(used_channels),
        "excluded_requested_channels": list(excluded_channels),
    })
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
