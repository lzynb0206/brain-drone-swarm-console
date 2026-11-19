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
from src.bci.lzy_personal_decoder import LzyPersonalDecoder
from src.ml.lzy_personal_model import (
    CALIBRATION_ALPHA,
    LzyPersonalModel,
    MODEL_VERSION,
    extract_lzy_fbcca_features,
)


def synthetic_ssvep(frequency_hz: float, seed: int = 31) -> np.ndarray:
    rng = np.random.default_rng(seed)
    timeline = np.arange(1250, dtype=float) / 250.0
    return np.stack([
        12.0 * np.sin(2.0 * np.pi * frequency_hz * timeline + channel * 0.13)
        + 4.0 * np.sin(4.0 * np.pi * frequency_hz * timeline + channel * 0.07)
        + rng.normal(0.0, 1.2, timeline.size)
        for channel in range(8)
    ])


class LzyPersonalAlgorithmTest(unittest.TestCase):
    def _write_model(self, root: Path) -> tuple[Path, Path]:
        model_path = root / "lzy.npz"
        metadata_path = root / "lzy.json"
        np.savez_compressed(
            model_path,
            version=np.asarray(MODEL_VERSION),
            class_labels=np.asarray(COMMANDS),
            frequencies_hz=np.asarray(TARGET_FREQUENCIES_HZ),
            channel_names=np.asarray(SSVEP_CHANNEL_NAMES),
            stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
            sample_rate_hz=np.asarray(250.0),
            sample_count=np.asarray(1250),
            nontarget_center=np.zeros(6),
            nontarget_scale=np.ones(6),
            calibration_alpha=np.asarray(CALIBRATION_ALPHA),
            idle_background_center=np.zeros(6),
            idle_background_scale=np.ones(6),
        )
        metadata_path.write_text(json.dumps({
            "model_version": MODEL_VERSION,
            "production_approved": False,
            "evaluation": {"block_held_out_accuracy": 0.47},
        }), encoding="utf-8")
        return model_path, metadata_path

    def test_overlapping_windows_cover_the_complete_five_seconds(self) -> None:
        result = extract_lzy_fbcca_features(
            synthetic_ssvep(11.0), 250.0, sample_count=1250,
        )
        self.assertEqual((3, 6), result.segment_scores.shape)
        self.assertEqual((0.0, 0.5, 1.0), result.segment_starts_seconds)
        self.assertEqual(3, int(np.argmax(result.values)))

    def test_model_loads_and_decoder_remains_research_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_path, metadata_path = self._write_model(Path(directory))
            model = LzyPersonalModel(model_path, metadata_path)
            self.assertTrue(model.enabled, model.error)
            decoder = LzyPersonalDecoder(model)
            result = decoder.decode(synthetic_ssvep(12.0), 250.0)
            self.assertEqual("右移", result.label)
            self.assertEqual(1250, result.sample_count)
            self.assertTrue(result.accepted)
            self.assertFalse(decoder.hardware_control_allowed)
            self.assertIn("lzy_calibrated_evidence", result.signal_quality)

    def test_short_window_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "完整 5 秒"):
            extract_lzy_fbcca_features(
                synthetic_ssvep(8.0)[:, :1000], 250.0, sample_count=1250,
            )


if __name__ == "__main__":
    unittest.main()
