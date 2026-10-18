from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from src.bci.config import TRUE_LABELS
from src.services.ssvep_experiment_protocol import (
    AutomaticExperimentConfig,
    AutomaticExperimentProtocol,
    ExperimentPhase,
    build_balanced_trial_plan,
)
from src.ml.train_transformer_ssvep import _analysis_aligned_eeg, _rejected_trial_ids


class BalancedPlanTest(unittest.TestCase):
    def test_plan_is_seeded_balanced_and_avoids_adjacent_duplicates(self) -> None:
        config = AutomaticExperimentConfig(repetitions_per_label=8, random_seed=73)

        first = build_balanced_trial_plan(config)
        second = build_balanced_trial_plan(config)

        self.assertEqual(first, second)
        self.assertEqual(len(TRUE_LABELS) * 8, len(first))
        self.assertEqual(
            {label: 8 for label in TRUE_LABELS},
            dict(Counter(trial.label for trial in first)),
        )
        self.assertTrue(
            all(left.label != right.label for left, right in zip(first, first[1:]))
        )
        for block_index in range(1, 9):
            labels = {
                trial.label for trial in first if trial.block_index == block_index
            }
            self.assertEqual(set(TRUE_LABELS), labels)

    def test_invalid_discard_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "丢弃"):
            AutomaticExperimentConfig(
                stimulus_seconds=2.0,
                discard_seconds=2.0,
            ).validate()

    def test_config_rejects_window_too_short_for_current_decoder(self) -> None:
        with self.assertRaisesRegex(ValueError, "4 秒"):
            AutomaticExperimentConfig(
                stimulus_seconds=2.0,
                discard_seconds=0.5,
            ).validate()


class ProtocolStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AutomaticExperimentConfig(
            repetitions_per_label=1,
            cue_seconds=1.0,
            stimulus_seconds=5.0,
            discard_seconds=1.0,
            rest_seconds=1.5,
            random_seed=4,
        )
        self.protocol = AutomaticExperimentProtocol(self.config)

    def test_save_acknowledgement_gates_rest_and_next_trial(self) -> None:
        transition = self.protocol.start(10.0)
        self.assertEqual(ExperimentPhase.CUE, transition.phase)
        self.assertIsNone(self.protocol.tick(10.99))

        transition = self.protocol.tick(11.0)
        self.assertEqual(ExperimentPhase.STIMULUS, transition.phase)
        transition = self.protocol.tick(16.0)
        self.assertEqual(ExperimentPhase.SAVING, transition.phase)

        # No amount of timer advancement can skip an unfinished disk save.
        self.assertIsNone(self.protocol.tick(999.0))
        transition = self.protocol.trial_saved(17.0)
        self.assertEqual(ExperimentPhase.REST, transition.phase)
        self.assertIsNone(self.protocol.tick(18.49))
        transition = self.protocol.tick(18.5)
        self.assertEqual(ExperimentPhase.CUE, transition.phase)
        self.assertEqual(2, transition.trial.trial_index)

    def test_pause_during_capture_is_deferred_until_saved(self) -> None:
        self.protocol.start(0.0)
        self.protocol.tick(1.0)

        self.assertFalse(self.protocol.request_pause(1.2))
        self.assertFalse(self.protocol.paused)
        self.protocol.tick(6.0)
        transition = self.protocol.trial_saved(6.1)

        self.assertEqual(ExperimentPhase.REST, transition.phase)
        self.assertTrue(self.protocol.paused)
        self.assertIsNone(self.protocol.tick(100.0))
        self.assertTrue(self.protocol.resume(100.0))
        self.assertIsNone(self.protocol.tick(101.49))
        self.assertEqual(ExperimentPhase.CUE, self.protocol.tick(101.5).phase)

    def test_abort_during_capture_is_deferred_until_saved(self) -> None:
        self.protocol.start(0.0)
        self.protocol.tick(1.0)

        self.assertIsNone(self.protocol.request_abort(1.5))
        self.protocol.tick(6.0)
        transition = self.protocol.trial_saved(6.2)

        self.assertEqual(ExperimentPhase.ABORTED, transition.phase)
        self.assertIn("保存后中止", transition.reason)

    def test_rejected_trial_appends_same_label_retry(self) -> None:
        original_total = self.protocol.total_trials
        self.protocol.start(0.0)
        self.protocol.tick(1.0)
        self.protocol.tick(6.0)
        rejected = self.protocol.current_trial
        self.protocol.trial_saved(6.1)

        retry = self.protocol.reject_current_trial()

        self.assertEqual(original_total + 1, self.protocol.total_trials)
        self.assertEqual(rejected.label, retry.label)
        self.assertEqual(rejected.trial_index, retry.retry_of_trial_index)
        self.assertEqual(1, self.protocol.rejected_trials)
        with self.assertRaisesRegex(RuntimeError, "已经"):
            self.protocol.reject_current_trial()


class TrainingHandoffTest(unittest.TestCase):
    def test_training_crop_starts_at_recorded_analysis_offset(self) -> None:
        eeg = np.tile(np.arange(750, dtype=np.float32), (8, 1))

        cropped = _analysis_aligned_eeg(
            eeg,
            250.0,
            {"analysis_start_offset_seconds": 0.5},
            250.0,
            500,
        )

        self.assertEqual((8, 500), cropped.shape)
        self.assertEqual(125.0, float(cropped[0, 0]))
        self.assertEqual(624.0, float(cropped[0, -1]))

    def test_lsl_analysis_marker_takes_priority_over_configured_offset(self) -> None:
        eeg = np.tile(np.arange(750, dtype=np.float32), (8, 1))
        timestamps = 10.0 + np.arange(750, dtype=np.float64) / 250.0

        cropped = _analysis_aligned_eeg(
            eeg,
            250.0,
            {"analysis_start_offset_seconds": 1.0},
            250.0,
            500,
            sample_timestamps=timestamps,
            analysis_lsl_time=10.5,
        )

        self.assertEqual((8, 500), cropped.shape)
        self.assertEqual(125.0, float(cropped[0, 0]))
        self.assertEqual(624.0, float(cropped[0, -1]))

    def test_lsl_analysis_window_rejects_timestamp_gap(self) -> None:
        eeg = np.tile(np.arange(750, dtype=np.float32), (8, 1))
        timestamps = 10.0 + np.arange(750, dtype=np.float64) / 250.0
        timestamps[300:] += 0.02

        with self.assertRaisesRegex(ValueError, "时间戳缺口"):
            _analysis_aligned_eeg(
                eeg,
                250.0,
                {"analysis_start_offset_seconds": 0.5},
                250.0,
                500,
                sample_timestamps=timestamps,
                analysis_lsl_time=10.5,
            )

    def test_rejected_marker_excludes_trial_from_training_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "event_markers.jsonl"
            path.write_text(
                '{"name":"auto_ssvep_rejected","trial_id":"trial-1",'
                '"metadata":{"rejection_reason":"operator_rejected"}}\n'
                '{"name":"trial_rest","trial_id":"trial-2","metadata":{}}\n',
                encoding="utf-8",
            )

            rejected = _rejected_trial_ids(path)

        self.assertEqual({"trial-1"}, rejected)


if __name__ == "__main__":
    unittest.main()
