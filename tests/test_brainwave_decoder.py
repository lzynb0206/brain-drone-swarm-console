from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import torch
import numpy as np

from src.ml.brainwave_decoder import BrainWaveDecoder
from scripts.reproduce_brainwave_decoder import (
    _source_sha256,
    grouped_block_folds,
    stratified_trial_folds,
)
from scripts.evaluate_brainwave_decoder_subject24 import crop_starts, make_segments


class BrainWaveDecoderTest(unittest.TestCase):
    def test_paper_shape_profile_and_logits(self):
        model = BrainWaveDecoder(
            channels=8, samples=250, classes=12, gru_layout="time_features"
        )
        self.assertEqual((48, 48, 85, 192, 192), tuple(model.profile["convolution_widths"]))
        self.assertEqual(128, model.profile["bigru_output_features"])
        with torch.no_grad():
            output = model(torch.randn(3, 8, 250))
        self.assertEqual((3, 12), tuple(output.shape))
        self.assertTrue(torch.isfinite(output).all())

    def test_rejects_wrong_channel_or_sample_shape(self):
        model = BrainWaveDecoder(
            channels=6, samples=250, classes=6, gru_layout="time_features"
        )
        with self.assertRaises(ValueError):
            model(torch.randn(2, 8, 250))
        with self.assertRaises(ValueError):
            model(torch.randn(2, 6, 500))

    def test_all_normalization_ablation_options(self):
        for normalization in ("batch", "group", "none"):
            model = BrainWaveDecoder(
                channels=6,
                samples=50,
                classes=12,
                normalization=normalization,
                gru_layout="time_features",
            )
            with torch.no_grad():
                self.assertEqual((2, 12), tuple(model(torch.randn(2, 6, 50)).shape))

    def test_configurable_temporal_kernel_preserves_sample_count(self):
        model = BrainWaveDecoder(
            channels=8,
            samples=250,
            classes=12,
            temporal_kernel_size=15,
            gru_layout="time_features",
        )
        with torch.no_grad():
            self.assertEqual((2, 12), tuple(model(torch.randn(2, 8, 250)).shape))
        self.assertEqual(15, model.profile["temporal_kernel_size"])

    def test_feature_map_gru_layout(self):
        model = BrainWaveDecoder(
            channels=8, samples=250, classes=12, gru_layout="feature_maps"
        )
        with torch.no_grad():
            self.assertEqual((2, 12), tuple(model(torch.randn(2, 8, 250)).shape))
        self.assertEqual("feature_maps", model.profile["gru_layout"])

    def test_per_feature_map_gru_matches_public_flattened_shape(self):
        model = BrainWaveDecoder(
            channels=8, samples=20, classes=12, gru_layout="per_feature_map"
        )
        self.assertEqual(192 * 20 * 128, model.classifier.in_features)
        with torch.no_grad():
            self.assertEqual((2, 12), tuple(model(torch.randn(2, 8, 20)).shape))
        self.assertEqual("per_feature_map", model.profile["gru_layout"])

    def test_grouped_folds_cover_each_block_once(self):
        folds = grouped_block_folds(np.arange(1, 16), 5)
        self.assertEqual([3, 3, 3, 3, 3], [len(fold) for fold in folds])
        self.assertEqual(list(range(1, 16)), sorted(np.concatenate(folds).tolist()))

    def test_stratified_trial_folds_cover_rows_once_and_balance_classes(self):
        labels = np.repeat(np.arange(12), 15)
        folds = stratified_trial_folds(labels, 5, seed=7)
        self.assertEqual([36] * 5, [len(fold) for fold in folds])
        self.assertEqual(list(range(180)), sorted(np.concatenate(folds).tolist()))
        for fold in folds:
            self.assertEqual([3] * 12, np.bincount(labels[fold], minlength=12).tolist())

    def test_directory_source_hash_only_includes_loaded_subjects(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "s1.mat").write_bytes(b"subject-one")
            first = _source_sha256(root, subject_count=1)
            (root / "s2.mat").write_bytes(b"partial-download")
            self.assertEqual(first, _source_sha256(root, subject_count=1))
            self.assertNotEqual(first, _source_sha256(root, subject_count=2))

    def test_subject24_five_windows_fit_inside_each_trial(self):
        starts = crop_starts("five_window_fusion")
        self.assertEqual(5, len(starts))
        self.assertEqual(34, starts[0])
        self.assertEqual(1000, starts[-1])
        self.assertTrue(all(a < b for a, b in zip(starts, starts[1:])))
        self.assertTrue(all(0 <= start <= 1000 for start in starts))

    def test_subject24_centered_crops_are_trial_major_and_zero_mean(self):
        eeg = np.arange(2 * 8 * 1250, dtype=np.float32).reshape(2, 8, 1250)
        views = make_segments(eeg, crop_starts("five_window_fusion"))
        self.assertEqual((2, 5, 8, 250), views.shape)
        np.testing.assert_allclose(views.mean(axis=-1), 0.0, atol=1e-5)


if __name__ == "__main__":
    unittest.main()
