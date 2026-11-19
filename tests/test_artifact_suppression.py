from __future__ import annotations

import unittest

import numpy as np

from src.bci.artifact_suppression import (
    fit_idle_artifact_baseline,
    suppress_transient_artifacts,
)


class ArtifactSuppressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_rate = 250.0
        timeline = np.arange(1000) / self.sample_rate
        rng = np.random.default_rng(2)
        self.idle = [
            np.asarray([
                2.0 * np.sin(2.0 * np.pi * (7.0 + channel * 0.2) * timeline)
                + rng.normal(0.0, 0.3, timeline.size)
                for channel in range(8)
            ])
            for _ in range(8)
        ]
        self.baseline = fit_idle_artifact_baseline(self.idle, self.sample_rate)

    def test_clean_window_is_retained(self) -> None:
        cleaned, report = suppress_transient_artifacts(self.idle[0], self.baseline)

        self.assertTrue(report["accepted"])
        self.assertEqual(0.0, report["bad_sample_fraction"])
        np.testing.assert_allclose(cleaned, self.idle[0])

    def test_short_multichannel_burst_is_interpolated(self) -> None:
        contaminated = self.idle[1].copy()
        contaminated[:, 400:410] += 200.0

        cleaned, report = suppress_transient_artifacts(contaminated, self.baseline)

        self.assertTrue(report["accepted"])
        self.assertGreater(report["bad_sample_fraction"], 0.0)
        self.assertLess(np.max(np.abs(cleaned[:, 400:410])), 10.0)

    def test_long_multichannel_burst_is_rejected(self) -> None:
        contaminated = self.idle[2].copy()
        contaminated[:, 250:650] += 200.0

        _, report = suppress_transient_artifacts(contaminated, self.baseline)

        self.assertFalse(report["accepted"])


if __name__ == "__main__":
    unittest.main()
