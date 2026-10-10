"""Replay plain or LSL-recorder CSV files through the EEGStream contract."""

from __future__ import annotations

import csv
import time
from pathlib import Path

from src.domain import EEGFrame


class CsvEEGStream:
    def __init__(
        self,
        path: Path | str,
        sample_rate_hz: float = 250.0,
        frame_size: int = 32,
        expected_channel_count: int = 8,
        loop: bool = True,
        realtime: bool = True,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.sample_rate_hz = float(sample_rate_hz)
        self.frame_size = int(frame_size)
        self.expected_channel_count = int(expected_channel_count)
        self.loop = bool(loop)
        self.realtime = bool(realtime)
        self._rows: list[tuple[float, ...]] = []
        self._timestamps: list[float] = []
        self._channel_names: tuple[str, ...] = ()
        self._position = 0
        self._connected = False
        self._total_samples_read = 0
        self._non_monotonic_timestamps = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self.source_name = f"CSV回放 · {self.path.name}"

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"EEG CSV 不存在：{self.path}")
        rows: list[tuple[float, ...]] = []
        with self.path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            first = next(reader, None)
            if first is None:
                raise ValueError("EEG CSV 是空文件")
            source_width = len(first)
            if source_width == 0:
                raise ValueError("EEG CSV 没有通道列")
            normalized_header = [item.strip().lower() for item in first]
            is_lsl_recorder = normalized_header[:3] == [
                "sample_index", "lsl_timestamp", "elapsed_seconds",
            ]
            if is_lsl_recorder:
                channel_offset = 3
                channel_names = tuple(
                    item.strip() or f"CH{index + 1}"
                    for index, item in enumerate(first[channel_offset:])
                )
            else:
                channel_offset = 0
                channel_names = tuple(f"CH{i + 1}" for i in range(source_width))
            width = source_width - channel_offset
            if width != self.expected_channel_count:
                raise ValueError(
                    f"EEG CSV 通道数应为 {self.expected_channel_count}，实际为 {width}"
                )
            has_index_header = [item.strip() for item in first] == [str(i) for i in range(width)]
            has_named_header = not self._is_numeric_row(first)
            timestamps: list[float] = []
            if is_lsl_recorder or has_index_header or has_named_header:
                self._channel_names = (
                    tuple(item.strip() or f"CH{index + 1}" for index, item in enumerate(first))
                    if has_named_header and not is_lsl_recorder
                    else channel_names
                )
            else:
                rows.append(self._parse_row(first, width, 1))
                self._channel_names = tuple(f"CH{i + 1}" for i in range(width))
            for line_number, row in enumerate(reader, start=2):
                if not row or all(not item.strip() for item in row):
                    continue
                if is_lsl_recorder:
                    if len(row) != source_width:
                        raise ValueError(f"EEG CSV 第 {line_number} 行列数不一致")
                    try:
                        timestamps.append(float(row[1]))
                    except ValueError as exc:
                        raise ValueError(f"EEG CSV 第 {line_number} 行时间戳不是数值") from exc
                    rows.append(self._parse_row(row[channel_offset:], width, line_number))
                else:
                    rows.append(self._parse_row(row, width, line_number))
        if len(rows) < self.frame_size:
            raise ValueError(f"EEG CSV 样本不足：{len(rows)} < {self.frame_size}")
        self._rows = rows
        self._timestamps = timestamps
        estimated_rate = self._estimate_sample_rate(timestamps)
        if estimated_rate is not None:
            self.sample_rate_hz = estimated_rate
        self._calculate_timestamp_health()
        self._position = 0
        self._total_samples_read = 0
        self._connected = True

    @staticmethod
    def _parse_row(row: list[str], width: int, line_number: int) -> tuple[float, ...]:
        if len(row) != width:
            raise ValueError(f"EEG CSV 第 {line_number} 行列数不一致")
        try:
            return tuple(float(value) for value in row)
        except ValueError as exc:
            raise ValueError(f"EEG CSV 第 {line_number} 行包含非数值") from exc

    @staticmethod
    def _is_numeric_row(row: list[str]) -> bool:
        try:
            for value in row:
                float(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _estimate_sample_rate(timestamps: list[float]) -> float | None:
        deltas = [right - left for left, right in zip(timestamps, timestamps[1:]) if right > left]
        if len(deltas) < 32:
            return None
        ordered = sorted(deltas)
        median = ordered[len(ordered) // 2]
        filtered = [value for value in deltas if median * 0.5 <= value <= median * 1.5]
        if not filtered:
            return None
        return 1.0 / (sum(filtered) / len(filtered))

    def disconnect(self) -> None:
        self._connected = False

    def flush(self) -> int:
        """CSV replay has no transport backlog; keep its current file position."""

        return 0

    def health_snapshot(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "stream_name": self.path.name,
            "source_id": str(self.path),
            "channel_count": len(self._channel_names),
            "nominal_sample_rate_hz": self.sample_rate_hz,
            "actual_sample_rate_hz": self.sample_rate_hz,
            "total_samples": self._total_samples_read,
            "non_monotonic_timestamps": self._non_monotonic_timestamps,
            "gap_count": self._gap_count,
            "max_gap_seconds": self._max_gap_seconds,
            "channel_names": self._channel_names,
            "channel_units": tuple("" for _ in self._channel_names),
        }

    def _calculate_timestamp_health(self) -> None:
        self._non_monotonic_timestamps = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        if len(self._timestamps) < 2:
            return
        expected = 1.0 / max(self.sample_rate_hz, 1.0)
        for left, right in zip(self._timestamps, self._timestamps[1:]):
            delta = right - left
            if delta <= 0:
                self._non_monotonic_timestamps += 1
            elif delta > expected * 2.0:
                self._gap_count += 1
                self._max_gap_seconds = max(self._max_gap_seconds, delta)

    def read_frame(self) -> EEGFrame:
        if not self._connected:
            raise RuntimeError("EEG CSV stream is not connected")
        frame_rows: list[tuple[float, ...]] = []
        frame_timestamps: list[float] = []
        while len(frame_rows) < self.frame_size:
            if self._position >= len(self._rows):
                if not self.loop:
                    raise EOFError("EEG CSV 已回放完毕")
                self._position = 0
            take = min(self.frame_size - len(frame_rows), len(self._rows) - self._position)
            frame_rows.extend(self._rows[self._position:self._position + take])
            if self._timestamps:
                frame_timestamps.extend(self._timestamps[self._position:self._position + take])
            self._position += take
        if self.realtime:
            time.sleep(self.frame_size / self.sample_rate_hz)
        self._total_samples_read += len(frame_rows)
        channels = tuple(tuple(row[index] for row in frame_rows) for index in range(len(self._channel_names)))
        return EEGFrame(
            timestamp=frame_timestamps[-1] if frame_timestamps else time.time(),
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self._channel_names,
            samples=channels,
            sample_timestamps=tuple(frame_timestamps),
            channel_units=tuple("" for _ in self._channel_names),
            source_id=str(self.path),
        )
