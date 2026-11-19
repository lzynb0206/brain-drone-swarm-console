"""Schemas and normalization helpers for persisted EEG experiment records."""

from __future__ import annotations

import csv
import json
import re
import uuid
from pathlib import Path
from typing import Mapping

import numpy as np

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
    "trial_metadata_json", "subject_name", "collection_type", "collection_scheme",
)
PHASE_NAMES = ("cue", "stimulus", "analysis", "rest")


def append_csv(path: Path, fields, row: dict) -> None:
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


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if np.isfinite(converted) else None


def canonical_phase(value: str) -> str:
    normalized = re.sub(r"[^a-z]+", "_", str(value).strip().lower()).strip("_")
    tokens = set(normalized.split("_"))
    for phase in PHASE_NAMES:
        if phase == normalized or phase in tokens:
            return phase
    return ""


def phase_times_from_metadata(metadata: Mapping[str, object]) -> dict[str, float]:
    values: dict[str, float] = {}
    nested = metadata.get("phase_timestamps_lsl", {})
    if isinstance(nested, Mapping):
        for phase in PHASE_NAMES:
            converted = optional_float(nested.get(phase))
            if converted is not None:
                values[phase] = converted
    for phase in PHASE_NAMES:
        converted = optional_float(metadata.get(f"{phase}_lsl_time"))
        if converted is not None:
            values[phase] = converted
    return values


def optional_float_array(value: object) -> np.ndarray:
    converted = optional_float(value)
    return np.asarray(np.nan if converted is None else converted, dtype=np.float64)


def csv_optional(value: object) -> str:
    converted = optional_float(value)
    return "" if converted is None else f"{converted:.9f}"


def safe_trial_id(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value)).strip("._")[:160]
    if not safe:
        raise ValueError("Trial ID must contain at least one safe character")
    return safe


def json_safe_dict(values: Mapping[str, object] | None) -> dict[str, object]:
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


def json_from_array(value: np.ndarray | None) -> dict[str, object]:
    if value is None:
        return {}
    try:
        decoded = json.loads(str(np.asarray(value).item()))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def validated_timestamps(values, sample_count: int) -> np.ndarray:
    timestamps = np.asarray(tuple(() if values is None else values), dtype=np.float64)
    if timestamps.size not in (0, sample_count):
        raise ValueError(f"EEG sample timestamps must be empty or contain {sample_count} values")
    if timestamps.size and not np.isfinite(timestamps).all():
        raise ValueError("EEG sample timestamps contain NaN or Inf")
    return timestamps


def normalized_labels(values, count: int, prefix: str) -> tuple[str, ...]:
    labels = tuple(str(value) for value in (() if values is None else values))
    if len(labels) == count:
        return labels
    if prefix:
        return tuple(f"{prefix}{index + 1}" for index in range(count))
    return tuple("" for _ in range(count))


def data_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(0, sum(1 for _ in handle) - 1)


__all__ = [
    "PHASE_NAMES", "RECOGNITION_FIELDS", "TRIAL_FIELDS", "append_csv",
    "canonical_phase", "csv_optional", "data_row_count", "json_from_array",
    "json_safe_dict", "normalized_labels", "optional_float",
    "optional_float_array", "phase_times_from_metadata", "safe_trial_id",
    "validated_timestamps",
]
