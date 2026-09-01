from __future__ import annotations

import math
import time

from src.domain import EEGFrame


class MockEEGStream:
    """生成稳定的 8 通道演示帧，不会连接 DeepBCI 或 LSL。

    【真实接口接入点】不要在本类里不断追加硬件判断；另建
    ``src/adapters/real/lsl_eeg_stream.py`` 实现同一个 EEGStream 协议。
    """

    def __init__(self, sample_rate_hz: float = 250.0, frame_size: int = 32) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_size = frame_size
        self._sample_index = 0
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def read_frame(self) -> EEGFrame:
        if not self._connected:
            raise RuntimeError("EEG stream is not connected")

        channels: list[tuple[float, ...]] = []
        for channel_index in range(8):
            frequency = 8.0 + channel_index * 0.65
            phase = channel_index * 0.42
            values = tuple(
                12.0
                * math.sin(
                    2.0
                    * math.pi
                    * frequency
                    * (self._sample_index + offset)
                    / self.sample_rate_hz
                    + phase
                )
                for offset in range(self.frame_size)
            )
            channels.append(values)

        frame = EEGFrame(
            timestamp=time.time(),
            sample_rate_hz=self.sample_rate_hz,
            channel_names=tuple(f"CH{index + 1}" for index in range(8)),
            samples=tuple(channels),
        )
        self._sample_index += self.frame_size
        return frame
