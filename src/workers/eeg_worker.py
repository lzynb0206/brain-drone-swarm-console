"""QThread worker for continuous EEG acquisition, windowing and decoding."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

import numpy as np
from PySide6.QtCore import QThread, Signal

from src.bci.config import DECISION_STEP_SECONDS, WINDOW_SECONDS
from src.bci.preprocessing import assess_signal_quality
from src.domain import EEGFrame, IntentResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder
from src.services.experiment_timing import ExperimentClock, ExperimentMarkerOutlet, ExperimentTimestamp


@dataclass(frozen=True)
class _TrialCaptureRequest:
    label: str
    frequency_hz: float
    marker: ExperimentTimestamp
    duration_seconds: float | None
    analysis_offset_seconds: float
    metadata: dict[str, object]


class EEGWorker(QThread):
    """Read frames and decode sliding windows outside the Qt UI thread."""

    frame_received = Signal(object)
    recognition_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    labeled_trial_saved = Signal(str, str)
    continuous_chunk_saved = Signal(str)

    def __init__(
        self,
        stream: EEGStream,
        decoder: IntentDecoder,
        recorder: ExperimentRecorder,
        window_seconds: float = WINDOW_SECONDS,
        decision_step_seconds: float = DECISION_STEP_SECONDS,
        experiment_clock: ExperimentClock | None = None,
        marker_outlet: ExperimentMarkerOutlet | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stream = stream
        self.decoder = decoder
        self.recorder = recorder
        self.window_seconds = float(window_seconds)
        self.decision_step_seconds = float(decision_step_seconds)
        self.experiment_clock = experiment_clock or ExperimentClock()
        self.marker_outlet = marker_outlet or ExperimentMarkerOutlet()
        self._state_lock = threading.Lock()
        self._pending_true_label = ""
        self._pending_stimulus: _TrialCaptureRequest | None = None
        self._capture_generation = 0
        self._active_capture_generation = -1
        self._active_trial_metadata: dict[str, object] = {}
        self._active_capture_seconds: float | None = None
        self._active_analysis_offset_seconds = 0.0
        self._active_stimulus_lsl_time = 0.0
        self._analysis_marker_generation = -1
        self._labeled_buffer: np.ndarray | None = None
        self._labeled_timestamps = np.asarray([], dtype=np.float64)
        self._labeled_start_time = ""
        self._subject_id = ""
        self._session_id = ""
        self._continuous_enabled = False
        self._continuous_buffer: np.ndarray | None = None
        self._continuous_timestamps = np.asarray([], dtype=np.float64)
        self._continuous_started_at = ""
        self._continuous_chunk_index = 0
        self._continuous_chunk_seconds = 10.0
        self._channel_names: tuple[str, ...] = ()
        self._original_channel_names: tuple[str, ...] = ()
        self._channel_units: tuple[str, ...] = ()
        self._paradigm_type = "SSVEP"
        self._pending_online_decode_reset = ""
        self.setObjectName("EEG-Acquisition-Decode")

    @property
    def source_name(self) -> str:
        return str(getattr(self.stream, "source_name", type(self.stream).__name__))

    def set_experiment_context(self, subject_id: str, session_id: str) -> None:
        with self._state_lock:
            self._subject_id = str(subject_id)
            self._session_id = str(session_id)

    def capture_next_trial(self, true_label: str) -> None:
        with self._state_lock:
            self._pending_true_label = str(true_label)
            self._active_trial_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
            self._capture_generation += 1

    def capture_stimulus_trial(self, true_label: str, frequency_hz: float, marker_timestamp: float) -> None:
        """Backward-compatible three-argument capture used by the manual window."""

        self.capture_experiment_trial(
            true_label,
            frequency_hz,
            marker_timestamp,
            duration_seconds=self.window_seconds if true_label else None,
        )

    def capture_experiment_trial(
        self,
        true_label: str,
        frequency_hz: float,
        marker_timestamp: float | None = None,
        duration_seconds: float | None = None,
        discard_seconds: float = 0.0,
        metadata: Mapping[str, object] | None = None,
    ) -> str:
        """Queue one labelled raw trial and return its stable trial ID.

        ``duration_seconds`` is the total raw interval saved from stimulus
        onset.  ``discard_seconds`` only marks the later analysis start; the
        onset samples remain in the NPZ so different latency/window choices
        can be evaluated offline.
        """

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
            self._pending_stimulus = _TrialCaptureRequest(
                label=label,
                frequency_hz=float(frequency_hz),
                marker=stamp,
                duration_seconds=duration,
                analysis_offset_seconds=offset,
                metadata=context,
            )
        return trial_id

    def record_stimulus_marker(self, name: str, timestamp: float) -> None:
        self.record_experiment_marker(name, timestamp)

    def record_experiment_marker(
        self,
        name: str,
        timestamp: float | None = None,
        metadata: Mapping[str, object] | None = None,
        timestamp_domain: str = "auto",
    ) -> dict[str, object]:
        """Record and best-effort publish one marker in LSL clock time."""

        stamp = self.experiment_clock.stamp(timestamp, timestamp_domain)
        subject_id, session_id = self._experiment_context()
        context = dict(metadata or {})
        self.recorder.record_marker(
            name,
            stamp.lsl_seconds,
            subject_id,
            session_id,
            context,
            unix_timestamp=stamp.unix_seconds,
            clock_source=stamp.clock_source,
        )
        published = self.marker_outlet.publish(name, stamp.lsl_seconds, {
            **context,
            "subject_id": subject_id,
            "session_id": session_id,
            "unix_timestamp": stamp.unix_seconds,
            "clock_source": stamp.clock_source,
        })
        return {**stamp.as_dict(), "name": str(name), "published_to_lsl": published}

    def record_trial_phase(
        self,
        phase: str,
        timestamp: float | None = None,
        metadata: Mapping[str, object] | None = None,
        timestamp_domain: str = "auto",
    ) -> dict[str, object]:
        normalized = str(phase).strip().lower()
        if normalized not in self.recorder.PHASE_NAMES:
            raise ValueError(f"unsupported experiment phase: {phase!r}")
        context = dict(metadata or {})
        context["phase"] = normalized
        return self.record_experiment_marker(
            f"trial_{normalized}", timestamp, context, timestamp_domain,
        )

    def cancel_experiment_trial(self, reason: str = "aborted") -> None:
        """Cancel pending/active capture so an aborted UI cannot save stale EEG."""

        with self._state_lock:
            request = self._pending_stimulus
            context = dict(
                request.metadata if request is not None else self._active_trial_metadata
            )
            self._pending_stimulus = None
            self._pending_true_label = ""
            self._capture_generation += 1
            self._active_trial_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
        context["reason"] = str(reason)
        self.record_experiment_marker("trial_cancelled", metadata=context)

    def set_continuous_recording(self, enabled: bool) -> None:
        with self._state_lock:
            self._continuous_enabled = bool(enabled)

    def set_paradigm_type(self, paradigm_type: str) -> None:
        with self._state_lock:
            self._paradigm_type = str(paradigm_type or "SSVEP")

    def reset_online_decoding(self, reason: str = "开始采集新的完整窗口") -> None:
        """Request a thread-safe rolling-window and decoder-state reset."""

        with self._state_lock:
            self._pending_online_decode_reset = str(reason).strip() or "在线解码重置"

    def pending_true_label(self) -> str:
        with self._state_lock:
            return self._pending_true_label

    def stop(self) -> None:
        self.requestInterruption()
        # Let the normal acquisition loop flush the final partial recording.
        # Force-close only if a transport remains blocked beyond its read timeout.
        if not self.wait(2000):
            try:
                self.stream.disconnect()
            except Exception:
                pass
            self.wait(1000)
        self.marker_outlet.stop()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                self.stream.connect()
                self.status_changed.emit(f"已连接 · {self.source_name}")
                self._acquire_connected_stream()
            except Exception as exc:
                if self.isInterruptionRequested():
                    break
                self.error_occurred.emit(str(exc))
                self.status_changed.emit("连接中断，1秒后重试")
                self.msleep(1000)
            finally:
                try:
                    self.stream.disconnect()
                except Exception:
                    pass
        self.marker_outlet.stop()
        self.status_changed.emit("已停止")

    def _acquire_connected_stream(self) -> None:
        buffer: np.ndarray | None = None
        buffer_timestamps = np.asarray([], dtype=np.float64)
        samples_since_decision = 0
        last_data_at = time.monotonic()
        no_data_warning_emitted = False
        while not self.isInterruptionRequested():
            buffer, buffer_timestamps, samples_since_decision = self._apply_online_decode_reset(
                buffer, buffer_timestamps, samples_since_decision,
            )
            buffer, buffer_timestamps, samples_since_decision = self._apply_pending_stimulus(
                buffer, buffer_timestamps, samples_since_decision,
            )
            try:
                frame: EEGFrame = self.stream.read_frame()
            except TimeoutError:
                silent_for = time.monotonic() - last_data_at
                if silent_for >= 10.0:
                    self.error_occurred.emit("连续10秒未收到EEG数据，采集已自动停止")
                    self.requestInterruption()
                    break
                if silent_for >= 2.0 and not no_data_warning_emitted:
                    no_data_warning_emitted = True
                    self.status_changed.emit("警告：连续2秒未收到EEG数据，CSV保持打开")
                continue
            # A phase transition can arrive while pull_chunk/read_frame is
            # blocked.  Apply it before processing the frame so the first
            # samples at/after the stimulus marker are not silently lost.
            buffer, buffer_timestamps, samples_since_decision = self._apply_pending_stimulus(
                buffer, buffer_timestamps, samples_since_decision,
            )
            buffer, buffer_timestamps, samples_since_decision = self._apply_online_decode_reset(
                buffer, buffer_timestamps, samples_since_decision,
            )
            last_data_at = time.monotonic()
            no_data_warning_emitted = False
            self.frame_received.emit(frame)
            values = np.asarray(frame.samples, dtype=float)
            if values.ndim != 2 or values.shape[1] == 0:
                continue
            frame_timestamps = self._frame_timestamps(frame, values.shape[1])
            self._channel_names = tuple(frame.channel_names)
            self._original_channel_names = tuple(
                frame.original_channel_names or frame.channel_names
            )
            self._channel_units = tuple(frame.channel_units)
            if buffer is None or buffer.shape[0] != values.shape[0]:
                buffer = values
                buffer_timestamps = frame_timestamps.copy()
                samples_since_decision = values.shape[1]
            else:
                buffer = np.concatenate((buffer, values), axis=1)
                buffer_timestamps = np.concatenate((buffer_timestamps, frame_timestamps))
                samples_since_decision += values.shape[1]
            window_samples = max(100, round(frame.sample_rate_hz * self.window_seconds))
            with self._state_lock:
                paradigm_type = self._paradigm_type
                capture_seconds = self._active_capture_seconds
            labeled_window_samples = max(
                100,
                round(frame.sample_rate_hz * (
                    capture_seconds
                    if capture_seconds is not None else
                    4.0 if paradigm_type != "SSVEP" else self.window_seconds
                )),
            )
            self._collect_continuous(values, frame_timestamps, frame.sample_rate_hz)
            self._collect_labeled_trial(
                values, frame_timestamps, frame.sample_rate_hz, labeled_window_samples,
            )
            if buffer.shape[1] > window_samples:
                buffer = buffer[:, -window_samples:]
                buffer_timestamps = buffer_timestamps[-window_samples:]
            if paradigm_type != "SSVEP":
                continue
            step_samples = max(1, round(frame.sample_rate_hz * self.decision_step_seconds))
            if buffer.shape[1] < window_samples or samples_since_decision < step_samples:
                continue
            samples_since_decision = 0
            window = buffer[:, -window_samples:].copy()
            window_timestamps = buffer_timestamps[-window_samples:].copy()
            try:
                result: IntentResult = self.decoder.decode(window, frame.sample_rate_hz)
                subject_id, session_id = self._experiment_context()
                start_time = (datetime.now().astimezone() - timedelta(seconds=self.window_seconds)).isoformat(
                    timespec="milliseconds"
                )
                self.recorder.record(
                    result, window, source=self.source_name, subject_id=subject_id,
                    session_id=session_id, start_time=start_time,
                    sample_timestamps=window_timestamps,
                    channel_names=self._channel_names,
                    original_channel_names=self._original_channel_names,
                    channel_units=self._channel_units,
                    clock_source=self.experiment_clock.source,
                )
                self.recognition_received.emit(result)
            except Exception as exc:
                self.error_occurred.emit(f"EEG解码/保存失败：{exc}")
        self._flush_continuous(0.0)

    def _apply_online_decode_reset(
        self,
        buffer: np.ndarray | None,
        buffer_timestamps: np.ndarray,
        samples_since_decision: int,
    ) -> tuple[np.ndarray | None, np.ndarray, int]:
        with self._state_lock:
            reason = self._pending_online_decode_reset
            self._pending_online_decode_reset = ""
        if not reason:
            return buffer, buffer_timestamps, samples_since_decision
        reset_decoder = getattr(self.decoder, "reset", None)
        if callable(reset_decoder):
            reset_decoder()
        self.status_changed.emit(f"{reason}：旧 EEG 缓存已清空")
        return None, np.asarray([], dtype=np.float64), 0

    def _apply_pending_stimulus(
        self,
        buffer: np.ndarray | None,
        buffer_timestamps: np.ndarray,
        samples_since_decision: int,
    ) -> tuple[np.ndarray | None, np.ndarray, int]:
        with self._state_lock:
            request = self._pending_stimulus
            self._pending_stimulus = None
        if request is None:
            return buffer, buffer_timestamps, samples_since_decision
        label = request.label
        discarded = 0
        flush = getattr(self.stream, "flush", None)
        # Labelled trials are aligned from their sample timestamps.  Flushing
        # here could throw away valid post-onset EEG while this worker was
        # blocked in read_frame.  Operational control still needs an explicit
        # reset because it does not persist an onset-aligned raw trial.
        if callable(flush) and not label:
            try:
                discarded = int(flush() or 0)
            except Exception as exc:
                self.error_occurred.emit(f"清空 LSL 历史缓存失败：{exc}")
        with self._state_lock:
            self._pending_true_label = label
            self._active_trial_metadata = dict(request.metadata)
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
            "true_label": label,
            "frequency_hz": request.frequency_hz,
            "discarded_samples": discarded,
            "analysis_start_offset_seconds": request.analysis_offset_seconds,
            "capture_duration_seconds": request.duration_seconds,
            "mode": "experiment" if label else "control",
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
            "stimulus_capture_started", request.marker.lsl_seconds, marker_metadata,
        )
        if label:
            message = f"刺激同步采集：{label}，已按 LSL 时间戳对齐（保留起始原始段）"
        else:
            message = f"在线脑控已同步，已清空 {discarded} 个历史样本并重置解码器"
        self.status_changed.emit(message)
        return None, np.asarray([], dtype=np.float64), 0

    def _collect_continuous(
        self, values: np.ndarray, timestamps: np.ndarray, sample_rate_hz: float,
    ) -> None:
        with self._state_lock:
            enabled = self._continuous_enabled
        if not enabled:
            self._flush_continuous(sample_rate_hz)
            return
        if self._continuous_buffer is None or self._continuous_buffer.shape[0] != values.shape[0]:
            self._continuous_buffer = values.copy()
            self._continuous_timestamps = timestamps.copy()
            self._continuous_started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        else:
            self._continuous_buffer = np.concatenate((self._continuous_buffer, values), axis=1)
            self._continuous_timestamps = np.concatenate((self._continuous_timestamps, timestamps))
        threshold = max(1, round(sample_rate_hz * self._continuous_chunk_seconds))
        while self._continuous_buffer is not None and self._continuous_buffer.shape[1] >= threshold:
            chunk = self._continuous_buffer[:, :threshold].copy()
            chunk_timestamps = self._continuous_timestamps[:threshold].copy()
            self._continuous_buffer = self._continuous_buffer[:, threshold:]
            self._continuous_timestamps = self._continuous_timestamps[threshold:]
            self._save_continuous_chunk(chunk, chunk_timestamps, sample_rate_hz)
            self._continuous_started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _flush_continuous(self, sample_rate_hz: float) -> None:
        if self._continuous_buffer is None or self._continuous_buffer.shape[1] == 0:
            self._continuous_buffer = None
            self._continuous_timestamps = np.asarray([], dtype=np.float64)
            return
        fs = float(sample_rate_hz or getattr(self.stream, "sample_rate_hz", 0.0) or 0.0)
        if fs > 0:
            self._save_continuous_chunk(
                self._continuous_buffer.copy(), self._continuous_timestamps.copy(), fs,
            )
        self._continuous_buffer = None
        self._continuous_timestamps = np.asarray([], dtype=np.float64)
        self._continuous_started_at = ""

    def _save_continuous_chunk(
        self, chunk: np.ndarray, timestamps: np.ndarray, sample_rate_hz: float,
    ) -> None:
        subject_id, session_id = self._experiment_context()
        path = self.recorder.record_continuous_chunk(
            chunk,
            sample_rate_hz,
            self.source_name,
            subject_id,
            session_id,
            self._continuous_chunk_index,
            self._continuous_started_at,
            sample_timestamps=timestamps,
            channel_names=self._channel_names,
            original_channel_names=self._original_channel_names,
            channel_units=self._channel_units,
        )
        self._continuous_chunk_index += 1
        self.continuous_chunk_saved.emit(path)

    def _collect_labeled_trial(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
        window_samples: int,
    ) -> None:
        with self._state_lock:
            label = self._pending_true_label
            generation = self._capture_generation
            trial_metadata = dict(self._active_trial_metadata)
            analysis_offset = self._active_analysis_offset_seconds
            stimulus_lsl_time = self._active_stimulus_lsl_time
        if not label:
            self._reset_labeled_capture()
            return
        if timestamps.size and stimulus_lsl_time > 0.0:
            distance = abs(float(timestamps[-1]) - stimulus_lsl_time)
            if distance <= 3600.0:
                if float(timestamps[-1]) < stimulus_lsl_time:
                    return
                keep = timestamps >= stimulus_lsl_time
                values = values[:, keep]
                timestamps = timestamps[keep]
                if values.shape[1] == 0:
                    return
            else:
                # Legacy/offline producers may not share the LSL clock.  Keep
                # them usable, but make the downgrade explicit in metadata.
                trial_metadata.setdefault("sample_clock_alignment", "unverified_fallback")
                with self._state_lock:
                    if self._capture_generation == generation:
                        self._active_trial_metadata.setdefault(
                            "sample_clock_alignment", "unverified_fallback",
                        )
        if generation != self._active_capture_generation:
            self._active_capture_generation = generation
            self._labeled_buffer = values.copy()
            self._labeled_timestamps = timestamps.copy()
            first_lsl = float(timestamps[0]) if timestamps.size else self.experiment_clock.now().lsl_seconds
            first_unix = self.experiment_clock.to_unix(first_lsl, "lsl")
            self._labeled_start_time = datetime.fromtimestamp(first_unix).astimezone().isoformat(
                timespec="milliseconds"
            )
        elif self._labeled_buffer is None or self._labeled_buffer.shape[0] != values.shape[0]:
            self._labeled_buffer = values.copy()
            self._labeled_timestamps = timestamps.copy()
            first_lsl = float(timestamps[0]) if timestamps.size else self.experiment_clock.now().lsl_seconds
            first_unix = self.experiment_clock.to_unix(first_lsl, "lsl")
            self._labeled_start_time = datetime.fromtimestamp(first_unix).astimezone().isoformat(
                timespec="milliseconds"
            )
        else:
            self._labeled_buffer = np.concatenate((self._labeled_buffer, values), axis=1)
            self._labeled_timestamps = np.concatenate((self._labeled_timestamps, timestamps))
        if trial_metadata.get("trial_id") and generation != self._analysis_marker_generation:
            analysis_target = stimulus_lsl_time + analysis_offset
            eligible = self._labeled_timestamps[self._labeled_timestamps >= analysis_target]
            if eligible.size:
                analysis_time = float(eligible[0])
                trial_metadata["analysis_lsl_time"] = analysis_time
                with self._state_lock:
                    if self._capture_generation == generation:
                        self._active_trial_metadata["analysis_lsl_time"] = analysis_time
                self.record_trial_phase(
                    "analysis",
                    analysis_time,
                    trial_metadata,
                    timestamp_domain="lsl",
                )
                self._analysis_marker_generation = generation
        if self._labeled_buffer.shape[1] < window_samples:
            return

        labeled_window = self._labeled_buffer[:, :window_samples].copy()
        labeled_timestamps = self._labeled_timestamps[:window_samples].copy()
        with self._state_lock:
            paradigm_type = self._paradigm_type
        if paradigm_type == "SSVEP":
            result: IntentResult = self.decoder.decode(labeled_window, sample_rate_hz)
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
        saved = self.recorder.record(
            result, labeled_window, label, self.source_name, subject_id, session_id,
            self._labeled_start_time,
            sample_timestamps=labeled_timestamps,
            channel_names=self._channel_names,
            original_channel_names=self._original_channel_names,
            channel_units=self._channel_units,
            paradigm_type=paradigm_type,
            log_recognition=paradigm_type == "SSVEP",
            trial_metadata=trial_metadata,
            clock_source=self.experiment_clock.source,
        )
        if paradigm_type == "SSVEP":
            self.recognition_received.emit(result)
        self.labeled_trial_saved.emit(label, saved["raw_trial_path"])
        with self._state_lock:
            if self._capture_generation == generation:
                self._pending_true_label = ""
                self._active_trial_metadata = {}
                self._active_capture_seconds = None
                self._active_analysis_offset_seconds = 0.0
                self._active_stimulus_lsl_time = 0.0
        self._reset_labeled_capture()

    def _experiment_context(self) -> tuple[str, str]:
        with self._state_lock:
            return self._subject_id, self._session_id

    def _reset_labeled_capture(self) -> None:
        self._labeled_buffer = None
        self._labeled_timestamps = np.asarray([], dtype=np.float64)
        self._active_capture_generation = -1
        self._labeled_start_time = ""

    def _frame_timestamps(self, frame: EEGFrame, sample_count: int) -> np.ndarray:
        """Use source timestamps, or synthesize an aligned sequence for legacy streams."""

        if len(frame.sample_timestamps) == sample_count:
            timestamps = np.asarray(frame.sample_timestamps, dtype=np.float64)
            if timestamps.size and timestamps[-1] >= self.experiment_clock.UNIX_THRESHOLD_SECONDS:
                converted_end = self.experiment_clock.to_lsl(float(timestamps[-1]), "unix")
                timestamps = timestamps + (converted_end - float(timestamps[-1]))
            return timestamps
        fs = float(frame.sample_rate_hz or 1.0)
        end = self.experiment_clock.to_lsl(float(frame.timestamp), "auto")
        start = end - (sample_count - 1) / fs
        return start + np.arange(sample_count, dtype=np.float64) / fs
