"""Explicit filter variants for controlled, window-local SSVEP comparisons."""
from __future__ import annotations

import numpy as np
from scipy import signal

from src.bci.preprocessing import (
    FILTER_BANK_LOW_EDGES_HZ, FILTER_BANK_HIGH_EDGE_HZ,
    FILTER_BANK_WEIGHTS, fft_bandpass,
)

FILTER_METHODS = ("fft", "fft_reflect", "butter4", "cheby1_4")


def filtered_banks(data, fs, method="fft"):
    """Never read beyond the supplied window; reflection is not future EEG."""
    if method not in FILTER_METHODS:
        raise ValueError(f"Unknown filter method: {method}")
    values = np.asarray(data, dtype=float)
    if values.ndim != 2 or values.shape[1] < 32 or not np.isfinite(values).all():
        raise ValueError("Expected finite channels × samples")
    if not np.isfinite(fs) or fs <= 2 * FILTER_BANK_HIGH_EDGE_HZ:
        raise ValueError("Sampling rate must exceed 100 Hz")
    pad = min(values.shape[1] - 1, int(round(fs * .5)))
    for low, weight in zip(FILTER_BANK_LOW_EDGES_HZ, FILTER_BANK_WEIGHTS):
        if method == "fft":
            filtered = fft_bandpass(values, fs, low, FILTER_BANK_HIGH_EDGE_HZ)
        elif method == "fft_reflect":
            extended = np.pad(values, ((0, 0), (pad, pad)), mode="reflect")
            filtered = fft_bandpass(extended, fs, low, FILTER_BANK_HIGH_EDGE_HZ)[:, pad:-pad]
        else:
            kwargs = dict(N=4, Wn=(low, FILTER_BANK_HIGH_EDGE_HZ), btype="bandpass", fs=fs, output="sos")
            sos = (signal.butter(**kwargs) if method == "butter4"
                   else signal.cheby1(rp=.5, **kwargs))
            filtered = signal.sosfiltfilt(sos, values, axis=1, padtype="odd", padlen=pad)
        yield filtered, weight
