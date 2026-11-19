"""Research-workbench configuration and mutable presentation state."""

from __future__ import annotations

from collections.abc import Callable

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.services.ssvep_experiment_protocol import AutomaticExperimentConfig
from src.services.hybrid_experiment_protocol import HybridExperimentConfig
from src.bci.montage import LEGACY, HYBRID_IDS
from src.services.acquisition_profiles import PROFILES_BY_ID


class ResearchWorkbenchService:
    """Own paradigm, decoder and data-browser state."""

    def __init__(self, record: Callable[[str], str]) -> None:
        self._record = record
        self.paradigm_frequency = TARGET_FREQUENCIES_HZ[0]
        self.paradigm_type = "SSVEP"
        self.stimulus_size = 40
        self.duty_cycle = 60
        self.decoder_settings_by_profile = {}
        self.data_tab = "标签管理"
        self.selected_session = 2
        self.log_filter = "INFO"
        self.automatic_experiment_config = AutomaticExperimentConfig()
        self.hybrid_experiment_config = HybridExperimentConfig(serial_pair_count=84)
        self.collection_schemes = {}
        self.montage_id = LEGACY.id
        self.recording_mode = "external_bdf"

    @property
    def acquisition_profile(self):
        key = ("external32" if self.recording_mode == "external_bdf" else
               "hybrid8" if self.montage_id in HYBRID_IDS else "ssvep8")
        return PROFILES_BY_ID[key]

    @property
    def decoder_settings(self):
        profile = self.acquisition_profile
        return self.decoder_settings_by_profile.get(profile.id, profile.default_settings())

    @property
    def algorithm(self):
        return self.acquisition_profile.algorithm

    @property
    def collection_scheme(self) -> str:
        return self.collection_schemes.get(self.paradigm_type, "formal")

    def apply_paradigm(self) -> str:
        if self.paradigm_type != "SSVEP":
            return self._record(
                f"{self.paradigm_type} 科研实验：在实验采集页选择独立呈现或本机 EEG；MI 模型离线验证"
            )
        return self._record(
            f"范式已应用：{self.paradigm_frequency}Hz，尺寸{self.stimulus_size}%，占空比{self.duty_cycle}%"
        )

    def reset_paradigm(self) -> str:
        self.paradigm_frequency = TARGET_FREQUENCIES_HZ[0]
        self.stimulus_size = 40
        self.duty_cycle = 60
        return self._record("范式参数已恢复默认")

    def generate_dataset(self) -> str:
        return self._record("当前阶段保存原始带标签试次，尚未执行训练/验证集划分")
