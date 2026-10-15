"""Experiment marker journal and late trial-phase backfilling."""

from __future__ import annotations

import csv
import json
import threading
import uuid
from pathlib import Path
from typing import Mapping

import numpy as np

from src.services.experiment_record_schema import (
    TRIAL_FIELDS,
    canonical_phase,
    csv_optional,
    json_from_array,
    json_safe_dict,
    safe_trial_id,
)


class ExperimentMarkerStore:
    """Append marker events and backfill phase times into saved trials."""

    def __init__(
        self,
        raw_trial_dir: Path,
        raw_trial_index_path: Path,
        marker_log_path: Path,
        lock: threading.Lock,
    ) -> None:
        self.raw_trial_dir = raw_trial_dir
        self.raw_trial_index_path = raw_trial_index_path
        self.marker_log_path = marker_log_path
        self._lock = lock
        self._trial_phase_times: dict[str, dict[str, float]] = {}
        self._trial_paths: dict[str, Path] = {}

    def phase_times_for(self, trial_id: str) -> dict[str, float]:
        return dict(self._trial_phase_times.get(trial_id, {}))

    def remember_trial(self, trial_id: str, path: Path) -> None:
        self._trial_paths[trial_id] = path

    def record(
        self,
        name: str,
        timestamp: float,
        subject_id: str = "",
        session_id: str = "",
        metadata: dict[str, object] | None = None,
        *,
        unix_timestamp: float | None = None,
        clock_source: str = "",
    ) -> None:
        safe_metadata = json_safe_dict(metadata)
        trial_id = str(safe_metadata.get("trial_id", "")).strip()
        phase = canonical_phase(str(safe_metadata.get("phase", "") or name))
        payload = {
            "name": str(name),
            "timestamp": float(timestamp),
            "lsl_timestamp": float(timestamp),
            "unix_timestamp": (
                float(unix_timestamp) if unix_timestamp is not None else None
            ),
            "clock_source": str(clock_source),
            "subject_id": str(subject_id),
            "session_id": str(session_id),
            "trial_id": trial_id,
            "phase": phase,
            "metadata": safe_metadata,
        }
        with self._lock:
            if trial_id and phase:
                self._trial_phase_times.setdefault(trial_id, {})[phase] = float(timestamp)
                self._patch_trial_phase(trial_id, phase, float(timestamp))
            with self.marker_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _patch_trial_phase(self, trial_id: str, phase: str, timestamp: float) -> None:
        path = self._trial_paths.get(trial_id)
        if path is None:
            candidate = self.raw_trial_dir / f"{safe_trial_id(trial_id)}.npz"
            path = candidate if candidate.exists() else None
        if path is None or not path.exists():
            return

        with np.load(path, allow_pickle=False) as payload:
            arrays = {key: np.asarray(payload[key]) for key in payload.files}
        context = json_from_array(arrays.get("trial_context"))
        context[f"{phase}_lsl_time"] = float(timestamp)
        context.setdefault("trial_id", trial_id)
        arrays[f"{phase}_lsl_time"] = np.asarray(float(timestamp), dtype=np.float64)
        arrays["trial_context"] = np.asarray(
            json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        metadata = json_from_array(arrays.get("metadata"))
        phase_times = metadata.get("phase_timestamps_lsl")
        if not isinstance(phase_times, dict):
            phase_times = {}
        phase_times[phase] = float(timestamp)
        metadata["phase_timestamps_lsl"] = phase_times
        metadata["trial_context"] = context
        arrays["metadata"] = np.asarray(json.dumps(metadata, ensure_ascii=False))
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
        temporary.replace(path)
        self._patch_trial_index(trial_id, phase, timestamp, context)

    def _patch_trial_index(
        self,
        trial_id: str,
        phase: str,
        timestamp: float,
        context: Mapping[str, object],
    ) -> None:
        path = self.raw_trial_index_path
        if not path.exists():
            return
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        changed = False
        for row in rows:
            if row.get("trial_id") != trial_id:
                continue
            row[f"{phase}_lsl_time"] = csv_optional(timestamp)
            row["trial_metadata_json"] = json.dumps(
                dict(context), ensure_ascii=False, sort_keys=True
            )
            changed = True
        if not changed:
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=TRIAL_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)


__all__ = ["ExperimentMarkerStore"]
