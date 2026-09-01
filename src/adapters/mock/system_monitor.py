from __future__ import annotations

import math

from src.domain import SystemMetrics


class MockSystemMonitor:
    """生成演示资源曲线。

    【真实接口接入点】正式版另建 ``RealSystemMonitor``，整合 psutil、GPU、
    LSL/Crazyflie 链路指标和干扰检测，不要修改本 Mock。
    """

    def __init__(self) -> None:
        self._tick = 0

    def snapshot(self) -> SystemMetrics:
        phase = self._tick / 6.0
        self._tick += 1
        return SystemMetrics(
            cpu_percent=42.0 + math.sin(phase) * 8.0,
            gpu_percent=36.0 + math.cos(phase * 0.8) * 7.0,
            memory_percent=58.0 + math.sin(phase * 0.4) * 3.0,
            latency_ms=16.0 + math.cos(phase) * 4.0,
            throughput_mbps=4.2 + math.sin(phase * 0.6) * 0.5,
        )
