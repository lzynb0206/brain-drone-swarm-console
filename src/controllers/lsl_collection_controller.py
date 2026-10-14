"""Independent LSL discovery and raw EEG collection lifecycle."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QWidget

from src.domain import EEGFrame, EEGStreamDescriptor
from src.services import RawEEGCsvRecorder
from src.workers import LSLRecorderWorker


class LSLCollectionController(QObject):
    """Own an LSL receiver, its recorder worker, and presentation-safe state."""

    changed = Signal()
    notification_requested = Signal(str)
    discovery_completed = Signal(object, str)

    def __init__(
        self,
        stream,
        recorder: RawEEGCsvRecorder,
        dialog_parent: QWidget,
    ) -> None:
        super().__init__(dialog_parent)
        self.stream = stream
        self.recorder = recorder
        self.dialog_parent = dialog_parent
        self.worker: LSLRecorderWorker | None = None
        self.history = [deque(maxlen=1250) for _ in range(8)]
        self.channel_names = tuple(f"CH{index + 1}" for index in range(8))
        self.sample_rate = 0.0
        self.status = "未连接 LSL EEG 流"
        self.events: deque[str] = deque(maxlen=20)
        self.waveform_paused = False
        self.discovery_in_progress = False
        self.discovery_completed.connect(self._finish_discovery)

    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def discover(self) -> None:
        discover_streams = getattr(self.stream, "discover_streams", None)
        if not callable(discover_streams):
            self.notification_requested.emit("当前容器没有独立LSL接收器")
            return
        if self.discovery_in_progress:
            self.notification_requested.emit("正在搜索LSL EEG流，请稍候…")
            return
        if self.running:
            self.notification_requested.emit("请先停止LSL收集，再切换数据流")
            return
        self.discovery_in_progress = True
        self.status = "正在搜索 type=EEG 的LSL流…"
        self.changed.emit()

        def run_discovery() -> None:
            try:
                self.discovery_completed.emit(list(discover_streams()), "")
            except Exception as exc:
                self.discovery_completed.emit([], str(exc))

        threading.Thread(
            target=run_discovery,
            name="LSL-Collector-Discovery",
            daemon=True,
        ).start()

    def start(self, session_id: str, subject_id: str) -> None:
        if self.stream is None:
            self.notification_requested.emit("当前容器没有独立LSL接收器")
            return
        if self.running:
            self.notification_requested.emit("LSL收集已经在运行")
            return
        if self.recorder.is_recording:
            self.recorder.stop()
        worker = LSLRecorderWorker(
            self.stream,
            self.recorder,
            session_id,
            subject_id,
            parent=self,
        )
        worker.frame_received.connect(self._on_frame)
        worker.status_changed.connect(self._on_status)
        worker.error_occurred.connect(self._on_error)
        worker.recording_stopped.connect(self._on_stopped)
        self.worker = worker
        self.waveform_paused = False
        self.status = "正在连接LSL EEG流…"
        worker.start()
        self.changed.emit()

    def stop(self) -> None:
        if self.running:
            self.worker.stop()
        elif self.recorder.is_recording:
            self.recorder.stop()
        self.status = "LSL收集已停止"
        self.changed.emit()

    def toggle_waveform(self) -> None:
        self.waveform_paused = not self.waveform_paused
        self.notification_requested.emit(
            "已暂停LSL波形，CSV仍持续保存"
            if self.waveform_paused
            else "已恢复LSL波形"
        )
        self.changed.emit()

    def _finish_discovery(
        self,
        descriptors: list[EEGStreamDescriptor],
        error: str,
    ) -> None:
        self.discovery_in_progress = False
        if error:
            self.status = "LSL搜索失败"
            self.notification_requested.emit(f"LSL搜索失败：{error}")
            self.changed.emit()
            return
        if not descriptors:
            self.status = "未发现LSL EEG流"
            self.notification_requested.emit("未发现流：请在‘采集 → Networking’启动LSL输出")
            self.changed.emit()
            return
        labels = [descriptor.display_name for descriptor in descriptors]
        selected, accepted = QInputDialog.getItem(
            self.dialog_parent,
            "选择LSL EEG数据流",
            "发现的数据流",
            labels,
            0,
            False,
        )
        if not accepted:
            self.changed.emit()
            return
        descriptor = descriptors[labels.index(selected)]
        try:
            self.stream.select_stream(descriptor)
        except Exception as exc:
            self.notification_requested.emit(f"选择LSL流失败：{exc}")
            self.changed.emit()
            return
        self.channel_names = descriptor.channel_names or tuple(
            f"CH{index + 1}" for index in range(descriptor.channel_count)
        )
        history_size = max(1, round((descriptor.nominal_sample_rate_hz or 250.0) * 5))
        self.history = [
            deque(maxlen=history_size) for _ in range(descriptor.channel_count)
        ]
        self.status = f"已选择 {descriptor.display_name}"
        self.events.appendleft(f"{datetime.now():%H:%M:%S}  {self.status}")
        self.changed.emit()

    def _on_frame(self, frame: EEGFrame) -> None:
        if len(self.history) != len(frame.samples):
            self.history = [deque(maxlen=1250) for _ in frame.samples]
        if not self.waveform_paused:
            for history, values in zip(self.history, frame.samples):
                history.extend(float(value) for value in values)
        self.channel_names = tuple(frame.channel_names)
        self.sample_rate = float(frame.sample_rate_hz)
        self.changed.emit()

    def _on_status(self, status: str) -> None:
        self.status = status
        self.events.appendleft(f"{datetime.now():%H:%M:%S}  {status}")
        self.changed.emit()

    def _on_error(self, error: str) -> None:
        self.events.appendleft(f"{datetime.now():%H:%M:%S}  ERROR  {error}")
        self.changed.emit()

    def _on_stopped(self, path: str, sample_count: int) -> None:
        message = f"LSL收集结束：{sample_count}个样本"
        if path:
            message += f" · {path}"
        self.events.appendleft(f"{datetime.now():%H:%M:%S}  {message}")
        self.status = message
        self.changed.emit()


__all__ = ["LSLCollectionController"]
