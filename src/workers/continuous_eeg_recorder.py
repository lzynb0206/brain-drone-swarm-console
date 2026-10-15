"""Buffered continuous-EEG chunk persistence for the acquisition worker."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

import numpy as np

from src.services.experiment_recorder import ExperimentRecorder


class ContinuousEEGRecorder:
    """Accumulate frames and persist fixed-duration chunks outside EEGWorker."""

    def __init__(
        self,
        recorder: ExperimentRecorder,
        chunk_saved: Callable[[str], None],
        chunk_seconds: float = 10.0,
    ) -> None:
        self.recorder = recorder
        self.chunk_saved = chunk_saved
        self.chunk_seconds = float(chunk_seconds)
        self._state_lock = threading.Lock()
        self._enabled = False
        self._buffer: np.ndarray | None = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._started_at = ""
        self._chunk_index = 0
        self._last_sample_rate_hz = 0.0
        self._context: dict[str, object] = {}

    def set_enabled(self, enabled: bool) -> None:
        with self._state_lock:
            self._enabled = bool(enabled)

    def append(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
        *,
        source: str,
        subject_id: str,
        session_id: str,
        channel_names: tuple[str, ...],
        original_channel_names: tuple[str, ...],
        channel_units: tuple[str, ...],
    ) -> None:
        self._remember_context(
            sample_rate_hz,
            source=source,
            subject_id=subject_id,
            session_id=session_id,
            channel_names=channel_names,
            original_channel_names=original_channel_names,
            channel_units=channel_units,
        )
        with self._state_lock:
            enabled = self._enabled
        if not enabled:
            self.flush(sample_rate_hz)
            return
        if self._buffer is None or self._buffer.shape[0] != values.shape[0]:
            self._buffer = values.copy()
            self._timestamps = timestamps.copy()
            self._started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        else:
            self._buffer = np.concatenate((self._buffer, values), axis=1)
            self._timestamps = np.concatenate((self._timestamps, timestamps))
        threshold = max(1, round(sample_rate_hz * self.chunk_seconds))
        while self._buffer is not None and self._buffer.shape[1] >= threshold:
            chunk = self._buffer[:, :threshold].copy()
            chunk_timestamps = self._timestamps[:threshold].copy()
            self._buffer = self._buffer[:, threshold:]
            self._timestamps = self._timestamps[threshold:]
            self._save(chunk, chunk_timestamps, sample_rate_hz)
            self._started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")

    def flush(self, sample_rate_hz: float = 0.0) -> None:
        if self._buffer is None or self._buffer.shape[1] == 0:
            self._clear_buffer()
            return
        rate = float(sample_rate_hz or self._last_sample_rate_hz)
        if rate > 0.0:
            self._save(self._buffer.copy(), self._timestamps.copy(), rate)
        self._clear_buffer()

    def _remember_context(self, sample_rate_hz: float, **context: object) -> None:
        self._last_sample_rate_hz = float(sample_rate_hz)
        self._context = context

    def _save(
        self,
        chunk: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
    ) -> None:
        context = self._context
        path = self.recorder.record_continuous_chunk(
            chunk,
            sample_rate_hz,
            str(context.get("source", "")),
            str(context.get("subject_id", "")),
            str(context.get("session_id", "")),
            self._chunk_index,
            self._started_at,
            sample_timestamps=timestamps,
            channel_names=tuple(context.get("channel_names", ())),
            original_channel_names=tuple(context.get("original_channel_names", ())),
            channel_units=tuple(context.get("channel_units", ())),
        )
        self._chunk_index += 1
        self.chunk_saved(path)

    def _clear_buffer(self) -> None:
        self._buffer = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._started_at = ""


__all__ = ["ContinuousEEGRecorder"]
