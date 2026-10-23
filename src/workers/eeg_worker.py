"""QThread worker for continuous EEG acquisition, windowing and decoding."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Mapping

import numpy as np
from PySide6.QtCore import QThread, Signal

from src.bci.config import DECISION_STEP_SECONDS, WINDOW_SECONDS
from src.domain import EEGFrame, IntentResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder
from src.services.experiment_timing import ExperimentClock, ExperimentMarkerOutlet
from src.workers.continuous_eeg_recorder import ContinuousEEGRecorder
from src.workers.labeled_trial_recorder import LabeledTrialRecorder


NUMERICAL_THREAD_STACK_BYTES = 8 * 1024 * 1024


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
        # macOS gives a Qt-created worker thread a much smaller stack than the
        # main Python thread.  NumPy's bundled OpenBLAS can exceed that default
        # while SciPy filtering/FBCCA enters LAPACK, terminating the process
        # with SIGBUS instead of raising a catchable Python exception.
        self.setStackSize(NUMERICAL_THREAD_STACK_BYTES)
        self.stream = stream
        self.decoder = decoder
        self.recorder = recorder
        self._online_recorder = recorder
        self.window_seconds = float(window_seconds)
        self.decision_step_seconds = float(decision_step_seconds)
        self.experiment_clock = experiment_clock or ExperimentClock()
        self.marker_outlet = marker_outlet or ExperimentMarkerOutlet()
        self._state_lock = threading.Lock()
        self._subject_id = ""
        self._session_id = ""
        self._continuous_recorder = ContinuousEEGRecorder(
            recorder,
            self.continuous_chunk_saved.emit,
        )
        self._session_continuous_recorder: ContinuousEEGRecorder | None = None
        self._channel_names: tuple[str, ...] = ()
        self._original_channel_names: tuple[str, ...] = ()
        self._channel_units: tuple[str, ...] = ()
        self._labeled_trial_recorder = LabeledTrialRecorder(
            recorder,
            decoder,
            self.experiment_clock,
            self.marker_outlet,
            source_name=lambda: self.source_name,
            experiment_context=self._experiment_context,
            channel_metadata=self._channel_metadata,
            status_changed=self.status_changed.emit,
            error_occurred=self.error_occurred.emit,
            recognition_received=self.recognition_received.emit,
            trial_saved=self.labeled_trial_saved.emit,
            record_phase=self.record_trial_phase,
        )
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
        self._labeled_trial_recorder.capture_next(true_label)

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

        return self._labeled_trial_recorder.queue_capture(
            true_label,
            frequency_hz,
            marker_timestamp,
            duration_seconds,
            discard_seconds,
            metadata,
        )

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
        self._current_online_recorder().record_marker(
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

        context = self._labeled_trial_recorder.cancel()
        context["reason"] = str(reason)
        self.record_experiment_marker("trial_cancelled", metadata=context)

    def set_continuous_recording(self, enabled: bool) -> None:
        self._continuous_recorder.set_enabled(enabled)

    def begin_session_recording(self, recorder: ExperimentRecorder) -> None:
        """Route online logs and a raw EEG copy to a purpose-specific folder."""

        with self._state_lock:
            previous = self._session_continuous_recorder
            self._online_recorder = recorder
            self._session_continuous_recorder = ContinuousEEGRecorder(
                recorder,
                self.continuous_chunk_saved.emit,
            )
            self._session_continuous_recorder.set_enabled(True)
        if previous is not None:
            previous.set_enabled(False)
            previous.flush()

    def end_session_recording(self) -> None:
        """Finish the purpose-specific raw copy and restore the worker recorder."""

        with self._state_lock:
            session_recorder = self._session_continuous_recorder
            self._session_continuous_recorder = None
            self._online_recorder = self.recorder
        if session_recorder is not None:
            session_recorder.set_enabled(False)
            session_recorder.flush()

    def set_paradigm_type(self, paradigm_type: str) -> None:
        self._labeled_trial_recorder.set_paradigm_type(paradigm_type)

    def reset_online_decoding(self, reason: str = "开始采集新的完整窗口") -> None:
        """Request a thread-safe rolling-window and decoder-state reset."""

        with self._state_lock:
            self._pending_online_decode_reset = str(reason).strip() or "在线解码重置"

    def pending_true_label(self) -> str:
        return self._labeled_trial_recorder.pending_true_label()

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
            paradigm_type = self._labeled_trial_recorder.paradigm_type
            capture_seconds = self._labeled_trial_recorder.capture_seconds
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
                self._current_online_recorder().record(
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
        if not self._labeled_trial_recorder.apply_pending(self.stream):
            return buffer, buffer_timestamps, samples_since_decision
        return None, np.asarray([], dtype=np.float64), 0

    def _collect_continuous(
        self, values: np.ndarray, timestamps: np.ndarray, sample_rate_hz: float,
    ) -> None:
        subject_id, session_id = self._experiment_context()
        self._continuous_recorder.append(
            values,
            timestamps,
            sample_rate_hz,
            source=self.source_name,
            subject_id=subject_id,
            session_id=session_id,
            channel_names=self._channel_names,
            original_channel_names=self._original_channel_names,
            channel_units=self._channel_units,
        )
        with self._state_lock:
            session_recorder = self._session_continuous_recorder
        if session_recorder is not None:
            session_recorder.append(
                values,
                timestamps,
                sample_rate_hz,
                source=self.source_name,
                subject_id=subject_id,
                session_id=session_id,
                channel_names=self._channel_names,
                original_channel_names=self._original_channel_names,
                channel_units=self._channel_units,
            )

    def _flush_continuous(self, sample_rate_hz: float) -> None:
        fallback_rate = float(
            sample_rate_hz or getattr(self.stream, "sample_rate_hz", 0.0) or 0.0
        )
        self._continuous_recorder.flush(fallback_rate)
        with self._state_lock:
            session_recorder = self._session_continuous_recorder
        if session_recorder is not None:
            session_recorder.flush(fallback_rate)

    def _current_online_recorder(self) -> ExperimentRecorder:
        with self._state_lock:
            return self._online_recorder

    def _collect_labeled_trial(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
        window_samples: int,
    ) -> None:
        self._labeled_trial_recorder.collect(
            values,
            timestamps,
            sample_rate_hz,
            window_samples,
        )

    def _experiment_context(self) -> tuple[str, str]:
        with self._state_lock:
            return self._subject_id, self._session_id

    def _channel_metadata(
        self,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        return self._channel_names, self._original_channel_names, self._channel_units

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
