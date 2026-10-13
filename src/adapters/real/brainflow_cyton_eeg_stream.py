"""Direct ExpandableEEG/OpenBCI Cyton acquisition through BrainFlow."""

from __future__ import annotations

import glob
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from src.bci.config import SSVEP_CHANNEL_NAMES
from src.domain import EEGFrame


@dataclass(frozen=True)
class SerialDeviceDescriptor:
    """One serial device exposed by the operating system."""

    port: str
    description: str = ""
    hardware_id: str = ""

    @property
    def display_name(self) -> str:
        detail = self.description.strip()
        return f"{self.port} · {detail}" if detail and detail != "n/a" else self.port


class BrainFlowCytonEEGStream:
    """Read the manual's 8-channel Cyton-compatible board without OpenBCI GUI.

    ExpandableEEG's Bluetooth mode appears as a serial/COM port.  BrainFlow's
    Cyton board driver consumes that port and returns calibrated EEG values in
    microvolts.  Imports are intentionally lazy so the rest of the application
    can still start on a machine where the hardware dependency is not installed.
    """

    CYTON_BOARD_ID = 0

    def __init__(
        self,
        serial_port: str = "",
        frame_size: int = 32,
        *,
        board_factory: Callable[[int, Any], Any] | None = None,
        params_factory: Callable[[], Any] | None = None,
        board_api: Any | None = None,
        port_lister: Callable[[], list[Any]] | None = None,
        channel_names: tuple[str, ...] = SSVEP_CHANNEL_NAMES,
    ) -> None:
        self.serial_port = str(serial_port)
        self.frame_size = max(1, int(frame_size))
        self._board_factory = board_factory
        self._params_factory = params_factory
        self._board_api = board_api
        self._port_lister = port_lister
        self.channel_names = tuple(str(value) for value in channel_names)
        if len(self.channel_names) != 8 or any(not value.strip() for value in self.channel_names):
            raise ValueError("Cyton direct acquisition requires exactly 8 non-empty channel names")
        self._board: Any | None = None
        self._eeg_channels: tuple[int, ...] = ()
        self._timestamp_channel = -1
        self._timestamps: deque[float] = deque(maxlen=2048)
        self._last_timestamp: float | None = None
        self._total_samples = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self._non_monotonic_timestamps = 0
        self.sample_rate_hz = 250.0
        self.source_name = "OpenBCI Direct · 未选择串口"

    @property
    def connected(self) -> bool:
        return self._board is not None

    def configure_serial_port(self, serial_port: str) -> None:
        if self.connected:
            raise RuntimeError("请先停止采集，再切换串口设备")
        serial_port = str(serial_port).strip()
        if not serial_port:
            raise ValueError("串口不能为空")
        self.serial_port = serial_port
        self.source_name = f"OpenBCI Direct · {serial_port}"

    def discover_devices(self) -> list[SerialDeviceDescriptor]:
        """List paired Bluetooth COM ports and USB serial dongles."""

        if self._port_lister is not None:
            ports = list(self._port_lister())
        else:
            try:
                from serial.tools import list_ports

                ports = list(list_ports.comports())
            except ImportError:
                # macOS/Linux can still offer a useful list before pyserial is
                # installed; Windows users receive the actionable dependency error.
                candidates = sorted(set(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.*")))
                if not candidates:
                    raise RuntimeError("缺少 pyserial，无法搜索蓝牙 COM/串口设备")
                return [SerialDeviceDescriptor(port=value) for value in candidates]
        descriptors = [
            item
            if isinstance(item, SerialDeviceDescriptor)
            else SerialDeviceDescriptor(
                port=str(getattr(item, "device", item)),
                description=str(getattr(item, "description", "") or ""),
                hardware_id=str(getattr(item, "hwid", "") or ""),
            )
            for item in ports
        ]
        ignored_fragments = ("bluetooth-incoming-port", "debug-console")
        descriptors = [
            item
            for item in descriptors
            if not any(fragment in item.port.lower() for fragment in ignored_fragments)
        ]
        return sorted(descriptors, key=lambda item: item.port.lower())

    def _runtime(self) -> tuple[Callable[[int, Any], Any], Callable[[], Any], Any]:
        if self._board_factory is not None and self._params_factory is not None:
            return self._board_factory, self._params_factory, self._board_api or self._board_factory
        try:
            from brainflow.board_shim import BoardShim, BrainFlowInputParams
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError(
                "未安装 BrainFlow；请执行 pip install brainflow pyserial 后重启软件"
            ) from exc
        return BoardShim, BrainFlowInputParams, BoardShim

    def connect(self) -> None:
        if self.connected:
            return
        if not self.serial_port:
            raise RuntimeError("请先刷新设备并选择 ExpandableEEG 蓝牙 COM/串口")
        board_factory, params_factory, board_api = self._runtime()
        params = params_factory()
        params.serial_port = self.serial_port
        board = board_factory(self.CYTON_BOARD_ID, params)
        try:
            board.prepare_session()
            board.start_stream(45000)
        except Exception:
            try:
                board.release_session()
            except Exception:
                pass
            raise
        self._board = board
        self._board_api = board_api
        self.sample_rate_hz = float(board_api.get_sampling_rate(self.CYTON_BOARD_ID))
        self._eeg_channels = tuple(
            int(value) for value in board_api.get_eeg_channels(self.CYTON_BOARD_ID)
        )[:8]
        self._timestamp_channel = int(board_api.get_timestamp_channel(self.CYTON_BOARD_ID))
        if len(self._eeg_channels) != 8:
            self.disconnect()
            raise RuntimeError(f"Cyton驱动应返回8个EEG通道，实际为{len(self._eeg_channels)}个")
        self._reset_health()
        self.source_name = f"OpenBCI Direct · {self.serial_port}"

    def disconnect(self) -> None:
        board, self._board = self._board, None
        if board is None:
            return
        try:
            board.stop_stream()
        except Exception:
            pass
        try:
            board.release_session()
        except Exception:
            pass

    def flush(self) -> int:
        board = self._board
        if board is None:
            return 0
        try:
            count = int(board.get_board_data_count())
            if count > 0:
                board.get_board_data(count)
            return count
        except Exception:
            return 0

    def read_frame(self) -> EEGFrame:
        board = self._board
        if board is None:
            raise RuntimeError("OpenBCI/ExpandableEEG设备尚未连接")
        deadline = time.monotonic() + 1.0
        data = np.empty((0, 0), dtype=float)
        while time.monotonic() < deadline:
            data = np.asarray(board.get_board_data(self.frame_size), dtype=float)
            if data.ndim == 2 and data.shape[1] > 0:
                break
            time.sleep(0.01)
        if data.ndim != 2 or data.shape[1] == 0:
            raise TimeoutError("等待 ExpandableEEG 数据")
        eeg = data[list(self._eeg_channels), :]
        sample_count = int(eeg.shape[1])
        timestamps = self._extract_timestamps(data, sample_count)
        self._update_health(timestamps)
        return EEGFrame(
            timestamp=timestamps[-1],
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
            samples=tuple(tuple(float(value) for value in row) for row in eeg),
            sample_timestamps=timestamps,
            channel_units=("uV",) * 8,
            source_id=f"brainflow-cyton:{self.serial_port}",
        )

    def playback_snapshot(self) -> dict[str, object]:
        return {
            "backend": "hardware",
            "connected": self.connected,
            "source": self.source_name,
            "serial_port": self.serial_port,
            "sample_rate_hz": self.sample_rate_hz,
        }

    def health_snapshot(self) -> dict[str, object]:
        return {
            "backend": "hardware",
            "connected": self.connected,
            "stream_name": self.source_name,
            "serial_port": self.serial_port,
            "channel_count": 8,
            "nominal_sample_rate_hz": 250.0,
            "actual_sample_rate_hz": self._calibrated_sample_rate(),
            "total_samples": self._total_samples,
            "gap_count": self._gap_count,
            "max_gap_seconds": self._max_gap_seconds,
            "non_monotonic_timestamps": self._non_monotonic_timestamps,
            "channel_names": self.channel_names,
            "channel_units": ("uV",) * 8,
            "reference": "SRB2/A1",
            "bias": "BIAS/A2",
        }

    def _extract_timestamps(self, data: np.ndarray, sample_count: int) -> tuple[float, ...]:
        if 0 <= self._timestamp_channel < data.shape[0]:
            values = tuple(float(value) for value in data[self._timestamp_channel, :])
            if len(values) == sample_count and all(value > 0 for value in values):
                return values
        end = time.time()
        return tuple(
            end - (sample_count - 1 - index) / self.sample_rate_hz
            for index in range(sample_count)
        )

    def _reset_health(self) -> None:
        self._timestamps.clear()
        self._last_timestamp = None
        self._total_samples = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self._non_monotonic_timestamps = 0

    def _update_health(self, timestamps: tuple[float, ...]) -> None:
        expected = 1.0 / max(self.sample_rate_hz, 1.0)
        for timestamp in timestamps:
            if self._last_timestamp is not None:
                delta = timestamp - self._last_timestamp
                if delta <= 0:
                    self._non_monotonic_timestamps += 1
                else:
                    self._timestamps.append(timestamp)
                    if delta > expected * 2.0:
                        self._gap_count += 1
                        self._max_gap_seconds = max(self._max_gap_seconds, delta)
            else:
                self._timestamps.append(timestamp)
            self._last_timestamp = timestamp
        self._total_samples += len(timestamps)

    def _calibrated_sample_rate(self) -> float:
        if len(self._timestamps) < 2:
            return self.sample_rate_hz
        duration = self._timestamps[-1] - self._timestamps[0]
        return (len(self._timestamps) - 1) / duration if duration > 0 else self.sample_rate_hz


__all__ = ["BrainFlowCytonEEGStream", "SerialDeviceDescriptor"]
