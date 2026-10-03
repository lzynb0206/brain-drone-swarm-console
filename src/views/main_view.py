"""Main dashboard shell: lifecycle, EEG events and shared input handling."""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QLabel, QMessageBox, QStackedWidget, QWidget
from src.domain import EEGFrame, IntentResult, Role, User
from src.ports import EEGStream, EngineerDataProvider, IntentDecoder, SwarmGateway, SystemMonitor
from src.services import ExperimentRecorder, MissionPlanningError, WorkbenchService
from src.views.engineer_dashboard import EngineerDashboard
from src.views.main_action_router import MainActionRouterMixin
from src.views.main_dialogs import MainDialogsMixin
from src.views.role_pages import AdminPage, EngineerCanvasPage, ResearchPage, SoldierPage
from src.workers import EEGWorker
from src.widgets.scifi_canvas import CYAN, TEXT, Canvas

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QPlainTextEdit

    from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


class MainView(
    MainActionRouterMixin,
    MainDialogsMixin,
    QWidget,
):
    """Lifecycle shell coordinating independent role widgets and workers."""

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
        self._role_stack = QStackedWidget(self)
        self.research_widget = ResearchPage(self, self._role_stack)
        self.soldier_widget = SoldierPage(self, self._role_stack)
        self.admin_widget = AdminPage(self, self._role_stack)
        self.engineer_canvas_widget = EngineerCanvasPage(self, self._role_stack)
        self._role_widgets = {
            Role.RESEARCHER: self.research_widget,
            Role.SOLDIER: self.soldier_widget,
            Role.COMMANDER: self.soldier_widget,
            Role.ADMIN: self.admin_widget,
            Role.ENGINEER: self.engineer_canvas_widget,
        }
        for page in (
            self.research_widget,
            self.soldier_widget,
            self.admin_widget,
            self.engineer_canvas_widget,
        ):
            self._role_stack.addWidget(page)
            page.action_requested.connect(self._activate)
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
            self.engineer_dashboard = EngineerDashboard(
                system_monitor, engineer_data_provider, self._role_stack,
            )
            self.engineer_dashboard.logout_requested.connect(self.logout_requested.emit)
            self._role_stack.addWidget(self.engineer_dashboard)
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
        page = (
            self.engineer_dashboard
            if user.role == Role.ENGINEER and self.engineer_dashboard is not None
            else self._role_widgets[user.role]
        )
        self._role_stack.setCurrentWidget(page)
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

    def _run_simulated_structured_mission(self) -> None:
        """Inject the hand-written FBCCA-style result into the normal mission path."""
        self._on_recognition(self.actions.simulated_fbcca_result())

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
        c.fill("#071020")

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
        self._role_stack.setGeometry(self.rect())
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

    def update(self, *args) -> None:  # type: ignore[override]
        """Refresh the shell and whichever concrete role widget is visible."""
        super().update(*args)
        role_stack = getattr(self, "_role_stack", None)
        if role_stack is not None and role_stack.currentWidget() is not None:
            role_stack.currentWidget().update()

    # Compatibility hooks for integrations that rendered a page into an
    # off-screen Canvas before role pages became independent QWidget objects.
    def _render_compat(self, page, method_name: str, canvas: Canvas) -> None:
        page._regions = self._regions
        getattr(page, method_name)(canvas)
        self._regions = page._regions

    def _paint_research(self, canvas: Canvas) -> None:
        self._render_compat(self.research_widget, "_paint_research", canvas)

    def _paint_soldier(self, canvas: Canvas) -> None:
        self._render_compat(self.soldier_widget, "_paint_soldier", canvas)

    def _soldier_brain(self, canvas: Canvas) -> None:
        self._render_compat(self.soldier_widget, "_soldier_brain", canvas)

    def _paint_admin(self, canvas: Canvas) -> None:
        self._render_compat(self.admin_widget, "_paint_admin", canvas)

    def _paint_engineer(self, canvas: Canvas) -> None:
        self._render_compat(self.engineer_canvas_widget, "_paint_engineer", canvas)

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
