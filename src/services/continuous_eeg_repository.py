"""NPZ and CSV-index persistence for continuous EEG chunks."""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from src.services.experiment_record_schema import (
    append_csv,
    normalized_labels,
    validated_timestamps,
)


class ContinuousEEGRepository:
    FIELDS = (
        "timestamp",
        "subject_id",
        "session_id",
        "source",
        "chunk_index",
        "path",
        "sample_rate_hz",
        "channels",
        "samples",
        "started_at",
    )

    def __init__(
        self,
        root: Path,
        continuous_dir: Path,
        index_path: Path,
        lock: threading.Lock,
    ) -> None:
        self.root = root
        self.continuous_dir = continuous_dir
        self.index_path = index_path
        self._lock = lock

    def record(
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
        data = np.asarray(eeg, dtype=np.float32)
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("Continuous EEG chunk must have shape (channels, samples)")
        if not np.isfinite(data).all():
            raise ValueError("Continuous EEG chunk contains NaN or Inf")
        timestamps = validated_timestamps(sample_timestamps, data.shape[1])
        names = normalized_labels(channel_names, data.shape[0], "CH")
        original_values = tuple(() if original_channel_names is None else original_channel_names)
        original_names = normalized_labels(original_values or names, data.shape[0], "CH")
        units = normalized_labels(channel_units, data.shape[0], "")
        safe_session = re.sub(r"[^0-9A-Za-z_.-]+", "_", session_id or "session")
        session_path = self.continuous_dir / safe_session
        session_path.mkdir(parents=True, exist_ok=True)
        path = session_path / f"chunk-{int(chunk_index):06d}-{uuid.uuid4().hex[:8]}.npz"
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
        with self._lock:
            append_csv(
                self.index_path,
                self.FIELDS,
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


__all__ = ["ContinuousEEGRepository"]
