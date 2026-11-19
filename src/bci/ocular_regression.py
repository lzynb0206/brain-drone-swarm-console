"""Conservative Fp1/Fp2 regression for posterior SSVEP channels.

Coefficients are fitted on idle EEG only.  The regressor is restricted to the
0.5-6 Hz ocular band so it does not directly subtract the 8-13 Hz SSVEP
fundamentals used by the current stimulus profile.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal


@dataclass(frozen=True)
class OcularRegressionModel:
    coefficients: np.ndarray
    sample_rate_hz: float
    frontal_indices: tuple[int, int]
    target_indices: tuple[int, ...]
    low_hz: float
    high_hz: float


def _ocular_band(data: np.ndarray, sample_rate_hz: float, low_hz: float, high_hz: float):
    nyquist = float(sample_rate_hz) / 2.0
    if not 0.0 < low_hz < high_hz < nyquist:
        raise ValueError("invalid ocular regression band")
    sos = signal.butter(
        4,
        [float(low_hz) / nyquist, float(high_hz) / nyquist],
        btype="bandpass",
        output="sos",
    )
    return signal.sosfiltfilt(sos, data, axis=-1)


def _references(
    window: np.ndarray,
    sample_rate_hz: float,
    frontal_indices: tuple[int, int],
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    frontal = _ocular_band(
        window[np.asarray(frontal_indices)],
        sample_rate_hz,
        low_hz,
        high_hz,
    )
    vertical = 0.5 * (frontal[0] + frontal[1])
    horizontal = 0.5 * (frontal[0] - frontal[1])
    values = np.column_stack((vertical, horizontal))
    return values - values.mean(axis=0, keepdims=True)


def fit_ocular_regression(
    idle_windows,
    sample_rate_hz: float,
    *,
    frontal_indices: tuple[int, int] = (0, 7),
    target_indices: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    low_hz: float = 0.5,
    high_hz: float = 6.0,
    ridge_fraction: float = 0.01,
    target_valid_masks=None,
) -> OcularRegressionModel:
    """Fit frontal-to-posterior ocular coupling from idle windows only."""

    windows = np.asarray(idle_windows, dtype=np.float64)
    if windows.ndim != 3 or windows.shape[0] < 2 or windows.shape[2] < 100:
        raise ValueError("idle_windows must have shape (trials>=2, channels, samples>=100)")
    if not np.isfinite(windows).all():
        raise ValueError("idle_windows contains NaN or Inf")
    maximum_index = max((*frontal_indices, *target_indices))
    if maximum_index >= windows.shape[1]:
        raise ValueError("ocular regression channel index out of range")
    if target_valid_masks is None:
        valid_masks = np.ones((len(windows), len(target_indices)), dtype=bool)
    else:
        valid_masks = np.asarray(target_valid_masks, dtype=bool)
        if valid_masks.shape != (len(windows), len(target_indices)):
            raise ValueError(
                "target_valid_masks must have shape (trials, target_channels)"
            )
    reference_rows = []
    target_rows = []
    for window in windows:
        reference_rows.append(_references(
            window,
            sample_rate_hz,
            frontal_indices,
            low_hz,
            high_hz,
        ))
        target = _ocular_band(
            window[np.asarray(target_indices)],
            sample_rate_hz,
            low_hz,
            high_hz,
        ).T
        target_rows.append(target - target.mean(axis=0, keepdims=True))
    coefficients = np.zeros((2, len(target_indices)), dtype=np.float64)
    for target_position in range(len(target_indices)):
        valid_trials = np.flatnonzero(valid_masks[:, target_position])
        if not len(valid_trials):
            continue
        design = np.concatenate(
            [reference_rows[index] for index in valid_trials],
            axis=0,
        )
        target = np.concatenate(
            [target_rows[index][:, target_position] for index in valid_trials],
            axis=0,
        )
        gram = design.T @ design
        ridge = max(float(np.trace(gram)) / max(1, gram.shape[0]), 1e-12) * float(
            ridge_fraction
        )
        coefficients[:, target_position] = np.linalg.solve(
            gram + ridge * np.eye(gram.shape[0]),
            design.T @ target,
        )
    return OcularRegressionModel(
        coefficients=coefficients,
        sample_rate_hz=float(sample_rate_hz),
        frontal_indices=tuple(int(value) for value in frontal_indices),
        target_indices=tuple(int(value) for value in target_indices),
        low_hz=float(low_hz),
        high_hz=float(high_hz),
    )


def apply_ocular_regression(samples, model: OcularRegressionModel) -> np.ndarray:
    """Subtract the fitted low-frequency ocular projection from target EEG."""

    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] < 100:
        raise ValueError("samples must have shape (channels, samples>=100)")
    if not np.isfinite(data).all():
        raise ValueError("samples contains NaN or Inf")
    references = _references(
        data,
        model.sample_rate_hz,
        model.frontal_indices,
        model.low_hz,
        model.high_hz,
    )
    predicted = (references @ model.coefficients).T
    output = data.copy()
    output[np.asarray(model.target_indices)] -= predicted
    return output


__all__ = [
    "OcularRegressionModel",
    "apply_ocular_regression",
    "fit_ocular_regression",
]
