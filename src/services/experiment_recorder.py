"""Persist recognition logs, experiment sessions and labeled raw EEG trials."""

from __future__ import annotations

import csv
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from src.bci.config import LABEL_TO_FREQUENCY
from src.domain import IntentResult


class ExperimentRecorder:
    RECOGNITION_FIELDS = (
        "timestamp", "subject_id", "session_id", "source", "true_label",
        "target_frequency_hz", "candidate_label", "prediction", "confidence",
        "margin", "accepted", "reason", "sample_rate_hz", "sample_count",
        "used_channels", "signal_quality_json", "scores_json", "raw_trial_id",
        "raw_trial_path",
    )
    TRIAL_FIELDS = (
        "trial_id", "start_time", "subject_id", "session_id", "path", "true_label",
        "target_frequency_hz", "candidate_label", "prediction", "confidence",
        "margin", "accepted", "reason", "source", "sample_rate_hz", "channels",
        "samples", "signal_quality_json",
    )

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.raw_trial_dir = self.root / "raw_trials"
        self.session_dir = self.root / "sessions"
        self.recognition_log_dir = self.root / "recognition_logs"
        self.export_dir = self.root / "exports"
        self.recognition_log_path = self.recognition_log_dir / "recognition_log.csv"
        self.raw_trial_index_path = self.session_dir / "raw_trials_index.csv"
        self._lock = threading.Lock()
        self._known_sessions: set[str] = set()
        for directory in (
            self.raw_trial_dir, self.session_dir, self.recognition_log_dir, self.export_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        result: IntentResult,
        eeg_window,
        true_label: str = "",
        source: str = "",
        subject_id: str = "",
        session_id: str = "",
        start_time: str = "",
    ) -> dict[str, str]:
        """Log a decision and save the exact EEG array only for a labeled trial."""

        data = np.asarray(eeg_window, dtype=np.float32)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("Raw EEG trial must have shape (channels, samples)")
        if not np.isfinite(data).all():
            raise ValueError("Raw EEG trial contains NaN or Inf")

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        start_time = start_time or timestamp
        quality_json = json.dumps(result.signal_quality, ensure_ascii=False, sort_keys=True)
        scores_json = json.dumps(result.scores, ensure_ascii=False, sort_keys=True)
        trial_id = ""
        relative_trial_path = ""
        with self._lock:
            self._ensure_session(session_id, subject_id, source, timestamp)
            if true_label:
                trial_id = f"trial-{datetime.now():%Y%m%d-%H%M%S-%f}-{uuid.uuid4().hex[:8]}"
                trial_path = self.raw_trial_dir / f"{trial_id}.npz"
                metadata = {
                    "trial_id": trial_id,
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "start_time": start_time,
                    "source": source,
                    "true_label": true_label,
                    "target_frequency_hz": LABEL_TO_FREQUENCY.get(true_label),
                    "candidate_label": result.label,
                    "prediction": result.prediction,
                    "confidence": result.confidence,
                    "margin": result.margin,
                    "accepted": result.accepted,
                    "reason": result.reason,
                    "signal_quality": result.signal_quality,
                }
                np.savez_compressed(
                    trial_path,
                    eeg=data,
                    fs=np.asarray(result.sample_rate_hz, dtype=np.float32),
                    true_label=np.asarray(true_label),
                    subject_id=np.asarray(subject_id),
                    session_id=np.asarray(session_id),
                    start_time=np.asarray(start_time),
                    signal_quality=np.asarray(quality_json),
                    metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
                )
                relative_trial_path = str(trial_path.relative_to(self.root))
                self._append_csv(
                    self.raw_trial_index_path,
                    self.TRIAL_FIELDS,
                    {
                        "trial_id": trial_id,
                        "start_time": start_time,
                        "subject_id": subject_id,
                        "session_id": session_id,
                        "path": relative_trial_path,
                        "true_label": true_label,
                        "target_frequency_hz": LABEL_TO_FREQUENCY.get(true_label, ""),
                        "candidate_label": result.label,
                        "prediction": result.prediction or "",
                        "confidence": f"{result.confidence:.8f}",
                        "margin": f"{result.margin:.8f}",
                        "accepted": int(result.accepted),
                        "reason": result.reason,
                        "source": source,
                        "sample_rate_hz": result.sample_rate_hz,
                        "channels": data.shape[0],
                        "samples": data.shape[1],
                        "signal_quality_json": quality_json,
                    },
                )

            self._append_csv(
                self.recognition_log_path,
                self.RECOGNITION_FIELDS,
                {
                    "timestamp": timestamp,
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "source": source,
                    "true_label": true_label,
                    "target_frequency_hz": LABEL_TO_FREQUENCY.get(true_label, ""),
                    "candidate_label": result.label,
                    "prediction": result.prediction or "",
                    "confidence": f"{result.confidence:.8f}",
                    "margin": f"{result.margin:.8f}",
                    "accepted": int(result.accepted),
                    "reason": result.reason,
                    "sample_rate_hz": result.sample_rate_hz,
                    "sample_count": result.sample_count,
                    "used_channels": ",".join(str(value) for value in result.used_channels_1based),
                    "signal_quality_json": quality_json,
                    "scores_json": scores_json,
                    "raw_trial_id": trial_id,
                    "raw_trial_path": relative_trial_path,
                },
            )
        return {"trial_id": trial_id, "raw_trial_path": relative_trial_path}

    def _ensure_session(self, session_id: str, subject_id: str, source: str, timestamp: str) -> None:
        if not session_id or session_id in self._known_sessions:
            return
        safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", session_id)
        path = self.session_dir / f"{safe_name}.json"
        if not path.exists():
            payload = {
                "session_id": session_id,
                "subject_id": subject_id,
                "source": source,
                "created_at": timestamp,
                "raw_eeg_storage": "file_npz",
                "postgresql_policy": "metadata_and_file_path_only",
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._known_sessions.add(session_id)

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
