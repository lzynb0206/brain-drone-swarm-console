"""Runtime-selectable EEG source used by the research acquisition center."""

from __future__ import annotations

from pathlib import Path

from src.adapters.real.brainflow_cyton_eeg_stream import (
    BrainFlowCytonEEGStream,
    SerialDeviceDescriptor,
)
from src.adapters.real.csv_eeg_stream import CsvEEGStream
from src.adapters.real.expandable_eeg_serial_stream import ExpandableEEGSerialStream
from src.adapters.real.lsl_eeg_stream import LSLEEGStream
from src.domain import EEGFrame, EEGStreamDescriptor
from src.ports import EEGStream


class SelectableEEGStream:
    """Keep one worker-facing stream while switching CSV, LSL and hardware.

    The worker keeps one stable ``EEGStream`` object while the research UI can
    discover/select OpenBCI streams or the direct Cyton-compatible serial board.
    Selection happens only while disconnected.
    """

    def __init__(
        self,
        initial: EEGStream,
        lsl: LSLEEGStream,
        hardware: BrainFlowCytonEEGStream | ExpandableEEGSerialStream | None = None,
    ) -> None:
        self._active = initial
        self._lsl = lsl
        self._hardware = hardware

    @property
    def source_name(self) -> str:
        return str(getattr(self._active, "source_name", type(self._active).__name__))

    @property
    def sample_rate_hz(self) -> float:
        return float(getattr(self._active, "sample_rate_hz", 0.0) or 0.0)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._active, "connected", False))

    def connect(self) -> None:
        self._active.connect()

    def disconnect(self) -> None:
        self._active.disconnect()

    def read_frame(self) -> EEGFrame:
        return self._active.read_frame()

    def flush(self) -> int:
        flush = getattr(self._active, "flush", None)
        return int(flush() or 0) if callable(flush) else 0

    def discover_streams(self, timeout_seconds: float | None = None) -> list[EEGStreamDescriptor]:
        return self._lsl.discover_streams(timeout_seconds)

    def select_stream(self, descriptor: EEGStreamDescriptor) -> None:
        if self.connected:
            raise RuntimeError("请先断开当前 EEG 数据源，再切换 LSL 流")
        self._lsl.select_stream(descriptor)
        self._active = self._lsl

    def select_lsl(self, source_id: str = "") -> None:
        """Select the configured live LSL source without starting it."""
        if self.connected:
            raise RuntimeError("请先断开当前 EEG 数据源，再切换到实时 LSL")
        self._lsl.require_source_id(source_id)
        self._active = self._lsl

    def create_lsl_receiver(self) -> LSLEEGStream:
        """Create an independent inlet for the dedicated LSL collection page."""
        return LSLEEGStream(
            stream_name=self._lsl.stream_name,
            stream_type=self._lsl.stream_type,
            timeout_seconds=self._lsl.timeout_seconds,
            frame_size=self._lsl.frame_size,
            expected_channel_count=self._lsl.expected_channel_count,
        )

    def discover_hardware_devices(self) -> list[SerialDeviceDescriptor]:
        if self._hardware is None:
            raise RuntimeError("当前容器未配置 OpenBCI/ExpandableEEG 硬件后端")
        return self._hardware.discover_devices()

    def select_hardware(self, device: SerialDeviceDescriptor | str) -> None:
        if self.connected:
            raise RuntimeError("请先断开当前 EEG 数据源，再切换采集设备")
        if self._hardware is None:
            raise RuntimeError("当前容器未配置 OpenBCI/ExpandableEEG 硬件后端")
        port = device.port if isinstance(device, SerialDeviceDescriptor) else str(device)
        self._hardware.configure_serial_port(port)
        self._active = self._hardware

    def configure_hardware_bridge(
        self, device: SerialDeviceDescriptor | str,
    ) -> BrainFlowCytonEEGStream | ExpandableEEGSerialStream:
        """Configure hardware for a Bluetooth-to-LSL publisher.

        The active worker-facing source is left unchanged so the primary EEG
        worker can consume the LSL stream emitted by that publisher.
        """

        if self.connected:
            raise RuntimeError("请先断开当前 EEG 数据源，再启动硬件桥接")
        if self._hardware is None:
            raise RuntimeError("当前容器未配置 OpenBCI/ExpandableEEG 硬件后端")
        port = device.port if isinstance(device, SerialDeviceDescriptor) else str(device)
        self._hardware.configure_serial_port(port)
        return self._hardware

    def select_csv(
        self,
        path: Path | str,
        *,
        sample_rate_hz: float = 250.0,
        frame_size: int = 32,
    ) -> None:
        if self.connected:
            raise RuntimeError("请先断开当前 EEG 数据源，再导入 CSV")
        self._active = CsvEEGStream(
            path,
            sample_rate_hz=sample_rate_hz,
            frame_size=frame_size,
            expected_channel_count=None,
            loop=True,
            realtime=True,
        )

    def playback_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self._active, "playback_snapshot", None)
        if callable(snapshot):
            return dict(snapshot())
        return {
            "backend": (
                "lsl" if self._active is self._lsl
                else "hardware" if self._hardware is not None and self._active is self._hardware
                else "unavailable"
            ),
            "connected": self.connected,
            "source": self.source_name,
            "sample_rate_hz": self.sample_rate_hz,
        }

    def rewind_csv(self) -> None:
        rewind = getattr(self._active, "rewind", None)
        if not callable(rewind):
            raise RuntimeError("当前数据源不是 CSV 回放")
        rewind()

    def seek_csv(self, fraction: float) -> None:
        seek = getattr(self._active, "seek_fraction", None)
        if not callable(seek):
            raise RuntimeError("当前数据源不是 CSV 回放")
        seek(fraction)

    def set_csv_playback_speed(self, speed: float) -> None:
        setter = getattr(self._active, "set_playback_speed", None)
        if not callable(setter):
            raise RuntimeError("当前数据源不是 CSV 回放")
        setter(speed)

    def health_snapshot(self) -> dict[str, object]:
        health = getattr(self._active, "health_snapshot", None)
        if callable(health):
            return dict(health())
        return {
            "connected": self.connected,
            "stream_name": self.source_name,
            "actual_sample_rate_hz": self.sample_rate_hz,
        }
