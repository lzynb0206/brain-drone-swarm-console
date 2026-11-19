from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.bci.personal_score_decoder import PersonalScoreCalibrationDecoder
from src.ml.dual_fbcca_features import extract_dual_fbcca_features
from src.ml.personal_score_calibration import PersonalScoreCalibrationModel
from src.ml.personal_score_contract import FEATURE_NAMES, MODEL_VERSION


def synthetic_ssvep(frequency_hz: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    timeline = np.arange(1250, dtype=float) / 250.0
    rows = []
    for channel in range(8):
        phase = channel * 0.19
        rows.append(
            10.0 * np.sin(2.0 * np.pi * frequency_hz * timeline + phase)
            + 4.0 * np.sin(4.0 * np.pi * frequency_hz * timeline + phase / 2.0)
            + rng.normal(0.0, 1.5, timeline.size)
        )
    return np.asarray(rows)


class PersonalScoreCalibrationTest(unittest.TestCase):
    def test_dual_feature_branches_rank_the_synthetic_target(self):
        result = extract_dual_fbcca_features(
            synthetic_ssvep(11.0), 250.0, sample_count=1250,
        )
        self.assertEqual((12,), result.values.shape)
        self.assertEqual(3, int(np.argmax(result.standard50_scores)))
        self.assertEqual(3, int(np.argmax(result.equal46_scores)))

    def test_runtime_loads_contract_and_decodes_without_sklearn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / "model.npz"
            metadata_path = root / "metadata.json"
            coefficients = np.zeros((6, 12), dtype=float)
            for index in range(6):
                coefficients[index, index] = 8.0
                coefficients[index, index + 6] = 8.0
            np.savez_compressed(
                model_path,
                version=np.asarray(MODEL_VERSION),
                class_labels=np.asarray(COMMANDS),
                frequencies_hz=np.asarray(TARGET_FREQUENCIES_HZ),
                channel_names=np.asarray(SSVEP_CHANNEL_NAMES),
                feature_names=np.asarray(FEATURE_NAMES),
                sample_rate_hz=np.asarray(250.0),
                sample_count=np.asarray(1250),
                stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
                scaler_mean=np.zeros(12),
                scaler_scale=np.ones(12),
                coefficients=coefficients,
                intercept=np.zeros(6),
                confidence_threshold=np.asarray(0.0),
                margin_threshold=np.asarray(0.0),
            )
            metadata_path.write_text(json.dumps({
                "model_version": MODEL_VERSION,
                "production_approved": False,
            }), encoding="utf-8")
            model = PersonalScoreCalibrationModel(model_path, metadata_path)
            self.assertTrue(model.enabled, model.error)
            decoder = PersonalScoreCalibrationDecoder(model)
            result = decoder.decode(synthetic_ssvep(10.0), 250.0)
            self.assertEqual("后退", result.label)
            self.assertEqual(1250, result.sample_count)
            self.assertFalse(decoder.hardware_control_allowed)
            self.assertIn("standard50_fbcca_scores", result.signal_quality)

    def test_short_window_is_rejected_instead_of_resampled(self):
        with self.assertRaisesRegex(ValueError, "完整 5 秒"):
            extract_dual_fbcca_features(
                synthetic_ssvep(9.0)[:, :1000], 250.0, sample_count=1250,
            )


if __name__ == "__main__":
    unittest.main()
