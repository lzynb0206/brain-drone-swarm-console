from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from src.bci.config import COMMANDS, SSVEP_STIMULUS_LAYOUT, TARGET_FREQUENCIES_HZ
from src.bci.personal_template_decoder import PersonalTemplateSSVEPDecoder
from src.ml.personal_template_model import (
    GUIDED_CALIBRATION_CONDITION,
    ISOLATED_CALIBRATION_CONDITION,
    PersonalTemplateModel,
    extract_template_feature,
    train_personal_template_model,
)
from src.services.single_target_calibration_protocol import (
    SingleTargetCalibrationConfig,
    build_single_target_plan,
)
from src.services.single_target_template_analysis import evaluate_single_target_run


class SingleTargetPlanTest(unittest.TestCase):
    def test_default_plan_collects_ten_trials_for_each_command(self) -> None:
        plan = build_single_target_plan(SingleTargetCalibrationConfig())

        self.assertEqual(60, len(plan))
        self.assertEqual(
            {label: 10 for label in COMMANDS},
            dict(Counter(trial.label for trial in plan)),
        )

    def test_plan_is_balanced_seeded_and_has_no_adjacent_duplicates(self) -> None:
        config = SingleTargetCalibrationConfig(repetitions_per_command=3)
        first = build_single_target_plan(config)
        second = build_single_target_plan(config)

        self.assertEqual(first, second)
        self.assertEqual(18, len(first))
        self.assertEqual(
            {label: 3 for label in COMMANDS},
            dict(Counter(trial.label for trial in first)),
        )
        self.assertTrue(all(a.label != b.label for a, b in zip(first, first[1:])))


class SingleTargetTemplateAnalysisTest(unittest.TestCase):
    def test_synthetic_single_frequency_trials_are_separated_by_personal_templates(self) -> None:
        rng = np.random.default_rng(18)
        fs = 250.0
        timeline = np.arange(1250) / fs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial_index = 0
            for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ):
                for repetition in range(3):
                    trial_index += 1
                    signal = 8.0 * np.sin(2 * np.pi * frequency * timeline)
                    signal += 3.0 * np.sin(4 * np.pi * frequency * timeline)
                    eeg = np.vstack([
                        (1.0 + channel * 0.05) * signal
                        + rng.normal(0.0, 1.2, timeline.size)
                        for channel in range(8)
                    ]).astype(np.float32)
                    np.savez_compressed(
                        root / f"trial-{trial_index:03d}-{label}.npz",
                        eeg=eeg,
                        fs=np.asarray(fs, dtype=np.float32),
                        true_label=np.asarray(label),
                        discard_seconds=np.asarray(1.0, dtype=np.float32),
                    )

            report = evaluate_single_target_run(root)

            self.assertEqual(18, report["trial_count"])
            self.assertGreaterEqual(report["accuracy"], 0.90)
            self.assertTrue((root / "analysis.json").is_file())

    def test_personal_feature_is_invariant_to_online_window_phase(self) -> None:
        fs = 250.0
        timeline = np.arange(500) / fs
        signal = np.vstack([
            np.sin(2 * np.pi * 18.0 * timeline + channel * 0.1)
            + 0.25 * np.sin(2 * np.pi * 36.0 * timeline)
            for channel in range(8)
        ])

        first = extract_template_feature(signal, fs)
        shifted = extract_template_feature(np.roll(signal, 37, axis=1), fs)

        self.assertGreater(float(np.dot(first, shifted)), 0.99)

    def test_builds_runtime_model_from_all_trials_without_claiming_accuracy(self) -> None:
        rng = np.random.default_rng(24)
        fs = 250.0
        timeline = np.arange(1250) / fs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            trial_index = 0
            for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ):
                for _ in range(3):
                    trial_index += 1
                    wave = np.sin(2 * np.pi * frequency * timeline)
                    eeg = np.vstack([
                        wave + rng.normal(0.0, 0.08, timeline.size)
                        for _channel in range(8)
                    ]).astype(np.float32)
                    np.savez_compressed(
                        run / f"trial-{trial_index:03d}-{label}.npz",
                        eeg=eeg,
                        fs=np.asarray(fs, dtype=np.float32),
                        true_label=np.asarray(label),
                        discard_seconds=np.asarray(1.0, dtype=np.float32),
                        stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
                        calibration_condition=np.asarray(ISOLATED_CALIBRATION_CONDITION),
                        visible_target_count=np.asarray(1, dtype=np.int32),
                        signal_quality=np.asarray(
                            '{"usable_mask": [true, true, true, true, true, true, true, true]}'
                        ),
                    )
            model_path = root / "personal.npz"
            metadata_path = root / "personal.json"

            metadata = train_personal_template_model(run, model_path, metadata_path)
            model = PersonalTemplateModel(model_path, metadata_path)
            decoder = PersonalTemplateSSVEPDecoder(model)
            result = decoder.decode(eeg[:, 250:1250], fs)

            self.assertTrue(model.enabled)
            self.assertEqual(18, metadata["trial_count"])
            self.assertEqual({label: 3 for label in COMMANDS}, metadata["trials_per_class"])
            self.assertIsNone(metadata["accuracy"])
            self.assertGreaterEqual(metadata["leave_one_trial_consistency"], 0.50)
            self.assertTrue(result.accepted)
            self.assertGreaterEqual(len(result.used_channels_1based), 1)
            self.assertTrue(set(result.used_channels_1based).issubset(set(range(1, 9))))
            self.assertFalse(result.safety_verified)
            self.assertFalse(metadata["production_approved"])
            self.assertFalse(decoder.hardware_control_allowed)

            experimental_model_path = root / "personal-experimental.npz"
            experimental_metadata_path = root / "personal-experimental.json"
            experimental_metadata = train_personal_template_model(
                run,
                experimental_model_path,
                experimental_metadata_path,
                experimental_override=True,
                experimental_channels_1based=(2, 4, 6, 7),
            )
            experimental_model = PersonalTemplateModel(
                experimental_model_path, experimental_metadata_path,
            )
            experimental_result = PersonalTemplateSSVEPDecoder(
                experimental_model,
            ).decode(eeg[:, 250:1250], fs)

            self.assertTrue(experimental_model.enabled)
            self.assertTrue(experimental_model.experimental_override)
            self.assertEqual("kernel_ridge_trial_retrieval", experimental_model.prediction_mode)
            self.assertEqual(["Oz", "PO3", "PO4", "P3"], experimental_metadata["channel_names"])
            self.assertAlmostEqual(0.21, experimental_model.confidence_threshold, places=6)
            self.assertAlmostEqual(0.015, experimental_model.margin_threshold, places=6)
            self.assertTrue(experimental_result.accepted)
            self.assertTrue(experimental_result.signal_quality["experimental_override"])
            self.assertFalse(experimental_metadata["production_approved"])

    def test_guided_multi_target_run_is_not_accepted_as_isolated_template(self) -> None:
        fs = 250.0
        timeline = np.arange(1250) / fs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for trial_index, (label, frequency) in enumerate(
                ((label, frequency) for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ)
                 for _ in range(3)),
                start=1,
            ):
                eeg = np.tile(np.sin(2 * np.pi * frequency * timeline), (8, 1))
                np.savez_compressed(
                    root / f"trial-{trial_index:03d}-{label}.npz",
                    eeg=eeg,
                    fs=np.asarray(fs),
                    true_label=np.asarray(label),
                    discard_seconds=np.asarray(1.0),
                    stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
                    calibration_condition=np.asarray(GUIDED_CALIBRATION_CONDITION),
                    visible_target_count=np.asarray(len(COMMANDS)),
                )

            with self.assertRaisesRegex(ValueError, "单目标独闪"):
                train_personal_template_model(
                    root, root / "model.npz", root / "metadata.json",
                )


if __name__ == "__main__":
    unittest.main()
