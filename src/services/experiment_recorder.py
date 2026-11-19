"""Persist recognition logs, experiment sessions and labeled raw EEG trials."""

from __future__ import annotations

import csv
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Mapping

import numpy as np

from src.bci.config import LABEL_TO_FREQUENCY
from src.domain import IntentResult
from src.services.command_audit_repository import CommandAuditRepository
from src.services.continuous_eeg_repository import ContinuousEEGRepository
from src.services.acquisition_identity import acquisition_directory, capture_identity
from src.services.experiment_record_schema import (
    PHASE_NAMES,
    RECOGNITION_FIELDS,
    TRIAL_FIELDS,
    append_csv,
    canonical_phase,
    csv_optional,
    data_row_count,
    json_from_array,
    json_safe_dict,
    normalized_labels,
    optional_float,
    optional_float_array,
    phase_times_from_metadata,
    safe_trial_id,
    validated_timestamps,
)
from src.services.experiment_marker_store import ExperimentMarkerStore
from src.services.recognition_log_repository import RecognitionLogRepository


class ExperimentRecorder:
    RECOGNITION_FIELDS = RECOGNITION_FIELDS
    TRIAL_FIELDS = TRIAL_FIELDS
    PHASE_NAMES = PHASE_NAMES
    _append_csv = staticmethod(append_csv)
    _canonical_phase = staticmethod(canonical_phase)
    _phase_times_from_metadata = staticmethod(phase_times_from_metadata)
    _optional_float = staticmethod(optional_float)
    _optional_float_array = staticmethod(optional_float_array)
    _csv_optional = staticmethod(csv_optional)
    _safe_trial_id = staticmethod(safe_trial_id)
    _json_safe_dict = staticmethod(json_safe_dict)
    _json_from_array = staticmethod(json_from_array)
    _validated_timestamps = staticmethod(validated_timestamps)
    _normalized_labels = staticmethod(normalized_labels)
    _data_row_count = staticmethod(data_row_count)

    def __init__(
        self,
        root: Path | str,
        *,
        recognition_log_max_bytes: int = RecognitionLogRepository.DEFAULT_MAX_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.raw_trial_dir = self.root / "raw_trials"
        self.session_dir = self.root / "sessions"
        self.recognition_log_dir = self.root / "recognition_logs"
        self.export_dir = self.root / "exports"
        self.continuous_dir = self.root / "continuous_raw"
        self.recognition_log_path = self.recognition_log_dir / "recognition_log.csv"
        self.raw_trial_index_path = self.session_dir / "raw_trials_index.csv"
        self.marker_log_path = self.session_dir / "event_markers.jsonl"
        self.continuous_index_path = self.session_dir / "continuous_raw_index.csv"
        self.command_log_path = self.recognition_log_dir / "command_execution_log.csv"
        self._lock = threading.Lock()
        self._known_sessions: set[str] = set()
        for directory in (
            self.raw_trial_dir, self.session_dir, self.recognition_log_dir, self.export_dir,
            self.continuous_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._marker_store = ExperimentMarkerStore(
            self.raw_trial_dir,
            self.raw_trial_index_path,
            self.marker_log_path,
            self._lock,
        )
        self._continuous_repository = ContinuousEEGRepository(
            self.root,
            self.continuous_dir,
            self.continuous_index_path,
            self._lock,
        )
        self._command_audit = CommandAuditRepository(self.command_log_path, self._lock)
        self._recognition_logs = RecognitionLogRepository(
            self.recognition_log_dir,
            max_bytes=recognition_log_max_bytes,
        )

    def record(
        self,
        result: IntentResult,
        eeg_window,
        true_label: str = "",
        source: str = "",
        subject_id: str = "",
        session_id: str = "",
        start_time: str = "",
        sample_timestamps=(),
        channel_names=(),
        original_channel_names=(),
        channel_units=(),
        paradigm_type: str = "SSVEP",
        log_recognition: bool = True,
        trial_metadata: Mapping[str, object] | None = None,
        clock_source: str = "",
    ) -> dict[str, str]:
        """Log a decision and save the exact EEG array only for a labeled trial."""

        data = np.asarray(eeg_window, dtype=np.float32)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("Raw EEG trial must have shape (channels, samples)")
        if not np.isfinite(data).all():
            raise ValueError("Raw EEG trial contains NaN or Inf")
        timestamps = self._validated_timestamps(sample_timestamps, data.shape[1])
        names = self._normalized_labels(channel_names, data.shape[0], "CH")
        original_values = tuple(
            () if original_channel_names is None else original_channel_names
        )
        original_names = self._normalized_labels(
            original_values or names, data.shape[0], "CH"
        )
        units = self._normalized_labels(channel_units, data.shape[0], "")

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        start_time = start_time or timestamp
        quality_json = json.dumps(result.signal_quality, ensure_ascii=False, sort_keys=True)
        compact_quality_json = self._recognition_logs.compact_quality_json(
            result.signal_quality
        )
        scores_json = json.dumps(result.scores, ensure_ascii=False, sort_keys=True)
        trial_id = ""
        relative_trial_path = ""
        window_start = float(timestamps[0]) if timestamps.size else None
        window_end = float(timestamps[-1]) if timestamps.size else None
        window_duration = (
            max(0.0, window_end - window_start)
            if window_start is not None and window_end is not None else None
        )
        supplied_metadata = self._json_safe_dict(trial_metadata)
        subject_id, session_id = capture_identity(supplied_metadata, (subject_id, session_id))
        with self._lock:
            self._ensure_session(session_id, subject_id, source, timestamp, supplied_metadata)
            if true_label:
                requested_trial_id = str(supplied_metadata.get("trial_id", "")).strip()
                trial_id = self._safe_trial_id(requested_trial_id or self.new_trial_id())
                directory = acquisition_directory(self.raw_trial_dir, supplied_metadata)
                directory.mkdir(parents=True, exist_ok=True)
                trial_path = directory / f"{trial_id}.npz"
                if trial_path.exists():
                    raise ValueError(f"Trial ID already exists: {trial_id}")
                phase_times = self._phase_times_from_metadata(supplied_metadata)
                phase_times.update(self._marker_store.phase_times_for(trial_id))
                for phase in self.PHASE_NAMES:
                    value = phase_times.get(phase)
                    if value is not None:
                        supplied_metadata[f"{phase}_lsl_time"] = float(value)
                supplied_metadata["trial_id"] = trial_id
                supplied_metadata["window_start_lsl_time"] = window_start
                supplied_metadata["window_end_lsl_time"] = window_end
                supplied_metadata["window_duration_seconds"] = window_duration
                supplied_metadata["clock_source"] = str(
                    supplied_metadata.get("clock_source") or clock_source
                )
                metadata = {
                    "trial_id": trial_id,
                    "subject_id": subject_id,
                    "subject_name": supplied_metadata.get("subject_name", ""),
                    "collection_type": supplied_metadata.get("collection_type", ""),
                    "collection_scheme": supplied_metadata.get("collection_scheme", ""),
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
                    "paradigm_type": str(paradigm_type),
                    "channel_names": list(names),
                    "original_channel_names": list(original_names),
                    "channel_units": list(units),
                    "block_id": supplied_metadata.get("block_id", ""),
                    "block_index": supplied_metadata.get("block_index", ""),
                    "trial_index": supplied_metadata.get("trial_index", ""),
                    "repetition": supplied_metadata.get("repetition", ""),
                    "random_seed": supplied_metadata.get("random_seed", ""),
                    "phase_timestamps_lsl": {
                        phase: supplied_metadata.get(f"{phase}_lsl_time")
                        for phase in self.PHASE_NAMES
                    },
                    "window_start_lsl_time": window_start,
                    "window_end_lsl_time": window_end,
                    "window_duration_seconds": window_duration,
                    "clock_source": supplied_metadata.get("clock_source", ""),
                    "trial_context": supplied_metadata,
                }
                np.savez_compressed(
                    trial_path,
                    eeg=data,
                    fs=np.asarray(result.sample_rate_hz, dtype=np.float32),
                    true_label=np.asarray(true_label),
                    subject_id=np.asarray(subject_id),
                    subject_name=np.asarray(str(supplied_metadata.get("subject_name", ""))),
                    collection_type=np.asarray(str(supplied_metadata.get("collection_type", ""))),
                    collection_scheme=np.asarray(str(supplied_metadata.get("collection_scheme", ""))),
                    session_id=np.asarray(session_id),
                    start_time=np.asarray(start_time),
                    signal_quality=np.asarray(quality_json),
                    sample_timestamps=timestamps,
                    channel_names=np.asarray(names),
                    original_channel_names=np.asarray(original_names),
                    channel_units=np.asarray(units),
                    paradigm_type=np.asarray(str(paradigm_type)),
                    cue_lsl_time=self._optional_float_array(supplied_metadata.get("cue_lsl_time")),
                    stimulus_lsl_time=self._optional_float_array(supplied_metadata.get("stimulus_lsl_time")),
                    analysis_lsl_time=self._optional_float_array(supplied_metadata.get("analysis_lsl_time")),
                    rest_lsl_time=self._optional_float_array(supplied_metadata.get("rest_lsl_time")),
                    window_start_lsl_time=self._optional_float_array(window_start),
                    window_end_lsl_time=self._optional_float_array(window_end),
                    clock_source=np.asarray(str(supplied_metadata.get("clock_source", ""))),
                    trial_context=np.asarray(json.dumps(supplied_metadata, ensure_ascii=False, sort_keys=True)),
                    metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
                )
                relative_trial_path = str(trial_path.relative_to(self.root))
                self._marker_store.remember_trial(trial_id, trial_path)
                self._append_csv(
                    self.raw_trial_index_path,
                    self.TRIAL_FIELDS,
                    {
                        "trial_id": trial_id,
                        "start_time": start_time,
                        "subject_id": subject_id,
                        "subject_name": supplied_metadata.get("subject_name", ""),
                        "collection_type": supplied_metadata.get("collection_type", ""),
                        "collection_scheme": supplied_metadata.get("collection_scheme", ""),
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
                        "block_id": supplied_metadata.get("block_id", ""),
                        "block_index": supplied_metadata.get("block_index", ""),
                        "trial_index": supplied_metadata.get("trial_index", ""),
                        "repetition": supplied_metadata.get("repetition", ""),
                        "random_seed": supplied_metadata.get("random_seed", ""),
                        "cue_lsl_time": self._csv_optional(supplied_metadata.get("cue_lsl_time")),
                        "stimulus_lsl_time": self._csv_optional(supplied_metadata.get("stimulus_lsl_time")),
                        "analysis_lsl_time": self._csv_optional(supplied_metadata.get("analysis_lsl_time")),
                        "rest_lsl_time": self._csv_optional(supplied_metadata.get("rest_lsl_time")),
                        "window_start_lsl_time": self._csv_optional(window_start),
                        "window_end_lsl_time": self._csv_optional(window_end),
                        "window_duration_seconds": self._csv_optional(window_duration),
                        "clock_source": supplied_metadata.get("clock_source", ""),
                        "trial_metadata_json": json.dumps(
                            supplied_metadata, ensure_ascii=False, sort_keys=True,
                        ),
                    },
                )

            if log_recognition:
                self._recognition_logs.append(
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
                        "signal_quality_json": compact_quality_json,
                        "scores_json": scores_json,
                        "raw_trial_id": trial_id,
                        "raw_trial_path": relative_trial_path,
                        "window_start_lsl_time": self._csv_optional(window_start),
                        "window_end_lsl_time": self._csv_optional(window_end),
                        "window_duration_seconds": self._csv_optional(window_duration),
                        "clock_source": str(supplied_metadata.get("clock_source") or clock_source),
                    },
                )
        return {"trial_id": trial_id, "raw_trial_path": relative_trial_path}

    def record_marker(
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
        subject_id, session_id = capture_identity(metadata or {}, (subject_id, session_id))
        self._marker_store.record(
            name,
            timestamp,
            subject_id,
            session_id,
            metadata,
            unix_timestamp=unix_timestamp,
            clock_source=clock_source,
        )

    @staticmethod
    def new_trial_id() -> str:
        return f"trial-{datetime.now():%Y%m%d-%H%M%S-%f}-{uuid.uuid4().hex[:8]}"

    def record_continuous_chunk(
        self,
        eeg,
        sample_rate_hz: float,
        source: str,
        subject_id: str,
        session_id: str,
        chunk_index: int,
        started_at: str,
        sample_timestamps=(),
        channel_names=(),
        original_channel_names=(),
        channel_units=(),
    ) -> str:
        return self._continuous_repository.record(
            eeg,
            sample_rate_hz,
            source,
            subject_id,
            session_id,
            chunk_index,
            started_at,
            sample_timestamps,
            channel_names,
            original_channel_names,
            channel_units,
        )

    def record_command_event(
        self,
        status: str,
        message: str,
        action: str = "",
        command_id: str = "",
        uav_id: str = "",
        source: str = "swarm_gateway",
    ) -> None:
        self._command_audit.record(
            status,
            message,
            action,
            command_id,
            uav_id,
            source,
        )

    def _ensure_session(self, session_id: str, subject_id: str, source: str, timestamp: str,
                        context: Mapping[str, object] | None = None) -> None:
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
            if context and context.get("acquisition_subject_id"):
                payload.update({key: context.get(key, "") for key in (
                    "subject_name", "subject_folder", "acquisition_date", "collection_type", "collection_scheme")})
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._known_sessions.add(session_id)

    def stats(self) -> dict[str, int]:
        return {
            "recognitions": self._recognition_logs.row_count(),
            "labeled_trials": self._data_row_count(self.raw_trial_index_path),
        }

    def recent_trials(self, limit: int = 12) -> list[dict[str, str]]:
        if not self.raw_trial_index_path.exists():
            return []
        with self.raw_trial_index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return list(reversed(rows[-limit:]))
