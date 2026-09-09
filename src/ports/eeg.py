from __future__ import annotations

from typing import Protocol

from src.domain import EEGFrame


class EEGStream(Protocol):
    """脑电数据流边界。

    【真实接口接入点】推荐新增 ``LSLEEGStream``：DeepBCI 驱动先把
    8 通道、250 Hz EEG 推入 LSL，本适配器负责 resolve/inlet、统一时间戳
    和组装 ``EEGFrame``。不要在 Qt 主线程中阻塞读取；应在 ``QThread``
    中持续 ``read_frame``，再用 Signal 把帧交给界面。
    """

    def connect(self) -> None:
        """发现并连接 EEG/LSL 数据源。"""
        ...

    def disconnect(self) -> None:
        """停止采集并安全释放 LSL inlet 或串口。"""
        ...

    def read_frame(self) -> EEGFrame:
        """返回带统一时间戳的一小帧多通道采样。"""
        ...

    def flush(self) -> int:
        """丢弃数据源历史缓存，并返回丢弃的采样点数；不支持时返回 0。"""
        ...
