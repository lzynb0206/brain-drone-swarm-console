"""Lab Streaming Layer implementation of the EEGStream boundary."""

from __future__ import annotations

import time
from collections import deque
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
        expected_channel_count: int = 8,
        resolver: Callable[..., list[Any]] | None = None,
        inlet_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.timeout_seconds = float(timeout_seconds)
        self.frame_size = int(frame_size)
        self.expected_channel_count = int(expected_channel_count)
        self._resolver = resolver
        self._inlet_factory = inlet_factory
        self._inlet = None
        self._channel_count = 0
        self.sample_rate_hz = 0.0
        self.nominal_sample_rate_hz = 0.0
        self._timestamps: deque[float] = deque(maxlen=2048)
        self.source_name = f"LSL · {stream_name}"

    @property
    def connected(self) -> bool:
        return self._inlet is not None

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
        if self._channel_count != self.expected_channel_count:
            self.disconnect()
            raise RuntimeError(
                f"LSL EEG 通道数应为 {self.expected_channel_count}，实际为 {self._channel_count}"
            )
        self.nominal_sample_rate_hz = float(info.nominal_srate()) or 250.0
        self.sample_rate_hz = self.nominal_sample_rate_hz
        self._timestamps.clear()
        resolved_name = str(info.name() or self.stream_name)
        self.source_name = f"LSL · {resolved_name}"

    def disconnect(self) -> None:
        inlet, self._inlet = self._inlet, None
        if inlet is not None:
            try:
                inlet.close_stream()
            except Exception:
                pass

    def flush(self) -> int:
        inlet = self._inlet
        if inlet is None:
            return 0
        self._timestamps.clear()
        try:
            return int(inlet.flush() or 0)
        except AttributeError:
            discarded = 0
            while True:
                samples, _timestamps = inlet.pull_chunk(timeout=0.0, max_samples=512)
                if not samples:
                    break
                discarded += len(samples)
            return discarded

    def read_frame(self) -> EEGFrame:
        inlet = self._inlet
        if inlet is None:
            raise RuntimeError("LSL EEG stream is not connected")
        samples, timestamps = inlet.pull_chunk(timeout=1.0, max_samples=self.frame_size)
        if not samples:
            raise TimeoutError("等待 LSL EEG 数据")
        for timestamp in timestamps or ():
            value = float(timestamp)
            if value > 0:
                self._timestamps.append(value)
        calibrated = self._calibrated_sample_rate()
        if calibrated is not None:
            self.sample_rate_hz = calibrated
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

    def _calibrated_sample_rate(self) -> float | None:
        if len(self._timestamps) < 64:
            return None
        values = list(self._timestamps)
        deltas = [right - left for left, right in zip(values, values[1:]) if right > left]
        if len(deltas) < 32:
            return None
        ordered = sorted(deltas)
        median = ordered[len(ordered) // 2]
        filtered = [value for value in deltas if median * 0.5 <= value <= median * 1.5]
        if len(filtered) < 32:
            return None
        estimate = 1.0 / (sum(filtered) / len(filtered))
        nominal = self.nominal_sample_rate_hz or 250.0
        if not (nominal * 0.8 <= estimate <= nominal * 1.2):
            return None
        return float(estimate)
