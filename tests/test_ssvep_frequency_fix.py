"""Software regression checks; synthetic data is not human accuracy evidence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.preprocessing import (
    FILTER_BANK_HIGH_EDGE_HZ,
    filter_bank,
    prepare_ssvep_channels,
)
from src.ml.frequency_profile import record_matches_current_profile
from src.ml.transformer_predictor import TransformerSSVEPPredictor


class FrequencyFixTest(unittest.TestCase):
    def test_fbcca_filter_bank_stops_at_50hz(self):
        self.assertEqual(50.0, FILTER_BANK_HIGH_EDGE_HZ)
        t = np.arange(1000) / 250.0
        eeg = np.tile(
            np.sin(2 * np.pi * 48 * t) + np.sin(2 * np.pi * 52 * t),
            (2, 1),
        )
        for filtered, _weight in filter_bank(eeg, 250.0):
            spectrum = np.abs(np.fft.rfft(filtered, axis=1))
            frequencies = np.fft.rfftfreq(filtered.shape[1], 1 / 250.0)
            at_48hz = float(np.max(spectrum[:, np.isclose(frequencies, 48.0)]))
            at_52hz = float(np.max(spectrum[:, np.isclose(frequencies, 52.0)]))
            self.assertGreater(at_48hz, 100.0)
            self.assertLess(at_52hz, 1e-8)

        references = FBCCADecoder()._reference_signals(250.0, 1000)
        self.assertEqual(8, references[4].shape[0])  # 12/24/36/48 Hz
        self.assertEqual(6, references[5].shape[0])  # 13/26/39 Hz; 52 Hz excluded

    def test_new_targets_are_decoded_with_default_rejection_gate(self):
        self.assertEqual((8., 9., 10., 11., 12., 13.), TARGET_FREQUENCIES_HZ)
        rng = np.random.default_rng(20261017)
        t = np.arange(1000) / 250.0
        decoder = FBCCADecoder()
        for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ):
            with self.subTest(label=label):
                eeg = rng.normal(0, 5, (8, 1000))
                eeg += 5 * np.sin(2 * np.pi * frequency * t)
                eeg += 2 * np.sin(4 * np.pi * frequency * t)
                result = decoder.decode(eeg, 250.)
                self.assertEqual(label, result.label)
                self.assertTrue(result.accepted)

    def test_white_noise_and_out_of_profile_17hz_are_not_commands(self):
        rng = np.random.default_rng(20261017)
        decoder = FBCCADecoder()
        for _ in range(200):
            self.assertFalse(decoder.decode(rng.normal(0, 10, (8, 1000)), 250.).accepted)
        t = np.arange(1000) / 250.
        eeg = rng.normal(0, 3, (8, 1000)) + 10 * np.sin(2 * np.pi * 17 * t)
        self.assertFalse(decoder.decode(eeg, 250.).accepted)

    def test_drift_and_mains_are_rejected(self):
        t = np.arange(500) / 250.
        for signal in (100 * t, 20 * np.sin(2 * np.pi * 50 * t)):
            with self.assertRaises(ValueError):
                prepare_ssvep_channels(np.tile(signal, (8, 1)), range(1, 9))

    def test_duplicate_selection_keeps_a_requested_representative(self):
        t = np.arange(500) / 250.
        eeg = np.tile(10 * np.sin(2 * np.pi * 16 * t), (8, 1))
        _, used, _ = prepare_ssvep_channels(eeg, (3,))
        self.assertEqual((3,), used)
        _, used, quality = prepare_ssvep_channels(eeg, (3, 5))
        self.assertEqual((3,), used)
        self.assertEqual({"5": 3}, quality["redundant_channels"])

    def test_calibration_tracks_each_frequency_without_capping(self):
        decoder = FBCCADecoder()
        baseline = dict(zip(COMMANDS, (0.65, 0.15, 0.2, 0.18, 0.14, 0.16)))
        for _ in range(8):
            decoder.observe_idle(0.65, 0.45, baseline)
        self.assertAlmostEqual(0.68, decoder._class_thresholds[0])
        self.assertAlmostEqual(0.65, decoder._background[0])
        decoder.begin_idle_calibration()
        np.testing.assert_array_equal(decoder._background, np.zeros(6))

    def test_training_records_require_matching_profile_and_idle_condition(self):
        row = {"true_label": "起飞", "target_frequency_hz": "8"}
        self.assertFalse(record_matches_current_profile(row))
        metadata = {"target_frequencies_hz": list(TARGET_FREQUENCIES_HZ)}
        row["trial_metadata_json"] = json.dumps(metadata)
        self.assertTrue(record_matches_current_profile(row))
        row["target_frequency_hz"] = "16"
        self.assertFalse(record_matches_current_profile(row))
        row["true_label"] = "空闲"
        self.assertFalse(record_matches_current_profile(row))
        metadata["idle_targets_flicker"] = True
        row["trial_metadata_json"] = json.dumps(metadata)
        self.assertTrue(record_matches_current_profile(row))

    def test_legacy_weights_rejected_before_loading_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.pt").touch()
            (root / "metadata.json").write_text("{}", encoding="utf-8")
            predictor = TransformerSSVEPPredictor(root / "model.pt", root / "metadata.json")
            self.assertFalse(predictor.enabled)
            self.assertIn("旧频率", predictor.error)


if __name__ == "__main__":
    unittest.main()
