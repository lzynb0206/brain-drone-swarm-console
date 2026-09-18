from __future__ import annotations

import unittest

import numpy as np

from src.bci.preprocessing import assess_signal_quality
from src.bci.stimulus import measure_frequency


class StimulusMeasurementTest(unittest.TestCase):
    def test_measures_rendered_frequency_from_frame_timestamps(self) -> None:
        refresh_hz = 120.0
        times = np.arange(0.0, 4.0, 1.0 / refresh_hz)
        values = 0.5 + 0.5 * np.sin(2.0 * np.pi * 13.75 * times)
        result, _, _ = measure_frequency(times, values, 13.75)
        self.assertAlmostEqual(13.75, result.measured_hz, delta=0.3)
        self.assertAlmostEqual(refresh_hz, result.refresh_rate_hz, delta=0.5)


class SignalQualityTest(unittest.TestCase):
    def test_flags_flat_and_50hz_dominated_channels(self) -> None:
        fs = 250.0
        timeline = np.arange(1000) / fs
        data = np.vstack((
            np.zeros_like(timeline),
            20.0 * np.sin(2.0 * np.pi * 50.0 * timeline),
            10.0 * np.sin(2.0 * np.pi * 13.75 * timeline),
        ))
        quality = assess_signal_quality(data, fs)
        self.assertIn("平坦/方差过低", quality["channels"][0]["issues"])
        self.assertIn("50Hz工频占比过高", quality["channels"][1]["issues"])
        self.assertTrue(quality["channels"][2]["usable"])

if __name__ == "__main__":
    unittest.main()
