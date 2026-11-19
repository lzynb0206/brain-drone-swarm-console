"""Reference-free transient artifact suppression for short EEG windows.

This module is deliberately not described as EOG/ICA cleaning: without an EOG
or frontal reference, ocular sources cannot be identified reliably.  Instead it
uses a robust baseline to find brief, unusually large multichannel excursions
and electrode jumps, interpolates short contaminated intervals, and rejects
windows that are too contaminated to repair safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import signal


@dataclass(frozen=True)
class ArtifactBaseline:
    """Per-channel robust scales estimated only from idle EEG windows."""

    amplitude_center: np.ndarray
    amplitude_scale: np.ndarray
    derivative_center: np.ndarray
    derivative_scale: np.ndarray
    sample_rate_hz: float


def _validated_window(samples) -> np.ndarray:
    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 100:
        raise ValueError("EEG artifact input must have shape (channels, samples>=100)")
    if not np.isfinite(data).all():
        raise ValueError("EEG artifact input contains NaN or Inf")
    return data


def _robust_scale(values: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    center = np.median(values, axis=axis)
    expanded = np.expand_dims(center, axis=axis)
    scale = 1.4826 * np.median(np.abs(values - expanded), axis=axis)
    fallback = np.std(values, axis=axis)
    scale = np.where(scale > 1e-9, scale, np.maximum(fallback, 1e-9))
    return center, scale


def _detector_signal(data: np.ndarray, sample_rate_hz: float) -> np.ndarray:
    """Return detrended 0.5-30 Hz data used only for artifact detection."""

    detrended = signal.detrend(data, axis=1, type="linear")
    nyquist = float(sample_rate_hz) / 2.0
    high_hz = min(30.0, nyquist * 0.90)
    if high_hz <= 0.5:
        return detrended
    sos = signal.butter(4, (0.5, high_hz), btype="bandpass", fs=sample_rate_hz, output="sos")
    return signal.sosfiltfilt(sos, detrended, axis=1)


def fit_idle_artifact_baseline(
    idle_windows: Iterable[np.ndarray],
    sample_rate_hz: float,
) -> ArtifactBaseline:
    """Fit robust detector scales from idle windows without using class labels."""

    fs = float(sample_rate_hz)
    if fs <= 0:
        raise ValueError("EEG sample rate must be positive")
    prepared = [_detector_signal(_validated_window(window), fs) for window in idle_windows]
    if not prepared:
        raise ValueError("At least one idle EEG window is required for calibration")
    channel_count = prepared[0].shape[0]
    if any(window.shape[0] != channel_count for window in prepared):
        raise ValueError("Idle EEG windows must have the same channel count")
    calibration = np.concatenate(prepared, axis=1)
    derivatives = np.diff(calibration, axis=1)
    amp_center, amp_scale = _robust_scale(calibration, axis=1)
    diff_center, diff_scale = _robust_scale(derivatives, axis=1)
    return ArtifactBaseline(
        amplitude_center=amp_center,
        amplitude_scale=amp_scale,
        derivative_center=diff_center,
        derivative_scale=diff_scale,
        sample_rate_hz=fs,
    )


def _dilate(mask: np.ndarray, radius_samples: int) -> np.ndarray:
    if radius_samples <= 0 or not np.any(mask):
        return mask.copy()
    kernel = np.ones(2 * radius_samples + 1, dtype=int)
    return np.convolve(mask.astype(int), kernel, mode="same") > 0


def _longest_true_run(mask: np.ndarray) -> int:
    if not np.any(mask):
        return 0
    padded = np.pad(mask.astype(np.int8), (1, 1))
    edges = np.flatnonzero(np.diff(padded))
    return int(np.max(edges[1::2] - edges[::2]))


def suppress_transient_artifacts(
    samples,
    baseline: ArtifactBaseline,
    *,
    amplitude_z: float = 8.0,
    derivative_z: float = 10.0,
    dilation_seconds: float = 0.08,
    max_bad_fraction: float = 0.25,
    max_bad_run_seconds: float = 0.75,
) -> tuple[np.ndarray, dict[str, object]]:
    """Interpolate brief robust outliers and reject heavily contaminated windows."""

    data = _validated_window(samples)
    fs = float(baseline.sample_rate_hz)
    if data.shape[0] != baseline.amplitude_scale.size:
        raise ValueError("EEG channel count does not match artifact baseline")

    detected = _detector_signal(data, fs)
    amplitude_score = np.abs(
        (detected - baseline.amplitude_center[:, None])
        / baseline.amplitude_scale[:, None]
    )
    derivative = np.diff(detected, axis=1, prepend=detected[:, :1])
    derivative_score = np.abs(
        (derivative - baseline.derivative_center[:, None])
        / baseline.derivative_scale[:, None]
    )

    per_channel = (amplitude_score > float(amplitude_z)) | (
        derivative_score > float(derivative_z)
    )
    minimum_channels = max(2, int(np.ceil(data.shape[0] * 0.25)))
    multichannel = np.sum(per_channel, axis=0) >= minimum_channels
    radius = max(0, int(round(float(dilation_seconds) * fs)))
    multichannel = _dilate(multichannel, radius)
    channel_masks = np.vstack([
        _dilate(channel_mask, radius) | multichannel
        for channel_mask in per_channel
    ])
    any_bad = np.any(channel_masks, axis=0)
    bad_fraction = float(np.mean(any_bad))
    longest_bad_run = _longest_true_run(any_bad)
    accepted = (
        bad_fraction <= float(max_bad_fraction)
        and longest_bad_run <= int(round(float(max_bad_run_seconds) * fs))
    )

    cleaned = data.copy()
    timeline = np.arange(data.shape[1])
    if accepted:
        for channel_index, channel_mask in enumerate(channel_masks):
            if not np.any(channel_mask):
                continue
            good = ~channel_mask
            if np.count_nonzero(good) < 2:
                accepted = False
                break
            cleaned[channel_index, channel_mask] = np.interp(
                timeline[channel_mask], timeline[good], data[channel_index, good]
            )

    report = {
        "accepted": bool(accepted),
        "bad_sample_fraction": bad_fraction,
        "bad_sample_count": int(np.count_nonzero(any_bad)),
        "longest_bad_run_samples": int(longest_bad_run),
        "longest_bad_run_seconds": float(longest_bad_run / fs),
        "channels_with_artifact": [
            int(index + 1) for index, mask in enumerate(channel_masks) if np.any(mask)
        ],
        "amplitude_z": float(amplitude_z),
        "derivative_z": float(derivative_z),
        "dilation_seconds": float(dilation_seconds),
        "max_bad_fraction": float(max_bad_fraction),
        "max_bad_run_seconds": float(max_bad_run_seconds),
        "method": "idle-calibrated robust transient interpolation (no EOG)",
    }
    return cleaned, report


__all__ = [
    "ArtifactBaseline",
    "fit_idle_artifact_baseline",
    "suppress_transient_artifacts",
]
