"""Application facade composing focused workbench services."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.domain import IntentResult, MissionPlan
from src.ports import SwarmGateway, SystemMonitor
from src.services.admin_operations_service import AdminOperationsService
from src.services.bci_execution_service import BCIExecutionService
from src.services.export_service import WorkbenchExportService
from src.services.research_workbench_service import MappingEntry, ResearchWorkbenchService
from src.services.structured_mission_service import StructuredMissionService
from src.services.swarm_control_service import SwarmControlService


class WorkbenchService:
    """Stable facade for UI clients; behavior lives in focused collaborators."""

    _RESEARCH_FIELDS = frozenset({
        "paradigm_frequency", "paradigm_type", "stimulus_size", "duty_cycle",
        "algorithm", "algorithm_mode", "loaded_algorithms", "default_algorithm",
        "controller", "controller_values", "mappings", "data_tab",
        "selected_session", "log_filter", "marker_count", "automatic_experiment_config",
    })
    _ADMIN_FIELDS = frozenset({
        "storage_percent", "last_backup", "user_roles", "self_test_run", "layout_mode",
    })

    def __init__(
        self,
        swarm_gateway: SwarmGateway | None = None,
        system_monitor: SystemMonitor | None = None,
        experiment_recorder=None,
    ) -> None:
        object.__setattr__(self, "swarm_gateway", swarm_gateway)
        object.__setattr__(self, "system_monitor", system_monitor)
        object.__setattr__(self, "experiment_recorder", experiment_recorder)
        object.__setattr__(self, "event_log", [])

        research = ResearchWorkbenchService(self._record)
        swarm = SwarmControlService(swarm_gateway, experiment_recorder, self._record)
        bci = BCIExecutionService(
            swarm_gateway, research.mappings, swarm.send_command, self._record,
        )
        swarm.disable_bci = bci.disable
        admin = AdminOperationsService(system_monitor, self._record)
        exporter = WorkbenchExportService(research, self._record)
        object.__setattr__(self, "research", research)
        object.__setattr__(self, "swarm", swarm)
        object.__setattr__(self, "bci", bci)
        object.__setattr__(self, "admin", admin)
        object.__setattr__(self, "exporter", exporter)

        missions = StructuredMissionService(swarm_gateway, self._record, experiment_recorder)
        object.__setattr__(self, "structured_missions", missions)

    def __getattr__(self, name: str):
        if name in self._RESEARCH_FIELDS:
            return getattr(self.research, name)
        if name in self._ADMIN_FIELDS:
            return getattr(self.admin, name)
        if name == "command_history":
            return self.swarm.command_history
        if name == "emergency_stopped":
            return self.swarm.emergency_stopped
        if name == "bci_control_enabled":
            return self.bci.enabled
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        if name in self._RESEARCH_FIELDS and "research" in self.__dict__:
            setattr(self.research, name, value)
            if name == "mappings" and "bci" in self.__dict__:
                self.bci.mappings = value
            return
        if name in self._ADMIN_FIELDS and "admin" in self.__dict__:
            setattr(self.admin, name, value)
            return
        if name == "emergency_stopped" and "swarm" in self.__dict__:
            self.swarm.emergency_stopped = bool(value)
            return
        if name == "bci_control_enabled" and "bci" in self.__dict__:
            self.bci.enabled = bool(value)
            return
        object.__setattr__(self, name, value)

    def _record(self, message: str) -> str:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.insert(0, f"{stamp}  {message}")
        del self.event_log[40:]
        return message

    def apply_paradigm(self) -> str:
        return self.research.apply_paradigm()

    def reset_paradigm(self) -> str:
        return self.research.reset_paradigm()

    def select_algorithm(self, name: str) -> str:
        return self.research.select_algorithm(name)

    def load_algorithm(self) -> str:
        return self.research.load_algorithm()

    def unload_algorithm(self) -> str:
        return self.research.unload_algorithm()

    def set_default_algorithm(self) -> str:
        return self.research.set_default_algorithm()

    def benchmark_algorithm(self) -> str:
        return self.research.benchmark_algorithm()

    def apply_controller(self) -> str:
        return self.research.apply_controller()

    def save_controller_preset(self) -> str:
        return self.research.save_controller_preset()

    def reset_controller(self) -> str:
        return self.research.reset_controller()

    def add_mapping(self, frequency: str, action: str, parameter: str) -> str:
        return self.research.add_mapping(frequency, action, parameter)

    def update_mapping(self, index: int, action: str, parameter: str) -> str:
        return self.research.update_mapping(index, action, parameter)

    def delete_mapping(self, index: int) -> str:
        return self.research.delete_mapping(index)

    def add_marker(self, label: str = "") -> str:
        return self.research.add_marker(label)

    def generate_dataset(self) -> str:
        return self.research.generate_dataset()

    def prepare_single_uav_mission(self, result: IntentResult) -> MissionPlan:
        return self.structured_missions.prepare(result)

    def simulated_fbcca_result(self) -> IntentResult:
        return self.structured_missions.simulated_fbcca_result()

    def is_single_uav_mission(self, result: IntentResult) -> bool:
        return self.structured_missions.supports_result(result)

    def execute_single_uav_mission(self, plan: MissionPlan) -> str:
        try:
            self.structured_missions.execute(plan)
        except RuntimeError as exc:
            return self._record(f"单机任务执行失败：{exc}")
        return self._record(f"单机任务已提交：{plan.task_label} → {plan.target.name}")

    def send_swarm_command(self, action: str, uav_id: str | None = None, parameters=None) -> str:
        return self.swarm.send_command(action, uav_id, parameters)

    def configure_swarm_uris(self, uris: list[str]) -> str:
        return self.swarm.configure_uris(uris)

    def swarm_takeoff_height_m(self) -> float:
        return self.swarm.takeoff_height_m()

    def set_swarm_takeoff_height(self, height_m: float) -> str:
        return self.swarm.set_takeoff_height(height_m)

    def adjust_swarm_takeoff_height(self, delta_m: float) -> str:
        return self.swarm.adjust_takeoff_height(delta_m)

    def connect_swarm(self) -> str:
        return self.swarm.connect()

    def disconnect_swarm(self) -> str:
        return self.swarm.disconnect()

    def arm_swarm(self) -> str:
        return self.swarm.arm()

    def disarm_swarm(self) -> str:
        return self.swarm.disarm()

    def stop_swarm_motion(self) -> str:
        return self.swarm.stop_motion()

    def stop_swarm_motors(self) -> str:
        return self.swarm.stop_motors()

    def set_manual_control(self, vertical: float, forward: float, lateral: float) -> str | None:
        return self.swarm.set_manual_input(vertical, forward, lateral)

    def emergency_stop(self) -> str:
        return self.swarm.emergency_stop()

    def reset_emergency_after_reboot(self) -> str:
        return self.swarm.reset_emergency_after_reboot()

    def set_bci_control_enabled(self, enabled: bool, eeg_source: str) -> str:
        return self.bci.set_enabled(enabled, eeg_source)

    def start_bci_control_session(self, eeg_source: str) -> str:
        return self.bci.start_session(eeg_source)

    def handle_intent(
        self, label: str, accepted: bool, eeg_source: str,
        frequency_hz: float | None = None,
        safety_verified: bool = False,
    ) -> str | None:
        return self.bci.handle(
            label,
            accepted,
            eeg_source,
            frequency_hz,
            safety_verified=safety_verified,
        )

    def run_self_test(self) -> str:
        return self.admin.run_self_test()

    def clean_cache(self) -> str:
        return self.admin.clean_cache()

    def create_backup(self) -> str:
        return self.admin.create_backup()

    def restore_backup(self) -> str:
        return self.admin.restore_backup()

    def update_user_role(self, username: str, role: str) -> str:
        return self.admin.update_user_role(username, role)

    def toggle_layout(self) -> str:
        return self.admin.toggle_layout()

    def workspace_summary(self) -> str:
        return self.admin.workspace_summary(len(self.loaded_algorithms), len(self.mappings))

    def export_workspace_config(self, output_dir: Path | None = None) -> Path:
        return self.exporter.export_workspace_config(output_dir)

    def export_soldier_report(self, output_dir: Path | None = None) -> Path:
        return self.exporter.export_soldier_report(output_dir)


__all__ = ["MappingEntry", "WorkbenchService"]
