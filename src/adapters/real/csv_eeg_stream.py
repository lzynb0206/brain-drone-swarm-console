"""Replay an eight-channel CSV file through the normal EEGStream contract."""

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
        self._channel_names: tuple[str, ...] = ()
        self._position = 0
        self._connected = False
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
            width = len(first)
            if width == 0:
                raise ValueError("EEG CSV 没有通道列")
            if width != self.expected_channel_count:
                raise ValueError(
                    f"EEG CSV 通道数应为 {self.expected_channel_count}，实际为 {width}"
                )
            has_index_header = [item.strip() for item in first] == [str(i) for i in range(width)]
            if has_index_header:
                self._channel_names = tuple(f"CH{i + 1}" for i in range(width))
            else:
                rows.append(self._parse_row(first, width, 1))
                self._channel_names = tuple(f"CH{i + 1}" for i in range(width))
            for line_number, row in enumerate(reader, start=2):
                if not row or all(not item.strip() for item in row):
                    continue
                rows.append(self._parse_row(row, width, line_number))
        if len(rows) < self.frame_size:
            raise ValueError(f"EEG CSV 样本不足：{len(rows)} < {self.frame_size}")
        self._rows = rows
        self._position = 0
        self._connected = True

    @staticmethod
    def _parse_row(row: list[str], width: int, line_number: int) -> tuple[float, ...]:
        if len(row) != width:
            raise ValueError(f"EEG CSV 第 {line_number} 行列数不一致")
        try:
            return tuple(float(value) for value in row)
        except ValueError as exc:
            raise ValueError(f"EEG CSV 第 {line_number} 行包含非数值") from exc

    def disconnect(self) -> None:
        self._connected = False

    def flush(self) -> int:
        """CSV replay has no transport backlog; keep its current file position."""

        return 0

    def read_frame(self) -> EEGFrame:
        if not self._connected:
            raise RuntimeError("EEG CSV stream is not connected")
        frame_rows: list[tuple[float, ...]] = []
        while len(frame_rows) < self.frame_size:
            if self._position >= len(self._rows):
                if not self.loop:
                    raise EOFError("EEG CSV 已回放完毕")
                self._position = 0
            take = min(self.frame_size - len(frame_rows), len(self._rows) - self._position)
            frame_rows.extend(self._rows[self._position:self._position + take])
            self._position += take
        if self.realtime:
            time.sleep(self.frame_size / self.sample_rate_hz)
        channels = tuple(tuple(row[index] for row in frame_rows) for index in range(len(self._channel_names)))
        return EEGFrame(
            timestamp=time.time(),
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self._channel_names,
            samples=channels,
        )
