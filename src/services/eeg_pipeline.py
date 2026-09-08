"""Background acquisition, windowing, FBCCA inference and persistence."""

from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from src.domain import EEGFrame, RecognitionResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder


class EEGPipelineWorker(QThread):
    frame_received = Signal(object)
    recognition_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    labeled_trial_saved = Signal(str, str)

    def __init__(
        self,
        stream: EEGStream,
        decoder: IntentDecoder,
        recorder: ExperimentRecorder,
        window_seconds: float = 4.0,
        decision_step_seconds: float = 1.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stream = stream
        self.decoder = decoder
        self.recorder = recorder
        self.window_seconds = float(window_seconds)
        self.decision_step_seconds = float(decision_step_seconds)
        self._label_lock = threading.Lock()
        self._pending_true_label = ""
        self._capture_generation = 0
        self._active_capture_generation = -1
        self._labeled_buffer: np.ndarray | None = None
        self.setObjectName("EEG-Acquisition-FBCCA")

    @property
    def source_name(self) -> str:
        return str(getattr(self.stream, "source_name", type(self.stream).__name__))

    def capture_next_trial(self, true_label: str) -> None:
        with self._label_lock:
            self._pending_true_label = str(true_label)
            self._capture_generation += 1

    def pending_true_label(self) -> str:
        with self._label_lock:
            return self._pending_true_label

    def stop(self) -> None:
        self.requestInterruption()
        try:
            self.stream.disconnect()
        except Exception:
            pass
        self.wait(2500)

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
        while not self.isInterruptionRequested():
            try:
                frame: EEGFrame = self.stream.read_frame()
            except TimeoutError:
                continue
            self.frame_received.emit(frame)
            values = np.asarray(frame.samples, dtype=float)
            if values.ndim != 2 or values.shape[1] == 0:
                continue
            if buffer is None or buffer.shape[0] != values.shape[0]:
                buffer = values
                samples_since_decision = values.shape[1]
            else:
                buffer = np.concatenate((buffer, values), axis=1)
                samples_since_decision += values.shape[1]
            window_samples = max(100, round(frame.sample_rate_hz * self.window_seconds))
            self._collect_labeled_trial(values, frame.sample_rate_hz, window_samples)
            if buffer.shape[1] > window_samples:
                buffer = buffer[:, -window_samples:]
            step_samples = max(1, round(frame.sample_rate_hz * self.decision_step_seconds))
            if buffer.shape[1] < window_samples or samples_since_decision < step_samples:
                continue
            samples_since_decision = 0
            window = buffer[:, -window_samples:].copy()
            try:
                result: RecognitionResult = self.decoder.decode(window, frame.sample_rate_hz)
                self.recorder.record(result, window, source=self.source_name)
                self.recognition_received.emit(result)
            except Exception as exc:
                self.error_occurred.emit(f"FBCCA/保存失败：{exc}")

    def _collect_labeled_trial(self, values: np.ndarray, sample_rate_hz: float, window_samples: int) -> None:
        with self._label_lock:
            label = self._pending_true_label
            generation = self._capture_generation
        if not label:
            self._labeled_buffer = None
            self._active_capture_generation = -1
            return
        if generation != self._active_capture_generation:
            self._active_capture_generation = generation
            self._labeled_buffer = values.copy()
        elif self._labeled_buffer is None or self._labeled_buffer.shape[0] != values.shape[0]:
            self._labeled_buffer = values.copy()
        else:
            self._labeled_buffer = np.concatenate((self._labeled_buffer, values), axis=1)
        if self._labeled_buffer.shape[1] < window_samples:
            return

        labeled_window = self._labeled_buffer[:, :window_samples].copy()
        result: RecognitionResult = self.decoder.decode(labeled_window, sample_rate_hz)
        saved = self.recorder.record(result, labeled_window, label, self.source_name)
        self.recognition_received.emit(result)
        self.labeled_trial_saved.emit(label, saved["raw_trial_path"])
        with self._label_lock:
            if self._capture_generation == generation:
                self._pending_true_label = ""
        self._labeled_buffer = None
        self._active_capture_generation = -1
