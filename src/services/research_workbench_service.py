"""Research-workbench configuration and mutable presentation state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ


@dataclass
class MappingEntry:
    frequency: str
    action: str
    parameter: str


class ResearchWorkbenchService:
    """Own paradigm, decoder, controller, mapping and data-browser state."""

    def __init__(self, record: Callable[[str], str]) -> None:
        self._record = record
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
        self.marker_count = 0

    def apply_paradigm(self) -> str:
        if self.paradigm_type != "SSVEP":
            return self._record(
                f"{self.paradigm_type} 采集模式已应用：仅保存带标签原始 EEG，不运行 FBCCA"
            )
        return self._record(
            f"范式已应用：{self.paradigm_frequency}Hz，尺寸{self.stimulus_size}%，占空比{self.duty_cycle}%"
        )

    def reset_paradigm(self) -> str:
        self.paradigm_frequency = TARGET_FREQUENCIES_HZ[0]
        self.stimulus_size = 40
        self.duty_cycle = 60
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
        if self.algorithm == "Transformer-SSVEP":
            return self._record(
                "Transformer基准应按会话划分，并报告准确率、空闲误触发率和触发延迟"
            )
        return self._record("FBCCA 为无训练解码器；请使用同一批带标签试次作为基线")

    def apply_controller(self) -> str:
        values = ", ".join(
            f"{key}={value / 100:.2f}" for key, value in self.controller_values.items()
        )
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
