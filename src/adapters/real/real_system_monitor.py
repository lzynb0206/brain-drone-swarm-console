"""Local process/system metrics without generated demo curves."""

from __future__ import annotations

import os

from src.domain import SystemMetrics


class RealSystemMonitor:
    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            psutil = None
        self._psutil = psutil
        if self._psutil is not None:
            self._psutil.cpu_percent(interval=None)

    def snapshot(self) -> SystemMetrics:
        if self._psutil is not None:
            cpu = float(self._psutil.cpu_percent(interval=None))
            memory = float(self._psutil.virtual_memory().percent)
        else:
            cpu = min(100.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1) * 100.0)
            memory = 0.0
        return SystemMetrics(
            cpu_percent=cpu,
            gpu_percent=0.0,
            memory_percent=memory,
            latency_ms=0.0,   # 由真实链路适配器接入后再填写。
            throughput_mbps=0.0,
        )
