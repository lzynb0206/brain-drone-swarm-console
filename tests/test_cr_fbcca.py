import unittest

import numpy as np

from src.bci.cr_fbcca import CR_WEIGHT, contrast_regularized_fbcca_scores


class ContrastRegularizedFBCCATest(unittest.TestCase):
    def test_zero_training_score_detects_synthetic_target(self) -> None:
        fs = 250.0
        count = 1000
        timeline = np.arange(count) / fs
        rng = np.random.default_rng(20261031)
        channels = []
        for channel in range(8):
            phase = channel * 0.23
            wave = (
                1.5 * np.sin(2 * np.pi * 11.0 * timeline + phase)
                + 0.9 * np.sin(2 * np.pi * 22.0 * timeline + phase / 2)
                + 0.5 * np.sin(2 * np.pi * 33.0 * timeline - phase)
            )
            channels.append(wave + 0.2 * rng.normal(size=count))
        values = np.asarray(channels)
        values -= values.mean(axis=1, keepdims=True)
        values /= values.std(axis=1, keepdims=True)

        result = contrast_regularized_fbcca_scores(values, fs)

        self.assertEqual(3, result.prediction_index)
        self.assertEqual(3, result.equal_harmonics)
        self.assertEqual(CR_WEIGHT, result.correction_weight)
        self.assertEqual((-0.8, -0.6, -0.4, 0.4, 0.6, 0.8), result.local_offsets_hz)

    def test_zero_correction_is_exactly_base_evidence_direction(self) -> None:
        fs = 250.0
        timeline = np.arange(1000) / fs
        values = np.stack([
            np.sin(2 * np.pi * 9.0 * timeline + channel * 0.1)
            for channel in range(4)
        ])

        result = contrast_regularized_fbcca_scores(
            values,
            fs,
            correction_weight=0.0,
        )
        centered = result.fbcca - result.fbcca.mean()
        expected = centered / np.linalg.norm(centered)

        np.testing.assert_allclose(result.fused, expected, atol=1e-12)
        self.assertEqual(int(np.argmax(result.fbcca)), result.prediction_index)

    def test_candidate_order_does_not_need_to_be_sorted(self) -> None:
        fs = 250.0
        timeline = np.arange(500) / fs
        values = np.stack([
            np.sin(2 * np.pi * 11.25 * timeline + channel * 0.2)
            for channel in range(4)
        ])
        frequencies = (9.25, 11.25, 10.25)

        result = contrast_regularized_fbcca_scores(
            values,
            fs,
            frequencies_hz=frequencies,
        )

        self.assertEqual(1, result.prediction_index)
        self.assertEqual(4, result.equal_harmonics)


if __name__ == "__main__":
    unittest.main()
