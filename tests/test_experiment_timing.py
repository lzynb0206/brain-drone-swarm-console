from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.domain import IntentResult
from src.services import ExperimentClock, ExperimentMarkerOutlet, ExperimentRecorder
from src.workers import EEGWorker


def _result(sample_count: int) -> IntentResult:
    return IntentResult(
        timestamp=1.0,
        label="前进",
        confidence=0.9,
        margin=0.8,
        accepted=True,
        reason="test",
        frequency_hz=13.75,
        scores={"前进": 0.9},
        sample_rate_hz=250.0,
        sample_count=sample_count,
        used_channels_1based=tuple(range(1, 9)),
        signal_quality={"status": "good"},
    )


class ExperimentClockTest(unittest.TestCase):
    def test_translates_unix_and_lsl_timestamps_without_pylsl(self) -> None:
        clock = ExperimentClock(local_clock=lambda: 100.0, unix_clock=lambda: 1_000.0)
        unix_stamp = clock.stamp(995.0, "unix")
        lsl_stamp = clock.stamp(105.0, "lsl")
        self.assertEqual(95.0, unix_stamp.lsl_seconds)
        self.assertEqual(995.0, unix_stamp.unix_seconds)
        self.assertEqual(105.0, lsl_stamp.lsl_seconds)
        self.assertEqual(1_005.0, lsl_stamp.unix_seconds)
        self.assertEqual("injected_lsl_local_clock", lsl_stamp.clock_source)

    def test_marker_outlet_publishes_json_at_supplied_timestamp(self) -> None:
        published: list[tuple[list[str], float]] = []

        class Outlet:
            def __init__(self, _info):
                pass

            def push_sample(self, values, timestamp):
                published.append((values, timestamp))

        outlet = ExperimentMarkerOutlet(
            info_factory=lambda *args: args,
            outlet_factory=Outlet,
        )
        self.assertTrue(outlet.publish("trial_cue", 12.5, {"trial_id": "T1"}))
        values, timestamp = published[0]
        payload = json.loads(values[0])
        self.assertEqual(12.5, timestamp)
        self.assertEqual("trial_cue", payload["name"])
        self.assertEqual("T1", payload["metadata"]["trial_id"])


class ExperimentTrialTimingTest(unittest.TestCase):
    def test_recorder_saves_full_timing_and_backfills_late_rest_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            trial_id = "trial-timing-001"
            recorder.record_marker(
                "trial_cue", 9.0, "S01", "session-1",
                {"trial_id": trial_id, "phase": "cue"},
                unix_timestamp=1009.0,
                clock_source="test-clock",
            )
            timestamps = 10.0 + np.arange(625) / 250.0
            saved = recorder.record(
                _result(625),
                np.ones((8, 625)),
                true_label="前进",
                source="test",
                subject_id="S01",
                session_id="session-1",
                sample_timestamps=timestamps,
                trial_metadata={
                    "trial_id": trial_id,
                    "block_id": "block-1",
                    "block_index": 1,
                    "trial_index": 3,
                    "repetition": 2,
                    "random_seed": 42,
                    "stimulus_lsl_time": 10.0,
                    "analysis_lsl_time": 10.5,
                },
                clock_source="test-clock",
            )
            recorder.record_marker(
                "trial_rest", 12.6, "S01", "session-1",
                {"trial_id": trial_id, "phase": "rest"},
                unix_timestamp=1012.6,
                clock_source="test-clock",
            )

            trial_path = Path(directory) / saved["raw_trial_path"]
            with np.load(trial_path, allow_pickle=False) as payload:
                self.assertEqual((8, 625), payload["eeg"].shape)
                self.assertAlmostEqual(9.0, float(payload["cue_lsl_time"]))
                self.assertAlmostEqual(10.0, float(payload["stimulus_lsl_time"]))
                self.assertAlmostEqual(10.5, float(payload["analysis_lsl_time"]))
                self.assertAlmostEqual(12.6, float(payload["rest_lsl_time"]))
                self.assertAlmostEqual(10.0, float(payload["window_start_lsl_time"]))
                self.assertAlmostEqual(timestamps[-1], float(payload["window_end_lsl_time"]))
                context = json.loads(str(payload["trial_context"]))
                self.assertEqual(42, context["random_seed"])
                self.assertAlmostEqual(12.6, context["rest_lsl_time"])

            index_path = Path(directory) / "sessions" / "raw_trials_index.csv"
            with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual("block-1", row["block_id"])
            self.assertEqual("2", row["repetition"])
            self.assertAlmostEqual(12.6, float(row["rest_lsl_time"]))
            self.assertAlmostEqual(timestamps[-1], float(row["window_end_lsl_time"]))

    def test_worker_keeps_onset_samples_and_marks_analysis_offset(self) -> None:
        class Stream:
            source_name = "LSL · test"

            @staticmethod
            def flush():
                return 4

        class Decoder:
            @staticmethod
            def decode(window, _sample_rate_hz):
                return _result(window.shape[1])

            @staticmethod
            def reset():
                return None

        class MarkerOutlet:
            def __init__(self):
                self.events = []

            def publish(self, name, timestamp, metadata=None):
                self.events.append((name, timestamp, metadata))
                return True

            @staticmethod
            def stop():
                return None

        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            marker_outlet = MarkerOutlet()
            worker = EEGWorker(
                Stream(),
                Decoder(),
                recorder,
                experiment_clock=ExperimentClock(
                    local_clock=lambda: 100.0,
                    unix_clock=lambda: 1_000.0,
                ),
                marker_outlet=marker_outlet,
            )
            worker.set_experiment_context("S01", "session-1")
            trial_id = worker.capture_experiment_trial(
                "前进",
                13.75,
                marker_timestamp=10.0,
                duration_seconds=2.5,
                discard_seconds=0.5,
                metadata={
                    "trial_id": "trial-worker-001",
                    "block_id": "block-1",
                    "repetition": 1,
                    "random_seed": 7,
                    "analysis_duration_seconds": 2.0,
                },
            )
            _, _, _ = worker._apply_pending_stimulus(
                None, np.asarray([], dtype=np.float64), 0,
            )
            timestamps = 10.0 + np.arange(625) / 250.0
            worker._channel_names = tuple(f"CH{index + 1}" for index in range(8))
            worker._collect_labeled_trial(
                np.ones((8, 625)), timestamps, 250.0, 625,
            )
            self.assertEqual("trial-worker-001", trial_id)
            trial_path = next((Path(directory) / "raw_trials").glob("*.npz"))
            with np.load(trial_path, allow_pickle=False) as payload:
                self.assertEqual(625, payload["eeg"].shape[1])
                self.assertAlmostEqual(10.0, float(payload["sample_timestamps"][0]))
                self.assertAlmostEqual(10.5, float(payload["analysis_lsl_time"]))
                context = json.loads(str(payload["trial_context"]))
                self.assertEqual(0.5, context["analysis_start_offset_seconds"])
                self.assertEqual(2.0, context["analysis_duration_seconds"])
            self.assertIn("stimulus_capture_started", [event[0] for event in marker_outlet.events])
            self.assertIn("trial_analysis", [event[0] for event in marker_outlet.events])

    def test_cancelling_active_capture_never_saves_the_old_trial(self) -> None:
        class Stream:
            source_name = "LSL · test"

        class Decoder:
            @staticmethod
            def decode(window, _sample_rate_hz):
                return _result(window.shape[1])

        class MarkerOutlet:
            @staticmethod
            def publish(*_args, **_kwargs):
                return True

            @staticmethod
            def stop():
                return None

        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            worker = EEGWorker(
                Stream(), Decoder(), recorder,
                experiment_clock=ExperimentClock(
                    local_clock=lambda: 100.0,
                    unix_clock=lambda: 1_000.0,
                ),
                marker_outlet=MarkerOutlet(),
            )
            worker.capture_experiment_trial(
                "前进", 13.75, marker_timestamp=10.0,
                duration_seconds=2.0, discard_seconds=0.5,
                metadata={"trial_id": "trial-cancelled-001"},
            )
            worker._apply_pending_stimulus(None, np.asarray([], dtype=np.float64), 0)
            worker._collect_labeled_trial(
                np.ones((8, 100)), 10.0 + np.arange(100) / 250.0, 250.0, 500,
            )
            worker.cancel_experiment_trial("test_abort")
            worker._collect_labeled_trial(
                np.ones((8, 500)), 10.4 + np.arange(500) / 250.0, 250.0, 500,
            )
            self.assertEqual([], list((Path(directory) / "raw_trials").glob("*.npz")))
            self.assertEqual("", worker.pending_true_label())

    def test_zero_usable_channels_still_saves_labelled_raw_trial(self) -> None:
        class Stream:
            source_name = "LSL · test"

        class Decoder:
            @staticmethod
            def decode(_window, _sample_rate_hz):
                raise ValueError(
                    "没有可用 EEG 通道：请检查接触、工频、漂移及通道重复"
                )

            @staticmethod
            def reset():
                return None

        class MarkerOutlet:
            @staticmethod
            def publish(*_args, **_kwargs):
                return True

            @staticmethod
            def stop():
                return None

        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            worker = EEGWorker(
                Stream(), Decoder(), recorder,
                experiment_clock=ExperimentClock(
                    local_clock=lambda: 100.0,
                    unix_clock=lambda: 1_000.0,
                ),
                marker_outlet=MarkerOutlet(),
            )
            worker.capture_experiment_trial(
                "前进", 9.0, marker_timestamp=10.0,
                duration_seconds=4.0, discard_seconds=0.0,
                metadata={"trial_id": "trial-low-quality-001"},
            )
            worker._apply_pending_stimulus(None, np.asarray([], dtype=np.float64), 0)
            worker._channel_names = tuple(f"CH{index + 1}" for index in range(8))
            timestamps = 10.0 + np.arange(1000) / 250.0
            worker._collect_labeled_trial(
                np.zeros((8, 1000)), timestamps, 250.0, 1000,
            )

            trial_path = next((Path(directory) / "raw_trials").glob("*.npz"))
            with np.load(trial_path, allow_pickle=False) as payload:
                self.assertEqual("前进", str(payload["true_label"]))
                self.assertEqual((8, 1000), payload["eeg"].shape)
                quality = json.loads(str(payload["signal_quality"]))
                self.assertEqual("bad", quality["status"])
                self.assertTrue(quality["collection_continued"])
            self.assertEqual("", worker.pending_true_label())


if __name__ == "__main__":
    unittest.main()
