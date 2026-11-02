"""Main dashboard shell: lifecycle, EEG events and shared input handling."""

from __future__ import annotations

from src.bci.montage import HYBRID_IDS

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import QStackedWidget, QWidget
from src.bci.preprocessing import assess_signal_quality
from src.bci.config import (
    DECISION_STEP_SECONDS,
    RESEARCH_DATA_ROOT,
    SSVEP_CHANNEL_NAMES,
    WINDOW_SECONDS,
)
from src.controllers import (
    CSVReplayController,
    EEGAcquisitionController,
    ExperimentController,
    LSLCollectionController,
    ManualFlightController,
    RecognitionController,
    SingleTargetCalibrationController,
    SoldierBCIController,
    ToastController,
    TrialAnalysisController,
)
from src.domain import EEGFrame, IntentResult, Role, User
from src.ports import EEGStream, EngineerDataProvider, IntentDecoder, SwarmGateway, SystemMonitor
from src.services import (
    ExperimentRecorder,
    LSLEEGOutlet,
    RawEEGCsvRecorder,
    WorkbenchService,
)
from src.views.engineer_dashboard import EngineerDashboard
from src.controllers.main_action_router import MainActionRouter
from src.views.main_dialogs import MainDialogsMixin
from src.views.role_pages import AdminPage, EngineerCanvasPage, ResearchPage, SoldierPage
from src.workers import EEGWorker
from src.widgets.scifi_canvas import Canvas

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QPlainTextEdit

    from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


class MainView(
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
        ssvep_test_recorder: ExperimentRecorder | None = None,
        soldier_ssvep_recorder: ExperimentRecorder | None = None,
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
        self.ssvep_test_recorder = ssvep_test_recorder
        self.soldier_ssvep_recorder = soldier_ssvep_recorder
        self.swarm_gateway = swarm_gateway
        self.system_monitor = system_monitor
        self.engineer_data_provider = engineer_data_provider
        self.actions = WorkbenchService(
            swarm_gateway,
            system_monitor,
            soldier_ssvep_recorder or experiment_recorder,
        )
        active_decoder = str(getattr(intent_decoder, "active_name", "")).strip()
        if active_decoder:
            self.actions.algorithm = active_decoder
            self.actions.default_algorithm = active_decoder
            self.actions.loaded_algorithms.add(active_decoder)
        self.user: User | None = None
        self.research_page = "采集"
        self.acquisition_stage = "setup"
        self.acquisition_tab = "Dashboard"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        self.toast = ToastController(self)
        self._toast_scroll_timer = self.toast.timer
        self._eeg_history = [deque(maxlen=360) for _ in range(8)]
        self._eeg_display_history = [deque(maxlen=360) for _ in range(8)]
        self._eeg_sample_rate = 0.0
        self._eeg_channel_names: tuple[str, ...] = tuple(SSVEP_CHANNEL_NAMES)
        self._eeg_original_channel_names: tuple[str, ...] = ()
        self._eeg_channel_units: tuple[str, ...] = tuple("" for _ in range(8))
        self._eeg_quality: dict[str, object] = {}
        self._eeg_health: dict[str, object] = {}
        self._eeg_status = "尚未启动"
        self._latest_recognition: IntentResult | None = None
        self._experiment_session_id = ""
        self._eeg_events: deque[str] = deque(maxlen=12)
        self._pipeline_worker: EEGWorker | None = None
        self._stimulus_window: SSVEPStimulusWindow | None = None
        self._stimulus_worker: EEGWorker | None = None
        self._uri_dialog: QDialog | None = None
        self._uri_editor: QPlainTextEdit | None = None
        self._height_dialog: QDialog | None = None
        self._height_input: QDoubleSpinBox | None = None
        self._distance_dialog: QDialog | None = None
        self._distance_input: QDoubleSpinBox | None = None
        csv_root = (
            experiment_recorder.root / "csv_sessions"
            if experiment_recorder is not None
            else RESEARCH_DATA_ROOT / "csv_sessions"
        )
        self.csv_replay = CSVReplayController(eeg_stream, self)
        self.acquisition = EEGAcquisitionController(
            eeg_stream,
            RawEEGCsvRecorder(csv_root),
            LSLEEGOutlet(source_id="brain-drone-swarm-console-expandable-eeg"),
            self,
        )
        self.soldier_bci = SoldierBCIController(self)
        self.experiment = ExperimentController(self)
        self.single_target_calibration = SingleTargetCalibrationController(self)
        self.recognition = RecognitionController(self)
        self.manual_flight = ManualFlightController(self)
        analysis_root = (
            experiment_recorder.export_dir / "trial_analysis"
            if experiment_recorder is not None
            else RESEARCH_DATA_ROOT / "exports" / "trial_analysis"
        )
        self.trial_analysis = TrialAnalysisController(self, analysis_root)
        self.action_router = MainActionRouter(self)
        lsl_collector_stream = None
        lsl_factory = getattr(self.eeg_stream, "create_lsl_receiver", None)
        if callable(lsl_factory):
            try:
                lsl_collector_stream = lsl_factory()
            except Exception:
                lsl_collector_stream = None
        self.lsl_collector = LSLCollectionController(
            lsl_collector_stream,
            RawEEGCsvRecorder(csv_root),
            self,
        )
        self.lsl_collector.changed.connect(self._refresh_lsl_collector_page)
        self.lsl_collector.notification_requested.connect(self._notify)
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
            page.action_requested.connect(self.action_router.activate)
        self._build_eeg_worker()
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
        self.setMouseTracking(True)

    def set_user(self, user: User) -> None:
        self.user = user
        self.research_page = "采集"
        self.acquisition_stage = "setup"
        self.acquisition_tab = "Dashboard"
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
            if user.role == Role.SOLDIER:
                # Soldier mode owns a one-click live chain.  Do not silently
                # start an LSL consumer and leave the operator guessing which
                # research-page process is expected to publish its input.
                self._disconnect_eeg_pipeline(stop_csv_recording=True)
                self._eeg_status = "等待启动 SSVEP 实时采集链路"
            else:
                self._disconnect_eeg_pipeline(stop_csv_recording=True)
                self._eeg_status = "等待配置实时采集会话"
        else:
            self.deactivate()
        self.update()

    def _start_eeg_pipeline(self) -> None:
        if self._pipeline_worker is not None and (
            self._pipeline_worker.isFinished() or self._pipeline_worker.isInterruptionRequested()
        ):
            self._build_eeg_worker(replace=True)
        if self._pipeline_worker is not None and not self._pipeline_worker.isRunning():
            self._eeg_status = "正在连接数据源…"
            self._pipeline_worker.start()

    def _activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Compatibility entry point for tests and non-widget callers."""

        self.action_router.activate(action, x, y)

    def _build_eeg_worker(self, replace: bool = False) -> None:
        if replace and self._pipeline_worker is not None:
            old_worker = self._pipeline_worker
            if old_worker.isRunning():
                old_worker.stop()
            old_worker.deleteLater()
            self._pipeline_worker = None
        if self._pipeline_worker is not None:
            return
        recorder = self._recorder_for_current_role()
        if self.eeg_stream is None or self.intent_decoder is None or recorder is None:
            return
        worker = EEGWorker(
            self.eeg_stream,
            self.intent_decoder,
            recorder,
            window_seconds=WINDOW_SECONDS,
            decision_step_seconds=DECISION_STEP_SECONDS,
            parent=self,
        )
        worker.frame_received.connect(self._on_eeg_frame)
        worker.recognition_received.connect(self._on_recognition)
        worker.status_changed.connect(self._on_eeg_status)
        worker.error_occurred.connect(self._on_eeg_error)
        worker.labeled_trial_saved.connect(self._on_labeled_trial_saved)
        worker.continuous_chunk_saved.connect(self._on_continuous_chunk_saved)
        worker.finished.connect(lambda active_worker=worker: self._on_eeg_worker_finished(active_worker))
        worker.set_paradigm_type("SSVEP+MI混合" if self.actions.montage_id in HYBRID_IDS else self.actions.paradigm_type)
        if self.user is not None and self._experiment_session_id:
            worker.set_experiment_context(
                self.user.uid or self.user.username, self._experiment_session_id,
            )
        self._pipeline_worker = worker

    def _recorder_for_current_role(self) -> ExperimentRecorder | None:
        if (
            self.user is not None
            and self.user.role in {Role.SOLDIER, Role.COMMANDER}
            and self.soldier_ssvep_recorder is not None
        ):
            return self.soldier_ssvep_recorder
        return self.experiment_recorder

    def _disconnect_eeg_pipeline(self, stop_csv_recording: bool = True) -> None:
        self.acquisition.disconnect_pipeline(stop_csv_recording)

    def _stop_soldier_lsl_bridge(self) -> None:
        self.soldier_bci.stop_bridge()

    def _discover_lsl_collector_streams(self) -> None:
        self.lsl_collector.discover()

    def _start_lsl_collection(self) -> None:
        subject_id = self.user.uid or self.user.username if self.user is not None else ""
        self.lsl_collector.start(
            self._experiment_session_id,
            subject_id,
        )

    def _stop_lsl_collection(self) -> None:
        self.lsl_collector.stop()

    def _toggle_lsl_collector_waveform(self) -> None:
        self.lsl_collector.toggle_waveform()

    def _open_lsl_collector_directory(self) -> None:
        directory = self.lsl_collector.recorder.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self._notify(f"无法自动打开目录：{directory}")

    def deactivate(self) -> None:
        self.experiment.deactivate()
        self.single_target_calibration.deactivate()
        if self._stimulus_window is not None:
            self._stimulus_window.close()
        if self.acquisition.raw_recorder.is_recording:
            self.acquisition.raw_recorder.stop()
        self.acquisition.continuous_recording = False
        if self._pipeline_worker is not None:
            self._pipeline_worker.end_session_recording()
            self._pipeline_worker.set_continuous_recording(False)
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._pipeline_worker.stop()
        self.lsl_collector.stop()
        self._stop_soldier_lsl_bridge()
        self.acquisition.outlet.stop()
        self.actions.bci.disable()
        self.soldier_bci.reset_state()

    def _on_eeg_frame(self, frame: EEGFrame) -> None:
        self.single_target_calibration.ingest_frame(frame)
        self.soldier_bci.complete_after_frame(frame)
        self.acquisition.handle_frame(
            frame,
            publish_outlet=not self.soldier_bci.outlet_started_here,
        )
        if not self._static_data_analysis_is_visible():
            self.update()

    def _on_recognition(self, result: IntentResult) -> None:
        self.recognition.process(result)

    def _activate_selected_decoder(self) -> str:
        """Activate the selected UI algorithm in the live decoder manager."""

        selector = getattr(self.intent_decoder, "select", None)
        if callable(selector):
            try:
                message = str(selector(self.actions.algorithm))
            except Exception as exc:
                return f"算法未加载：{exc}"
            self.actions.load_algorithm()
            return message
        return self.actions.load_algorithm()

    def _set_default_selected_decoder(self) -> str:
        message = self._activate_selected_decoder()
        if message.startswith("算法未加载"):
            return message
        return self.actions.set_default_algorithm()

    def _unload_selected_decoder(self) -> str:
        active = str(getattr(self.intent_decoder, "active_name", "")).strip()
        if active and active == self.actions.algorithm:
            return "运行中的解码器不能卸载，请先加载另一个可用算法"
        return self.actions.unload_algorithm()

    def _run_simulated_structured_mission(self) -> None:
        """Inject the hand-written FBCCA-style result into the normal mission path."""
        self._on_recognition(self.actions.simulated_fbcca_result())

    def _handle_single_uav_mission(self, result: IntentResult) -> None:
        self.recognition.handle_single_uav_mission(result)

    def _on_eeg_status(self, status: str) -> None:
        self._eeg_status = status
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  {status}")
        self.update()

    def _on_eeg_error(self, message: str) -> None:
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  ERROR  {message}")
        self.experiment.notify_error(message)
        self.update()

    def _on_labeled_trial_saved(self, label: str, path: str) -> None:
        self.experiment.notify_trial_saved(label, path)
        self._notify(f"真实标签“{label}”和原始 EEG 已保存：{path}")

    def _on_continuous_chunk_saved(self, path: str) -> None:
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  连续 EEG 已保存：{path}")
        self.update()

    def _on_eeg_worker_finished(self, worker: EEGWorker) -> None:
        if self._pipeline_worker is not worker:
            return
        self.experiment.handle_worker_finished()
        self.single_target_calibration.handle_worker_finished()
        self.acquisition.close_recording_after_worker()
        self.soldier_bci.handle_worker_finished()
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
        self.toast.paint(c)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._role_stack.setGeometry(self.rect())
        super().resizeEvent(event)

    @property
    def _active_joystick(self) -> str | None:
        return self.manual_flight.active_stick

    @_active_joystick.setter
    def _active_joystick(self, value: str | None) -> None:
        self.manual_flight.active_stick = value

    @property
    def _manual_vertical(self) -> float:
        return self.manual_flight.vertical

    @_manual_vertical.setter
    def _manual_vertical(self, value: float) -> None:
        self.manual_flight.vertical = float(value)

    @property
    def _manual_forward(self) -> float:
        return self.manual_flight.forward

    @_manual_forward.setter
    def _manual_forward(self, value: float) -> None:
        self.manual_flight.forward = float(value)

    @property
    def _manual_lateral(self) -> float:
        return self.manual_flight.lateral

    @_manual_lateral.setter
    def _manual_lateral(self, value: float) -> None:
        self.manual_flight.lateral = float(value)

    def _update_manual_stick(self, stick: str, x: float, y: float) -> None:
        self.manual_flight.update_stick(stick, x, y)

    def _send_manual_input(self) -> None:
        self.manual_flight.send()

    def _center_joysticks(self, send: bool = False) -> None:
        self.manual_flight.center(send=send)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        # This works while the application has focus on both macOS and Windows.
        # A macOS system-wide key hook would require an explicit Accessibility grant.
        if event.key() == Qt.Key_B and not event.isAutoRepeat():
            self._center_joysticks(send=False)
            self._handle_ssvep_emergency()
            return
        super().keyPressEvent(event)

    def _refresh_runtime(self) -> None:
        # The data-analysis page is event-driven. Repainting its large report
        # on this 300 ms telemetry timer wastes CPU and can starve mouse input.
        if self._static_data_analysis_is_visible():
            return
        health = getattr(self.eeg_stream, "health_snapshot", None)
        if callable(health):
            try:
                self._eeg_health = dict(health())
            except Exception:
                pass
        if self._eeg_sample_rate > 0 and self._eeg_history and all(
            len(history) >= 32 for history in self._eeg_history
        ):
            try:
                self._eeg_quality = assess_signal_quality(
                    np.asarray([list(history) for history in self._eeg_history], dtype=float),
                    self._eeg_sample_rate,
                )
            except Exception:
                pass
        if self.soldier_bci.stop_if_stale():
            return
        self.update()

    def _static_data_analysis_is_visible(self) -> bool:
        role_stack = getattr(self, "_role_stack", None)
        research_widget = getattr(self, "research_widget", None)
        return (
            role_stack is not None
            and research_widget is not None
            and role_stack.currentWidget() is research_widget
            and self.research_page == "数据分析"
        )

    def _refresh_lsl_collector_page(self) -> None:
        role_stack = getattr(self, "_role_stack", None)
        if (
            role_stack is not None
            and role_stack.currentWidget() is self.research_widget
            and self.research_page == "LSL收集"
        ):
            self.update()

    def _refresh_live_page(self) -> None:
        """Refresh live telemetry pages without repainting static analysis."""

        if not self._static_data_analysis_is_visible():
            self.update()

    def update(self, *args) -> None:  # type: ignore[override]
        """Refresh the shell and whichever concrete role widget is visible."""
        super().update(*args)
        role_stack = getattr(self, "_role_stack", None)
        if role_stack is not None and role_stack.currentWidget() is not None:
            role_stack.currentWidget().update()

    def _notify(self, message: str) -> None:
        self.toast.notify(message)

    def _animate_toast(self) -> None:
        self.toast.animate()

    def _clear_toast(self, generation: int) -> None:
        self.toast.clear(generation)

    @property
    def _toast(self) -> str:
        return self.toast.message

    @property
    def _toast_generation(self) -> int:
        return self.toast.generation

    @property
    def _toast_scroll_started(self) -> float:
        return self.toast.scroll_started
