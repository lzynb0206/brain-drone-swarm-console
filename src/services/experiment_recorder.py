"""Durable recognition logs and labeled raw EEG trials."""

from __future__ import annotations

import csv
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from src.bci.config import LABEL_TO_FREQUENCY
from src.domain import RecognitionResult


class ExperimentRecorder:
    RECOGNITION_FIELDS = (
        "timestamp", "source", "true_label", "prediction", "accepted", "reason",
        "frequency_hz", "best_score", "margin", "sample_rate_hz", "sample_count",
        "used_channels", "scores_json", "raw_trial_id", "raw_trial_path",
    )
    TRIAL_FIELDS = (
        "trial_id", "timestamp", "path", "true_label", "target_frequency_hz",
        "prediction", "source", "sample_rate_hz", "channels", "samples",
    )

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.raw_trial_dir = self.root / "raw_trials"
        self.recognition_log_path = self.root / "recognition_log.csv"
        self.raw_trial_index_path = self.root / "raw_trials_index.csv"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_trial_dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        result: RecognitionResult,
        eeg_window,
        true_label: str = "",
        source: str = "",
    ) -> dict[str, str]:
        """Log every decision and, when labeled, save the exact raw window."""

        data = np.asarray(eeg_window, dtype=np.float32)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("Raw EEG trial must have shape (channels, samples)")
        if not np.isfinite(data).all():
            raise ValueError("Raw EEG trial contains NaN or Inf")

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        trial_id = ""
        relative_trial_path = ""
        with self._lock:
            if true_label:
                trial_id = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:8]}"
                trial_path = self.raw_trial_dir / f"{trial_id}.npz"
                metadata = {
                    "timestamp": timestamp,
                    "source": source,
                    "true_label": true_label,
                    "target_frequency_hz": LABEL_TO_FREQUENCY.get(true_label),
                    "prediction": result.prediction,
                    "accepted": result.accepted,
                }
                np.savez_compressed(
                    trial_path,
                    eeg=data,
                    fs=np.asarray(result.sample_rate_hz, dtype=np.float32),
                    true_label=np.asarray(true_label),
                    metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
                )
                relative_trial_path = str(trial_path.relative_to(self.root))
                self._append_csv(
                    self.raw_trial_index_path,
                    self.TRIAL_FIELDS,
                    {
                        "trial_id": trial_id,
                        "timestamp": timestamp,
                        "path": relative_trial_path,
                        "true_label": true_label,
                        "target_frequency_hz": LABEL_TO_FREQUENCY.get(true_label, ""),
                        "prediction": result.prediction or "",
                        "source": source,
                        "sample_rate_hz": result.sample_rate_hz,
                        "channels": data.shape[0],
                        "samples": data.shape[1],
                    },
                )

            row = {
                "timestamp": timestamp,
                "source": source,
                "true_label": true_label,
                "prediction": result.prediction or "",
                "accepted": int(result.accepted),
                "reason": result.reason,
                "frequency_hz": result.frequency_hz if result.frequency_hz is not None else "",
                "best_score": f"{result.best_score:.8f}",
                "margin": f"{result.margin:.8f}",
                "sample_rate_hz": result.sample_rate_hz,
                "sample_count": result.sample_count,
                "used_channels": ",".join(str(value) for value in result.used_channels_1based),
                "scores_json": json.dumps(result.scores, ensure_ascii=False, sort_keys=True),
                "raw_trial_id": trial_id,
                "raw_trial_path": relative_trial_path,
            }
            self._append_csv(self.recognition_log_path, self.RECOGNITION_FIELDS, row)
        return {"trial_id": trial_id, "raw_trial_path": relative_trial_path}

    @staticmethod
    def _append_csv(path: Path, fields, row: dict) -> None:
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    def stats(self) -> dict[str, int]:
        return {
            "recognitions": self._data_row_count(self.recognition_log_path),
            "labeled_trials": self._data_row_count(self.raw_trial_index_path),
        }

    @staticmethod
    def _data_row_count(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)

    def recent_trials(self, limit: int = 12) -> list[dict[str, str]]:
        if not self.raw_trial_index_path.exists():
            return []
        with self.raw_trial_index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return list(reversed(rows[-limit:]))
