"""Independent LSL-to-CSV recorder used by the research LSL collection page."""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from src.domain import EEGFrame
from src.ports import EEGStream
from src.services.raw_eeg_csv_recorder import RawEEGCsvRecorder


class LSLRecorderWorker(QThread):
    frame_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    recording_stopped = Signal(str, int)

    def __init__(
        self,
        stream: EEGStream,
        recorder: RawEEGCsvRecorder,
        session_id: str,
        subject_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stream = stream
        self.recorder = recorder
        self.session_id = session_id
        self.subject_id = subject_id
        self.setObjectName("LSL-Raw-CSV-Recorder")

    def stop(self) -> None:
        self.requestInterruption()
        if not self.wait(2000):
            try:
                self.stream.disconnect()
            except Exception:
                pass
            self.wait(1000)

    def run(self) -> None:
        reason = "用户停止"
        last_data_at = time.monotonic()
        warned = False
        sample_count = 0
        try:
            self.stream.connect()
            source = str(getattr(self.stream, "source_name", "LSL EEG"))
            self.recorder.start(self.session_id, self.subject_id, source)
            self.status_changed.emit(f"已连接 · {source}")
            while not self.isInterruptionRequested():
                try:
                    frame: EEGFrame = self.stream.read_frame()
                except TimeoutError:
                    silent_for = time.monotonic() - last_data_at
                    if silent_for >= 10.0:
                        reason = "连续10秒无数据，已自动停止"
                        self.error_occurred.emit(reason)
                        break
                    if silent_for >= 2.0 and not warned:
                        warned = True
                        self.status_changed.emit("警告：连续2秒未收到 LSL EEG 数据")
                    continue
                last_data_at = time.monotonic()
                warned = False
                sample_count += self.recorder.append(frame)
                self.frame_received.emit(frame)
        except Exception as exc:
            reason = f"采集失败：{exc}"
            self.error_occurred.emit(str(exc))
        finally:
            try:
                self.stream.disconnect()
            except Exception:
                pass
            path = self.recorder.stop()
            self.recording_stopped.emit(str(path or ""), sample_count)
            self.status_changed.emit(reason)


__all__ = ["LSLRecorderWorker"]
