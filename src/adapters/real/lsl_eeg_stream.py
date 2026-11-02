"""Lab Streaming Layer implementation of the EEGStream boundary."""

from __future__ import annotations

import re
import time
from collections import deque
from typing import Any, Callable, Iterable

from src.bci.config import SSVEP_CHANNEL_NAMES
from src.domain import EEGFrame, EEGStreamDescriptor


class LSLEEGStream:
    """Discover and read OpenBCI-compatible EEG streams from LSL.

    The adapter retains the original ``EEGStream`` interface, while exposing
    optional discovery/selection and transport-health methods for the research
    acquisition UI. ``pylsl`` remains a lazy dependency so CSV replay works on
    machines without an LSL runtime.
    """

    def __init__(
        self,
        stream_name: str = "obci_eeg1",
        stream_type: str = "EEG",
        timeout_seconds: float = 3.0,
        frame_size: int = 32,
        expected_channel_count: int | None = 8,
        resolver: Callable[..., list[Any]] | None = None,
        inlet_factory: Callable[..., Any] | None = None,
        montage_names: tuple[str, ...] = SSVEP_CHANNEL_NAMES,
    ) -> None:
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.timeout_seconds = float(timeout_seconds)
        self.frame_size = int(frame_size)
        self.expected_channel_count = (
            int(expected_channel_count) if expected_channel_count is not None else None
        )
        self.montage_names = tuple(montage_names)
        self._resolver = resolver
        self._inlet_factory = inlet_factory
        self._inlet = None
        self._selected_info: Any | None = None
        self._known_streams: dict[str, Any] = {}
        self._channel_count = 0
        self._channel_names: tuple[str, ...] = ()
        self._original_channel_names: tuple[str, ...] = ()
        self._channel_units: tuple[str, ...] = ()
        self._source_id = ""
        self._required_source_id = ""
        self._uid = ""
        self.sample_rate_hz = 0.0
        self.nominal_sample_rate_hz = 0.0
        self._timestamps: deque[float] = deque(maxlen=2048)
        self._last_timestamp: float | None = None
        self._total_samples = 0
        self._non_monotonic_timestamps = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self.source_name = f"LSL · {stream_name}"

    @property
    def connected(self) -> bool:
        return self._inlet is not None

    def _runtime(self) -> tuple[Callable[..., list[Any]], Callable[..., Any]]:
        if self._resolver is not None and self._inlet_factory is not None:
            return self._resolver, self._inlet_factory
        try:
            from pylsl import StreamInlet, resolve_byprop
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError("未安装或无法加载 pylsl；请执行 pip install pylsl") from exc
        return self._resolver or resolve_byprop, self._inlet_factory or StreamInlet

    def discover_streams(self, timeout_seconds: float | None = None) -> list[EEGStreamDescriptor]:
        """Return all visible EEG streams, with OpenBCI's default stream first."""

        resolver, _ = self._runtime()
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        infos = resolver("type", self.stream_type, minimum=1, timeout=timeout)
        self._known_streams.clear()
        descriptors: list[EEGStreamDescriptor] = []
        for info in infos:
            descriptor = self._descriptor(info)
            key = descriptor.uid or self._stream_key(descriptor.name, descriptor.source_id)
            self._known_streams[key] = info
            descriptors.append(descriptor)
        descriptors.sort(key=lambda item: (item.name != self.stream_name, item.name, item.source_id))
        return descriptors

    def select_stream(self, descriptor: EEGStreamDescriptor) -> None:
        """Select a descriptor returned by :meth:`discover_streams`."""

        if self.connected:
            raise RuntimeError("请先断开当前 EEG 流，再切换 LSL 数据源")
        key = descriptor.uid or self._stream_key(descriptor.name, descriptor.source_id)
        self._selected_info = self._known_streams.get(key)
        self._required_source_id = descriptor.source_id
        self.stream_name = descriptor.name
        self.stream_type = descriptor.stream_type or "EEG"
        self.expected_channel_count = descriptor.channel_count or self.expected_channel_count
        self.source_name = f"LSL · {descriptor.name}"

    def require_source_id(self, source_id: str = "") -> None:
        """Restrict the next connection to one exact LSL producer."""

        if self.connected:
            raise RuntimeError("请先断开当前 EEG 流，再修改 LSL source_id 过滤器")
        self._required_source_id = str(source_id).strip()
        self._selected_info = None

    def connect(self) -> None:
        resolver, inlet_factory = self._runtime()
        streams: Iterable[Any]
        if self._selected_info is not None:
            streams = [self._selected_info]
        elif self._required_source_id:
            streams = resolver(
                "source_id", self._required_source_id,
                minimum=1, timeout=self.timeout_seconds,
            )
        else:
            streams = resolver("name", self.stream_name, minimum=1, timeout=self.timeout_seconds)
            if not streams:
                streams = resolver("type", self.stream_type, minimum=1, timeout=self.timeout_seconds)
        streams = list(streams)
        if not streams:
            source_requirement = (
                f", source_id={self._required_source_id!r}"
                if self._required_source_id else ""
            )
            raise RuntimeError(
                f"未发现 LSL 流：name={self.stream_name!r}, type={self.stream_type!r}"
                f"{source_requirement}"
            )
        self._inlet = inlet_factory(streams[0], max_buflen=8, recover=True)
        info = self._inlet.info()
        descriptor = self._descriptor(info)
        self._channel_count = descriptor.channel_count
        if (
            self.expected_channel_count is not None
            and self._channel_count != self.expected_channel_count
        ):
            self.disconnect()
            raise RuntimeError(
                f"LSL EEG 通道数应为 {self.expected_channel_count}，实际为 {self._channel_count}"
            )
        self.nominal_sample_rate_hz = descriptor.nominal_sample_rate_hz or 250.0
        self.sample_rate_hz = self.nominal_sample_rate_hz
        self._original_channel_names = descriptor.channel_names or tuple(
            f"CH{i + 1}" for i in range(self._channel_count)
        )
        self._channel_names = self._configured_montage_names(self._original_channel_names, self.montage_names)
        self._channel_units = descriptor.channel_units or tuple("" for _ in self._channel_names)
        self._source_id = descriptor.source_id
        self._uid = descriptor.uid
        self._reset_health()
        self.source_name = f"LSL · {descriptor.name or self.stream_name}"

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
        self._last_timestamp = None
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
        sample_timestamps = tuple(float(value) for value in (timestamps or ()))
        self._update_health(sample_timestamps)
        calibrated = self._calibrated_sample_rate()
        if calibrated is not None:
            self.sample_rate_hz = calibrated
        width = self._channel_count or len(samples[0])
        normalized = [tuple(float(value) for value in row[:width]) for row in samples]
        if any(len(row) != width for row in normalized):
            raise ValueError("LSL EEG 帧通道数不一致")
        channels = tuple(tuple(row[index] for row in normalized) for index in range(width))
        return EEGFrame(
            timestamp=sample_timestamps[-1] if sample_timestamps else time.time(),
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self._channel_names or tuple(f"CH{i + 1}" for i in range(width)),
            samples=channels,
            sample_timestamps=sample_timestamps,
            channel_units=self._channel_units,
            source_id=self._source_id,
            original_channel_names=self._original_channel_names,
        )

    def health_snapshot(self) -> dict[str, object]:
        """Return counters suitable for status display and experiment logs."""

        return {
            "connected": self.connected,
            "stream_name": self.stream_name,
            "source_id": self._source_id,
            "channel_count": self._channel_count,
            "nominal_sample_rate_hz": self.nominal_sample_rate_hz,
            "actual_sample_rate_hz": self.sample_rate_hz,
            "total_samples": self._total_samples,
            "non_monotonic_timestamps": self._non_monotonic_timestamps,
            "gap_count": self._gap_count,
            "max_gap_seconds": self._max_gap_seconds,
            "channel_names": self._channel_names,
            "original_channel_names": self._original_channel_names,
            "channel_units": self._channel_units,
        }

    def _reset_health(self) -> None:
        self._timestamps.clear()
        self._last_timestamp = None
        self._total_samples = 0
        self._non_monotonic_timestamps = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0

    def _update_health(self, timestamps: tuple[float, ...]) -> None:
        expected_interval = 1.0 / (self.nominal_sample_rate_hz or 250.0)
        for timestamp in timestamps:
            if timestamp <= 0:
                continue
            if self._last_timestamp is not None:
                delta = timestamp - self._last_timestamp
                if delta <= 0:
                    self._non_monotonic_timestamps += 1
                else:
                    if delta > expected_interval * 2.0:
                        self._gap_count += 1
                        self._max_gap_seconds = max(self._max_gap_seconds, delta)
                    self._timestamps.append(timestamp)
            else:
                self._timestamps.append(timestamp)
            self._last_timestamp = timestamp
        self._total_samples += len(timestamps)

    @classmethod
    def _descriptor(cls, info: Any) -> EEGStreamDescriptor:
        count = int(cls._safe_call(info, "channel_count", 0) or 0)
        names, units = cls._channel_metadata(info, count)
        return EEGStreamDescriptor(
            name=str(cls._safe_call(info, "name", "") or "未命名EEG流"),
            stream_type=str(cls._safe_call(info, "type", "EEG") or "EEG"),
            channel_count=count,
            nominal_sample_rate_hz=float(cls._safe_call(info, "nominal_srate", 0.0) or 0.0),
            source_id=str(cls._safe_call(info, "source_id", "") or ""),
            uid=str(cls._safe_call(info, "uid", "") or ""),
            channel_names=names,
            channel_units=units,
        )

    @staticmethod
    def _safe_call(value: Any, method: str, default: Any) -> Any:
        callback = getattr(value, method, None)
        if not callable(callback):
            return default
        try:
            return callback()
        except Exception:
            return default

    @classmethod
    def _channel_metadata(cls, info: Any, count: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
        default_names = tuple(f"CH{i + 1}" for i in range(count))
        default_units = tuple("" for _ in range(count))
        try:
            channel = info.desc().child("channels").child("channel")
        except Exception:
            return default_names, default_units
        names: list[str] = []
        units: list[str] = []
        for index in range(count):
            if index:
                try:
                    channel = channel.next_sibling()
                except Exception:
                    break
            if channel is None:
                break
            try:
                name = channel.child_value("label") or channel.child_value("name")
            except Exception:
                name = ""
            try:
                unit = channel.child_value("unit")
            except Exception:
                unit = ""
            names.append(str(name or f"CH{index + 1}"))
            units.append(str(unit or ""))
        if len(names) != count:
            return default_names, default_units
        return tuple(names), tuple(units)

    @staticmethod
    def _stream_key(name: str, source_id: str) -> str:
        return f"{name}\0{source_id}"

    @staticmethod
    def _configured_montage_names(names: tuple[str, ...], montage_names=SSVEP_CHANNEL_NAMES) -> tuple[str, ...]:
        """Map only unambiguous generic 8-channel labels to the fixed montage."""

        if len(names) != len(montage_names):
            return names
        generic_patterns = (
            re.compile(r"CH\d+"),
            re.compile(r"CHANNEL\d+"),
            re.compile(r"EEG\d+"),
            re.compile(r"EXGCHANNEL\d+"),
        )
        compact = [re.sub(r"[^A-Z0-9]", "", name.upper()) for name in names]
        if all(any(pattern.fullmatch(name) for pattern in generic_patterns) for name in compact):
            return tuple(montage_names)
        return names

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
