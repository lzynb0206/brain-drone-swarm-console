"""Research-workbench configuration and mutable presentation state."""

from __future__ import annotations

from collections.abc import Callable

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.services.ssvep_experiment_protocol import AutomaticExperimentConfig


class ResearchWorkbenchService:
    """Own paradigm, decoder and data-browser state."""

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
        self.data_tab = "标签管理"
        self.selected_session = 2
        self.log_filter = "INFO"
        self.marker_count = 0
        self.automatic_experiment_config = AutomaticExperimentConfig()

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
        if self.algorithm == "个人模板-SSVEP":
            return self._record(
                "60段单目标数据已用于个人模板＋FBCCA一致性识别；请在SSVEP测试页另采在线结果评估"
            )
        if self.algorithm == "Transformer-SSVEP":
            return self._record(
                "Transformer基准应按会话划分，并报告准确率、空闲误触发率和触发延迟"
            )
        return self._record("FBCCA 为无训练解码器；请使用同一批带标签试次作为基线")

    def add_marker(self, label: str = "") -> str:
        self.marker_count += 1
        suffix = f"：{label}" if label else ""
        return self._record(f"下一完整 EEG 试次已标注{suffix}")

    def generate_dataset(self) -> str:
        return self._record("当前阶段保存原始带标签试次，尚未执行训练/验证集划分")
