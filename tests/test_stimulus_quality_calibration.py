from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.bci.preprocessing import assess_signal_quality
from src.bci.stimulus import measure_frequency
from src.domain import DeviceStatus, Position, UAVTelemetry
from src.services import HeightCalibrationService


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


class HeightCalibrationTest(unittest.TestCase):
    def test_saves_reviewable_offsets_without_applying_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = HeightCalibrationService(directory, minimum_samples=3)
            for height in (0.48, 0.49, 0.49, 0.50):
                service.add_telemetry((
                    UAVTelemetry(
                        "CF-01", 90, 0, height, "悬停", DeviceStatus.NORMAL,
                        Position(0, 0, height), 4.0, True, 1.0,
                    ),
                ))
            results, path = service.finish()
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertTrue(results["CF-01"].accepted)
        self.assertAlmostEqual(0.01, results["CF-01"].suggested_offset_m, delta=0.01)
        self.assertFalse(payload["automatic_application"])


if __name__ == "__main__":
    unittest.main()
