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
        command_cooldown_seconds: float = 2.5,
    ) -> None:
        if float(command_cooldown_seconds) < 0.0:
            raise ValueError("command_cooldown_seconds cannot be negative")
        self.gateway = gateway
        self.mappings = mappings
        self._command_sender = command_sender
        self._record = record
        self.command_cooldown_seconds = float(command_cooldown_seconds)
        self.enabled = False
        self.session_active = False
        self._session_ready_at = 0.0
        self._pending_label = ""
        self._pending_count = 0
        self._last_command_time = 0.0
        self._requires_release = False
        self._release_notice_sent = False

    def disable(self) -> None:
        self.enabled = False
        self.session_active = False
        self._session_ready_at = 0.0
        self._pending_label = ""
        self._pending_count = 0
        self._last_command_time = 0.0
        self._requires_release = False
        self._release_notice_sent = False

    @property
    def last_command_time(self) -> float:
        """Monotonic timestamp of the latest command admitted for execution."""

        return self._last_command_time

    @staticmethod
    def _is_live_eeg_source(source: str) -> bool:
        normalized = source.strip().lower()
        return normalized.startswith((
            "lsl",
            "openbci direct",
            "expandableeeg native",
        ))

    def set_enabled(self, enabled: bool, eeg_source: str) -> str:
        if not enabled:
            self.disable()
            return self._record("脑电识别继续运行，但自动飞行执行已关闭")
        if not self._is_live_eeg_source(eeg_source):
            return self._record("脑控执行被拒绝：只有实时 LSL 或直接硬件数据源可以驱动实机")
        if self.gateway is None:
            return self._record("脑控执行被拒绝：蜂群接口未配置")
        status = self.gateway.status()
        if not status.connected:
            return self._record("脑控执行被拒绝：请先连接实机")
        if not status.control_enabled:
            return self._record("脑控执行被拒绝：实机当前为只读遥测模式")
        if status.emergency:
            return self._record("脑控执行被拒绝：系统处于急停锁定状态")
        self.enabled = True
        self.session_active = False
        self._session_ready_at = 0.0
        self._pending_label = ""
        self._pending_count = 0
        self._requires_release = False
        self._release_notice_sent = False
        if status.airborne:
            return self._record("脑控执行已授权：飞行指令需连续确认，执行后必须先释放注视")
        return self._record("脑控起飞已授权：无人机保持地面静止，识别到起飞后才启动 Flow Hover")

    def start_session(self, eeg_source: str, warmup_seconds: float = 2.0) -> str:
        """Activate command execution only after the full-screen stimulus starts."""

        if not self.enabled:
            return self._record("SSVEP 在线驾驶启动失败：请先开启脑控授权")
        if not self._is_live_eeg_source(eeg_source):
            self.disable()
            return self._record("SSVEP 在线驾驶启动失败：EEG 已不再是实时数据源")
        status = self.gateway.status() if self.gateway is not None else None
        if status is None or not status.connected or not status.control_enabled or status.emergency:
            self.disable()
            return self._record("SSVEP 在线驾驶启动失败：实机链路或安全状态不满足要求")
        self.session_active = True
        self._session_ready_at = time.monotonic() + max(0.0, float(warmup_seconds))
        self._pending_label = ""
        self._pending_count = 0
        self._requires_release = False
        self._release_notice_sent = False
        return self._record("SSVEP 在线驾驶已启动：等待新的完整 EEG 窗口后接受飞行指令")

    def handle(
        self,
        label: str,
        accepted: bool,
        eeg_source: str,
        frequency_hz: float | None = None,
        safety_verified: bool = False,
    ) -> str | None:
        if not self.enabled or not self.session_active:
            return None
        if not self._is_live_eeg_source(eeg_source):
            self.disable()
            return self._record("数据源已不再是实时硬件/LSL，脑控执行自动关闭")
        if not accepted or label == "空闲":
            self._pending_label = ""
            self._pending_count = 0
            self._requires_release = False
            self._release_notice_sent = False
            return None

        if time.monotonic() < self._session_ready_at:
            return None

        # One sustained fixation must produce at most one flight command.  The
        # operator has to look away (or be rejected as idle) before another
        # command can be accepted.
        if self._requires_release:
            if not self._release_notice_sent:
                self._release_notice_sent = True
                return self._record("脑控指令已执行：请先移开视线，检测到空闲后方可再次触发")
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
            "左移": "left",
            "右移": "right",
            # Keep old model metadata readable while the UI/dataset migrate to
            # the physically accurate lateral-movement labels.
            "左转": "left",
            "右转": "right",
            "降落": "land",
        }.get(mapped_label)
        if action is None:
            return None

        status = self.gateway.status()
        state = str(status.state).strip().lower()
        if not status.connected or not status.control_enabled or status.emergency:
            self.disable()
            return self._record("脑控执行已自动关闭：实机链路或安全状态不再满足要求")
        if action == "takeoff" and (status.airborne or state in {"taking_off", "landing"}):
            self._requires_release = True
            return self._record("脑控起飞被拒绝：无人机已在飞行或正在切换飞行状态")
        if action in {"forward", "backward", "left", "right"} and not (
            status.armed and status.airborne
        ):
            self._requires_release = True
            return self._record(f"脑控{mapped_label}被拒绝：无人机尚未完成起飞并进入悬停")
        if action == "land" and not (status.armed and status.airborne):
            self._requires_release = True
            return self._record("脑控降落被拒绝：无人机当前未在飞行")
        if safety_verified:
            self._pending_label = ""
            self._pending_count = 0
        else:
            if mapped_label == self._pending_label:
                self._pending_count += 1
            else:
                self._pending_label = mapped_label
                self._pending_count = 1
            if self._pending_count < 2:
                return self._record(f"脑控候选待二次确认：{mapped_label}")
        now = time.monotonic()
        if now - self._last_command_time < self.command_cooldown_seconds:
            return None
        self._pending_count = 0
        self._last_command_time = now
        self._requires_release = True
        self._release_notice_sent = False
        return self._command_sender(action)
