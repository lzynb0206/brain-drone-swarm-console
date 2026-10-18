"""Phase-insensitive feature extraction for personal SSVEP templates."""

from __future__ import annotations

import numpy as np

from src.bci.config import SSVEP_CHANNEL_NAMES
from src.bci.preprocessing import fft_bandpass, remove_linear_drift


def _unit(values: np.ndarray) -> np.ndarray:
    flat = np.asarray(values, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(flat))
    if not np.isfinite(norm) or norm <= 1e-12:
        return np.zeros_like(flat)
    return flat / norm


def _resample(data: np.ndarray, source_rate: float, target_rate: float, count: int) -> np.ndarray:
    if data.shape[1] == count and abs(source_rate - target_rate) < 1e-6:
        return data
    source_time = np.arange(data.shape[1], dtype=np.float64) / source_rate
    target_time = np.arange(count, dtype=np.float64) / target_rate
    return np.vstack([
        np.interp(target_time, source_time, channel, left=channel[0], right=channel[-1])
        for channel in data
    ])


def extract_template_feature(
    samples,
    sample_rate_hz: float,
    *,
    channel_count: int = len(SSVEP_CHANNEL_NAMES),
    target_rate_hz: float = 250.0,
    sample_count: int = 500,
) -> np.ndarray:
    """Return one unit-length feature that is insensitive to window phase."""

    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < channel_count or data.shape[1] < 100:
        raise ValueError(f"个人模板输入必须为至少{channel_count}通道、100采样点的 EEG 窗口")
    fs = float(sample_rate_hz)
    if fs <= 0 or not np.isfinite(data[:channel_count]).all():
        raise ValueError("个人模板输入包含无效采样率或 NaN/Inf")
    required_source_samples = max(100, round(sample_count * fs / target_rate_hz))
    if data.shape[1] < required_source_samples:
        raise ValueError(
            f"个人模板需要约 {sample_count / target_rate_hz:g} 秒 EEG，"
            f"当前只有 {data.shape[1] / fs:.2f} 秒"
        )
    segment = data[:channel_count, -required_source_samples:]
    segment = _resample(segment, fs, target_rate_hz, sample_count)
    segment = remove_linear_drift(segment)
    segment = fft_bandpass(segment, target_rate_hz, 6.0, 45.0)
    scale = np.std(segment, axis=1, keepdims=True)
    segment = segment / np.where(scale > 1e-9, scale, 1.0)

    # Log spectrum: target response and the operator/electrode-specific spatial
    # distribution, without requiring equal stimulus phase at inference time.
    tapered = segment * np.hanning(sample_count)[None, :]
    spectrum = np.abs(np.fft.rfft(tapered, axis=1)) ** 2
    bins = np.fft.rfftfreq(sample_count, 1.0 / target_rate_hz)
    band = (bins >= 6.0) & (bins <= 45.0)
    log_spectrum = np.log1p(spectrum[:, band])
    log_spectrum -= np.mean(log_spectrum, axis=1, keepdims=True)
    spectral_feature = _unit(log_spectrum)

    # Autocorrelation retains each trial's repeating waveform shape while also
    # removing its absolute phase.  Lags cover periods from roughly 4–45 Hz.
    autocorrelation = np.fft.irfft(
        np.abs(np.fft.rfft(segment, axis=1)) ** 2,
        n=sample_count,
        axis=1,
    )
    autocorrelation /= np.maximum(np.abs(autocorrelation[:, :1]), 1e-12)
    waveform_feature = _unit(autocorrelation[:, 1:64])

    # Concatenating square-root weights makes cosine similarity equal to the
    # intended weighted combination after the final normalization.
    return _unit(np.concatenate((np.sqrt(0.75) * spectral_feature,
                                 np.sqrt(0.25) * waveform_feature)))


__all__ = ["extract_template_feature"]

