"""Lab Streaming Layer implementation of the EEGStream boundary."""

from __future__ import annotations

import time
from typing import Any, Callable

from src.domain import EEGFrame


class LSLEEGStream:
    """Resolve an OpenBCI/DeepBCI EEG stream and return small channel-major frames.

    ``pylsl`` is imported lazily so CSV/offline mode remains usable when the LSL
    runtime is not installed.
    """

    def __init__(
        self,
        stream_name: str = "obci_eeg1",
        stream_type: str = "EEG",
        timeout_seconds: float = 3.0,
        frame_size: int = 32,
        resolver: Callable[..., list[Any]] | None = None,
        inlet_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.timeout_seconds = float(timeout_seconds)
        self.frame_size = int(frame_size)
        self._resolver = resolver
        self._inlet_factory = inlet_factory
        self._inlet = None
        self._channel_count = 0
        self.sample_rate_hz = 0.0
        self.source_name = f"LSL · {stream_name}"

    def connect(self) -> None:
        if self._resolver is None or self._inlet_factory is None:
            try:
                from pylsl import StreamInlet, resolve_byprop
            except (ImportError, RuntimeError) as exc:
                raise RuntimeError("未安装或无法加载 pylsl；请执行 pip install pylsl") from exc
            resolver = self._resolver or resolve_byprop
            inlet_factory = self._inlet_factory or StreamInlet
        else:
            resolver = self._resolver
            inlet_factory = self._inlet_factory

        streams = resolver("name", self.stream_name, minimum=1, timeout=self.timeout_seconds)
        if not streams:
            streams = resolver("type", self.stream_type, minimum=1, timeout=self.timeout_seconds)
        if not streams:
            raise RuntimeError(
                f"未发现 LSL 流：name={self.stream_name!r}, type={self.stream_type!r}"
            )
        self._inlet = inlet_factory(streams[0], max_buflen=8, recover=True)
        info = self._inlet.info()
        self._channel_count = int(info.channel_count())
        self.sample_rate_hz = float(info.nominal_srate()) or 250.0
        resolved_name = str(info.name() or self.stream_name)
        self.source_name = f"LSL · {resolved_name}"

    def disconnect(self) -> None:
        inlet, self._inlet = self._inlet, None
        if inlet is not None:
            try:
                inlet.close_stream()
            except Exception:
                pass

    def read_frame(self) -> EEGFrame:
        inlet = self._inlet
        if inlet is None:
            raise RuntimeError("LSL EEG stream is not connected")
        samples, timestamps = inlet.pull_chunk(timeout=1.0, max_samples=self.frame_size)
        if not samples:
            raise TimeoutError("等待 LSL EEG 数据")
        width = self._channel_count or len(samples[0])
        normalized = [tuple(float(value) for value in row[:width]) for row in samples]
        if any(len(row) != width for row in normalized):
            raise ValueError("LSL EEG 帧通道数不一致")
        channels = tuple(tuple(row[index] for row in normalized) for index in range(width))
        return EEGFrame(
            timestamp=float(timestamps[-1]) if timestamps else time.time(),
            sample_rate_hz=self.sample_rate_hz,
            channel_names=tuple(f"CH{i + 1}" for i in range(width)),
            samples=channels,
        )
