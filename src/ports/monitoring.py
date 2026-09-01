from __future__ import annotations

from typing import Protocol

from src.domain import SystemMetrics


class SystemMonitor(Protocol):
    """系统资源与链路诊断边界。

    【真实接口接入点】正式实现可组合 psutil、GPU 查询、LSL 流目录、
    Crazyflie 链路统计和 50 Hz 干扰检测结果。界面只消费快照，采样工作应
    在后台线程或独立监控进程中完成。
    """

    def snapshot(self) -> SystemMetrics:
        """返回某一时刻的 CPU/GPU/内存/延迟/吞吐量快照。"""
        ...
