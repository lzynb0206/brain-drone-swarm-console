"""Preprocessing shared by FBCCA and future offline evaluation tools."""

from __future__ import annotations

from typing import Iterable

import numpy as np


# Parameters from the inherited FBCCA implementation: three sub-bands starting
# at 6/14/22 Hz and w(n)=n^-1.25+0.25.  The upper edge of 80 Hz preserves the
# four harmonics used by its 8-13 Hz reference signals.
FILTER_BANK_LOW_EDGES_HZ = (6.0, 14.0, 22.0)
FILTER_BANK_WEIGHTS = tuple(index ** -1.25 + 0.25 for index in range(1, 4))


def remove_linear_drift(data: np.ndarray) -> np.ndarray:
    """Remove offsets and ramps before FFT filtering (not after spectral leakage)."""
    centered = data - np.mean(data, axis=1, keepdims=True)
    t = np.linspace(-1.0, 1.0, data.shape[1])
    return centered - ((centered @ t) / np.dot(t, t))[:, None] * t


def assess_signal_quality(samples, sample_rate_hz: float, *, deduplicate_channels=None) -> dict[str, object]:
    """Measure per-channel amplitude, variance and 50 Hz interference."""

    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[1] < 32:
        raise ValueError("EEG quality input must have shape (channels, samples)")
    finite = np.isfinite(data).all(axis=1)
    centered = data - np.nanmean(data, axis=1, keepdims=True)
    std_uv = np.nanstd(centered, axis=1)
    max_amplitude_uv = np.nanmax(np.abs(centered), axis=1)
    detrended = remove_linear_drift(np.nan_to_num(data))
    filtered = fft_bandpass(detrended, sample_rate_hz, 6.0, min(48.0, sample_rate_hz * 0.48))
    filtered_variance = np.nanvar(filtered, axis=1)

    frequencies = np.fft.rfftfreq(data.shape[1], d=1.0 / float(sample_rate_hz))
    power = np.abs(np.fft.rfft(np.nan_to_num(centered), axis=1)) ** 2
    total_band = (frequencies >= 1.0) & (frequencies <= min(80.0, sample_rate_hz * 0.48))
    mains_band = (frequencies >= 49.0) & (frequencies <= 51.0)
    total_power = np.sum(power[:, total_band], axis=1)
    mains_power = np.sum(power[:, mains_band], axis=1) if np.any(mains_band) else np.zeros(data.shape[0])
    mains_ratio = np.divide(mains_power, total_power, out=np.zeros_like(mains_power), where=total_power > 0)

    usable = finite & (std_uv >= 0.5) & (filtered_variance >= 0.04) & (max_amplitude_uv <= 2500.0) & (mains_ratio <= 0.5)
    # Remove redundant inputs, not whole trials: nearby electrodes can genuinely
    # share an SSVEP. Keeping one representative avoids whitening tiny residuals.
    duplicate_of: dict[int, int] = {}
    retained: list[int] = []
    candidates = (range(data.shape[0]) if deduplicate_channels is None
                  else (int(channel) - 1 for channel in deduplicate_channels))
    for index in candidates:
        if not usable[index]:
            continue
        for other in retained:
            correlation = float(np.corrcoef(filtered[index], filtered[other])[0, 1])
            if abs(correlation) >= 0.995:
                duplicate_of[int(index)] = int(other)
                usable[index] = False
                break
        else:
            retained.append(int(index))
    channels = []
    for index in range(data.shape[0]):
        issues = []
        if not finite[index]:
            issues.append("NaN/Inf")
        if std_uv[index] < 0.5:
            issues.append("平坦/方差过低")
        if filtered_variance[index] < 0.04:
            issues.append("有效频段信号过弱/仅漂移")
        if index in duplicate_of:
            issues.append(f"与通道{duplicate_of[index] + 1}近乎重复")
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
        "redundant_channels": {str(index + 1): other + 1 for index, other in duplicate_of.items()},
        "channels": channels,
    }


def prepare_ssvep_channels(samples, channels_1based: Iterable[int], sample_rate_hz: float = 250.0):
    """Validate/select channels and return standardized data plus quality metadata."""

    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] < 100:
        raise ValueError("EEG window must have shape (channels, samples) with at least 100 samples")
    requested = tuple(int(ch) for ch in channels_1based if 1 <= int(ch) <= data.shape[0])
    if not requested:
        requested = tuple(range(1, data.shape[0] + 1))
    requested = tuple(dict.fromkeys(requested))
    quality = assess_signal_quality(data, sample_rate_hz, deduplicate_channels=requested)
    selected = data[[channel - 1 for channel in requested], :]
    selected = remove_linear_drift(selected)
    standard_deviation = selected.std(axis=1, keepdims=True)
    global_mask = np.asarray(quality["usable_mask"], dtype=bool)
    usable = (standard_deviation[:, 0] > 1e-12) & global_mask[[channel - 1 for channel in requested]]
    if not np.any(usable):
        raise ValueError("没有可用 EEG 通道：请检查接触、工频、漂移及通道重复")
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
    high_hz = min(80.0, sample_rate_hz * 0.48)
    for low_hz, weight in zip(FILTER_BANK_LOW_EDGES_HZ, FILTER_BANK_WEIGHTS):
        if low_hz < high_hz:
            yield fft_bandpass(data, sample_rate_hz, low_hz, high_hz), weight
