"""Onset-aligned labelled EEG trial capture for :mod:`eeg_worker`."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

import numpy as np

from src.bci.preprocessing import assess_signal_quality
from src.domain import IntentResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder
from src.services.experiment_timing import (
    ExperimentClock,
    ExperimentMarkerOutlet,
    ExperimentTimestamp,
)


@dataclass(frozen=True)
class TrialCaptureRequest:
    label: str
    frequency_hz: float
    marker: ExperimentTimestamp
    duration_seconds: float | None
    analysis_offset_seconds: float
    metadata: dict[str, object]


class LabeledTrialRecorder:
    """Own pending-trial state, sample alignment, decoding and persistence."""

    def __init__(
        self,
        recorder: ExperimentRecorder,
        decoder: IntentDecoder,
        experiment_clock: ExperimentClock,
        marker_outlet: ExperimentMarkerOutlet,
        *,
        source_name: Callable[[], str],
        experiment_context: Callable[[], tuple[str, str]],
        channel_metadata: Callable[
            [], tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
        ],
        status_changed: Callable[[str], None],
        error_occurred: Callable[[str], None],
        recognition_received: Callable[[IntentResult], None],
        trial_saved: Callable[[str, str], None],
        record_phase: Callable[..., object],
    ) -> None:
        self.recorder = recorder
        self.decoder = decoder
        self.experiment_clock = experiment_clock
        self.marker_outlet = marker_outlet
        self._source_name = source_name
        self._experiment_context = experiment_context
        self._channel_metadata = channel_metadata
        self._status_changed = status_changed
        self._error_occurred = error_occurred
        self._recognition_received = recognition_received
        self._trial_saved = trial_saved
        self._record_phase = record_phase
        self._state_lock = threading.Lock()
        self._pending_true_label = ""
        self._pending_request: TrialCaptureRequest | None = None
        self._capture_generation = 0
        self._active_capture_generation = -1
        self._active_metadata: dict[str, object] = {}
        self._active_capture_seconds: float | None = None
        self._active_analysis_offset_seconds = 0.0
        self._active_stimulus_lsl_time = 0.0
        self._analysis_marker_generation = -1
        self._buffer: np.ndarray | None = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._start_time = ""
        self._paradigm_type = "SSVEP"

    @property
    def capture_seconds(self) -> float | None:
        with self._state_lock:
            return self._active_capture_seconds

    @property
    def paradigm_type(self) -> str:
        with self._state_lock:
            return self._paradigm_type

    def set_paradigm_type(self, paradigm_type: str) -> None:
        with self._state_lock:
            self._paradigm_type = str(paradigm_type or "SSVEP")

    def capture_next(self, true_label: str) -> None:
        with self._state_lock:
            self._pending_true_label = str(true_label)
            self._active_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
            self._capture_generation += 1

    def queue_capture(
        self,
        true_label: str,
        frequency_hz: float,
        marker_timestamp: float | None,
        duration_seconds: float | None,
        discard_seconds: float,
        metadata: Mapping[str, object] | None,
    ) -> str:
        label = str(true_label)
        duration = None if duration_seconds is None else float(duration_seconds)
        offset = max(0.0, float(discard_seconds))
        if duration is not None and duration <= 0.0:
            raise ValueError("duration_seconds must be positive")
        if duration is not None and offset >= duration:
            raise ValueError("discard_seconds must be shorter than duration_seconds")
        context = dict(metadata or {})
        trial_id = str(context.get("trial_id", "")).strip()
        if label and not trial_id:
            trial_id = self.recorder.new_trial_id()
            context["trial_id"] = trial_id
        stamp = self.experiment_clock.stamp(marker_timestamp, "auto")
        context.setdefault("stimulus_lsl_time", stamp.lsl_seconds)
        context.setdefault("analysis_start_offset_seconds", offset)
        context.setdefault("capture_duration_seconds", duration)
        context.setdefault("clock_source", stamp.clock_source)
        with self._state_lock:
            self._pending_request = TrialCaptureRequest(
                label=label,
                frequency_hz=float(frequency_hz),
                marker=stamp,
                duration_seconds=duration,
                analysis_offset_seconds=offset,
                metadata=context,
            )
        return trial_id

    def cancel(self) -> dict[str, object]:
        with self._state_lock:
            request = self._pending_request
            context = dict(
                request.metadata if request is not None else self._active_metadata
            )
            self._pending_request = None
            self._pending_true_label = ""
            self._capture_generation += 1
            self._active_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
        return context

    def pending_true_label(self) -> str:
        with self._state_lock:
            return self._pending_true_label

    def apply_pending(self, stream: EEGStream) -> bool:
        with self._state_lock:
            request = self._pending_request
            self._pending_request = None
        if request is None:
            return False
        discarded = 0
        flush = getattr(stream, "flush", None)
        if callable(flush) and not request.label:
            try:
                discarded = int(flush() or 0)
            except Exception as exc:
                self._error_occurred(f"清空 LSL 历史缓存失败：{exc}")
        with self._state_lock:
            self._pending_true_label = request.label
            self._active_metadata = dict(request.metadata)
            self._active_capture_seconds = request.duration_seconds
            self._active_analysis_offset_seconds = request.analysis_offset_seconds
            self._active_stimulus_lsl_time = request.marker.lsl_seconds
            self._capture_generation += 1
        reset_decoder = getattr(self.decoder, "reset", None)
        if callable(reset_decoder):
            reset_decoder()
        subject_id, session_id = self._experiment_context()
        marker_metadata = {
            **request.metadata,
            "phase": "stimulus",
            "true_label": request.label,
            "frequency_hz": request.frequency_hz,
            "discarded_samples": discarded,
            "analysis_start_offset_seconds": request.analysis_offset_seconds,
            "capture_duration_seconds": request.duration_seconds,
            "mode": "experiment" if request.label else "control",
        }
        self.recorder.record_marker(
            "stimulus_capture_started",
            request.marker.lsl_seconds,
            subject_id,
            session_id,
            marker_metadata,
            unix_timestamp=request.marker.unix_seconds,
            clock_source=request.marker.clock_source,
        )
        self.marker_outlet.publish(
            "stimulus_capture_started",
            request.marker.lsl_seconds,
            marker_metadata,
        )
        if request.label:
            message = (
                f"刺激同步采集：{request.label}，已按 LSL 时间戳对齐"
                "（保留起始原始段）"
            )
        else:
            message = f"在线脑控已同步，已清空 {discarded} 个历史样本并重置解码器"
        self._status_changed(message)
        return True

    def collect(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
        window_samples: int,
    ) -> None:
        with self._state_lock:
            label = self._pending_true_label
            generation = self._capture_generation
            trial_metadata = dict(self._active_metadata)
            analysis_offset = self._active_analysis_offset_seconds
            stimulus_lsl_time = self._active_stimulus_lsl_time
        if not label:
            self._reset_buffer()
            return
        values, timestamps = self._align_to_stimulus(
            values,
            timestamps,
            stimulus_lsl_time,
            generation,
            trial_metadata,
        )
        if values.shape[1] == 0:
            return
        self._append(values, timestamps, generation)
        self._mark_analysis_start(
            generation,
            stimulus_lsl_time,
            analysis_offset,
            trial_metadata,
        )
        if self._buffer is None or self._buffer.shape[1] < window_samples:
            return
        self._save(label, generation, sample_rate_hz, window_samples, trial_metadata)

    def _align_to_stimulus(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        stimulus_lsl_time: float,
        generation: int,
        trial_metadata: dict[str, object],
    ) -> tuple[np.ndarray, np.ndarray]:
        if not timestamps.size or stimulus_lsl_time <= 0.0:
            return values, timestamps
        distance = abs(float(timestamps[-1]) - stimulus_lsl_time)
        if distance <= 3600.0:
            if float(timestamps[-1]) < stimulus_lsl_time:
                return values[:, :0], timestamps[:0]
            keep = timestamps >= stimulus_lsl_time
            return values[:, keep], timestamps[keep]
        trial_metadata.setdefault("sample_clock_alignment", "unverified_fallback")
        with self._state_lock:
            if self._capture_generation == generation:
                self._active_metadata.setdefault(
                    "sample_clock_alignment",
                    "unverified_fallback",
                )
        return values, timestamps

    def _append(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        generation: int,
    ) -> None:
        new_capture = generation != self._active_capture_generation
        channel_changed = self._buffer is None or self._buffer.shape[0] != values.shape[0]
        if new_capture or channel_changed:
            self._active_capture_generation = generation
            self._buffer = values.copy()
            self._timestamps = timestamps.copy()
            first_lsl = (
                float(timestamps[0])
                if timestamps.size
                else self.experiment_clock.now().lsl_seconds
            )
            first_unix = self.experiment_clock.to_unix(first_lsl, "lsl")
            self._start_time = datetime.fromtimestamp(first_unix).astimezone().isoformat(
                timespec="milliseconds"
            )
            return
        self._buffer = np.concatenate((self._buffer, values), axis=1)
        self._timestamps = np.concatenate((self._timestamps, timestamps))

    def _mark_analysis_start(
        self,
        generation: int,
        stimulus_lsl_time: float,
        analysis_offset: float,
        trial_metadata: dict[str, object],
    ) -> None:
        if not trial_metadata.get("trial_id"):
            return
        if generation == self._analysis_marker_generation:
            return
        analysis_target = stimulus_lsl_time + analysis_offset
        eligible = self._timestamps[self._timestamps >= analysis_target]
        if not eligible.size:
            return
        analysis_time = float(eligible[0])
        trial_metadata["analysis_lsl_time"] = analysis_time
        with self._state_lock:
            if self._capture_generation == generation:
                self._active_metadata["analysis_lsl_time"] = analysis_time
        self._record_phase(
            "analysis",
            analysis_time,
            trial_metadata,
            timestamp_domain="lsl",
        )
        self._analysis_marker_generation = generation

    def _save(
        self,
        label: str,
        generation: int,
        sample_rate_hz: float,
        window_samples: int,
        trial_metadata: dict[str, object],
    ) -> None:
        if self._buffer is None:
            return
        labeled_window = self._buffer[:, :window_samples].copy()
        labeled_timestamps = self._timestamps[:window_samples].copy()
        paradigm_type = self.paradigm_type
        if paradigm_type == "SSVEP":
            result = self.decoder.decode(labeled_window, sample_rate_hz)
        else:
            try:
                quality = assess_signal_quality(labeled_window, sample_rate_hz)
            except Exception as exc:
                quality = {"status": "unknown", "reason": str(exc)}
            result = IntentResult(
                timestamp=datetime.now().timestamp(),
                label="",
                confidence=0.0,
                margin=0.0,
                accepted=False,
                reason="运动想象采集模式：仅保存原始 EEG，尚未启用 MI 分类器",
                frequency_hz=0.0,
                scores={},
                sample_rate_hz=float(sample_rate_hz),
                sample_count=int(labeled_window.shape[1]),
                used_channels_1based=tuple(range(1, labeled_window.shape[0] + 1)),
                signal_quality=quality,
            )
        subject_id, session_id = self._experiment_context()
        channel_names, original_channel_names, channel_units = self._channel_metadata()
        saved = self.recorder.record(
            result,
            labeled_window,
            label,
            self._source_name(),
            subject_id,
            session_id,
            self._start_time,
            sample_timestamps=labeled_timestamps,
            channel_names=channel_names,
            original_channel_names=original_channel_names,
            channel_units=channel_units,
            paradigm_type=paradigm_type,
            log_recognition=paradigm_type == "SSVEP",
            trial_metadata=trial_metadata,
            clock_source=self.experiment_clock.source,
        )
        if paradigm_type == "SSVEP":
            self._recognition_received(result)
        self._trial_saved(label, saved["raw_trial_path"])
        with self._state_lock:
            if self._capture_generation == generation:
                self._pending_true_label = ""
                self._active_metadata = {}
                self._active_capture_seconds = None
                self._active_analysis_offset_seconds = 0.0
                self._active_stimulus_lsl_time = 0.0
        self._reset_buffer()

    def _reset_buffer(self) -> None:
        self._buffer = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._active_capture_generation = -1
        self._start_time = ""


__all__ = ["LabeledTrialRecorder", "TrialCaptureRequest"]
