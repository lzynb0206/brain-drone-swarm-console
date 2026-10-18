"""Display-only conditioning for the live EEG monitor.

The acquisition, recording, LSL and decoder paths must keep the original
samples.  This processor mirrors the OpenBCI GUI's default visual treatment on
a separate stream so the operator can inspect a stable, readable trace without
silently changing research data.
"""

from __future__ import annotations

import numpy as np
from scipy import signal


class EEGDisplayProcessor:
    """Apply the OpenBCI-style live display filter with persistent IIR state."""

    def __init__(
        self,
        *,
        low_hz: float = 5.0,
        high_hz: float = 50.0,
        order: int = 4,
        mains_hz: tuple[float, ...] = (50.0, 60.0),
        notch_quality: float = 30.0,
    ) -> None:
        self.low_hz = float(low_hz)
        self.high_hz = float(high_hz)
        self.order = int(order)
        self.mains_hz = tuple(float(value) for value in mains_hz)
        self.notch_quality = float(notch_quality)
        self._sample_rate_hz = 0.0
        self._channel_count = 0
        self._sos = np.empty((0, 6), dtype=np.float64)
        self._state: np.ndarray | None = None

    def reset(self) -> None:
        self._sample_rate_hz = 0.0
        self._channel_count = 0
        self._sos = np.empty((0, 6), dtype=np.float64)
        self._state = None

    def process(self, samples, sample_rate_hz: float) -> np.ndarray:
        """Return filtered ``channels x samples`` data without mutating input."""

        data = np.asarray(samples, dtype=np.float64)
        if data.ndim != 2:
            raise ValueError("EEG display input must have shape (channels, samples)")
        if data.shape[1] == 0:
            return data.copy()
        if not np.isfinite(data).all():
            data = np.nan_to_num(data, copy=True)
        sample_rate_hz = float(sample_rate_hz)
        if sample_rate_hz <= 0:
            raise ValueError("EEG display sample rate must be positive")
        if (
            self._state is None
            or self._channel_count != data.shape[0]
            or abs(self._sample_rate_hz - sample_rate_hz) > 1e-6
        ):
            self._configure(data, sample_rate_hz)

        filtered = np.empty_like(data)
        assert self._state is not None
        for channel in range(data.shape[0]):
            filtered[channel], self._state[channel] = signal.sosfilt(
                self._sos,
                data[channel],
                zi=self._state[channel],
            )
        return filtered

    def _configure(self, data: np.ndarray, sample_rate_hz: float) -> None:
        nyquist_hz = sample_rate_hz / 2.0
        high_hz = min(self.high_hz, nyquist_hz * 0.95)
        if not 0.0 < self.low_hz < high_hz:
            raise ValueError(
                f"EEG display band {self.low_hz:g}-{high_hz:g} Hz is invalid "
                f"for {sample_rate_hz:g} Hz sampling"
            )
        sections = [
            signal.butter(
                self.order,
                (self.low_hz, high_hz),
                btype="bandpass",
                fs=sample_rate_hz,
                output="sos",
            )
        ]
        for frequency_hz in self.mains_hz:
            if 0.0 < frequency_hz < nyquist_hz * 0.98:
                numerator, denominator = signal.iirnotch(
                    frequency_hz,
                    self.notch_quality,
                    fs=sample_rate_hz,
                )
                sections.append(signal.tf2sos(numerator, denominator))
        self._sos = np.vstack(sections).astype(np.float64, copy=False)
        initial_state = signal.sosfilt_zi(self._sos)
        self._state = np.stack(
            [initial_state * float(data[channel, 0]) for channel in range(data.shape[0])]
        )
        self._sample_rate_hz = sample_rate_hz
        self._channel_count = data.shape[0]


__all__ = ["EEGDisplayProcessor"]
