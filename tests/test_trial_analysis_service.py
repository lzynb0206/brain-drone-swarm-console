from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.services.trial_analysis_service import TrialAnalysisService


class TrialAnalysisServiceTest(unittest.TestCase):
    @staticmethod
    def _write_trial(
        path: Path,
        label: str = "右移",
        trial_index: int = 3,
        run_id: str = "",
    ) -> None:
        sample_rate = 250.0
        timeline = np.arange(1250, dtype=float) / sample_rate
        rng = np.random.default_rng(42)
        eeg = np.asarray([
            45.0 * np.sin(2 * np.pi * 12.0 * timeline + index * 0.31)
            + rng.normal(0.0, 4.0 + index * 0.2, timeline.size)
            for index in range(8)
        ], dtype=np.float32)
        np.savez_compressed(
            path,
            eeg=eeg,
            fs=np.asarray(sample_rate, dtype=np.float32),
            true_label=np.asarray(label),
            start_time=np.asarray("2026-10-22T12:00:00+08:00"),
            session_id=np.asarray("session-test"),
            channel_names=np.asarray(("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4")),
            trial_context=np.asarray(json.dumps({
                "trial_index": trial_index,
                **({"run_id": run_id} if run_id else {}),
            })),
        )

    def test_discovers_labeled_trials_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_trials = root / "raw_trials"
            raw_trials.mkdir()
            self._write_trial(raw_trials / "trial-right.npz")
            np.savez_compressed(raw_trials / "not-a-trial.npz", values=np.ones(4))
            service = TrialAnalysisService(root / "exports")

            records, warnings = service.discover(root)

        self.assertEqual(1, len(records))
        self.assertEqual("右移", records[0].true_label)
        self.assertEqual(5.0, records[0].duration_seconds)
        self.assertEqual("3", records[0].trial_index)
        self.assertEqual(1, len(warnings))

    def test_reads_experiment_run_and_formats_shared_run_start_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "renamed-trial.npz"
            self._write_trial(
                path,
                run_id="auto-20261022-190558-fa4fb5",
            )

            record = TrialAnalysisService.read_metadata(path)

        self.assertEqual("auto-20261022-190558-fa4fb5", record.experiment_id)
        self.assertEqual("2026-10-22T19:05:58", record.experiment_start_time)

    def test_renders_seven_panel_report_in_memory_and_saves_numeric_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "trial-right.npz"
            self._write_trial(trial)
            service = TrialAnalysisService(root / "exports")
            record = service.read_metadata(trial)

            report = service.generate_report(record, 0.8, 4.2)

            self.assertTrue(report.figure_png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(report.figure_png), 10_000)
            self.assertEqual([], list((root / "exports").rglob("*.png")))
            self.assertTrue(report.metrics_path.exists())
            self.assertTrue(report.channel_quality_path.exists())
            payload = json.loads(report.metrics_path.read_text(encoding="utf-8"))
            self.assertEqual("右移", payload["trial"]["true_label"])
            self.assertEqual({"start": 0.8, "end": 4.2}, payload["analysis_window_seconds"])
            self.assertEqual(6, len(payload["fbcca"]["scores"]))

    def test_shared_fft_limits_cover_notched_and_unnotched_curves(self) -> None:
        raw = np.asarray([0.004, 0.010, 0.100])
        notched = np.asarray([0.002, 0.008, 0.030])

        low, high = TrialAnalysisService._shared_log_limits(raw, notched)

        self.assertLess(low, float(min(raw.min(), notched.min())))
        self.assertGreater(high, float(max(raw.max(), notched.max())))


if __name__ == "__main__":
    unittest.main()
