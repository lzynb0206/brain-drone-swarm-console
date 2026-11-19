import unittest

import numpy as np

from src.bci.scn_fbcca import self_contrast_normalized_fbcca_scores


class SelfContrastNormalizedFBCCATest(unittest.TestCase):
    def test_detects_synthetic_three_harmonic_target_without_training(self) -> None:
        fs = 250.0
        count = 1250
        timeline = np.arange(count) / fs
        rng = np.random.default_rng(20261031)
        values = []
        for channel in range(8):
            phase = channel * 0.19
            wave = sum(
                amplitude * np.sin(2 * np.pi * harmonic * 11.0 * timeline + phase / harmonic)
                for harmonic, amplitude in ((1, 1.3), (2, 0.8), (3, 0.45))
            )
            values.append(wave + 0.25 * rng.normal(size=count))
        data = np.asarray(values)
        data -= data.mean(axis=1, keepdims=True)
        data /= data.std(axis=1, keepdims=True)

        result = self_contrast_normalized_fbcca_scores(data, fs)

        self.assertEqual(3, result.prediction_index)
        self.assertEqual(3, result.harmonics)
        self.assertEqual(0.2, result.frequency_resolution_hz)
        np.testing.assert_allclose(
            (-0.8, -0.6, -0.4, 0.4, 0.6, 0.8),
            result.flank_offsets_hz,
        )
        self.assertEqual((6, 6), result.flank_scores.shape)

    def test_four_second_offsets_follow_dft_resolution(self) -> None:
        fs = 250.0
        timeline = np.arange(1000) / fs
        data = np.stack([
            np.sin(2 * np.pi * 9.0 * timeline + channel * 0.2)
            for channel in range(4)
        ])

        result = self_contrast_normalized_fbcca_scores(data, fs)

        self.assertEqual(0.25, result.frequency_resolution_hz)
        self.assertEqual((-1.0, -0.75, -0.5, 0.5, 0.75, 1.0), result.flank_offsets_hz)
        self.assertEqual(1, result.prediction_index)

    def test_channel_scale_does_not_change_scores(self) -> None:
        fs = 250.0
        timeline = np.arange(500) / fs
        data = np.stack([
            np.sin(2 * np.pi * 12.0 * timeline + channel * 0.31)
            + 0.2 * np.sin(2 * np.pi * 24.0 * timeline)
            for channel in range(4)
        ])
        scaled = data * np.asarray([0.5, 2.0, 3.0, 4.0])[:, None]
        first_data = data / data.std(axis=1, keepdims=True)
        second_data = scaled / scaled.std(axis=1, keepdims=True)
        first = self_contrast_normalized_fbcca_scores(first_data, fs)
        second = self_contrast_normalized_fbcca_scores(second_data, fs)

        np.testing.assert_allclose(first.normalized, second.normalized, rtol=1e-8, atol=1e-8)
        self.assertEqual(first.prediction_index, second.prediction_index)

    def test_rejects_nonfinite_input(self) -> None:
        data = np.ones((2, 100))
        data[0, 2] = np.nan
        with self.assertRaisesRegex(ValueError, "NaN/Inf"):
            self_contrast_normalized_fbcca_scores(data, 250.0)


if __name__ == "__main__":
    unittest.main()
