"""QThread worker for continuous EEG acquisition, windowing and decoding."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import numpy as np
from PySide6.QtCore import QThread, Signal

from src.bci.config import DECISION_STEP_SECONDS, WINDOW_SECONDS
from src.bci.preprocessing import assess_signal_quality
from src.domain import EEGFrame, IntentResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder


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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stream = stream
        self.decoder = decoder
        self.recorder = recorder
        self.window_seconds = float(window_seconds)
        self.decision_step_seconds = float(decision_step_seconds)
        self._state_lock = threading.Lock()
        self._pending_true_label = ""
        self._pending_stimulus: tuple[str, float, float] | None = None
        self._capture_generation = 0
        self._active_capture_generation = -1
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
            self._capture_generation += 1

    def capture_stimulus_trial(self, true_label: str, frequency_hz: float, marker_timestamp: float) -> None:
        """Request a trial that begins only after the acquisition thread flushes LSL history."""

        with self._state_lock:
            self._pending_stimulus = (
                str(true_label), float(frequency_hz), float(marker_timestamp),
            )

    def record_stimulus_marker(self, name: str, timestamp: float) -> None:
        subject_id, session_id = self._experiment_context()
        self.recorder.record_marker(name, timestamp, subject_id, session_id)

    def set_continuous_recording(self, enabled: bool) -> None:
        with self._state_lock:
            self._continuous_enabled = bool(enabled)

    def set_paradigm_type(self, paradigm_type: str) -> None:
        with self._state_lock:
            self._paradigm_type = str(paradigm_type or "SSVEP")

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
        self.status_changed.emit("已停止")

    def _acquire_connected_stream(self) -> None:
        buffer: np.ndarray | None = None
        samples_since_decision = 0
        last_data_at = time.monotonic()
        no_data_warning_emitted = False
        while not self.isInterruptionRequested():
            buffer, samples_since_decision = self._apply_pending_stimulus(buffer, samples_since_decision)
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
                samples_since_decision = values.shape[1]
            else:
                buffer = np.concatenate((buffer, values), axis=1)
                samples_since_decision += values.shape[1]
            window_samples = max(100, round(frame.sample_rate_hz * self.window_seconds))
            with self._state_lock:
                paradigm_type = self._paradigm_type
            labeled_window_samples = (
                max(100, round(frame.sample_rate_hz * 4.0))
                if paradigm_type != "SSVEP"
                else window_samples
            )
            self._collect_continuous(values, frame_timestamps, frame.sample_rate_hz)
            self._collect_labeled_trial(
                values, frame_timestamps, frame.sample_rate_hz, labeled_window_samples,
            )
            if buffer.shape[1] > window_samples:
                buffer = buffer[:, -window_samples:]
            if paradigm_type != "SSVEP":
                continue
            step_samples = max(1, round(frame.sample_rate_hz * self.decision_step_seconds))
            if buffer.shape[1] < window_samples or samples_since_decision < step_samples:
                continue
            samples_since_decision = 0
            window = buffer[:, -window_samples:].copy()
            try:
                result: IntentResult = self.decoder.decode(window, frame.sample_rate_hz)
                subject_id, session_id = self._experiment_context()
                start_time = (datetime.now().astimezone() - timedelta(seconds=self.window_seconds)).isoformat(
                    timespec="milliseconds"
                )
                self.recorder.record(
                    result, window, source=self.source_name, subject_id=subject_id,
                    session_id=session_id, start_time=start_time,
                    channel_names=self._channel_names,
                    original_channel_names=self._original_channel_names,
                    channel_units=self._channel_units,
                )
                self.recognition_received.emit(result)
            except Exception as exc:
                self.error_occurred.emit(f"EEG解码/保存失败：{exc}")
        self._flush_continuous(0.0)

    def _apply_pending_stimulus(
        self, buffer: np.ndarray | None, samples_since_decision: int,
    ) -> tuple[np.ndarray | None, int]:
        with self._state_lock:
            request = self._pending_stimulus
            self._pending_stimulus = None
        if request is None:
            return buffer, samples_since_decision
        label, frequency_hz, marker_timestamp = request
        discarded = 0
        flush = getattr(self.stream, "flush", None)
        if callable(flush):
            try:
                discarded = int(flush() or 0)
            except Exception as exc:
                self.error_occurred.emit(f"清空 LSL 历史缓存失败：{exc}")
        with self._state_lock:
            self._pending_true_label = label
            self._capture_generation += 1
        reset_decoder = getattr(self.decoder, "reset", None)
        if callable(reset_decoder):
            reset_decoder()
        subject_id, session_id = self._experiment_context()
        self.recorder.record_marker(
            "stimulus_capture_started",
            marker_timestamp,
            subject_id,
            session_id,
            {"true_label": label, "frequency_hz": frequency_hz, "discarded_samples": discarded},
        )
        self.status_changed.emit(f"刺激同步采集：{label}，已清空 {discarded} 个历史样本")
        return None, 0

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
        if not label:
            self._reset_labeled_capture()
            return
        if generation != self._active_capture_generation:
            self._active_capture_generation = generation
            self._labeled_buffer = values.copy()
            self._labeled_timestamps = timestamps.copy()
            self._labeled_start_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
        elif self._labeled_buffer is None or self._labeled_buffer.shape[0] != values.shape[0]:
            self._labeled_buffer = values.copy()
            self._labeled_timestamps = timestamps.copy()
            self._labeled_start_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
        else:
            self._labeled_buffer = np.concatenate((self._labeled_buffer, values), axis=1)
            self._labeled_timestamps = np.concatenate((self._labeled_timestamps, timestamps))
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
        )
        if paradigm_type == "SSVEP":
            self.recognition_received.emit(result)
        self.labeled_trial_saved.emit(label, saved["raw_trial_path"])
        with self._state_lock:
            if self._capture_generation == generation:
                self._pending_true_label = ""
        self._reset_labeled_capture()

    def _experiment_context(self) -> tuple[str, str]:
        with self._state_lock:
            return self._subject_id, self._session_id

    def _reset_labeled_capture(self) -> None:
        self._labeled_buffer = None
        self._labeled_timestamps = np.asarray([], dtype=np.float64)
        self._active_capture_generation = -1
        self._labeled_start_time = ""

    @staticmethod
    def _frame_timestamps(frame: EEGFrame, sample_count: int) -> np.ndarray:
        """Use source timestamps, or synthesize an aligned sequence for legacy streams."""

        if len(frame.sample_timestamps) == sample_count:
            return np.asarray(frame.sample_timestamps, dtype=np.float64)
        fs = float(frame.sample_rate_hz or 1.0)
        end = float(frame.timestamp)
        start = end - (sample_count - 1) / fs
        return start + np.arange(sample_count, dtype=np.float64) / fs
