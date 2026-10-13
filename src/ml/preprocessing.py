"""Shared SciPy preprocessing for learned EEG decoders."""

from __future__ import annotations

from fractions import Fraction

import numpy as np


DEFAULT_FILTER_BANKS = ((6.0, 45.0), (10.0, 45.0), (14.0, 45.0), (18.0, 45.0))


def _scipy_signal():
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("FB-EEGNet 预处理需要 scipy") from exc
    return signal


def preprocess_trial(
    eeg, fs: float, target_fs: float = 250.0, target_samples: int = 1000,
    expected_channels: int = 8, filter_banks=DEFAULT_FILTER_BANKS,
) -> dict[str, object]:
    signal = _scipy_signal()
    data = np.asarray(eeg, dtype=np.float32)
    if data.ndim != 2 or data.shape[1] < 100 or not np.isfinite(data).all():
        raise ValueError("EEG 必须是有限值的 (channels, samples) 数组")
    if data.shape[0] != int(expected_channels):
        raise ValueError(f"需要 {expected_channels} 通道，实际为 {data.shape[0]}")
    if fs <= 0:
        raise ValueError("采样率必须大于 0")
    if abs(float(fs) - target_fs) > 1e-6:
        ratio = Fraction(target_fs / float(fs)).limit_denominator(1000)
        data = signal.resample_poly(data, ratio.numerator, ratio.denominator, axis=-1)
    if data.shape[1] != int(target_samples):
        data = signal.resample(data, int(target_samples), axis=-1)

    centered = data - np.mean(data, axis=-1, keepdims=True)
    std = np.std(centered, axis=-1)
    amplitude = np.max(np.abs(centered), axis=-1)
    quality_mask = (std >= 0.5) & (amplitude <= 2500.0)
    if not np.any(quality_mask):
        raise ValueError("没有通道通过信号质量检查")
    centered[~quality_mask] = 0.0

    def bandpass(values, low, high):
        nyquist = target_fs / 2.0
        sos = signal.butter(4, [low / nyquist, high / nyquist], btype="bandpass", output="sos")
        return signal.sosfiltfilt(sos, values, axis=-1).astype(np.float32)

    clean = bandpass(centered, 5.0, 45.0)
    if 50.0 < target_fs / 2.0:
        b, a = signal.iirnotch(50.0, 30.0, target_fs)
        clean = signal.filtfilt(b, a, clean, axis=-1).astype(np.float32)

    def normalize(values):
        mean = values.mean(axis=-1, keepdims=True)
        scale = values.std(axis=-1, keepdims=True)
        return ((values - mean) / np.where(scale < 1e-6, 1.0, scale)).astype(np.float32)

    banks = [normalize(bandpass(centered, float(low), float(high))) for low, high in filter_banks]
    return {
        "clean": normalize(clean),
        "filter_bank": np.stack(banks).astype(np.float32),
        "quality_mask": quality_mask,
        "target_fs": float(target_fs),
        "target_samples": int(target_samples),
    }


def preprocess_transformer_trial(
    eeg,
    fs: float,
    target_fs: float = 250.0,
    target_samples: int = 500,
    expected_channels: int = 8,
) -> dict[str, object]:
    """Prepare one two-second, eight-channel trial for Transformer inference.

    Sampling-rate conversion is allowed, but a longer recording is cropped
    instead of compressed in time.  This preserves the SSVEP frequencies.
    """

    signal = _scipy_signal()
    data = np.asarray(eeg, dtype=np.float32)
    if data.ndim != 2 or data.shape[1] < 100 or not np.isfinite(data).all():
        raise ValueError("EEG 必须是有限值的 (channels, samples) 数组")
    if data.shape[0] != int(expected_channels):
        raise ValueError(f"需要 {expected_channels} 通道，实际为 {data.shape[0]}")
    if fs <= 0 or target_fs <= 0:
        raise ValueError("采样率必须大于 0")
    if abs(float(fs) - target_fs) > 1e-6:
        ratio = Fraction(target_fs / float(fs)).limit_denominator(1000)
        data = signal.resample_poly(data, ratio.numerator, ratio.denominator, axis=-1)
    if data.shape[1] < int(target_samples):
        raise ValueError(
            f"EEG窗口过短：需要 {target_samples} 点，实际 {data.shape[1]} 点"
        )
    if data.shape[1] > int(target_samples):
        # Use the steady-state tail and discard the visual onset transient.
        data = data[:, -int(target_samples):]

    centered = data - np.mean(data, axis=-1, keepdims=True)
    std = np.std(centered, axis=-1)
    amplitude = np.max(np.abs(centered), axis=-1)
    quality_mask = (std >= 0.5) & (amplitude <= 2500.0)
    if not np.any(quality_mask):
        raise ValueError("没有通道通过信号质量检查")
    centered[~quality_mask] = 0.0

    nyquist = target_fs / 2.0
    high_hz = min(45.0, nyquist * 0.96)
    sos = signal.butter(
        4, [5.0 / nyquist, high_hz / nyquist], btype="bandpass", output="sos"
    )
    clean = signal.sosfiltfilt(sos, centered, axis=-1).astype(np.float32)
    if 50.0 < nyquist:
        b, a = signal.iirnotch(50.0, 30.0, target_fs)
        clean = signal.filtfilt(b, a, clean, axis=-1).astype(np.float32)
    mean = clean.mean(axis=-1, keepdims=True)
    scale = clean.std(axis=-1, keepdims=True)
    clean = ((clean - mean) / np.where(scale < 1e-6, 1.0, scale)).astype(np.float32)
    return {
        "clean": clean,
        "quality_mask": quality_mask,
        "target_fs": float(target_fs),
        "target_samples": int(target_samples),
    }
