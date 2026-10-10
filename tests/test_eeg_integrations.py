from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.adapters.real import CsvEEGStream, LSLEEGStream, SelectableEEGStream
from src.bci import FBCCADecoder
from src.bci.config import COMMANDS, FB_EEGNET_ENABLED, TARGET_FREQUENCIES_HZ
from src.domain import EEGFrame, IntentResult
from src.services import ExperimentRecorder, RawEEGCsvRecorder
from src.workers import EEGWorker


class CsvEEGStreamTest(unittest.TestCase):
    def test_reads_index_header_and_loops_channel_major_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eeg.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(range(8))
                for sample in range(6):
                    writer.writerow(sample * 10 + channel for channel in range(8))
            stream = CsvEEGStream(path, frame_size=4, realtime=False, loop=True)
            stream.connect()
            first = stream.read_frame()
            second = stream.read_frame()
        self.assertEqual(8, len(first.samples))
        self.assertEqual((0.0, 10.0, 20.0, 30.0), first.samples[0])
        self.assertEqual((40.0, 50.0, 0.0, 10.0), second.samples[0])

    def test_reads_lsl_recorder_schema_with_names_and_sample_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lsl_raw.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("sample_index", "lsl_timestamp", "elapsed_seconds", "C3", "C4"))
                for sample in range(40):
                    writer.writerow((sample, 10 + sample * 0.004, sample * 0.004, sample, -sample))
            stream = CsvEEGStream(
                path, frame_size=32, expected_channel_count=2, realtime=False, loop=False,
            )
            stream.connect()
            frame = stream.read_frame()
        self.assertEqual(("C3", "C4"), frame.channel_names)
        self.assertEqual(32, len(frame.sample_timestamps))
        self.assertAlmostEqual(10.124, frame.sample_timestamps[-1])
        self.assertAlmostEqual(250.0, frame.sample_rate_hz)

    def test_supports_variable_channel_csv_seek_and_playback_speed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "four_channel.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("C3", "C4", "Cz", "Pz"))
                for sample in range(80):
                    writer.writerow((sample, sample + 1, sample + 2, sample + 3))
            stream = CsvEEGStream(
                path, frame_size=8, expected_channel_count=None, realtime=False,
            )
            stream.connect()
            stream.seek_fraction(0.5)
            stream.set_playback_speed(2.0)
            frame = stream.read_frame()
            snapshot = stream.playback_snapshot()
        self.assertEqual(4, len(frame.samples))
        self.assertEqual(2.0, snapshot["speed"])
        self.assertGreater(snapshot["progress"], 0.5)


class RawEEGCsvRecorderTest(unittest.TestCase):
    def test_writes_lsl_compatible_csv_and_metadata_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawEEGCsvRecorder(directory, flush_interval_seconds=0)
            recorder.start(session_id="session-1", subject_id="S01", source="LSL · obci_eeg1")
            frame = EEGFrame(
                timestamp=10.004,
                sample_rate_hz=250.0,
                channel_names=("C3", "C4"),
                samples=((1.0, 2.0), (3.0, 4.0)),
                sample_timestamps=(10.0, 10.004),
                channel_units=("uV", "uV"),
                source_id="openbci",
            )
            self.assertEqual(2, recorder.append(frame))
            path = recorder.stop()
            assert path is not None
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            metadata = path.with_suffix(".json").read_text(encoding="utf-8")
        self.assertEqual(
            ["sample_index", "lsl_timestamp", "elapsed_seconds", "C3", "C4"],
            rows[0],
        )
        self.assertEqual(["0", "10.0", "0.0", "1.0", "3.0"], rows[1])
        self.assertIn('"sample_count": 2', metadata)


class FBCCADecoderTest(unittest.TestCase):
    def test_fb_eegnet_remains_disabled_until_new_model_assets_exist(self) -> None:
        self.assertFalse(FB_EEGNET_ENABLED)

    def test_recognizes_each_configured_frequency(self) -> None:
        fs = 250.0
        timeline = np.arange(round(fs * 4)) / fs
        decoder = FBCCADecoder(min_score=0.01, min_margin=0.001)
        rng = np.random.default_rng(20260908)
        for expected_label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ):
            base = np.sin(2 * np.pi * frequency * timeline)
            second = 0.35 * np.sin(2 * np.pi * 2 * frequency * timeline + 0.2)
            data = rng.normal(0, 0.08, size=(8, timeline.size))
            data[1] += base + second
            data[6] += 0.9 * base + second
            result = decoder.decode(data, fs)
            self.assertTrue(result.accepted, result)
            self.assertEqual(expected_label, result.prediction)


class ExperimentRecorderTest(unittest.TestCase):
    def test_logs_every_decision_and_saves_only_labeled_raw_trial(self) -> None:
        result = IntentResult(
            timestamp=1.0,
            label="前进",
            confidence=0.8,
            margin=0.7,
            accepted=True,
            reason="通过安全阈值",
            frequency_hz=13.75,
            scores={"起飞": 0.1, "前进": 0.8},
            sample_rate_hz=250.0,
            sample_count=1000,
            used_channels_1based=(2, 7),
            signal_quality={"status": "good"},
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            recorder.record(result, np.ones((8, 1000)), source="test", subject_id="S01", session_id="session-1")
            saved = recorder.record(
                result, np.ones((8, 1000)), true_label="前进", source="test",
                subject_id="S01", session_id="session-1", start_time="2026-09-08T10:00:00+08:00",
                sample_timestamps=np.arange(1000) / 250.0,
                channel_names=tuple(f"EEG{index + 1}" for index in range(8)),
                channel_units=("uV",) * 8,
            )
            stats = recorder.stats()
            trial_path = Path(directory) / saved["raw_trial_path"]
            payload = np.load(trial_path)
            self.assertEqual((8, 1000), payload["eeg"].shape)
            self.assertEqual("前进", str(payload["true_label"]))
            self.assertEqual("S01", str(payload["subject_id"]))
            self.assertEqual((1000,), payload["sample_timestamps"].shape)
            self.assertEqual("EEG1", str(payload["channel_names"][0]))
            self.assertEqual("uV", str(payload["channel_units"][0]))
            self.assertTrue((Path(directory) / "sessions" / "session-1.json").is_file())
        self.assertEqual({"recognitions": 2, "labeled_trials": 1}, stats)

    def test_mi_trial_is_saved_without_calling_fbcca_or_logging_a_decision(self) -> None:
        class DecoderThatMustNotRun:
            def decode(self, *_args, **_kwargs):
                raise AssertionError("FBCCA must not run in MI mode")

        class Stream:
            source_name = "LSL · mi-test"

        with tempfile.TemporaryDirectory() as directory:
            recorder = ExperimentRecorder(directory)
            worker = EEGWorker(Stream(), DecoderThatMustNotRun(), recorder)
            worker.set_experiment_context("S01", "mi-session")
            worker.set_paradigm_type("MI")
            worker.capture_next_trial("想象左手")
            samples = np.ones((8, 1000), dtype=float)
            timestamps = np.arange(1000, dtype=float) / 250.0
            worker._collect_labeled_trial(samples, timestamps, 250.0, 1000)
            stats = recorder.stats()
            trial = next((Path(directory) / "raw_trials").glob("*.npz"))
            with np.load(trial) as payload:
                paradigm_type = str(payload["paradigm_type"])
                true_label = str(payload["true_label"])
        self.assertEqual({"recognitions": 0, "labeled_trials": 1}, stats)
        self.assertEqual("MI", paradigm_type)
        self.assertEqual("想象左手", true_label)


class _FakeInfo:
    def channel_count(self): return 2
    def nominal_srate(self): return 250
    def name(self): return "obci_eeg1"


class _FakeInlet:
    def __init__(self, *_args, **_kwargs): pass
    def info(self): return _FakeInfo()
    def pull_chunk(self, **_kwargs): return [[1, 2], [3, 4]], [10.0, 10.004]
    def close_stream(self): pass


class LSLEEGStreamTest(unittest.TestCase):
    def test_wraps_lsl_samples_in_common_frame_contract(self) -> None:
        resolver = lambda *_args, **_kwargs: [object()]
        stream = LSLEEGStream(expected_channel_count=2, resolver=resolver, inlet_factory=_FakeInlet)
        stream.connect()
        frame = stream.read_frame()
        stream.disconnect()
        self.assertEqual((1.0, 3.0), frame.samples[0])
        self.assertEqual((2.0, 4.0), frame.samples[1])
        self.assertEqual(250.0, frame.sample_rate_hz)
        self.assertEqual((10.0, 10.004), frame.sample_timestamps)

    def test_discovers_selects_and_reports_timestamp_health(self) -> None:
        resolver = lambda *_args, **_kwargs: [_FakeInfo()]
        stream = LSLEEGStream(
            expected_channel_count=2, resolver=resolver, inlet_factory=_FakeInlet,
        )
        descriptors = stream.discover_streams()
        self.assertEqual(1, len(descriptors))
        self.assertEqual("obci_eeg1", descriptors[0].name)
        self.assertEqual(("CH1", "CH2"), descriptors[0].channel_names)
        stream.select_stream(descriptors[0])
        stream.connect()
        stream.read_frame()
        health = stream.health_snapshot()
        stream.disconnect()
        self.assertEqual(2, health["total_samples"])
        self.assertEqual(0, health["gap_count"])

    def test_selectable_source_switches_from_replay_to_discovered_lsl(self) -> None:
        class ReplaySource:
            source_name = "CSV回放"
            connected = False
            sample_rate_hz = 250.0

            def connect(self):
                self.connected = True

            def disconnect(self):
                self.connected = False

            def read_frame(self):
                raise AssertionError("should switch to LSL")

            def flush(self):
                return 0

        def resolver(*_args, **_kwargs):
            return [_FakeInfo()]

        lsl = LSLEEGStream(
            expected_channel_count=2, resolver=resolver, inlet_factory=_FakeInlet,
        )
        source = SelectableEEGStream(ReplaySource(), lsl)
        descriptor = source.discover_streams()[0]
        source.select_stream(descriptor)
        source.connect()
        frame = source.read_frame()
        self.assertTrue(source.source_name.startswith("LSL"))
        self.assertEqual((1.0, 3.0), frame.samples[0])


if __name__ == "__main__":
    unittest.main()
