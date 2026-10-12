"""Publish acquired EEG frames as a Lab Streaming Layer outlet."""

from __future__ import annotations

import threading
from typing import Any, Callable

from src.domain import EEGFrame


class LSLEEGOutlet:
    """Optional EEG output used by the acquisition page's Networking panel."""

    def __init__(
        self,
        stream_name: str = "obci_eeg1",
        stream_type: str = "EEG",
        source_id: str = "brain-drone-swarm-console-openbci",
        outlet_factory: Callable[[Any], Any] | None = None,
        info_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.source_id = source_id
        self._outlet_factory = outlet_factory
        self._info_factory = info_factory
        self._outlet: Any | None = None
        self._lock = threading.Lock()
        self._channel_count = 0
        self._sample_rate_hz = 0.0
        self._total_samples = 0

    @property
    def active(self) -> bool:
        return self._outlet is not None

    def start(
        self,
        channel_names: tuple[str, ...],
        sample_rate_hz: float,
        channel_units: tuple[str, ...] = (),
    ) -> None:
        if self.active:
            return
        try:
            if self._info_factory is None or self._outlet_factory is None:
                from pylsl import StreamInfo, StreamOutlet, cf_float32

                info_factory = StreamInfo
                outlet_factory = StreamOutlet
                channel_format = cf_float32
            else:
                info_factory = self._info_factory
                outlet_factory = self._outlet_factory
                channel_format = "float32"
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError("无法加载 pylsl，不能启动 LSL 输出") from exc
        names = channel_names or tuple(f"CH{index + 1}" for index in range(8))
        units = channel_units if len(channel_units) == len(names) else ("uV",) * len(names)
        info = info_factory(
            self.stream_name,
            self.stream_type,
            len(names),
            float(sample_rate_hz),
            channel_format,
            self.source_id,
        )
        try:
            channels = info.desc().append_child("channels")
            for name, unit in zip(names, units):
                channel = channels.append_child("channel")
                channel.append_child_value("label", str(name))
                channel.append_child_value("unit", str(unit or "uV"))
                channel.append_child_value("type", "EEG")
        except AttributeError:
            pass
        with self._lock:
            self._outlet = outlet_factory(info)
            self._channel_count = len(names)
            self._sample_rate_hz = float(sample_rate_hz)
            self._total_samples = 0

    def stop(self) -> None:
        with self._lock:
            self._outlet = None

    def push_frame(self, frame: EEGFrame) -> int:
        with self._lock:
            outlet = self._outlet
            expected_channels = self._channel_count
        if outlet is None:
            return 0
        if len(frame.samples) != expected_channels or not frame.samples:
            raise ValueError(
                f"LSL输出通道数应为{expected_channels}，实际为{len(frame.samples)}"
            )
        sample_count = len(frame.samples[0])
        if any(len(channel) != sample_count for channel in frame.samples):
            raise ValueError("LSL输出帧的通道长度不一致")
        chunk = [
            [float(frame.samples[channel][sample]) for channel in range(expected_channels)]
            for sample in range(sample_count)
        ]
        if not chunk:
            return 0
        # Let liblsl stamp the last sample with its monotonic local clock.
        # BrainFlow board timestamps are commonly Unix epoch values; passing
        # those as LSL clock values would place samples far in the future.
        outlet.push_chunk(chunk)
        with self._lock:
            self._total_samples += sample_count
        return sample_count

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "active": self._outlet is not None,
                "stream_name": self.stream_name,
                "stream_type": self.stream_type,
                "source_id": self.source_id,
                "channel_count": self._channel_count,
                "sample_rate_hz": self._sample_rate_hz,
                "total_samples": self._total_samples,
            }


__all__ = ["LSLEEGOutlet"]
