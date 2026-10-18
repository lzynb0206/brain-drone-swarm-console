from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from src.controllers.eeg_acquisition_controller import EEGAcquisitionController
from src.domain import EEGFrame
from src.services.eeg_display_processor import EEGDisplayProcessor
from src.views.research_acquisition_view import _resample_for_pixels


def _tone_amplitude(values: np.ndarray, sample_rate_hz: float, frequency_hz: float) -> float:
    times = np.arange(values.size, dtype=float) / sample_rate_hz
    sine = np.sin(2.0 * np.pi * frequency_hz * times)
    cosine = np.cos(2.0 * np.pi * frequency_hz * times)
    return float(
        2.0
        * np.hypot(np.dot(values, sine), np.dot(values, cosine))
        / values.size
    )


class EEGDisplayProcessorTest(unittest.TestCase):
    def test_openbci_style_filter_suppresses_drift_and_mains(self) -> None:
        sample_rate_hz = 250.0
        times = np.arange(2500, dtype=float) / sample_rate_hz
        raw = (
            200.0 * np.sin(2.0 * np.pi * 0.5 * times)
            + 30.0 * np.sin(2.0 * np.pi * 10.0 * times)
            + 100.0 * np.sin(2.0 * np.pi * 50.0 * times)
        )
        source = np.vstack([raw, raw * 0.5])
        original = source.copy()
        processor = EEGDisplayProcessor()
        filtered = np.concatenate(
            [
                processor.process(source[:, start : start + 8], sample_rate_hz)
                for start in range(0, source.shape[1], 8)
            ],
            axis=1,
        )

        stable = filtered[0, 500:]
        self.assertGreater(_tone_amplitude(stable, sample_rate_hz, 10.0), 20.0)
        self.assertLess(_tone_amplitude(stable, sample_rate_hz, 0.5), 2.0)
        self.assertLess(_tone_amplitude(stable, sample_rate_hz, 50.0), 2.0)
        np.testing.assert_array_equal(source, original)

    def test_controller_keeps_raw_and_display_histories_separate(self) -> None:
        host = SimpleNamespace(
            _eeg_history=[deque(maxlen=1250) for _ in range(2)],
            _eeg_display_history=[deque(maxlen=1250) for _ in range(2)],
            _eeg_sample_rate=0.0,
            _eeg_channel_names=(),
            _eeg_original_channel_names=(),
            _eeg_channel_units=(),
            _eeg_events=deque(maxlen=12),
        )
        recorder = MagicMock()
        recorder.is_recording = False
        outlet = MagicMock()
        outlet.active = False
        controller = EEGAcquisitionController(None, recorder, outlet, host)
        values = tuple(float(value) for value in np.linspace(-100.0, 100.0, 32))
        frame = EEGFrame(
            timestamp=1.0,
            sample_rate_hz=250.0,
            channel_names=("O1", "O2"),
            samples=(values, values),
            channel_units=("uV", "uV"),
        )

        controller.handle_frame(frame, publish_outlet=False)

        self.assertEqual(list(values), list(host._eeg_history[0]))
        self.assertEqual(len(values), len(host._eeg_display_history[0]))
        self.assertFalse(np.allclose(values, host._eeg_display_history[0]))

    def test_pixel_resampling_limits_path_vertices(self) -> None:
        data = np.linspace(-1.0, 1.0, 1250)
        reduced = _resample_for_pixels(data, 614.0)

        self.assertEqual(614, len(reduced))
        self.assertAlmostEqual(-1.0, float(reduced[0]))
        self.assertAlmostEqual(1.0, float(reduced[-1]))


if __name__ == "__main__":
    unittest.main()
