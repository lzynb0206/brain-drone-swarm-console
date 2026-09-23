"""Native PySide6 translation of every dashboard in the supplied React UI."""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.bci.config import COMMANDS, SSVEP_PROFILE_NAME, TARGET_FREQUENCIES_HZ, TRUE_LABELS
from src.domain import EEGFrame, IntentResult, Role, User
from src.ports import EEGStream, EngineerDataProvider, IntentDecoder, SwarmGateway, SystemMonitor
from src.services import ExperimentRecorder, MissionPlanningError, WorkbenchService
from src.views.engineer_dashboard import EngineerDashboard
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow
from src.workers import EEGWorker
from src.widgets.scifi_canvas import (
    BG,
    BLUE,
    BORDER,
    CYAN,
    GREEN,
    MUTED,
    PANEL,
    RED,
    TEXT,
    YELLOW,
    Canvas,
)


class MainView(QWidget):
    """A 1280×720 interactive canvas matching the reference front-end."""

    logout_requested = Signal()

    def __init__(
        self,
        eeg_stream: EEGStream | None = None,
        intent_decoder: IntentDecoder | None = None,
        experiment_recorder: ExperimentRecorder | None = None,
        swarm_gateway: SwarmGateway | None = None,
        system_monitor: SystemMonitor | None = None,
        engineer_data_provider: EngineerDataProvider | None = None,
    ) -> None:
        super().__init__()
        # 依赖从 app.py/container.py 注入。paintEvent 只读取线程安全缓存，
        # 不直接访问 EEG、无线电硬件或网络服务。
        self.eeg_stream = eeg_stream
        self.intent_decoder = intent_decoder
        self.experiment_recorder = experiment_recorder
        self.swarm_gateway = swarm_gateway
        self.system_monitor = system_monitor
        self.engineer_data_provider = engineer_data_provider
        self.actions = WorkbenchService(swarm_gateway, system_monitor, experiment_recorder)
        self.user: User | None = None
        self.research_page = "实验"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        self._regions: list[tuple[QRectF, str]] = []
        self._toast = ""
        self._toast_generation = 0
        self._toast_scroll_started = 0.0
        self._eeg_history = [deque(maxlen=360) for _ in range(8)]
        self._eeg_sample_rate = 0.0
        self._eeg_status = "尚未启动"
        self._latest_recognition: IntentResult | None = None
        self._experiment_session_id = ""
        self._eeg_events: deque[str] = deque(maxlen=12)
        self._pipeline_worker: EEGWorker | None = None
        self._stimulus_window: SSVEPStimulusWindow | None = None
        self._uri_dialog: QDialog | None = None
        self._uri_editor: QPlainTextEdit | None = None
        self._height_dialog: QDialog | None = None
        self._height_input: QDoubleSpinBox | None = None
        self._continuous_recording = False
        self._active_joystick: str | None = None
        self._manual_vertical = 0.0
        self._manual_forward = 0.0
        self._manual_lateral = 0.0
        if eeg_stream is not None and intent_decoder is not None and experiment_recorder is not None:
            self._pipeline_worker = EEGWorker(
                eeg_stream, intent_decoder, experiment_recorder, parent=self,
            )
            self._pipeline_worker.frame_received.connect(self._on_eeg_frame)
            self._pipeline_worker.recognition_received.connect(self._on_recognition)
            self._pipeline_worker.status_changed.connect(self._on_eeg_status)
            self._pipeline_worker.error_occurred.connect(self._on_eeg_error)
            self._pipeline_worker.labeled_trial_saved.connect(self._on_labeled_trial_saved)
            self._pipeline_worker.continuous_chunk_saved.connect(self._on_continuous_chunk_saved)
        # Kept as a small compatibility surface for integrations/tests.
        self.user_label = QLabel("", self)
        self.user_label.hide()
        self.engineer_dashboard: EngineerDashboard | None = None
        if system_monitor is not None and engineer_data_provider is not None:
            self.engineer_dashboard = EngineerDashboard(system_monitor, engineer_data_provider, self)
            self.engineer_dashboard.logout_requested.connect(self.logout_requested.emit)
            self.engineer_dashboard.hide()
        self._runtime_refresh_timer = QTimer(self)
        self._runtime_refresh_timer.timeout.connect(self._refresh_runtime)
        self._runtime_refresh_timer.start(300)
        self._toast_scroll_timer = QTimer(self)
        self._toast_scroll_timer.setInterval(33)
        self._toast_scroll_timer.timeout.connect(self._animate_toast)
        self.setMouseTracking(True)

    def set_user(self, user: User) -> None:
        self.user = user
        self.user_label.setText(f"{user.display_name}  ·  {user.role_label}")
        self.research_page = "实验"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        if self.engineer_dashboard is not None:
            self.engineer_dashboard.setVisible(user.role == Role.ENGINEER)
        if user.role in {Role.RESEARCHER, Role.SOLDIER}:
            self._experiment_session_id = f"session-{datetime.now():%Y%m%d-%H%M%S}-{user.username}"
            if self._pipeline_worker is not None:
                self._pipeline_worker.set_experiment_context(
                    user.uid or user.username, self._experiment_session_id,
                )
            self._start_eeg_pipeline()
        else:
            self.deactivate()
        self.update()

    def _start_eeg_pipeline(self) -> None:
        if self._pipeline_worker is not None and not self._pipeline_worker.isRunning():
            self._eeg_status = "正在连接数据源…"
            self._pipeline_worker.start()

    def deactivate(self) -> None:
        if self._stimulus_window is not None:
            self._stimulus_window.close()
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._pipeline_worker.stop()

    def _on_eeg_frame(self, frame: EEGFrame) -> None:
        if len(self._eeg_history) != len(frame.samples):
            self._eeg_history = [deque(maxlen=360) for _ in frame.samples]
        for history, values in zip(self._eeg_history, frame.samples):
            history.extend(float(value) for value in values)
        self._eeg_sample_rate = frame.sample_rate_hz
        self.update()

    def _on_recognition(self, result: IntentResult) -> None:
        self._latest_recognition = result
        label = result.label if result.accepted else f"候选{result.label}（拒绝）"
        self._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  FBCCA {label}  分数 {result.best_score:.3f}  差值 {result.margin:.3f}"
        )
        if self.actions.is_single_uav_mission(result):
            self._handle_single_uav_mission(result)
            self.update()
            return

        source = self._pipeline_worker.source_name if self._pipeline_worker is not None else ""
        command_message = self.actions.handle_intent(
            result.label, result.accepted, source, result.frequency_hz,
        )
        if command_message:
            self._eeg_events.appendleft(command_message)
        self.update()

    def _run_fixed_test_mission(self) -> None:
        """临时按钮入口：模拟 EEG 后端传入一条已解码的结构化任务。"""
        result = IntentResult(
            timestamp=time.time(),
            label="去A点",
            confidence=0.96,
            margin=0.32,
            accepted=True,
            reason="临时按钮测试任务",
            frequency_hz=0.0,
            scores={"去A点": 0.96},
            sample_rate_hz=0.0,
            sample_count=0,
            used_channels_1based=(),
            signal_quality={"source": "temporary_test_button"},
            task_type="GOTO",
            target_id="A",
            arrival_action="HOLD",
            observe_seconds=0.0,
            return_policy="NONE",
            landing_policy="NONE",
        )
        self._on_recognition(result)

    def _handle_single_uav_mission(self, result: IntentResult) -> None:
        try:
            plan = self.actions.prepare_single_uav_mission(result)
        except MissionPlanningError as exc:
            message = f"任务规划失败：{exc}"
            self._eeg_events.appendleft(message)
            self._notify(message)
            return

        start = plan.start_position
        target = plan.target.position
        arrival_labels = {"HOLD": "到达后保持当前位置", "HOVER": "到达后定时悬停观察"}
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
            self,
            "单机任务确认",
            confirmation,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            message = f"已取消任务：{plan.task_label}"
            self._eeg_events.appendleft(message)
            self._notify(message)
            return

        message = self.actions.execute_single_uav_mission(plan)
        self._eeg_events.appendleft(message)
        self._notify(message)

    def _on_eeg_status(self, status: str) -> None:
        self._eeg_status = status
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  {status}")
        self.update()

    def _on_eeg_error(self, message: str) -> None:
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  ERROR  {message}")
        self.update()

    def _on_labeled_trial_saved(self, label: str, path: str) -> None:
        self._notify(f"真实标签“{label}”和原始 EEG 已保存：{path}")

    def _on_continuous_chunk_saved(self, path: str) -> None:
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  连续 EEG 已保存：{path}")
        self.update()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._center_joysticks(send=True)
        self.deactivate()
        if self.swarm_gateway is not None:
            self.swarm_gateway.disconnect()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        c = Canvas(painter, self.width(), self.height())
        self._regions = []
        role = self.user.role if self.user else Role.SOLDIER
        if role == Role.ENGINEER and self.engineer_dashboard is not None:
            c.fill("#071020")
            return
        if role == Role.ADMIN:
            self._paint_admin(c)
        elif role == Role.RESEARCHER:
            self._paint_research(c)
        elif role == Role.ENGINEER:
            self._paint_engineer(c)
        else:
            self._paint_soldier(c)
        c.logout()
        self._hit(1224, 663, 56, 57, "logout")
        if self._toast:
            self._paint_toast(c)

    def _paint_toast(self, c: Canvas) -> None:
        c.rect(390, 655, 500, 45, "#15243a", CYAN, 7, 1.5)
        viewport = QRectF(410, 655, 460, 45)
        font = QFont(c.p.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Bold)
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(self._toast)
        if text_width <= viewport.width():
            c.text(410, 655, 460, 45, self._toast, 12, TEXT, True, Qt.AlignCenter)
            return

        elapsed = max(0.0, time.monotonic() - self._toast_scroll_started)
        offset = max(0.0, elapsed - 0.8) * 45.0
        gap = 90.0
        cycle = text_width + gap
        x = viewport.left() - (offset % cycle)
        baseline = viewport.top() + (viewport.height() - metrics.height()) / 2 + metrics.ascent()
        c.p.save()
        try:
            c.p.setClipRect(viewport)
            c.p.setFont(font)
            c.p.setPen(QColor(TEXT))
            c.p.drawText(QPointF(x, baseline), self._toast)
            c.p.drawText(QPointF(x + cycle, baseline), self._toast)
        finally:
            c.p.restore()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        if self.engineer_dashboard is not None:
            self.engineer_dashboard.setGeometry(self.rect())
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        point = QPointF(x, y)
        for rect, action in reversed(self._regions):
            if rect.contains(point):
                if action in {"manual-stick-height", "manual-stick-direction"}:
                    self._active_joystick = action
                    self._update_manual_stick(action, x, y)
                    self.grabMouse()
                    return
                self._activate(action, x, y)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        if self._active_joystick is not None:
            self._update_manual_stick(self._active_joystick, x, y)
            return
        interactive = any(rect.contains(QPointF(x, y)) for rect, _ in self._regions)
        self.setCursor(Qt.PointingHandCursor if interactive else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._active_joystick is not None:
            active = self._active_joystick
            self._active_joystick = None
            if active == "manual-stick-height":
                self._manual_vertical = 0.0
            else:
                self._manual_forward = 0.0
                self._manual_lateral = 0.0
            self.releaseMouse()
            self._send_manual_input()
            self.update()
            return
        super().mouseReleaseEvent(event)

    def _update_manual_stick(self, stick: str, x: float, y: float) -> None:
        if stick == "manual-stick-height":
            center_y, travel = 386.0, 82.0
            self._manual_vertical = max(-1.0, min(1.0, (center_y - y) / travel))
        else:
            center_x, center_y, travel = 760.0, 386.0, 82.0
            dx, dy = x - center_x, y - center_y
            length = math.hypot(dx, dy)
            if length > travel:
                dx, dy = dx * travel / length, dy * travel / length
            self._manual_lateral = max(-1.0, min(1.0, dx / travel))
            self._manual_forward = max(-1.0, min(1.0, -dy / travel))
        self._send_manual_input()
        self.update()

    def _send_manual_input(self) -> None:
        message = self.actions.set_manual_control(
            self._manual_vertical, self._manual_forward, self._manual_lateral,
        )
        if message:
            self._active_joystick = None
            self._manual_vertical = self._manual_forward = self._manual_lateral = 0.0
            self.releaseMouse()
            self._notify(message)

    def _center_joysticks(self, send: bool = False) -> None:
        self._active_joystick = None
        self._manual_vertical = self._manual_forward = self._manual_lateral = 0.0
        if send:
            self.actions.set_manual_control(0.0, 0.0, 0.0)
        self.update()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        # This works while the application has focus on both macOS and Windows.
        # A macOS system-wide key hook would require an explicit Accessibility grant.
        if event.key() == Qt.Key_B and not event.isAutoRepeat():
            self._notify(self.actions.emergency_stop())
            return
        super().keyPressEvent(event)

    def _refresh_runtime(self) -> None:
        self.update()

    def _hit(self, x: float, y: float, w: float, h: float, action: str) -> None:
        self._regions.append((QRectF(x, y, w, h), action))

    def _notify(self, message: str) -> None:
        self._toast = message
        self._toast_generation += 1
        self._toast_scroll_started = time.monotonic()
        self._toast_scroll_timer.start()
        generation = self._toast_generation
        display_ms = max(2600, min(18000, 2200 + max(0, len(message) - 28) * 260))
        QTimer.singleShot(display_ms, lambda: self._clear_toast(generation))
        self.update()

    def _animate_toast(self) -> None:
        if self._toast:
            self.update()
        else:
            self._toast_scroll_timer.stop()

    def _clear_toast(self, generation: int) -> None:
        if generation == self._toast_generation:
            self._toast = ""
            self._toast_scroll_timer.stop()
            self.update()

    def _activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Route every painted control to a stateful application action."""
        if action == "logout":
            self.deactivate()
            self.logout_requested.emit()
        elif action.startswith("research:"):
            self.research_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("engineer:"):
            self.engineer_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-root:"):
            self.soldier_root = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-page:"):
            self.soldier_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("paradigm-frequency:"):
            self.actions.paradigm_frequency = float(action.rsplit(":", 1)[1])
            self.update()
        elif action.startswith("paradigm-type:"):
            self.actions.paradigm_type = action.split(":", 1)[1]
            self._notify(f"脑控范式已切换：{self.actions.paradigm_type}")
        elif action == "paradigm-size":
            self.actions.stimulus_size = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-duty":
            self.actions.duty_cycle = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-apply":
            self._notify(self.actions.apply_paradigm())
        elif action == "paradigm-reset":
            self._notify(self.actions.reset_paradigm())
        elif action == "paradigm-start":
            self._open_ssvep_stimulus()
        elif action == "continuous-record-toggle":
            if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
                self._notify("EEG 采集未运行，不能开始连续录制")
                return
            self._continuous_recording = not self._continuous_recording
            self._pipeline_worker.set_continuous_recording(self._continuous_recording)
            self._notify("连续原始 EEG 录制已开启" if self._continuous_recording else "连续原始 EEG 录制已停止并保存")
        elif action.startswith("algorithm-select:"):
            self._notify(self.actions.select_algorithm(action.split(":", 1)[1]))
        elif action.startswith("algorithm-mode:"):
            self.actions.algorithm_mode = action.split(":", 1)[1]
            self._notify(f"算法模式已切换：{self.actions.algorithm_mode}")
        elif action == "algorithm-load":
            self._notify(self.actions.load_algorithm())
        elif action == "algorithm-unload":
            self._notify(self.actions.unload_algorithm())
        elif action == "algorithm-default":
            self._notify(self.actions.set_default_algorithm())
        elif action == "algorithm-benchmark":
            self._notify(self.actions.benchmark_algorithm())
        elif action.startswith("controller-select:"):
            self.actions.controller = action.split(":", 1)[1]
            self._notify(f"控制器已选择：{self.actions.controller}")
        elif action.startswith("controller-slider:"):
            key = action.rsplit(":", 1)[1]
            self.actions.controller_values[key] = max(0, min(100, round((x - 203) / 707 * 100)))
            self.update()
        elif action == "controller-apply":
            self._notify(self.actions.apply_controller())
        elif action == "controller-save":
            self._notify(self.actions.save_controller_preset())
        elif action == "controller-reset":
            self._notify(self.actions.reset_controller())
        elif action == "mapping-add":
            self._add_mapping_dialog()
        elif action.startswith("mapping-edit:"):
            self._edit_mapping_dialog(int(action.rsplit(":", 1)[1]))
        elif action.startswith("mapping-delete:"):
            self._notify(self.actions.delete_mapping(int(action.rsplit(":", 1)[1])))
        elif action.startswith("data-tab:"):
            self.actions.data_tab = action.split(":", 1)[1]
            if self.actions.data_tab == "数据集生成":
                self._notify(self.actions.generate_dataset())
            else:
                self.update()
        elif action.startswith("data-session:"):
            self.actions.selected_session = int(action.rsplit(":", 1)[1])
            self.update()
        elif action == "marker-add":
            if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
                self._notify("EEG 采集未运行，无法标注试次")
                return
            label, ok = QInputDialog.getItem(
                self, "标注下一次 EEG 试次", "真实标签", list(TRUE_LABELS), 0, False,
            )
            if ok:
                self._pipeline_worker.capture_next_trial(label)
                self._notify(self.actions.add_marker(label))
        elif action.startswith("log-filter:"):
            self.actions.log_filter = action.split(":", 1)[1]
            self.update()
        elif action == "self-test":
            self._notify(self.actions.run_self_test())
        elif action == "admin-clean-cache":
            self._notify(self.actions.clean_cache())
        elif action == "admin-backup":
            self._notify(self.actions.create_backup())
        elif action == "admin-restore":
            self._notify(self.actions.restore_backup())
        elif action.startswith("admin-device:"):
            self._show_device_dialog(action.split(":", 1)[1])
        elif action.startswith("admin-user:"):
            self._edit_user_dialog(action.split(":", 1)[1])
        elif action.startswith("swarm-command:"):
            self._notify(self.actions.send_swarm_command(action.split(":", 1)[1]))
        elif action == "swarm-configure-uris":
            self._configure_swarm_uris_dialog()
        elif action == "swarm-height-decrease":
            self._notify(self.actions.adjust_swarm_takeoff_height(-0.05))
        elif action == "swarm-height-increase":
            self._notify(self.actions.adjust_swarm_takeoff_height(0.05))
        elif action == "swarm-height-custom":
            self._show_takeoff_height_dialog()
        elif action == "swarm-single-control":
            self._single_uav_control_dialog()
        elif action == "mission-test":
            self._run_fixed_test_mission()
        elif action == "swarm-connect":
            self._notify(self.actions.connect_swarm())
        elif action == "swarm-disconnect":
            self._center_joysticks(send=True)
            self._notify(self.actions.disconnect_swarm())
        elif action == "swarm-arm":
            self._notify(self.actions.arm_swarm())
        elif action == "swarm-disarm":
            self._center_joysticks(send=True)
            self._notify(self.actions.disarm_swarm())
        elif action == "swarm-stop-motion":
            self._notify(self.actions.stop_swarm_motion())
        elif action == "swarm-motor-stop":
            self._center_joysticks(send=True)
            self._request_motor_stop()
        elif action == "bci-control-toggle":
            source = self._pipeline_worker.source_name if self._pipeline_worker is not None else ""
            self._notify(self.actions.set_bci_control_enabled(not self.actions.bci_control_enabled, source))
        elif action == "swarm-emergency-stop":
            self._center_joysticks(send=False)
            self._notify(self.actions.emergency_stop())
        elif action == "swarm-emergency-reset":
            self._notify(self.actions.reset_emergency_after_reboot())
        elif action == "soldier-export":
            path = self.actions.export_soldier_report()
            self._notify(f"报告已导出：{path}")
        elif action == "workspace-export":
            path = self.actions.export_workspace_config()
            self._notify(f"配置已导出：{path}")
        elif action == "workspace-summary":
            self._notify(self.actions.workspace_summary())
        elif action == "workspace-layout":
            self._notify(self.actions.toggle_layout())
        elif action == "workspace-help":
            self._notify("操作提示：选择模块后点击控件；所有结果会在底部提示")

    def _request_motor_stop(self) -> None:
        status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        if status is not None and status.airborne:
            answer = QMessageBox.warning(
                self,
                "确认立即停止电机",
                "无人机仍在空中。继续会立即关闭电机，飞机将直接下落。\n\n"
                "一般情况请优先点击“降落”；只有失控时才继续。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._notify("已取消电机停转")
                return
        self._notify(self.actions.stop_swarm_motors())

    def _open_ssvep_stimulus(self) -> None:
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            self._notify("请先确保 EEG 数据源已经连接")
            return
        if self._stimulus_window is None:
            self._stimulus_window = SSVEPStimulusWindow(parent=self)
            self._stimulus_window.status_changed.connect(self._on_eeg_status)
            self._stimulus_window.trial_started.connect(self._pipeline_worker.capture_stimulus_trial)
            self._stimulus_window.event_marker.connect(self._pipeline_worker.record_stimulus_marker)
            self._stimulus_window.emergency_requested.connect(
                lambda: self._notify(self.actions.emergency_stop())
            )
            self._stimulus_window.trial_stopped.connect(
                lambda label, path: self._notify(
                    f"刺激试次已结束：{label}" + (f"，测频记录：{path}" if path else "")
                )
            )
        self._stimulus_window.showFullScreen()
        self._stimulus_window.raise_()
        self._stimulus_window.activateWindow()
        self._stimulus_window.setFocus(Qt.OtherFocusReason)

    def _configure_swarm_uris_dialog(self) -> None:
        if self._uri_dialog is not None:
            self._uri_dialog.raise_()
            self._uri_dialog.activateWindow()
            return
        current = ""
        config = getattr(self.swarm_gateway, "config", None)
        if config is not None:
            current = "\n".join(getattr(config, "uris", ()))
        self._release_mouse_capture_for_dialog()
        dialog = QDialog(self)
        dialog.setWindowTitle("配置 Crazyflie URI")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.resize(520, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("当前 Flow Hover 模式只填写一个 URI（必须先断开）：", dialog))
        editor = QPlainTextEdit(dialog)
        editor.setPlainText(current)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._uri_dialog = dialog
        self._uri_editor = editor
        dialog.accepted.connect(lambda: self._accept_swarm_uris_dialog(editor.toPlainText()))
        dialog.rejected.connect(lambda: self._notify("已取消 URI 配置"))
        dialog.finished.connect(lambda _result: self._finish_swarm_uris_dialog(dialog))
        QTimer.singleShot(0, lambda: self._open_uri_dialog_if_current(dialog))

    def _open_uri_dialog_if_current(self, dialog: QDialog) -> None:
        if self._uri_dialog is dialog:
            dialog.open()

    def _accept_swarm_uris_dialog(self, text: str) -> None:
        uris = [line.strip() for line in text.splitlines() if line.strip()]
        self._notify(self.actions.configure_swarm_uris(uris))

    def _finish_swarm_uris_dialog(self, dialog: QDialog) -> None:
        if self._uri_dialog is dialog:
            self._uri_dialog = None
            self._uri_editor = None
        dialog.deleteLater()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)

    def _show_takeoff_height_dialog(self) -> None:
        if self._height_dialog is not None:
            self._height_dialog.raise_()
            self._height_dialog.activateWindow()
            return
        config = getattr(self.swarm_gateway, "config", None)
        minimum = float(getattr(config, "flow_min_height_m", 0.2))
        maximum = float(getattr(config, "flow_max_height_m", 2.0))
        self._release_mouse_capture_for_dialog()
        dialog = QDialog(self)
        dialog.setWindowTitle("设置 Flow Hover 启动高度")
        dialog.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"输入目标离地高度（{minimum:.2f}–{maximum:.2f}m）：", dialog))
        height_input = QDoubleSpinBox(dialog)
        height_input.setRange(minimum, maximum)
        height_input.setDecimals(2)
        height_input.setSingleStep(0.05)
        height_input.setSuffix(" m")
        height_input.setValue(self.actions.swarm_takeoff_height_m())
        layout.addWidget(height_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._height_dialog = dialog
        self._height_input = height_input
        dialog.accepted.connect(
            lambda: self._notify(self.actions.set_swarm_takeoff_height(height_input.value()))
        )
        dialog.rejected.connect(lambda: self._notify("已取消目标高度设置"))
        dialog.finished.connect(lambda _result: self._finish_takeoff_height_dialog(dialog))
        QTimer.singleShot(0, lambda: self._open_height_dialog_if_current(dialog))

    def _open_height_dialog_if_current(self, dialog: QDialog) -> None:
        if self._height_dialog is dialog:
            dialog.open()

    def _finish_takeoff_height_dialog(self, dialog: QDialog) -> None:
        if self._height_dialog is dialog:
            self._height_dialog = None
            self._height_input = None
        dialog.deleteLater()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)

    def _release_mouse_capture_for_dialog(self) -> None:
        self._active_joystick = None
        grabber = QWidget.mouseGrabber()
        if grabber is self:
            self.releaseMouse()

    def _single_uav_control_dialog(self) -> None:
        if self.swarm_gateway is None:
            self._notify("蜂群接口未配置")
            return
        identifiers = [item.uav_id for item in self.swarm_gateway.list_uavs()]
        if not identifiers:
            self._notify("尚未收到无人机遥测")
            return
        uav_id, ok = QInputDialog.getItem(self, "单架无人机控制", "无人机", identifiers, 0, False)
        if not ok:
            return
        labels = ("前进", "后退", "左移", "右移", "悬停")
        action_map = {"前进": "forward", "后退": "backward", "左移": "left", "右移": "right", "悬停": "hover"}
        label, ok = QInputDialog.getItem(self, "单架无人机控制", "动作", labels, 0, False)
        if ok:
            self._notify(self.actions.send_swarm_command(action_map[label], uav_id=uav_id))

    def _add_mapping_dialog(self) -> None:
        frequency, ok = QInputDialog.getItem(
            self, "添加映射", "SSVEP频率", [f"{value:g}Hz" for value in TARGET_FREQUENCIES_HZ], 0, False,
        )
        if not ok:
            return
        command, ok = QInputDialog.getItem(self, "添加映射", "映射动作", list(COMMANDS), 0, False)
        if not ok:
            return
        parameter, ok = QInputDialog.getText(self, "添加映射", "参数", text="15°")
        if ok and parameter.strip():
            self._notify(self.actions.add_mapping(frequency, command, parameter.strip()))

    def _edit_mapping_dialog(self, index: int) -> None:
        entry = self.actions.mappings[index]
        commands = list(COMMANDS)
        command, ok = QInputDialog.getItem(
            self, "修改映射", "映射动作", commands,
            commands.index(entry.action) if entry.action in commands else 0, False,
        )
        if not ok:
            return
        parameter, ok = QInputDialog.getText(self, "修改映射", "参数", text=entry.parameter)
        if ok and parameter.strip():
            self._notify(self.actions.update_mapping(index, command, parameter.strip()))

    def _show_device_dialog(self, name: str) -> None:
        swarm_status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        details = {
            "DeepBCI采集板": "类型：脑电采集设备\n固件：v2.1\n状态：在线\n采样率：250Hz",
            "Crazyflie-01": (
                f"类型：无人机\n接口：{swarm_status.backend}\n状态：{swarm_status.state}\n{swarm_status.message}"
                if swarm_status is not None else "Crazyflie 接口未配置"
            ),
            "智能头盔": "类型：多模态头盔\n固件：v1.0\n状态：离线\n最近在线：2026-08-30",
        }
        QMessageBox.information(self, f"设备详情 · {name}", details.get(name, "暂无设备信息"))

    def _edit_user_dialog(self, username: str) -> None:
        roles = ["士兵", "科研人员", "系统工程师", "管理员"]
        current = self.actions.user_roles.get(username, roles[0])
        role, ok = QInputDialog.getItem(
            self, f"编辑用户 · {username}", "角色", roles,
            roles.index(current) if current in roles else 0, False,
        )
        if ok:
            self._notify(self.actions.update_user_role(username, role))

    # ---------- Researcher dashboard ----------

    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        nav = [("实验", 234), ("范式", 302), ("算法", 370), ("控制", 438), ("映射", 506), ("数据", 574)]
        for name, x in nav:
            self._hit(x, 0, 62, 57, f"research:{name}")
        self._hit(642, 0, 60, 56, "self-test")
        self._hit(710, 0, 60, 56, "workspace-help")
        self._hit(1020, 8, 36, 38, "logout")
        self._hit(1068, 8, 36, 38, "workspace-summary")
        self._hit(1117, 8, 36, 38, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        {
            "实验": self._research_eeg,
            "范式": self._research_paradigm,
            "算法": self._research_algorithm,
            "控制": self._research_controller,
            "映射": self._research_mapping,
            "数据": self._research_data,
        }[self.research_page](c)

    def _research_eeg(self, c: Canvas) -> None:
        # EEG is acquired and decoded in EEGWorker. paintEvent only
        # renders cached values and never blocks on CSV/LSL or performs inference.
        c.panel(17, 73, 824, 305, "EEG 8通道实时波形")
        self._draw_live_eeg(c, 62, 124, 756, 232)

        c.panel(859, 73, 404, 305, "纯 FBCCA 识别结果")
        c.text(879, 116, 360, 25, self._eeg_status, 12, CYAN, True)
        if self._latest_recognition is None:
            c.text(879, 158, 360, 42, "等待积累完整 4 秒 EEG 窗口…", 14, MUTED)
        else:
            result = self._latest_recognition
            prediction = result.label if result.accepted else f"候选：{result.label}"
            c.text(879, 151, 150, 45, prediction, 24, GREEN if result.accepted else YELLOW, True)
            c.text(1040, 157, 195, 30, result.reason, 12, MUTED, False, Qt.AlignRight)
            c.text(879, 195, 350, 25, f"最高分 {result.best_score:.3f}   区分度 {result.margin:.3f}", 12, TEXT)
            for index, (label, score) in enumerate(result.scores.items()):
                y = 228 + index * 25
                c.text(879, y, 70, 20, label, 11, TEXT)
                c.rect(947, y + 5, 225, 9, "#1c2940", None, 4)
                c.rect(947, y + 5, min(225, score / max(0.001, result.best_score) * 225), 9, BLUE, None, 4)
                c.text(1178, y - 2, 55, 22, f"{score:.3f}", 10, MUTED, False, Qt.AlignRight)

        c.panel(17, 398, 824, 304, "采集与试次保存")
        source = self._pipeline_worker.source_name if self._pipeline_worker is not None else "未配置"
        pending = self._pipeline_worker.pending_true_label() if self._pipeline_worker is not None else ""
        stats = self.experiment_recorder.stats() if self.experiment_recorder is not None else {"recognitions": 0, "labeled_trials": 0}
        cards = (
            ("数据源", source, 41),
            ("采样率", f"{self._eeg_sample_rate or 250:.0f} Hz", 305),
            ("已记录识别", str(stats["recognitions"]), 569),
        )
        for title, value, x in cards:
            c.rect(x, 455, 248, 70, "#152239", "#2b3b58", 3)
            c.text(x + 14, 461, 220, 22, title, 11, MUTED)
            c.text(x + 14, 486, 220, 26, value, 14, TEXT, True)
        c.rect(41, 544, 760, 116, "#0c1628", "#2b3b58", 3)
        c.text(58, 553, 300, 28, f"已保存带标签原始试次：{stats['labeled_trials']}", 13, TEXT, True)
        c.text(58, 584, 700, 24, f"待保存真实标签：{pending or '无（点击右上角“添加标签”）'}", 12, CYAN)
        c.text(58, 605, 700, 20, f"会话：{self._experiment_session_id or '未建立'}", 10, MUTED)
        root = str(self.experiment_recorder.root) if self.experiment_recorder is not None else "未配置"
        c.text(58, 625, 720, 24, f"保存目录：{root}", 10, MUTED)
        c.button(58, 665, 198, 27,
                 "停止连续 EEG 录制" if self._continuous_recording else "开始连续 EEG 录制",
                 self._continuous_recording, accent=RED)
        self._hit(58, 662, 198, 34, "continuous-record-toggle")
        c.text(272, 665, 520, 27, "连续数据按 10 秒分块保存，停止时写入最后一块", 10, GREEN)

        c.panel(859, 398, 404, 304, "实时事件")
        if not self._eeg_events:
            c.text(879, 448, 345, 24, "暂无采集事件", 12, MUTED)
        for index, message in enumerate(list(self._eeg_events)[:11]):
            color = RED if "ERROR" in message else TEXT
            c.text(879, 438 + index * 22, 354, 20, message, 10, color)

    def _draw_live_eeg(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        histories = [list(values) for values in self._eeg_history]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 EEG 数据…", 15, MUTED, False, Qt.AlignCenter)
            return
        palette = ("#ef514d", "#f1c64b", "#62df8a", "#65d9e9", "#6c9bd9", "#987bd8", "#ef7ac8", "#e5e9f0")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#1d2c45")
            c.text(x, baseline - 11, 35, 20, f"CH{index + 1}", 9, MUTED)
            if len(values) < 2:
                continue
            center = sum(values) / len(values)
            amplitude = max(max(abs(value - center) for value in values), 1e-9)
            path = QPainterPath()
            for sample_index, value in enumerate(values):
                px = x + 40 + sample_index / max(1, len(values) - 1) * (w - 42)
                py = baseline - (value - center) / amplitude * row_height * 0.39
                if sample_index == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index % len(palette)]), 1.1))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.p.restore()

    def _research_paradigm(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-06 / M4.6：“应用配置”应调用 ParadigmService：
        # 校验 SSVEP 频率/刺激尺寸/占空比，保存到 YAML/JSON，并通知刺激程序；
        # 每次范式开始/结束还应向 LSL 写入 SSVEP_8Hz_start 等事件标记。
        c.rect(207, 262, 384, 252, PANEL, BORDER, 4)
        c.rect(232, 287, 47, 202, "#111c31", BLUE, 3)
        c.p.save()
        c.p.translate(255, 388)
        c.p.rotate(90)
        c.text(-55, -17, 110, 34, "SSVEP", 15, CYAN, True, Qt.AlignCenter)
        c.p.restore()
        options = [("SSVEP", 287), ("MI", 361), ("SSVEP+MI混合", 434)]
        for name, y in options:
            active = self.actions.paradigm_type == name
            c.rect(296, y, 270, 57, "#1a263a" if active else "#172236", BLUE if active else None, 3)
            c.text(312, y, 210, 57, name, 15, TEXT if active else MUTED)
            c.text(528, y, 22, 57, "⌄", 16, MUTED, False, Qt.AlignCenter)
            self._hit(296, y, 270, 57, f"paradigm-type:{name}")
        c.rect(623, 205, 450, 366, PANEL, BORDER, 4)
        c.text(656, 229, 380, 42, "SSVEP参数配置", 19, TEXT, True)
        c.text(656, 282, 130, 27, "频率配置", 14, MUTED)
        for index, frequency in enumerate(TARGET_FREQUENCIES_HZ):
            x, width = 656 + index * 63, 58
            c.button(x, 322, width, 42, f"{frequency:g}Hz", self.actions.paradigm_frequency == frequency)
            self._hit(x, 322, width, 42, f"paradigm-frequency:{frequency}")
        knobs = (
            ("刺激尺寸", 390, 739 + 299 * self.actions.stimulus_size / 100, "paradigm-size"),
            ("占空比", 437, 739 + 299 * self.actions.duty_cycle / 100, "paradigm-duty"),
        )
        for label, y, knob, action in knobs:
            c.text(656, y - 8, 88, 34, label, 14, MUTED)
            c.line(739, y + 7, 1038, y + 7, "#e6e8eb", 7)
            c.line(739, y + 7, knob, y + 7, BLUE, 7)
            c.circle(knob, y + 7, 8, "#477af0")
            c.text(1044, y - 8, 45, 30, f"{self.actions.stimulus_size if action == 'paradigm-size' else self.actions.duty_cycle}%", 11, TEXT)
            self._hit(730, y - 5, 318, 25, action)
        c.button(656, 492, 116, 46, "应用配置", True)
        c.button(783, 492, 116, 46, "恢复默认")
        c.button(910, 492, 130, 46, "全屏刺激", True, accent=CYAN)
        self._hit(656, 492, 116, 46, "paradigm-apply")
        self._hit(783, 492, 116, 46, "paradigm-reset")
        self._hit(910, 492, 130, 46, "paradigm-start")

    def _research_algorithm(self, c: Canvas) -> None:
        # This build intentionally uses only the non-trained FBCCA path.
        c.rect(48, 104, 1184, 69, PANEL, BORDER, 3)
        c.text(66, 116, 92, 40, "当前算法:", 14, TEXT)
        c.button(158, 121, 191, 36, self.actions.algorithm)
        radios = ["固定解码"]
        x = 404
        for label in radios:
            active = self.actions.algorithm_mode == label
            c.circle(x + 9, 139, 7, BLUE if active else None, "#738098", 2)
            c.text(x + 23, 121, 87, 36, label, 13, TEXT)
            self._hit(x, 116, 101, 46, f"algorithm-mode:{label}")
            x += 101
        c.rect(48, 189, 1184, 171, PANEL, BORDER, 3)
        metadata = [("名称:", "纯 FBCCA", 73, 207), ("类型:", "无训练模型", 356, 207),
                    ("数据源:", "CSV / LSL", 640, 207), ("准确率:", "待真实标签评估", 922, 207),
                    ("识别窗:", "4秒", 73, 256), ("更新步长:", "1秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA"]
        for i, name in enumerate(algorithms):
            x = 73 + i * 144
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            c.rect(x, 441, 128, 96, "#17243a", BLUE if selected else "#2b3a55", 3, 2 if selected else 1)
            c.text(x, 452, 128, 37, name, 14, TEXT, True, Qt.AlignCenter)
            c.rect(x + 36, 504, 32, 16, GREEN if loaded else "#526078", None, 8)
            c.circle(x + 61 if loaded else x + 45, 512, 5, TEXT)
            c.circle(x + 84, 512, 8, YELLOW if default else "#2b3954")
            self._hit(x, 441, 128, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_controller(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-08 / M6.5：滑块和本地应用动作已可操作；正式版
        # ControllerService 还需将 Kp/Ki/Kd 校验后交给实现 BaseController 的
        # PID/MPC 控制器；参数写入 YAML，并保留版本和操作者审计记录。
        c.rect(16, 72, 64, 648, "#101b30", "#1d2d4b")
        c.rect(31, 96, 32, 3, BLUE)
        for y in (124, 172, 220, 268, 316, 364):
            c.rect(36, y, 24, 24, "#253b60", None, 4)
        c.text(112, 97, 260, 36, "控制器参数工作台", 15, TEXT, True)
        c.rect(112, 152, 1120, 69, PANEL, BORDER, 3)
        c.text(130, 165, 106, 40, "当前控制器:", 14, TEXT)
        c.button(238, 169, 128, 36, self.actions.controller)
        modes = [("● 手动选择", True), ("□ 场景自动匹配", False), ("□ 自适应切换", False), ("□ 双控制器热备", False)]
        x = 402
        for label, active in modes:
            c.button(x, 171, 126, 31, label, False, not active)
            x += 133
        c.rect(112, 246, 1120, 324, PANEL, BORDER, 3)
        c.text(145, 268, 300, 40, f"{self.actions.controller}参数配置", 17, TEXT, True)
        for label, y in (("Kp", 346), ("Ki", 402), ("Kd", 458)):
            value = self.actions.controller_values[label]
            end = 203 + 707 * value / 100
            c.text(145, y - 18, 45, 35, label, 14, TEXT)
            c.line(203, y, 910, y, "#6d6f70", 7)
            c.line(203, y, end, y, "#eeeeee", 7)
            c.circle(end, y, 8, "#f2f2f2")
            c.text(932, y - 17, 74, 34, f"{value / 100:.2f}", 13, TEXT)
            self._hit(196, y - 14, 721, 28, f"controller-slider:{label}")
        for i, (label, action) in enumerate((("应用参数", "controller-apply"), ("保存预设", "controller-save"), ("恢复默认", "controller-reset"))):
            x = 486 + i * 137
            c.button(x, 502, 121, 36, label, i == 0)
            self._hit(x, 502, 121, 36, action)
        c.rect(112, 595, 1120, 160, PANEL, BORDER, 3)
        c.text(137, 606, 200, 42, "控制器库", 16, TEXT, True)
        controllers = ("PID", "MPC", "新增", "滑模控制", "鲁棒控制", "自适应控制")
        for i, name in enumerate(controllers):
            x = 137 + i * 128
            disabled = name == "新增"
            c.button(x, 659, 104, 40, name, name == self.actions.controller, disabled, "#69c77a")
            if not disabled:
                self._hit(x, 659, 104, 40, f"controller-select:{name}")

    def _research_mapping(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-09 / M4.5：映射表应来自 CommandMappingRepository。
        # 解码 label/频率先经本映射转换为 SwarmCommand，再由安全仲裁器检查
        # 置信度、二次确认、Flow 质量、电量、解锁和急停状态，
        # 最后才调用 swarm_gateway.send_command。
        panels = [(192, 112, 896, 302), (192, 438, 896, 184), (192, 646, 896, 150)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(217, 128, 430, 42, "SSVEP 识别与安全动作映射", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 185, 104, 34, text, 13, TEXT, True, Qt.AlignCenter)
        c.line(433, 216, 847, 216, "#273752")
        for i, row in enumerate(self.actions.mappings[:6]):
            y = 214 + i * 27
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 26, 847, y + 26, "#273752")
        c.button(576, 380, 128, 28, "添加映射", True)
        self._hit(576, 380, 128, 28, "mapping-add")
        c.text(217, 454, 360, 42, "Flow Deck 单机验收", 16, TEXT, True)
        acceptance = (
            ("LED 无桨测试", "led_test"),
            ("保持悬停", "hover"),
            ("受控降落", "land"),
        )
        for index, (name, action) in enumerate(acceptance):
            button_x = 310 + index * 190
            c.button(button_x, 510, 170, 42, name)
            self._hit(button_x, 510, 170, 42, f"swarm-command:{action}")
        c.text(235, 575, 830, 23, "连接、Flow Deck 测距/光流、解锁和急停检查通过后才会执行", 11, "#596477", False, Qt.AlignCenter)
        c.text(217, 663, 360, 42, "L3 战术意图 (占位)", 16, TEXT, True)

    def _research_data(self, c: Canvas) -> None:
        # The left list and counts come from the recorder index, not painted demo sessions.
        c.rect(16, 72, 1248, 35, "#0b1425")
        c.text(39, 72, 360, 35, "实验数据工作台", 17, TEXT, True)
        for i, name in enumerate(("数据浏览", "标签管理", "采集说明")):
            x = 543 + i * 106
            c.button(x, 72, 90, 34, name, self.actions.data_tab == name)
            self._hit(x, 72, 90, 34, f"data-tab:{name}")
        c.text(1185, 72, 62, 34, "⌕  ⇧", 20, MUTED, False, Qt.AlignCenter)
        c.rect(32, 123, 254, 597, "#0b1425", BORDER)
        c.text(40, 125, 190, 43, "数据浏览", 14, TEXT, True)
        c.text(260, 125, 20, 43, "×", 16, TEXT, True, Qt.AlignCenter)
        c.line(32, 168, 286, 168, BORDER)
        c.text(40, 169, 210, 38, "实验会话", 13, MUTED)
        trials = self.experiment_recorder.recent_trials(12) if self.experiment_recorder is not None else []
        names = [f"{row.get('timestamp', '')[5:19]}  {row.get('true_label', '')}" for row in trials]
        if not names:
            names = ["暂无带标签原始试次"]
        for i, name in enumerate(names):
            y = 211 + i * 40
            if trials and i == self.actions.selected_session:
                c.rect(41, y, 238, 36, BLUE, None, 3)
            c.text(52, y, 195, 36, name, 13, TEXT)
            if trials:
                c.text(250, y, 20, 36, "›", 18, MUTED, False, Qt.AlignCenter)
                self._hit(41, y, 238, 36, f"data-session:{i}")
        c.rect(304, 123, 944, 597, "#071020", BORDER)
        c.text(317, 124, 420, 44, "当前 EEG 缓存与保存状态", 14, TEXT, True)
        c.text(1203, 124, 34, 44, "⌕×", 18, MUTED, False, Qt.AlignCenter)
        c.line(304, 168, 1248, 168, BORDER)
        self._draw_live_eeg(c, 335, 205, 880, 330)
        stats = self.experiment_recorder.stats() if self.experiment_recorder is not None else {"recognitions": 0, "labeled_trials": 0}
        c.rect(335, 557, 880, 116, "#0c1628", "#263752", 3)
        c.text(355, 567, 840, 27, f"识别日志：{stats['recognitions']} 条    带真实标签试次：{stats['labeled_trials']} 条", 13, TEXT, True)
        c.text(355, 599, 840, 23, "点击右上角“添加标签”后，将重新采集并保存连续 4 秒带标签 EEG。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------

    def _paint_admin(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-12：四类真实服务：DeviceRegistry（设备/固件/在线）、
        # UserRepository（用户与 RBAC）、StorageService（XDF/SQLite 容量）、
        # BackupService（备份/恢复）。这些是高风险管理操作，必须二次确认、
        # 角色鉴权和写审计日志，不能只修改当前画面上的数字。
        c.fill("#070d1f")
        stats = [("▦", "设备总数", "12"), ("▤", "在线设备", "10"), ("!", "告警", "2"),
                 ("▱", "存储使用率", f"{self.actions.storage_percent}%")]
        for i, (icon, label, value) in enumerate(stats):
            x = 33 + i * 310
            c.rect(x, 32, 285, 90, PANEL, BORDER, 3)
            c.rect(x + 16, 53, 48, 48, "#13264a", "#254783", 3)
            c.text(x + 16, 53, 48, 48, icon, 24, BLUE, False, Qt.AlignCenter)
            c.text(x + 80, 48, 175, 28, label, 13, MUTED)
            c.text(x + 80, 72, 175, 34, value, 25, TEXT, True)
        c.panel(33, 155, 590, 263, "设备管理")
        self._table(c, 49, 224, 558, ["名称", "类型", "固件版本", "在线状态", "操作"],
                    [["1   DeepBCI 采集板", "无", "v2.1", "在线", "查看"],
                     ["2   Crazyflie-01", "无人机", "v1.3", "在线", "查看"],
                     ["3   智能头盔", "头盔", "v1.0", "离线", "查看"]],
                    [190, 88, 104, 104, 70])
        for i, name in enumerate(("DeepBCI采集板", "Crazyflie-01", "智能头盔")):
            self._hit(537, 268 + i * 45, 70, 44, f"admin-device:{name}")
        c.panel(657, 155, 590, 263, "用户管理")
        self._table(c, 673, 224, 558, ["用户名", "角色", "最后登录", "操作"],
                    [["张伟", self.actions.user_roles["张伟"], "2026-08-30", "编辑"],
                     ["李明", self.actions.user_roles["李明"], "2026-08-29", "编辑"],
                     ["王强", self.actions.user_roles["王强"], "2026-08-31", "编辑"]],
                    [120, 145, 194, 99])
        for i, name in enumerate(("张伟", "李明", "王强")):
            self._hit(1132, 268 + i * 45, 99, 44, f"admin-user:{name}")
        c.panel(33, 452, 590, 263, "存储管理")
        c.text(151, 590, 100, 50, f"{self.actions.storage_percent}%", 26, TEXT, True, Qt.AlignCenter)
        c.text(245, 674, 75, 35, f"{100 - self.actions.storage_percent}%", 13, MUTED, False, Qt.AlignCenter)
        c.button(412, 599, 139, 44, "▱  清理缓存")
        self._hit(412, 599, 139, 44, "admin-clean-cache")
        c.panel(657, 452, 590, 263, "系统备份")
        c.text(822, 547, 260, 32, "最近备份时间", 13, MUTED, False, Qt.AlignCenter)
        c.text(807, 581, 290, 35, self.actions.last_backup, 18, TEXT, False, Qt.AlignCenter)
        c.button(760, 642, 179, 46, "立即备份", True)
        c.button(963, 642, 181, 46, "恢复")
        self._hit(760, 642, 179, 46, "admin-backup")
        self._hit(963, 642, 181, 46, "admin-restore")

    def _table(self, c: Canvas, x: float, y: float, w: float, headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
        c.rect(x, y, w, 44, "#17243a")
        cursor = x
        for title, width in zip(headers, widths):
            c.text(cursor + 11, y, width - 12, 44, title, 13, TEXT, True)
            cursor += width
        for r, row in enumerate(rows):
            row_y = y + 44 + r * 45
            cursor = x
            for col, (value, width) in enumerate(zip(row, widths)):
                color = BLUE if col == len(row) - 1 else ("#606b80" if value == "离线" else TEXT)
                c.text(cursor + 11, row_y, width - 12, 44, value, 13, color)
                cursor += width
            c.line(x, row_y + 44, x + w, row_y + 44, "#273752")

    # ---------- Engineering dashboard ----------

    def _paint_engineer(self, c: Canvas) -> None:
        if self.engineer_page == "深度诊断":
            self._engineer_diagnostics(c)
        else:
            self._engineer_monitor(c)
        self._hit(550, 674, 96, 40, "engineer:系统监控")
        self._hit(648, 674, 94, 40, "engineer:深度诊断")

    def _engineer_monitor(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-11：CPU/GPU/内存/延迟/吞吐量来自
        # self.system_monitor.snapshot() 的定时缓存；LSL 卡片来自流发现服务，
        # 日志来自 logging/loguru 的队列处理器。不要在绘图线程读取日志文件。
        c.fill("#080e20")
        c.rect(0, 0, 255, 720, "#0d172a")
        c.text(24, 18, 180, 42, "模块树", 17, TEXT, True)
        modules = ["M1  蜂群作战管理", "M2  目标识别与打击", "M4  脑电解码引擎", "M4  脑电解码引擎",
                   "M6  设备协同管理", "M7  设备协同监控", "M7  信号质量监控", "M11  数据策管"]
        for i, name in enumerate(modules):
            y = 70 + i * 32
            if i == 6:
                c.rect(24, y, 231, 29, "#1a2c58")
                c.rect(24, y, 4, 29, BLUE)
            c.text(39, y, 205, 29, "• " + name, 12, TEXT if i == 6 else "#aeb7c8")
        c.text(280, 17, 520, 43, "LSL数据流监控", 20, TEXT, True)
        c.panel(280, 77, 654, 249, "LSL数据流监控")
        streams = [("EEG", "250Hz", "0.1%"), ("Crazyflie\n遥测流", "100Hz", "0.3%"), ("fNIRS\n流", "50Hz", "0.0%")]
        for i, (name, rate, loss) in enumerate(streams):
            x = 297 + i * 215
            c.rect(x, 133, 191, 178, "#17243a", "#2a3a57", 3)
            c.text(x + 16, 145, 104, 39, name, 14, TEXT, True)
            c.text(x + 109, 145, 70, 25, "采样率", 14, TEXT, True, Qt.AlignRight)
            c.text(x + 109, 175, 70, 27, rate, 20, TEXT, True, Qt.AlignRight)
            c.line(x + 16, 236, x + 175, 236, "#263750")
            c.text(x + 16, 242, 80, 28, f"丢包率{loss}", 11, MUTED)
            c.text(x + 126, 242, 49, 28, loss, 13, CYAN, False, Qt.AlignRight)
        c.rect(314, 174, 127, 114, "#07101f", BLUE, 3)
        for i, name in enumerate(("实时预处理", "实时预处理", "算法推理", "置信度输出")):
            c.text(334, 177 + i * 25, 102, 23, "• " + name, 11, TEXT if i < 2 else MUTED)
        c.panel(280, 354, 654, 335, "性能监控")
        c.text(767, 357, 151, 32, "━ CPU    ━ 内存    ━ 延迟", 11, "#72dc83", False, Qt.AlignRight)
        c.chart(361, 421, 553, 210, [BLUE, "#efaa42", GREEN], amp=.19, points=40)
        c.text(280, 689, 260, 27, "运行时间 01:23:45", 11, MUTED)
        c.text(530, 689, 160, 27, "帧率 60 FPS", 11, TEXT, True)
        c.text(788, 689, 146, 27, "数据吞吐量 4.2 MB/s", 11, MUTED, False, Qt.AlignRight)
        c.rect(960, 0, 320, 720, "#0d172a")
        for i, name in enumerate(("INFO", "WARN", "ERROR")):
            active = self.actions.log_filter == name
            c.text(960 + i * 106, 0, 106, 53, name, 13, TEXT if active else MUTED, active, Qt.AlignCenter)
            if active:
                c.rect(960 + i * 106, 52, 106, 3, BLUE)
            self._hit(960 + i * 106, 0, 106, 55, f"log-filter:{name}")
        log_sets = {
            "INFO": ["INFO: 算法已加载 GraSPNet  14:30:05", "INFO: LSL流已连接  14:30:01"],
            "WARN": ["WARN: 信号质量下降  14:30:12", "WARN: UAV-03 丢包率偏高  14:31:08"],
            "ERROR": ["ERROR: 暂无未处理错误  14:31:30"],
        }
        logs = log_sets[self.actions.log_filter]
        for i in range(19):
            value = logs[i % len(logs)]
            color = RED if value.startswith("ERROR") else (YELLOW if value.startswith("WARN") else TEXT)
            c.text(977, 67 + i * 24, 290, 22, value, 10, color, "算法" in value)
        c.button(550, 674, 96, 31, "系统监控", True, accent=CYAN)
        c.button(648, 674, 94, 31, "深度诊断")

    def _engineer_diagnostics(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-13 / M10：资源仪表复用 SystemMonitor；通信诊断
        # 读取各设备丢包率和往返延迟；干扰曲线来自 EEG 频谱/50 Hz 检测。
        # “一键自检”应调用 DiagnosticService，异步返回逐项结果和错误详情。
        c.fill("#050b16")
        c.rect(8, 8, 1263, 704, None, BORDER)
        c.rect(440, 0, 400, 40, "#0f1c31", CYAN, 12, 2)
        c.text(440, 0, 400, 39, "系统深度诊断", 17, CYAN, True, Qt.AlignCenter)
        c.panel(33, 66, 594, 296, "资源监控")
        c.gauge(168, 211, 70, 62, "CPU", "#5dbcd4")
        c.gauge(356, 211, 70, 45, "GPU", "#66c88a")
        c.gauge(549, 211, 70, 58, "内存", "#5dbcd4")
        c.panel(653, 66, 594, 296, "通信诊断")
        # bars
        bars = [(42, 67), (20, 53), (52, 56), (44, 40), (31, 45)]
        for i, (a, b) in enumerate(bars):
            x = 744 + i * 98
            c.rect(x, 242 - a * 1.3, 37, a * 1.3, CYAN)
            c.rect(x + 42, 242 - b * 1.3, 37, b * 1.3, GREEN)
        c.line(734, 242, 1224, 242, "#263752")
        path = QPainterPath(QPointF(734, 312))
        for i in range(110):
            xx = 734 + i
            y_coord = 312 - 31 * math.exp(-((i - 52) / 19) ** 2) + max(0, i - 84) * .25
            path.lineTo(xx, y_coord)
        c.p.setPen(QPen(QColor(YELLOW), 1.2))
        c.p.setBrush(QColor(219, 154, 55, 45))
        path.lineTo(843, 362)
        path.lineTo(734, 362)
        path.closeSubpath()
        c.p.drawPath(path)
        c.panel(33, 389, 594, 298, "干扰检测")
        c.chart(94, 444, 513, 228, [CYAN], amp=.05, points=90)
        c.rect(320, 458, 62, 196, QColor(241, 78, 80, 25))
        c.line(332, 444, 332, 663, RED, 1, Qt.DashLine)
        c.text(287, 462, 100, 42, "50Hz工频干扰\n幅值 12.5µV", 11, RED, True, Qt.AlignCenter)
        c.panel(653, 389, 594, 298, "自检报告")
        c.button(831, 473, 238, 46, "重新自检" if self.actions.self_test_run else "一键自检", True)
        self._hit(831, 473, 238, 46, "self-test")
        checks = [("电源系统  通过", "通过", GREEN), ("通信链路  通过", "通过", GREEN),
                  ("信号质量  已复测" if self.actions.self_test_run else "信号质量  警告 (阻抗偏高)",
                   "通过" if self.actions.self_test_run else "警告",
                   GREEN if self.actions.self_test_run else YELLOW)]
        for i, (label, state, color) in enumerate(checks):
            y = 551 + i * 66
            c.rect(831, y, 238, 49, "#0d172a", BORDER, 3)
            c.text(845, y, 174, 49, label, 13, TEXT)
            c.text(1018, y, 39, 49, state, 13, color, True, Qt.AlignRight)
        c.button(550, 674, 96, 31, "系统监控")
        c.button(648, 674, 94, 31, "深度诊断", True, accent=CYAN)

    # ---------- Soldier/commander dashboards ----------

    def _paint_soldier(self, c: Canvas) -> None:
        if self.soldier_root == "作战地图":
            self._soldier_combat(c)
        elif self.soldier_page == "状态监控":
            self._soldier_status(c)
        else:
            self._soldier_brain(c)
        # top right shell switch
        c.button(1103, 18, 81, 32, "作战地图", self.soldier_root == "作战地图", accent=CYAN)
        c.button(1185, 18, 76, 32, "单兵面板", self.soldier_root == "单兵面板", accent=CYAN)
        self._hit(1103, 18, 81, 34, "soldier-root:作战地图")
        self._hit(1185, 18, 78, 34, "soldier-root:单兵面板")

    def _soldier_combat(self, c: Canvas) -> None:
        c.fill("#03101d")
        # tactical grid
        for x in range(24, 952, 58):
            c.line(x, 24, x, 702, "#102536", .7)
        for y in range(25, 703, 54):
            c.line(16, y, 936, y, "#102536", .7)
        c.rect(16, 24, 920, 678, None, "#20354b")
        now = datetime.now().strftime("%H:%M:%S")
        c.text(24, 5, 140, 25, now, 12, "#c4d7e5")
        uavs = list(self.swarm_gateway.list_uavs()) if self.swarm_gateway is not None else []
        gateway_status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        connected = bool(gateway_status and gateway_status.connected)
        c.line(568, 0, 584, 27, CYAN, 1)
        c.line(584, 27, 872, 27, CYAN, 1)
        c.line(872, 27, 888, 0, CYAN, 1)
        # Keep the status icon and label visually centred in the header frame.
        c.circle(673, 18, 6, GREEN if connected else YELLOW)
        c.text(688, 3, 126, 28, "遥测已连接" if connected else "遥测未连接", 13, TEXT, True)
        average_battery = sum(uav.battery for uav in uavs) / len(uavs) if uavs else 0.0
        c.text(1124, 4, 134, 26, f"平均电量 {average_battery:.0f}%" if uavs else "无实机遥测", 12, TEXT, True, Qt.AlignRight)
        points = [
            (uav, 476 + max(-1.0, min(1.0, uav.position.x)) * 330,
             360 - max(-1.0, min(1.0, uav.position.y)) * 260)
            for uav in uavs
        ]
        for index, (_, x1, y1) in enumerate(points):
            if len(points) > 1:
                _, x2, y2 = points[(index + 1) % len(points)]
                c.line(x1, y1, x2, y2, "#355160", .8)
        if not points:
            c.text(230, 320, 500, 70, "没有真实无人机遥测\n请在脑控页连接 Crazyflie", 18, MUTED, True, Qt.AlignCenter)
        for uav, x, y in points:
            for radius in (23, 16, 9, 4):
                c.circle(x, y, radius, None, CYAN, 2)
            c.circle(x, y, 28, QColor(72, 231, 248, 22))
            c.text(x - 55, y + 25, 110, 24, uav.uav_id, 13, TEXT, True, Qt.AlignCenter)
        c.rect(936, 24, 326, 672, "#07111f", "#22354c")
        c.rect(952, 44, 4, 13, CYAN)
        c.text(964, 35, 240, 31, "蜂群状态总览", 15, CYAN, True)
        for i, uav in enumerate(uavs[:3]):
            y = 74 + i * 70
            c.rect(953, y, 285, 60, PANEL, BORDER, 3)
            c.rect(953, y, 4, 60, CYAN)
            c.text(966, y + 7, 150, 25, uav.uav_id, 13, TEXT, True)
            c.text(966, y + 29, 130, 21, f"电量 {uav.battery:.0f}%  {uav.battery_voltage or 0:.2f}V", 10, MUTED)
            c.text(1092, y + 5, 134, 23, f"高度 {uav.altitude_m:.2f}m", 12, TEXT, True, Qt.AlignRight)
            c.text(1092, y + 28, 134, 20, f"速度 {uav.speed_mps:.2f}m/s", 10, CYAN, False, Qt.AlignRight)
        c.rect(952, 303, 4, 13, CYAN)
        c.text(964, 294, 220, 31, "目标识别接口", 14, CYAN, True)
        c.rect(953, 332, 285, 62, PANEL, BORDER, 3)
        c.circle(974, 363, 7, None, YELLOW, 2)
        c.text(994, 340, 210, 28, "未接入", 14, TEXT, True)
        c.text(994, 366, 220, 20, "等待真实视觉模型和坐标接口", 10, MUTED)
        c.rect(952, 423, 4, 13, CYAN)
        c.text(964, 414, 220, 31, "操作员状态", 14, CYAN, True)
        c.circle(967, 460, 6, GREEN)
        c.text(980, 443, 220, 34, "多模态生命体征未接入", 13, TEXT, True)
        c.rect(952, 500, 4, 13, CYAN)
        c.text(964, 491, 220, 31, "指令日志", 14, CYAN, True)
        c.rect(953, 527, 285, 152, PANEL, BORDER, 3)
        for i, command in enumerate(self.actions.command_history[:5]):
            c.text(966, 535 + i * 26, 245, 25, f"指令：{command}", 11, RED if command == "紧急停止" else MUTED)

    def _soldier_brain(self, c: Canvas) -> None:
        c.fill("#111420")
        gateway_status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        connected = bool(gateway_status and gateway_status.connected)
        armed = bool(gateway_status and gateway_status.armed)
        airborne = bool(gateway_status and gateway_status.airborne)
        flow_mode = bool(gateway_status and gateway_status.backend == "flowdeck")
        emergency = bool(self.actions.emergency_stopped or (gateway_status and gateway_status.emergency))
        state = gateway_status.state if gateway_status is not None else "disconnected"
        status_color = (
            RED if emergency or state in {"flight_error", "stop_error", "battery_low", "battery_low_grounded"} else
            YELLOW if state in {"taking_off", "stopping_motors", "landing"} else
            GREEN if connected else YELLOW
        )
        status_text = (
            "急停锁定 · 需重启" if emergency else
            "电量过低 · 请立即受控降落" if state == "battery_low" else
            "低电压提示 · 已落地" if state == "battery_low_grounded" else
            "正在受控降落" if state == "landing" else
            "正在停止电机" if state == "stopping_motors" else
            "Flow Hover 操控中" if state == "manual_flight" else
            "正在建立 Flow Hover" if state == "taking_off" else
            "飞行状态异常 · 请处置" if airborne and not armed else
            "飞行中 · 控制已解锁" if armed and airborne else
            "实机已解锁" if armed else
            "实机已连接 · 待解锁" if connected and bool(gateway_status and gateway_status.control_enabled) else
            "只读遥测已连接" if connected else
            "实机未连接"
        )
        c.circle(594, 52, 6, status_color)
        c.text(608, 36, 220, 32, status_text, 14, TEXT, True)

        target_height = self.actions.swarm_takeoff_height_m()
        c.text(385, 126, 160, 32, "启动目标高度", 13, TEXT, True, Qt.AlignCenter)
        c.button(548, 126, 46, 32, "－", False, accent=BLUE)
        c.button(601, 126, 100, 32, f"{target_height:.2f} m", False, accent=CYAN)
        c.button(708, 126, 46, 32, "＋", False, accent=GREEN)
        c.text(762, 126, 145, 32, "范围 0.20–2.00m", 10, MUTED, False, Qt.AlignCenter)
        self._hit(548, 126, 46, 32, "swarm-height-decrease")
        self._hit(601, 126, 100, 32, "swarm-height-custom")
        self._hit(708, 126, 46, 32, "swarm-height-increase")
        c.button(930, 126, 145, 32, "测试任务", False, accent=YELLOW)
        self._hit(930, 126, 145, 32, "mission-test")

        c.panel(28, 82, 245, 304, "实机链路")
        c.text(45, 122, 210, 38, gateway_status.message if gateway_status else "蜂群接口未配置", 11, MUTED)
        c.button(45, 170, 96, 35, "断开" if connected else "连接", connected, accent=CYAN)
        self._hit(45, 170, 96, 35, "swarm-disconnect" if connected else "swarm-connect")
        control_label = (
            "停止 Hover" if armed and flow_mode else
            "启动 Flow Hover" if flow_mode else
            "停止操控" if armed else "解锁控制"
        )
        c.button(151, 170, 96, 35, control_label, armed, accent=YELLOW)
        self._hit(151, 170, 96, 35, "swarm-disarm" if armed else "swarm-arm")
        c.button(45, 215, 96, 35, "受控降落", False, accent=GREEN)
        self._hit(45, 215, 96, 35, "swarm-command:land")
        c.button(151, 215, 96, 35, "停止电机", False, accent=RED)
        self._hit(151, 215, 96, 35, "swarm-motor-stop")
        c.button(45, 260, 202, 37,
                 "脑控执行：开" if self.actions.bci_control_enabled else "脑控执行：关",
                 self.actions.bci_control_enabled, accent=RED)
        self._hit(45, 260, 202, 37, "bci-control-toggle")
        c.button(45, 307, 96, 32, "配置 URI", False, accent=CYAN)
        c.button(151, 307, 96, 32, "单机控制", False, accent=YELLOW)
        self._hit(45, 307, 96, 32, "swarm-configure-uris")
        self._hit(151, 307, 96, 32, "swarm-single-control")
        # Flow readiness belongs to each UAV telemetry item, not SwarmStatus.
        telemetry = tuple(self.swarm_gateway.list_uavs()) if self.swarm_gateway is not None else ()
        flow_ready = connected and bool(telemetry) and all(item.positioning_ok for item in telemetry)
        c.button(45, 349, 202, 32,
                 "Flow Deck 就绪" if flow_ready else "等待 Flow 质量",
                 flow_ready, accent=GREEN if flow_ready else YELLOW)

        # Official Flow Hover semantics: the backend streams body-frame vx/vy
        # and absolute ground distance. Mouse events only update normalized targets.
        height_cx, height_cy, stick_r, travel = 470.0, 386.0, 100.0, 82.0
        direction_cx, direction_cy = 760.0, 386.0
        c.text(370, 226, 200, 30, "左杆 · 高度升降", 15, TEXT, True, Qt.AlignCenter)
        c.text(650, 226, 220, 30, "右杆 · 前后左右", 15, TEXT, True, Qt.AlignCenter)
        c.circle(height_cx, height_cy, stick_r + 8, QColor(72, 231, 248, 18), CYAN, 2)
        c.circle(height_cx, height_cy, stick_r, "#17243a", "#35516d", 2)
        c.line(height_cx, height_cy - 72, height_cx, height_cy + 72, "#45627e", 3)
        c.text(height_cx - 22, height_cy - 96, 44, 26, "升", 12, GREEN, True, Qt.AlignCenter)
        c.text(height_cx - 22, height_cy + 70, 44, 26, "降", 12, BLUE, True, Qt.AlignCenter)
        height_knob_y = height_cy - self._manual_vertical * travel
        c.circle(height_cx, height_knob_y, 32, "#38c889", "#b8ffe0", 2)
        c.text(height_cx - 30, height_knob_y - 16, 60, 32,
               f"{self._manual_vertical * 100:+.0f}%", 10, TEXT, True, Qt.AlignCenter)
        if flow_mode:
            self._hit(height_cx - stick_r, height_cy - stick_r, stick_r * 2, stick_r * 2,
                      "manual-stick-height")

        c.circle(direction_cx, direction_cy, stick_r + 8, QColor(87, 153, 221, 20), BLUE, 2)
        c.circle(direction_cx, direction_cy, stick_r, "#17243a", "#35516d", 2)
        c.line(direction_cx - 72, direction_cy, direction_cx + 72, direction_cy, "#45627e", 2)
        c.line(direction_cx, direction_cy - 72, direction_cx, direction_cy + 72, "#45627e", 2)
        c.text(direction_cx - 24, direction_cy - 99, 48, 24, "前", 12, GREEN, True, Qt.AlignCenter)
        c.text(direction_cx - 24, direction_cy + 75, 48, 24, "后", 12, BLUE, True, Qt.AlignCenter)
        c.text(direction_cx - 99, direction_cy - 12, 32, 24, "左", 12, YELLOW, True, Qt.AlignCenter)
        c.text(direction_cx + 67, direction_cy - 12, 32, 24, "右", 12, YELLOW, True, Qt.AlignCenter)
        direction_knob_x = direction_cx + self._manual_lateral * travel
        direction_knob_y = direction_cy - self._manual_forward * travel
        c.circle(direction_knob_x, direction_knob_y, 32, "#5799dd", "#d4e9ff", 2)
        if flow_mode:
            self._hit(direction_cx - stick_r, direction_cy - stick_r, stick_r * 2, stick_r * 2,
                      "manual-stick-direction")
        confidence = self._latest_recognition.confidence if self._latest_recognition is not None else 0.0
        result_label = self._latest_recognition.label if self._latest_recognition is not None else "等待 EEG"
        c.text(456, 603, 280, 30, f"FBCCA：{result_label}", 13, MUTED)
        c.line(448, 653, 448 + 350 * max(0.0, min(1.0, confidence)), 653, "#63a9e9", 7)
        c.line(448 + 350 * max(0.0, min(1.0, confidence)), 653, 798, 653, "#35425a", 6)
        c.text(806, 628, 100, 51, f"置信度\n{confidence * 100:.1f}%", 14, TEXT, True)
        c.circle(1148, 608, 64, "#a52d32" if emergency else "#ee8284", RED, 4)
        c.circle(1148, 608, 73, QColor(239, 78, 80, 28))
        emergency_label = "确认已重启\n复位并断开" if emergency else "紧急停止"
        c.text(1084, 545, 128, 126, emergency_label, 13 if emergency else 19, TEXT, True, Qt.AlignCenter)
        self._hit(1075, 535, 146, 146, "swarm-emergency-reset" if emergency else "swarm-emergency-stop")
        self._paint_soldier_bottom_toggle(c)

    def _soldier_status(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-04 / M3：注意力、疲劳、压力和心率应来自
        # SoldierVitalsStream（EEG/EMG/心率/体温/血氧的 LSL 多模态流）。
        # 阈值告警交给独立 AlertService，并同步写入 XDF Marker 和审计日志。
        c.fill("#071020")
        c.rect(32, 33, 1215, 57, PANEL, BORDER, 3)
        c.rect(32, 33, 81, 4, CYAN)
        c.text(56, 41, 520, 41, "ID: S-07    |    姓名: 张伟    |    心率: 72 bpm 🏃", 16, TEXT, True)
        c.button(1135, 42, 88, 36, "导出报告", True)
        self._hit(1135, 42, 88, 36, "soldier-export")
        gauges = [(33, "85", "注意力", GREEN), (445, "62", "疲劳度", YELLOW), (859, "40", "情绪压力", RED)]
        for x, value, label, color in gauges:
            c.panel(x, 116, 388, 253)
            c.gauge(x + 194, 238, 66, int(value), label, color)
        c.panel(33, 395, 1214, 292)
        c.text(48, 401, 110, 34, "数值", 12, MUTED)
        # The reference chart intentionally contains only a short 60-second
        # sample on the left, leaving the remainder of the panel empty.
        for i in range(1, 10):
            c.line(98, 441 + i * 19.3, 1208, 441 + i * 19.3, "#263249", 1, Qt.DashLine)
        samples = [
            [12, 12.5, 13.2, 13.0, 14.2, 15.4, 15.8, 16.0, 15.2, 15.4, 14.8, 15.1, 14.5, 13.9, 13.0, 12.0, 11.5],
            [9.8, 10.5, 10.3, 9.7, 9.6, 10.0, 9.0, 8.4, 7.9, 6.8, 7.4, 7.8, 5.6, 7.1, 6.0, 5.8, 3.7],
            [2.0, 3.0, 4.0, 4.4, 4.3, 4.5, 3.9, 4.1, 3.3, 2.5, 2.0, 1.7, 0.7, 0.3, 1.0, 0.5, 0.3],
        ]
        for values, color in zip(samples, (GREEN, YELLOW, CYAN)):
            path = QPainterPath()
            for i, value in enumerate(values):
                px = 98 + i * 19.2
                py = 634 - value * 9.7
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            c.p.setPen(QPen(QColor(color), 1.8))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.text(563, 644, 160, 30, "最近60秒", 12, MUTED, False, Qt.AlignCenter)
        c.rect(508, 680, 264, 39, "#252414", "#5f5424", 3)
        c.text(518, 680, 244, 38, "⚠️  注意: 疲劳度偏高，建议休息", 11, YELLOW, True, Qt.AlignCenter)
        self._paint_soldier_bottom_toggle(c)

    def _paint_soldier_bottom_toggle(self, c: Canvas) -> None:
        c.text(24, 674, 45, 32, "脑控", 13, TEXT if self.soldier_page == "脑控" else MUTED, self.soldier_page == "脑控")
        c.text(68, 674, 14, 32, "|", 13, MUTED, False, Qt.AlignCenter)
        c.text(87, 674, 78, 32, "状态监控", 13, TEXT if self.soldier_page == "状态监控" else MUTED,
               self.soldier_page == "状态监控")
        self._hit(17, 669, 55, 42, "soldier-page:脑控")
        self._hit(82, 669, 94, 42, "soldier-page:状态监控")
