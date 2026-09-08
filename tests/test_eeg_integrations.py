from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.adapters.real import CsvEEGStream, LSLEEGStream
from src.bci import FBCCADecoder
from src.bci.config import COMMANDS, FB_EEGNET_ENABLED, TARGET_FREQUENCIES_HZ
from src.domain import IntentResult
from src.services import ExperimentRecorder


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
            )
            stats = recorder.stats()
            trial_path = Path(directory) / saved["raw_trial_path"]
            payload = np.load(trial_path)
            self.assertEqual((8, 1000), payload["eeg"].shape)
            self.assertEqual("前进", str(payload["true_label"]))
            self.assertEqual("S01", str(payload["subject_id"]))
            self.assertTrue((Path(directory) / "sessions" / "session-1.json").is_file())
        self.assertEqual({"recognitions": 2, "labeled_trials": 1}, stats)


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


if __name__ == "__main__":
    unittest.main()
