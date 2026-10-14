"""Background bridge from a physical EEG stream to Lab Streaming Layer."""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from src.domain import EEGFrame
from src.ports import EEGStream
from src.services import LSLEEGOutlet


class EEGLSLBridgeWorker(QThread):
    """Own the Bluetooth device and publish every frame before decoding."""

    status_changed = Signal(str)
    error_occurred = Signal(str)
    first_frame_published = Signal()

    def __init__(
        self,
        stream: EEGStream,
        outlet: LSLEEGOutlet,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stream = stream
        self.outlet = outlet
        self.setObjectName("ExpandableEEG-to-LSL-Bridge")

    def stop(self) -> None:
        self.requestInterruption()
        if not self.wait(2000):
            try:
                self.stream.disconnect()
            except Exception:
                pass
            self.wait(1000)

    def run(self) -> None:
        published_first = False
        last_data_at = time.monotonic()
        warned = False
        try:
            self.stream.connect()
            names = tuple(getattr(self.stream, "channel_names", ())) or tuple(
                f"CH{index + 1}" for index in range(8)
            )
            sample_rate = float(getattr(self.stream, "sample_rate_hz", 250.0) or 250.0)
            self.outlet.start(names, sample_rate, ("uV",) * len(names))
            self.status_changed.emit(
                f"蓝牙采集已连接 · LSL {self.outlet.stream_name} 已启动"
            )
            while not self.isInterruptionRequested():
                try:
                    frame: EEGFrame = self.stream.read_frame()
                except TimeoutError:
                    silent_for = time.monotonic() - last_data_at
                    if silent_for >= 10.0:
                        raise TimeoutError("连续10秒未收到采集盒脑电数据")
                    if silent_for >= 2.0 and not warned:
                        warned = True
                        self.status_changed.emit("警告：连续2秒未收到采集盒脑电数据")
                    continue
                last_data_at = time.monotonic()
                warned = False
                self.outlet.push_frame(frame)
                if not published_first:
                    published_first = True
                    self.first_frame_published.emit()
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(exc))
        finally:
            self.outlet.stop()
            try:
                self.stream.disconnect()
            except Exception:
                pass
            self.status_changed.emit("蓝牙采集与LSL输出已停止")


__all__ = ["EEGLSLBridgeWorker"]
