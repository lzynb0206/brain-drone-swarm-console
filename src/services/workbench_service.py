"""Stateful application actions and safety-aware adapter orchestration."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.domain import SwarmCommand
from src.ports import SwarmGateway, SystemMonitor


@dataclass
class MappingEntry:
    frequency: str
    action: str
    parameter: str


class WorkbenchService:
    """Owns mutable UI state and delegates device actions through Ports."""

    def __init__(
        self,
        swarm_gateway: SwarmGateway | None = None,
        system_monitor: SystemMonitor | None = None,
        experiment_recorder=None,
    ) -> None:
        self.swarm_gateway = swarm_gateway
        self.system_monitor = system_monitor
        self.experiment_recorder = experiment_recorder
        self.paradigm_frequency = TARGET_FREQUENCIES_HZ[0]
        self.paradigm_type = "SSVEP"
        self.stimulus_size = 40
        self.duty_cycle = 60
        self.algorithm = "FBCCA"
        self.algorithm_mode = "固定解码"
        self.loaded_algorithms = {"FBCCA"}
        self.default_algorithm = "FBCCA"
        self.controller = "PID"
        self.controller_values = {"Kp": 30, "Ki": 50, "Kd": 70}
        self.mappings = [
            MappingEntry(f"{frequency:g}Hz", command, "仅识别")
            for frequency, command in zip(TARGET_FREQUENCIES_HZ, COMMANDS)
        ]
        self.data_tab = "标签管理"
        self.selected_session = 2
        self.log_filter = "INFO"
        self.storage_percent = 68
        self.last_backup = "2026-08-30 22:00"
        self.user_roles = {"张伟": "士兵", "李明": "科研人员", "王强": "管理员"}
        self.marker_count = 0
        self.emergency_stopped = False
        self.bci_control_enabled = False
        self._pending_intent_label = ""
        self._pending_intent_count = 0
        self._last_bci_command_time = 0.0
        self.self_test_run = False
        self.layout_mode = "标准布局"
        self.command_history: list[str] = ["前进", "悬停"]
        self.event_log: list[str] = []
        if self.swarm_gateway is not None:
            add_listener = getattr(self.swarm_gateway, "add_event_listener", None)
            if callable(add_listener):
                add_listener(self._on_gateway_event)
            add_command_listener = getattr(self.swarm_gateway, "add_command_listener", None)
            if callable(add_command_listener):
                add_command_listener(self._on_command_result)

    def _record(self, message: str) -> str:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.insert(0, f"{stamp}  {message}")
        del self.event_log[40:]
        return message

    def _on_gateway_event(self, level: str, message: str) -> None:
        self._record(f"{level.upper()}  {message}")
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(level.upper(), message)

    def _on_command_result(self, status: str, message: str, command: SwarmCommand) -> None:
        self._record(f"{status.upper()}  {command.action}  {message}")
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(
                status.upper(), message, command.action, command.command_id, command.uav_id or "",
            )

    def apply_paradigm(self) -> str:
        return self._record(
            f"范式已应用：{self.paradigm_frequency}Hz，尺寸{self.stimulus_size}%，占空比{self.duty_cycle}%"
        )

    def reset_paradigm(self) -> str:
        self.paradigm_frequency, self.stimulus_size, self.duty_cycle = TARGET_FREQUENCIES_HZ[0], 40, 60
        return self._record("范式参数已恢复默认")

    def select_algorithm(self, name: str) -> str:
        self.algorithm = name
        return self._record(f"已选择算法 {name}")

    def load_algorithm(self) -> str:
        self.loaded_algorithms.add(self.algorithm)
        return self._record(f"算法 {self.algorithm} 已加载")

    def unload_algorithm(self) -> str:
        if self.algorithm == self.default_algorithm:
            return self._record("默认算法不能卸载，请先设置其他默认算法")
        self.loaded_algorithms.discard(self.algorithm)
        return self._record(f"算法 {self.algorithm} 已卸载")

    def set_default_algorithm(self) -> str:
        self.loaded_algorithms.add(self.algorithm)
        self.default_algorithm = self.algorithm
        return self._record(f"默认算法已设为 {self.algorithm}")

    def benchmark_algorithm(self) -> str:
        return self._record("FBCCA 为无训练解码器；请使用带真实标签的试次统计准确率")

    def apply_controller(self) -> str:
        values = ", ".join(f"{key}={value / 100:.2f}" for key, value in self.controller_values.items())
        return self._record(f"{self.controller} 参数已应用：{values}")

    def save_controller_preset(self) -> str:
        return self._record(f"{self.controller} 参数已保存为本地预设")

    def reset_controller(self) -> str:
        self.controller_values = {"Kp": 30, "Ki": 50, "Kd": 70}
        return self._record("PID 参数已恢复默认")

    def add_mapping(self, frequency: str, action: str, parameter: str) -> str:
        self.mappings.append(MappingEntry(frequency, action, parameter))
        return self._record(f"已添加映射：{frequency} → {action} {parameter}")

    def update_mapping(self, index: int, action: str, parameter: str) -> str:
        entry = self.mappings[index]
        entry.action, entry.parameter = action, parameter
        return self._record(f"已修改映射：{entry.frequency} → {action} {parameter}")

    def delete_mapping(self, index: int) -> str:
        entry = self.mappings.pop(index)
        return self._record(f"已删除映射：{entry.frequency}")

    def add_marker(self, label: str = "") -> str:
        self.marker_count += 1
        suffix = f"：{label}" if label else ""
        return self._record(f"下一完整 EEG 试次已标注{suffix}")

    def generate_dataset(self) -> str:
        return self._record("当前阶段保存原始带标签试次，尚未执行训练/验证集划分")

    def send_swarm_command(
        self, action: str, uav_id: str | None = None,
        parameters: dict[str, float | str] | None = None,
    ) -> str:
        labels = {
            "takeoff": "起飞", "land": "降落", "forward": "前进", "backward": "后退",
            "left": "左转", "right": "右转", "hover": "悬停", "formation": "队形切换",
            "switch_formation": "循环切换队形", "led_test": "LED连通性测试",
            "relative_square": "相对方形飞行测试", "hover_one_by_one": "逐架悬停验收",
            "hover_sync_test": "同步悬停验收", "relative_forward_back": "相对前后验收",
            "hover_sync_test": "同步悬停验收", "relative_forward_back": "相对前后验收",
        }
        if self.emergency_stopped:
            return self._record("系统处于急停状态，指令被安全策略拒绝")
        command_id = uuid.uuid4().hex
        command = SwarmCommand(action, uav_id=uav_id, parameters=parameters or {}, command_id=command_id)
        if self.swarm_gateway is not None:
            try:
                self.swarm_gateway.send_command(command)
            except RuntimeError as exc:
                if self.experiment_recorder is not None:
                    self.experiment_recorder.record_command_event(
                        "REJECTED", str(exc), action, command_id, uav_id or "",
                    )
                return self._record(f"指令失败：{exc}")
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(
                "SUBMITTED", "指令已进入后台队列", action, command_id, uav_id or "",
            )
        label = labels.get(action, action)
        if uav_id:
            label = f"{uav_id} · {label}"
        self.command_history.insert(0, label)
        del self.command_history[8:]
        return self._record(f"蜂群指令已提交：{label}")

    def set_formation(self, name: str) -> str:
        labels = {"line": "一字", "diamond": "菱形", "square": "方形"}
        if name not in labels:
            return self._record(f"未知队形：{name}")
        return self.send_swarm_command("formation", parameters={"name": name})

    def configure_swarm_uris(self, uris: list[str]) -> str:
        if self.swarm_gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.swarm_gateway.configure_uris(uris)
            return self._record(f"已配置 {len(uris)} 个 Crazyflie URI")
        except RuntimeError as exc:
            return self._record(f"URI 配置失败：{exc}")

    def connect_swarm(self) -> str:
        if self.swarm_gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.swarm_gateway.connect()
            self.emergency_stopped = False
            return self._record("Crazyflie 连接任务已在后台启动")
        except RuntimeError as exc:
            return self._record(f"连接失败：{exc}")

    def disconnect_swarm(self) -> str:
        if self.swarm_gateway is None:
            return self._record("蜂群接口未配置")
        self.bci_control_enabled = False
        self.swarm_gateway.disconnect()
        return self._record("蜂群连接已安全关闭")

    def arm_swarm(self, confirmation: str) -> str:
        if self.swarm_gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.swarm_gateway.arm(confirmation)
            return self._record("实机控制已解锁；飞行指令将发送到硬件")
        except RuntimeError as exc:
            return self._record(f"解锁失败：{exc}")

    def disarm_swarm(self) -> str:
        self.bci_control_enabled = False
        if self.swarm_gateway is not None:
            self.swarm_gateway.disarm()
        return self._record("实机控制和脑控执行均已锁定")

    def set_bci_control_enabled(self, enabled: bool, eeg_source: str) -> str:
        if not enabled:
            self.bci_control_enabled = False
            self._pending_intent_label = ""
            self._pending_intent_count = 0
            return self._record("脑电识别继续运行，但自动飞行执行已关闭")
        if not eeg_source.lower().startswith("lsl"):
            return self._record("脑控执行被拒绝：只有实时 LSL 数据源可以驱动实机")
        if self.swarm_gateway is None:
            return self._record("脑控执行被拒绝：蜂群接口未配置")
        status = self.swarm_gateway.status()
        if not status.connected or not status.armed:
            return self._record("脑控执行被拒绝：请先连接并解锁实机控制")
        if status.backend != "crazyflie":
            return self._record("脑控执行被拒绝：Flow Deck 单机模式仅开放人工验收")
        self.bci_control_enabled = True
        return self._record("脑控执行已开启：同一意图连续确认两次后才提交")

    def handle_intent(
        self, label: str, accepted: bool, eeg_source: str, frequency_hz: float | None = None,
    ) -> str | None:
        if not self.bci_control_enabled:
            return None
        if not eeg_source.lower().startswith("lsl"):
            self.bci_control_enabled = False
            return self._record("数据源已不再是实时 LSL，脑控执行自动关闭")
        if not accepted:
            self._pending_intent_label = ""
            self._pending_intent_count = 0
            return None
        mapped_label = label
        if frequency_hz is not None:
            frequency_key = f"{float(frequency_hz):g}Hz"
            mapped_label = next(
                (entry.action for entry in self.mappings if entry.frequency == frequency_key),
                label,
            )
        action = {
            "起飞": "takeoff", "前进": "forward", "后退": "backward",
            "左转": "left", "右转": "right", "切换队形": "switch_formation",
        }.get(mapped_label)
        if action is None:
            return None
        if mapped_label == self._pending_intent_label:
            self._pending_intent_count += 1
        else:
            self._pending_intent_label = mapped_label
            self._pending_intent_count = 1
        if self._pending_intent_count < 2:
            return self._record(f"脑控候选待二次确认：{mapped_label}")
        now = time.monotonic()
        if now - self._last_bci_command_time < 2.5:
            return None
        self._pending_intent_count = 0
        self._last_bci_command_time = now
        return self.send_swarm_command(action)

    def emergency_stop(self) -> str:
        hardware_message = ""
        if not self.emergency_stopped and self.swarm_gateway is not None:
            try:
                self.swarm_gateway.emergency_stop()
                hardware_message = "，硬件急停调用已发送"
            except RuntimeError as exc:
                hardware_message = f"；硬件急停未送达：{exc}"
        self.emergency_stopped = True
        self.bci_control_enabled = False
        self.command_history.insert(0, "紧急停止")
        return self._record(f"本地控制已锁定{hardware_message}")

    def run_self_test(self) -> str:
        self.self_test_run = True
        if self.system_monitor is not None:
            metrics = self.system_monitor.snapshot()
            return self._record(
                f"自检完成：CPU {metrics.cpu_percent:.0f}% / 内存 {metrics.memory_percent:.0f}% / 延迟 {metrics.latency_ms:.0f}ms"
            )
        return self._record("自检完成：演示环境各模块响应正常")

    def clean_cache(self) -> str:
        self.storage_percent = max(0, self.storage_percent - 6)
        return self._record(f"缓存清理完成，存储使用率降至 {self.storage_percent}%")

    def create_backup(self) -> str:
        self.last_backup = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._record(f"系统备份完成：{self.last_backup}")

    def restore_backup(self) -> str:
        return self._record(f"已校验备份 {self.last_backup}（演示环境未覆盖当前文件）")

    def update_user_role(self, username: str, role: str) -> str:
        self.user_roles[username] = role
        return self._record(f"用户 {username} 的角色已修改为 {role}")

    def toggle_layout(self) -> str:
        self.layout_mode = "专注布局" if self.layout_mode == "标准布局" else "标准布局"
        return self._record(f"工作台已切换为{self.layout_mode}")

    def workspace_summary(self) -> str:
        return self._record(
            f"工作台状态：{len(self.loaded_algorithms)}个算法已加载，{len(self.mappings)}条映射"
        )

    def export_workspace_config(self, output_dir: Path | None = None) -> Path:
        directory = output_dir or Path.cwd() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"workspace-config-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = {
            "paradigm": {
                "type": self.paradigm_type,
                "frequency_hz": self.paradigm_frequency,
                "stimulus_size_percent": self.stimulus_size,
                "duty_cycle_percent": self.duty_cycle,
            },
            "algorithm": self.algorithm,
            "algorithm_mode": self.algorithm_mode,
            "default_algorithm": self.default_algorithm,
            "controller": self.controller,
            "controller_values": self.controller_values,
            "mappings": [entry.__dict__ for entry in self.mappings],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._record(f"工作台配置已导出：{path.name}")
        return path

    def export_soldier_report(self, output_dir: Path | None = None) -> Path:
        directory = output_dir or Path.cwd() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"soldier-report-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = {
            "soldier_id": "S-07",
            "name": "张伟",
            "heart_rate_bpm": 72,
            "attention": 85,
            "fatigue": 62,
            "stress": 40,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._record(f"士兵状态报告已导出：{path.name}")
        return path
