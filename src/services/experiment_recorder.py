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


class ExperimentRecorder:
    RECOGNITION_FIELDS = (
        "timestamp", "subject_id", "session_id", "source", "true_label",
        "target_frequency_hz", "candidate_label", "prediction", "confidence",
        "margin", "accepted", "reason", "sample_rate_hz", "sample_count",
        "used_channels", "signal_quality_json", "scores_json", "raw_trial_id",
        "raw_trial_path", "window_start_lsl_time", "window_end_lsl_time",
        "window_duration_seconds", "clock_source",
    )
    TRIAL_FIELDS = (
        "trial_id", "start_time", "subject_id", "session_id", "path", "true_label",
        "target_frequency_hz", "candidate_label", "prediction", "confidence",
        "margin", "accepted", "reason", "source", "sample_rate_hz", "channels",
        "samples", "signal_quality_json", "block_id", "block_index", "trial_index",
        "repetition", "random_seed", "cue_lsl_time", "stimulus_lsl_time",
        "analysis_lsl_time", "rest_lsl_time", "window_start_lsl_time",
        "window_end_lsl_time", "window_duration_seconds", "clock_source",
        "trial_metadata_json",
    )
    PHASE_NAMES = ("cue", "stimulus", "analysis", "rest")

    def __init__(self, root: Path | str) -> None:
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
        self._trial_phase_times: dict[str, dict[str, float]] = {}
        self._trial_paths: dict[str, Path] = {}
        for directory in (
            self.raw_trial_dir, self.session_dir, self.recognition_log_dir, self.export_dir,
            self.continuous_dir,
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
        with self._lock:
            self._ensure_session(session_id, subject_id, source, timestamp)
            if true_label:
                requested_trial_id = str(supplied_metadata.get("trial_id", "")).strip()
                trial_id = self._safe_trial_id(requested_trial_id or self.new_trial_id())
                trial_path = self.raw_trial_dir / f"{trial_id}.npz"
                if trial_path.exists():
                    raise ValueError(f"Trial ID already exists: {trial_id}")
                phase_times = self._phase_times_from_metadata(supplied_metadata)
                phase_times.update(self._trial_phase_times.get(trial_id, {}))
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
                self._trial_paths[trial_id] = trial_path
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
        """Append a stimulus/experiment marker without storing sample points in a database."""

        safe_metadata = self._json_safe_dict(metadata)
        trial_id = str(safe_metadata.get("trial_id", "")).strip()
        phase = self._canonical_phase(str(safe_metadata.get("phase", "") or name))
        payload = {
            "name": str(name),
            # ``timestamp`` remains for readers of the first schema, but is
            # now explicitly the LSL/local-clock value.
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
                self._patch_trial_phase_locked(trial_id, phase, float(timestamp))
            with self.marker_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

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
        """Persist one bounded continuous EEG chunk and append its searchable index row."""

        data = np.asarray(eeg, dtype=np.float32)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("Continuous EEG chunk must have shape (channels, samples)")
        if not np.isfinite(data).all():
            raise ValueError("Continuous EEG chunk contains NaN or Inf")
        timestamps = self._validated_timestamps(sample_timestamps, data.shape[1])
        names = self._normalized_labels(channel_names, data.shape[0], "CH")
        original_values = tuple(
            () if original_channel_names is None else original_channel_names
        )
        original_names = self._normalized_labels(
            original_values or names, data.shape[0], "CH"
        )
        units = self._normalized_labels(channel_units, data.shape[0], "")
        safe_session = re.sub(r"[^0-9A-Za-z_.-]+", "_", session_id or "session")
        session_path = self.continuous_dir / safe_session
        session_path.mkdir(parents=True, exist_ok=True)
        name = f"chunk-{int(chunk_index):06d}-{uuid.uuid4().hex[:8]}.npz"
        path = session_path / name
        np.savez_compressed(
            path,
            eeg=data,
            fs=np.asarray(float(sample_rate_hz), dtype=np.float32),
            source=np.asarray(source),
            subject_id=np.asarray(subject_id),
            session_id=np.asarray(session_id),
            started_at=np.asarray(started_at),
            sample_timestamps=timestamps,
            channel_names=np.asarray(names),
            original_channel_names=np.asarray(original_names),
            channel_units=np.asarray(units),
        )
        relative = str(path.relative_to(self.root))
        fields = (
            "timestamp", "subject_id", "session_id", "source", "chunk_index",
            "path", "sample_rate_hz", "channels", "samples", "started_at",
        )
        with self._lock:
            self._append_csv(
                self.continuous_index_path,
                fields,
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "source": source,
                    "chunk_index": int(chunk_index),
                    "path": relative,
                    "sample_rate_hz": float(sample_rate_hz),
                    "channels": data.shape[0],
                    "samples": data.shape[1],
                    "started_at": started_at,
                },
            )
        return relative

    def record_command_event(
        self,
        status: str,
        message: str,
        action: str = "",
        command_id: str = "",
        uav_id: str = "",
        source: str = "swarm_gateway",
    ) -> None:
        fields = (
            "timestamp", "command_id", "action", "uav_id", "status", "message", "source",
        )
        with self._lock:
            self._append_csv(
                self.command_log_path,
                fields,
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "command_id": command_id,
                    "action": action,
                    "uav_id": uav_id,
                    "status": status,
                    "message": message,
                    "source": source,
                },
            )

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
        fields = tuple(str(field) for field in fields)
        exists = path.exists() and path.stat().st_size > 0
        if exists:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                current_fields = tuple(reader.fieldnames or ())
                previous_rows = list(reader) if current_fields != fields else []
            if current_fields != fields:
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(previous_rows)
                temporary.replace(path)
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    @classmethod
    def _canonical_phase(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z]+", "_", str(value).strip().lower()).strip("_")
        tokens = set(normalized.split("_"))
        for phase in cls.PHASE_NAMES:
            if phase == normalized or phase in tokens:
                return phase
        return ""

    @classmethod
    def _phase_times_from_metadata(cls, metadata: Mapping[str, object]) -> dict[str, float]:
        values: dict[str, float] = {}
        nested = metadata.get("phase_timestamps_lsl", {})
        if isinstance(nested, Mapping):
            for phase in cls.PHASE_NAMES:
                converted = cls._optional_float(nested.get(phase))
                if converted is not None:
                    values[phase] = converted
        for phase in cls.PHASE_NAMES:
            converted = cls._optional_float(metadata.get(f"{phase}_lsl_time"))
            if converted is not None:
                values[phase] = converted
        return values

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            converted = float(value)
        except (TypeError, ValueError):
            return None
        return converted if np.isfinite(converted) else None

    @classmethod
    def _optional_float_array(cls, value: object) -> np.ndarray:
        converted = cls._optional_float(value)
        return np.asarray(np.nan if converted is None else converted, dtype=np.float64)

    @classmethod
    def _csv_optional(cls, value: object) -> str:
        converted = cls._optional_float(value)
        return "" if converted is None else f"{converted:.9f}"

    @staticmethod
    def _safe_trial_id(value: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value)).strip("._")[:160]
        if not safe:
            raise ValueError("Trial ID must contain at least one safe character")
        return safe

    @staticmethod
    def _json_safe_dict(values: Mapping[str, object] | None) -> dict[str, object]:
        if values is None:
            return {}

        def default(value: object) -> object:
            item = getattr(value, "item", None)
            if callable(item):
                try:
                    return item()
                except (TypeError, ValueError):
                    pass
            return str(value)

        encoded = json.dumps(dict(values), ensure_ascii=False, sort_keys=True, default=default)
        return dict(json.loads(encoded))

    def _patch_trial_phase_locked(self, trial_id: str, phase: str, timestamp: float) -> None:
        """Backfill a late rest marker into an already-saved trial and index."""

        path = self._trial_paths.get(trial_id)
        if path is None:
            candidate = self.raw_trial_dir / f"{self._safe_trial_id(trial_id)}.npz"
            path = candidate if candidate.exists() else None
        if path is None or not path.exists():
            return

        with np.load(path, allow_pickle=False) as payload:
            arrays = {key: np.asarray(payload[key]) for key in payload.files}
        context = self._json_from_array(arrays.get("trial_context"))
        context[f"{phase}_lsl_time"] = float(timestamp)
        context.setdefault("trial_id", trial_id)
        arrays[f"{phase}_lsl_time"] = np.asarray(float(timestamp), dtype=np.float64)
        arrays["trial_context"] = np.asarray(
            json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        metadata = self._json_from_array(arrays.get("metadata"))
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
        self._patch_trial_index_locked(trial_id, phase, timestamp, context)

    def _patch_trial_index_locked(
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
            row[f"{phase}_lsl_time"] = self._csv_optional(timestamp)
            row["trial_metadata_json"] = json.dumps(
                dict(context), ensure_ascii=False, sort_keys=True,
            )
            changed = True
        if not changed:
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=self.TRIAL_FIELDS, extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)

    @staticmethod
    def _json_from_array(value: np.ndarray | None) -> dict[str, object]:
        if value is None:
            return {}
        try:
            decoded = json.loads(str(np.asarray(value).item()))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def stats(self) -> dict[str, int]:
        return {
            "recognitions": self._data_row_count(self.recognition_log_path),
            "labeled_trials": self._data_row_count(self.raw_trial_index_path),
        }

    @staticmethod
    def _validated_timestamps(values, sample_count: int) -> np.ndarray:
        timestamps = np.asarray(tuple(() if values is None else values), dtype=np.float64)
        if timestamps.size not in (0, sample_count):
            raise ValueError(
                f"EEG sample timestamps must be empty or contain {sample_count} values"
            )
        if timestamps.size and not np.isfinite(timestamps).all():
            raise ValueError("EEG sample timestamps contain NaN or Inf")
        return timestamps

    @staticmethod
    def _normalized_labels(values, count: int, prefix: str) -> tuple[str, ...]:
        labels = tuple(str(value) for value in (() if values is None else values))
        if len(labels) == count:
            return labels
        if prefix:
            return tuple(f"{prefix}{index + 1}" for index in range(count))
        return tuple("" for _ in range(count))

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
