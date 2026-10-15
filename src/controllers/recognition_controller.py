"""Recognition-result handling for the main dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from PySide6.QtWidgets import QMessageBox, QWidget

from src.domain import IntentResult
from src.services import MissionPlanningError


class RecognitionHost(Protocol):
    intent_decoder: object
    actions: object
    _latest_recognition: IntentResult | None
    _eeg_events: object
    _pipeline_worker: object | None
    _stimulus_window: object | None

    def _notify(self, message: str) -> None: ...
    def update(self, *args) -> None: ...


class RecognitionController:
    """Apply decoder results to calibration, missions and command cooldown."""

    def __init__(self, host: RecognitionHost) -> None:
        self.host = host

    def process(self, result: IntentResult) -> None:
        host = self.host
        host._latest_recognition = result
        label = result.label if result.accepted else f"候选{result.label}（拒绝）"
        decoder_name = str(getattr(host.intent_decoder, "active_name", "EEG解码"))
        host._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  {decoder_name} {label}  "
            f"分数 {result.best_score:.3f}  差值 {result.margin:.3f}"
        )
        stimulus_window = host._stimulus_window
        if (
            stimulus_window is not None
            and stimulus_window.control_mode
            and stimulus_window.running
            and not stimulus_window.stimuli_active
        ):
            if stimulus_window.calibrating_idle:
                observe_idle = getattr(host.intent_decoder, "observe_idle", None)
                if callable(observe_idle):
                    observe_idle(result)
            host.update()
            return
        if host.actions.is_single_uav_mission(result):
            self.handle_single_uav_mission(result)
            host.update()
            return

        worker = host._pipeline_worker
        source = worker.source_name if worker is not None else ""
        previous_command_time = host.actions.bci.last_command_time
        command_message = host.actions.handle_intent(
            result.label,
            result.accepted,
            source,
            result.frequency_hz,
            safety_verified=result.safety_verified,
        )
        if command_message:
            host._eeg_events.appendleft(command_message)
        if (
            host.actions.bci.last_command_time > previous_command_time
            and stimulus_window is not None
        ):
            stimulus_window.begin_cooldown(
                host.actions.bci.command_cooldown_seconds,
                result.label,
            )
        host.update()

    def handle_single_uav_mission(self, result: IntentResult) -> None:
        host = self.host
        try:
            plan = host.actions.prepare_single_uav_mission(result)
        except MissionPlanningError as exc:
            message = f"任务规划失败：{exc}"
            host._eeg_events.appendleft(message)
            host._notify(message)
            return

        start = plan.start_position
        target = plan.target.position
        arrival_labels = {
            "HOLD": "到达后保持当前位置",
            "HOVER": "到达后定时悬停观察",
        }
        return_labels = {"NONE": "不自动返航", "RETURN_TO_START": "返回起点"}
        landing_labels = {"NONE": "不自动降落", "LAND": "任务结束后降落"}
        steps = "\n".join(
            f"{index}. {step.description}"
            for index, step in enumerate(plan.steps, start=1)
        )
        confirmation = (
            f"任务：{plan.task_label}\n"
            f"任务类型：{plan.policy.task_type}\n"
            f"执行无人机：{plan.uav_id}\n"
            f"当前位置：({start.x:.2f}, {start.y:.2f}, {start.z:.2f}) m\n"
            f"目标位置：({target.x:.2f}, {target.y:.2f}, {target.z:.2f}) m\n"
            f"去程最短距离：{plan.outbound_distance_m:.2f} m\n"
            f"预计总航程：{plan.total_distance_m:.2f} m\n"
            f"直线路径点：{len(plan.waypoints)} 个\n"
            f"预计耗时：{plan.estimated_duration_s:.1f} s\n\n"
            f"到达策略：{arrival_labels[plan.policy.arrival_action]}\n"
            f"返航策略：{return_labels[plan.policy.return_policy]}\n"
            f"降落策略：{landing_labels[plan.policy.landing_policy]}\n\n"
            f"将要执行：\n{steps}\n\n"
            "是否确认执行？"
        )
        answer = QMessageBox.question(
            host if isinstance(host, QWidget) else None,
            "单机任务确认",
            confirmation,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            message = f"已取消任务：{plan.task_label}"
            host._eeg_events.appendleft(message)
            host._notify(message)
            return

        message = host.actions.execute_single_uav_mission(plan)
        host._eeg_events.appendleft(message)
        host._notify(message)


__all__ = ["RecognitionController"]
