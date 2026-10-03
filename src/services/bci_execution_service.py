"""Safety state machine for turning decoded intents into flight commands."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from src.services.research_workbench_service import MappingEntry


class BCIExecutionService:
    """Gate automatic execution independently from decoder and UI state."""

    def __init__(
        self,
        gateway,
        mappings: Sequence[MappingEntry],
        command_sender: Callable[[str], str],
        record: Callable[[str], str],
    ) -> None:
        self.gateway = gateway
        self.mappings = mappings
        self._command_sender = command_sender
        self._record = record
        self.enabled = False
        self._pending_label = ""
        self._pending_count = 0
        self._last_command_time = 0.0

    def disable(self) -> None:
        self.enabled = False
        self._pending_label = ""
        self._pending_count = 0

    def set_enabled(self, enabled: bool, eeg_source: str) -> str:
        if not enabled:
            self.disable()
            return self._record("脑电识别继续运行，但自动飞行执行已关闭")
        if not eeg_source.lower().startswith("lsl"):
            return self._record("脑控执行被拒绝：只有实时 LSL 数据源可以驱动实机")
        if self.gateway is None:
            return self._record("脑控执行被拒绝：蜂群接口未配置")
        status = self.gateway.status()
        if not status.connected or not status.armed:
            return self._record("脑控执行被拒绝：请先连接并解锁实机控制")
        self.enabled = True
        return self._record("脑控执行已开启：同一意图连续确认两次后才提交")

    def handle(
        self,
        label: str,
        accepted: bool,
        eeg_source: str,
        frequency_hz: float | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        if not eeg_source.lower().startswith("lsl"):
            self.disable()
            return self._record("数据源已不再是实时 LSL，脑控执行自动关闭")
        if not accepted:
            self._pending_label = ""
            self._pending_count = 0
            return None

        mapped_label = label
        if frequency_hz is not None:
            frequency_key = f"{float(frequency_hz):g}Hz"
            mapped_label = next(
                (entry.action for entry in self.mappings if entry.frequency == frequency_key),
                label,
            )
        action = {
            "起飞": "takeoff",
            "前进": "forward",
            "后退": "backward",
            "左转": "left",
            "右转": "right",
            "降落": "land",
        }.get(mapped_label)
        if action is None:
            return None
        if mapped_label == self._pending_label:
            self._pending_count += 1
        else:
            self._pending_label = mapped_label
            self._pending_count = 1
        if self._pending_count < 2:
            return self._record(f"脑控候选待二次确认：{mapped_label}")
        now = time.monotonic()
        if now - self._last_command_time < 2.5:
            return None
        self._pending_count = 0
        self._last_command_time = now
        return self._command_sender(action)
