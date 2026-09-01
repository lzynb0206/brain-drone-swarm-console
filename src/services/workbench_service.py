"""Stateful application actions used by the interactive desktop prototype.

This module turns the visual prototype into a working demo without pretending
that real hardware is connected.  Every method has a stable place where a real
repository/device service can replace the current in-memory behaviour later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.domain import SwarmCommand
from src.ports import SwarmGateway, SystemMonitor


@dataclass
class MappingEntry:
    frequency: str
    action: str
    parameter: str


class WorkbenchService:
    """Owns mutable demo state and delegates device actions through Ports."""

    def __init__(
        self,
        swarm_gateway: SwarmGateway | None = None,
        system_monitor: SystemMonitor | None = None,
    ) -> None:
        self.swarm_gateway = swarm_gateway
        self.system_monitor = system_monitor
        self.paradigm_frequency = 8
        self.paradigm_type = "SSVEP"
        self.stimulus_size = 40
        self.duty_cycle = 60
        self.algorithm = "GraSPNet"
        self.algorithm_mode = "A/B测试"
        self.loaded_algorithms = {"ResMTN", "GCD", "GraSPNet", "PathoGeoNet", "CNN"}
        self.default_algorithm = "GraSPNet"
        self.controller = "PID"
        self.controller_values = {"Kp": 30, "Ki": 50, "Kd": 70}
        self.mappings = [
            MappingEntry("8Hz", "左转", "15°"),
            MappingEntry("12Hz", "右转", "15°"),
            MappingEntry("15Hz", "前进", "2m/s"),
        ]
        self.data_tab = "标签管理"
        self.selected_session = 2
        self.log_filter = "INFO"
        self.storage_percent = 68
        self.last_backup = "2026-08-30 22:00"
        self.user_roles = {"张伟": "士兵", "李明": "科研人员", "王强": "管理员"}
        self.marker_count = 0
        self.emergency_stopped = False
        self.self_test_run = False
        self.layout_mode = "标准布局"
        self.command_history: list[str] = ["前进", "悬停"]
        self.event_log: list[str] = []

    def _record(self, message: str) -> str:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.insert(0, f"{stamp}  {message}")
        del self.event_log[40:]
        return message

    def apply_paradigm(self) -> str:
        return self._record(
            f"范式已应用：{self.paradigm_frequency}Hz，尺寸{self.stimulus_size}%，占空比{self.duty_cycle}%"
        )

    def reset_paradigm(self) -> str:
        self.paradigm_frequency, self.stimulus_size, self.duty_cycle = 8, 40, 60
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
        scores = {"GraSPNet": "91.2% / 68ms", "ResMTN": "89.6% / 74ms", "GCD": "87.9% / 61ms"}
        result = scores.get(self.algorithm, "86.5% / 82ms")
        return self._record(f"{self.algorithm} 基准测试完成：准确率/延迟 {result}")

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

    def add_marker(self) -> str:
        self.marker_count += 1
        return self._record(f"已添加实验标签 Marker-{self.marker_count:03d}")

    def generate_dataset(self) -> str:
        return self._record("数据集任务完成：已按 8:2 生成训练/验证集（演示）")

    def send_swarm_command(self, action: str) -> str:
        labels = {"forward": "前进", "backward": "后退", "left": "左转", "right": "右转", "hover": "悬停"}
        if self.emergency_stopped:
            return self._record("系统处于急停状态，指令被安全策略拒绝")
        command = SwarmCommand(action)
        if self.swarm_gateway is not None:
            try:
                self.swarm_gateway.send_command(command)
            except RuntimeError as exc:
                return self._record(f"指令失败：{exc}")
        label = labels.get(action, action)
        self.command_history.insert(0, label)
        del self.command_history[8:]
        return self._record(f"蜂群指令已提交：{label}")

    def emergency_stop(self) -> str:
        if not self.emergency_stopped and self.swarm_gateway is not None:
            self.swarm_gateway.emergency_stop()
        self.emergency_stopped = True
        self.command_history.insert(0, "紧急停止")
        return self._record("紧急停止已执行，后续控制指令已锁定")

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
