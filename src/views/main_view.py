"""Main dashboard shell: lifecycle, EEG events and shared input handling."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QInputDialog, QStackedWidget, QWidget
from src.bci.preprocessing import assess_signal_quality
from src.domain import EEGFrame, EEGStreamDescriptor, IntentResult, Role, User
from src.ports import EEGStream, EngineerDataProvider, IntentDecoder, SwarmGateway, SystemMonitor
from src.services import (
    ExperimentRecorder,
    LSLEEGOutlet,
    MissionPlanningError,
    RawEEGCsvRecorder,
    WorkbenchService,
)
from src.views.engineer_dashboard import EngineerDashboard
from src.views.main_action_router import MainActionRouterMixin
from src.views.main_dialogs import MainDialogsMixin
from src.views.role_pages import AdminPage, EngineerCanvasPage, ResearchPage, SoldierPage
from src.workers import EEGWorker, LSLRecorderWorker
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
    lsl_discovery_completed = Signal(object, str)
    lsl_collector_discovery_completed = Signal(object, str)

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
        self.research_page = "采集"
        self.acquisition_stage = "setup"
        self.acquisition_tab = "Dashboard"
        self._openbci_session_name = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        self._regions: list[tuple[QRectF, str]] = []
        self._toast = ""
        self._toast_generation = 0
        self._toast_scroll_started = 0.0
        self._eeg_history = [deque(maxlen=360) for _ in range(8)]
        self._eeg_sample_rate = 0.0
        self._eeg_channel_names: tuple[str, ...] = tuple(f"CH{i + 1}" for i in range(8))
        self._eeg_channel_units: tuple[str, ...] = tuple("" for _ in range(8))
        self._eeg_quality: dict[str, object] = {}
        self._eeg_health: dict[str, object] = {}
        self._lsl_discovery_in_progress = False
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
        csv_root = (
            experiment_recorder.root / "csv_sessions"
            if experiment_recorder is not None
            else Path.cwd() / "data" / "recordings" / "csv_sessions"
        )
        self._raw_csv_recorder = RawEEGCsvRecorder(csv_root)
        self._lsl_collector_recorder = RawEEGCsvRecorder(csv_root)
        self._csv_replay_paused = False
        self._collector_source_mode = "direct"
        self._collector_selected_device = ""
        self._collector_waveform_paused = False
        self._lsl_outlet = LSLEEGOutlet()
        self._lsl_collector_stream = None
        lsl_factory = getattr(self.eeg_stream, "create_lsl_receiver", None)
        if callable(lsl_factory):
            try:
                self._lsl_collector_stream = lsl_factory()
            except Exception:
                self._lsl_collector_stream = None
        self._lsl_collector_worker: LSLRecorderWorker | None = None
        self._lsl_collector_history = [deque(maxlen=1250) for _ in range(8)]
        self._lsl_collector_channel_names = tuple(f"CH{index + 1}" for index in range(8))
        self._lsl_collector_sample_rate = 0.0
        self._lsl_collector_status = "未连接 LSL EEG 流"
        self._lsl_collector_events: deque[str] = deque(maxlen=20)
        self._lsl_collector_waveform_paused = False
        self._lsl_collector_discovery_in_progress = False
        self._openbci_source = "CYTON"
        self._openbci_transport = "Serial"
        self._openbci_ganglion_transport = "Native BLE"
        self._openbci_wifi_mode = "Dynamic IP"
        self._openbci_streamer_mode = "File"
        self._openbci_session_format = "OpenBCI"
        self._openbci_session_name = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
        self._openbci_playback_path = ""
        self._openbci_board_type = "Cyton"
        self._openbci_status_message = "Select your data source and transfer protocol."
        self._openbci_ip = "192.168.4.1"
        self._openbci_channel_count = 8
        self._openbci_sample_rate = 250
        self._hardware_channel_settings = [
            {"gain": "x24", "input": "Normal", "bias": "Yes", "srb2": "On", "srb1": "Off"}
            for _ in range(8)
        ]
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
        self._build_eeg_worker()
        self.lsl_discovery_completed.connect(self._on_lsl_discovery_completed)
        self.lsl_collector_discovery_completed.connect(
            self._on_lsl_collector_discovery_completed
        )
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
                self._start_eeg_pipeline()
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

    def _build_eeg_worker(self, replace: bool = False) -> None:
        if replace and self._pipeline_worker is not None:
            old_worker = self._pipeline_worker
            if old_worker.isRunning():
                old_worker.stop()
            old_worker.deleteLater()
            self._pipeline_worker = None
        if self._pipeline_worker is not None:
            return
        if self.eeg_stream is None or self.intent_decoder is None or self.experiment_recorder is None:
            return
        worker = EEGWorker(
            self.eeg_stream, self.intent_decoder, self.experiment_recorder, parent=self,
        )
        worker.frame_received.connect(self._on_eeg_frame)
        worker.recognition_received.connect(self._on_recognition)
        worker.status_changed.connect(self._on_eeg_status)
        worker.error_occurred.connect(self._on_eeg_error)
        worker.labeled_trial_saved.connect(self._on_labeled_trial_saved)
        worker.continuous_chunk_saved.connect(self._on_continuous_chunk_saved)
        worker.finished.connect(lambda active_worker=worker: self._on_eeg_worker_finished(active_worker))
        worker.set_paradigm_type(self.actions.paradigm_type)
        if self.user is not None and self._experiment_session_id:
            worker.set_experiment_context(
                self.user.uid or self.user.username, self._experiment_session_id,
            )
        self._pipeline_worker = worker

    def _disconnect_eeg_pipeline(self, stop_csv_recording: bool = True) -> None:
        self._lsl_outlet.stop()
        if stop_csv_recording and self._raw_csv_recorder.is_recording:
            self._raw_csv_recorder.stop()
        self._continuous_recording = self._raw_csv_recorder.is_recording
        if self._pipeline_worker is not None:
            if stop_csv_recording:
                self._pipeline_worker.set_continuous_recording(False)
            if self._pipeline_worker.isRunning():
                self._pipeline_worker.stop()
        self._eeg_status = "已由用户断开"
        self._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  已由用户断开 EEG 数据源")
        self.update()

    def _restart_eeg_pipeline(self) -> None:
        self._disconnect_eeg_pipeline()
        self._build_eeg_worker(replace=True)
        self._start_eeg_pipeline()

    def _prepare_openbci_session(self) -> None:
        """Enter the OpenBCI runtime layout without replaying a configured CSV."""
        if self._openbci_source == "PLAYBACK":
            if not self._openbci_playback_path:
                self._openbci_status_message = "No playback file selected."
                self._notify("请先选择 OpenBCI / EEG 回放文件")
                self.update()
                return
            selector = getattr(self.eeg_stream, "select_csv", None)
            if not callable(selector):
                self._notify("当前 EEG 容器不支持运行时 CSV 回放")
                return
            self._disconnect_eeg_pipeline(stop_csv_recording=True)
            try:
                selector(self._openbci_playback_path)
            except Exception as exc:
                self._notify(f"回放文件加载失败：{exc}")
                return
            self._csv_replay_paused = False
            self._build_eeg_worker(replace=True)
            self.research_page = "实验"
            self._openbci_status_message = "Playback file ready."
            self._notify("回放文件已进入实验页；点击“开始回放”后才会播放")
            self.update()
            return
        if self._openbci_source == "SYNTHETIC":
            self._openbci_status_message = "Synthetic source disabled in research mode."
            self._notify("科研版本不生成模拟 EEG；请使用真实 LSL 或实验 CSV")
            self.update()
            return
        if self._openbci_source == "CYTON" and self._openbci_transport == "Serial":
            if self._csv_playback_snapshot().get("backend") != "hardware":
                if not self._discover_direct_devices():
                    return
            self._eeg_history = [deque(maxlen=1250) for _ in range(8)]
            self._eeg_sample_rate = 0.0
            self._eeg_channel_names = tuple(f"CH{index + 1}" for index in range(8))
            self._eeg_channel_units = ("uV",) * 8
            self._eeg_health = {}
            self._latest_recognition = None
            self.acquisition_stage = "runtime"
            self.acquisition_tab = "Dashboard"
            self._eeg_status = "设备已选择 · 点击 Start Data Stream 开始采集"
            self._openbci_status_message = "Direct Cyton-compatible session ready."
            self._build_eeg_worker(replace=True)
            self._notify("ExpandableEEG会话已建立；点击 Start Data Stream 开始8通道采集")
            self.update()
            return
        self._disconnect_eeg_pipeline(stop_csv_recording=True)
        selector = getattr(self.eeg_stream, "select_lsl", None)
        if callable(selector):
            try:
                selector()
            except Exception as exc:
                self._notify(f"切换实时 LSL 失败：{exc}")
                return
        elif self._csv_playback_snapshot().get("backend") == "csv":
            self._notify("当前容器只有 CSV 回放，不能作为 OpenBCI 实时采集源")
            return
        self._eeg_history = [deque(maxlen=360) for _ in range(self._openbci_channel_count)]
        self._eeg_sample_rate = 0.0
        self._eeg_channel_names = tuple(
            f"CH{index + 1}" for index in range(self._openbci_channel_count)
        )
        self._eeg_channel_units = tuple("" for _ in range(self._openbci_channel_count))
        self._eeg_health = {}
        self._latest_recognition = None
        self.acquisition_stage = "runtime"
        self.acquisition_tab = "Dashboard"
        self._eeg_status = "Session started · 点击 Start Data Stream"
        self._openbci_status_message = "Session started!"
        self._build_eeg_worker(replace=True)
        self._notify("OpenBCI 会话已建立；点击 Start Data Stream 接收实时 LSL")

    def _choose_openbci_playback_file(self) -> None:
        """Select an OpenBCI/EEG recording without starting replay."""
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select OpenBCI Playback File",
            str(Path.home()),
            "OpenBCI / EEG files (*.txt *.csv);;All files (*)",
        )
        if not path:
            return
        self._openbci_playback_path = path
        self._openbci_status_message = f"Playback file selected: {Path(path).name}"
        self._notify(f"已选择回放文件：{Path(path).name}；尚未开始播放")
        self.update()

    def _generate_openbci_session_name(self) -> None:
        username = self.user.username if self.user is not None else "researcher"
        self._openbci_session_name = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
        self._experiment_session_id = f"{self._openbci_session_name}-{username}"
        if self._pipeline_worker is not None:
            subject_id = self.user.uid or username if self.user is not None else username
            self._pipeline_worker.set_experiment_context(subject_id, self._experiment_session_id)
        self._openbci_status_message = "Autogenerated Session Name based on current date & time."
        self.update()

    def _toggle_openbci_data_stream(self) -> None:
        running = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        if running:
            self._disconnect_eeg_pipeline(stop_csv_recording=True)
            self._eeg_status = "Data stream stopped"
            self._notify("OpenBCI 实时数据流已停止")
            return
        if self._csv_playback_snapshot().get("backend") == "csv":
            self._notify("采集页不回放 CSV；请返回采集设置并选择真实设备")
            return
        self._build_eeg_worker(replace=True)
        self._start_eeg_pipeline()
        self._notify("正在连接 ExpandableEEG 并读取 8 通道 EEG…")

    def _return_openbci_control_panel(self) -> None:
        self._disconnect_eeg_pipeline(stop_csv_recording=True)
        self.acquisition_stage = "setup"
        self.acquisition_tab = "Dashboard"
        self._eeg_status = "等待配置实时采集会话"
        self.update()

    def _discover_lsl_streams(self) -> None:
        discover = getattr(self.eeg_stream, "discover_streams", None)
        if not callable(discover):
            self._notify("当前 EEG 数据源不是 LSL；请将 BDS_EEG_BACKEND 配置为 lsl")
            return
        if self._lsl_discovery_in_progress:
            self._notify("正在搜索 LSL EEG 流，请稍候…")
            return
        self._lsl_discovery_in_progress = True
        self._eeg_status = "正在搜索 LSL EEG 流…"

        def run_discovery() -> None:
            try:
                descriptors = list(discover())
                self.lsl_discovery_completed.emit(descriptors, "")
            except Exception as exc:
                self.lsl_discovery_completed.emit([], str(exc))

        threading.Thread(target=run_discovery, name="LSL-Discovery", daemon=True).start()
        self.update()

    def _select_collector_source(self, mode: str) -> None:
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._notify("请先停止当前采集，再切换数据来源")
            return
        if mode not in {"direct", "lsl"}:
            return
        self._collector_source_mode = mode
        if mode == "lsl":
            selector = getattr(self.eeg_stream, "select_lsl", None)
            if callable(selector):
                try:
                    selector()
                    self._eeg_status = "LSL输入已选择 · 请刷新数据流"
                except Exception as exc:
                    self._notify(f"切换LSL输入失败：{exc}")
                    return
        else:
            self._eeg_status = (
                f"已选择 {self._collector_selected_device} · 等待开始采集"
                if self._collector_selected_device
                else "等待搜索 ExpandableEEG 蓝牙COM/串口"
            )
        self.update()

    def _discover_direct_devices(self) -> bool:
        discover = getattr(self.eeg_stream, "discover_hardware_devices", None)
        selector = getattr(self.eeg_stream, "select_hardware", None)
        if not callable(discover) or not callable(selector):
            self._notify("当前容器未配置直接硬件采集后端")
            return False
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._notify("请先停止当前采集，再搜索设备")
            return False
        self._eeg_status = "正在搜索蓝牙COM/串口设备…"
        self.update()
        try:
            devices = list(discover())
        except Exception as exc:
            self._eeg_status = "设备搜索失败"
            self._notify(f"设备搜索失败：{exc}")
            return False
        if not devices:
            self._eeg_status = "未发现串口设备"
            self._notify("未发现设备：请先在系统蓝牙中配对 ExpandableEEG，使其生成COM/串口")
            return False
        labels = [device.display_name for device in devices]
        selected, accepted = QInputDialog.getItem(
            self, "选择 ExpandableEEG", "蓝牙COM/串口设备", labels, 0, False,
        )
        if not accepted:
            self._eeg_status = "已取消设备选择"
            return False
        device = devices[labels.index(selected)]
        self._disconnect_eeg_pipeline(stop_csv_recording=True)
        try:
            selector(device)
        except Exception as exc:
            self._notify(f"选择设备失败：{exc}")
            return False
        self._collector_source_mode = "direct"
        self._collector_selected_device = device.port
        self._eeg_status = f"已选择 {device.port} · 8通道 / 250 Hz"
        self._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已选择 ExpandableEEG：{device.display_name}"
        )
        self._build_eeg_worker(replace=True)
        self.update()
        return True

    def _refresh_collector_source(self) -> None:
        if self._collector_source_mode == "direct":
            self._discover_direct_devices()
        else:
            self._discover_lsl_streams()

    def _start_integrated_collection(self) -> None:
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._notify("采集已经在运行")
            return
        snapshot = self._csv_playback_snapshot()
        if self._collector_source_mode == "direct":
            if snapshot.get("backend") != "hardware" and not self._discover_direct_devices():
                return
        else:
            selector = getattr(self.eeg_stream, "select_lsl", None)
            if snapshot.get("backend") != "lsl" and callable(selector):
                try:
                    selector()
                except Exception as exc:
                    self._notify(f"切换LSL输入失败：{exc}")
                    return
        self._eeg_history = [deque(maxlen=1250) for _ in range(8)]
        self._eeg_channel_names = tuple(f"CH{index + 1}" for index in range(8))
        self._eeg_channel_units = ("uV",) * 8
        self._eeg_health = {}
        self._collector_waveform_paused = False
        self._build_eeg_worker(replace=True)
        source = str(getattr(self.eeg_stream, "source_name", "EEG"))
        subject_id = self.user.uid or self.user.username if self.user is not None else ""
        if not self._raw_csv_recorder.is_recording:
            self._raw_csv_recorder.start(
                session_id=self._experiment_session_id,
                subject_id=subject_id,
                source=source,
            )
        self._continuous_recording = True
        if self._pipeline_worker is not None:
            self._pipeline_worker.set_continuous_recording(True)
        self._start_eeg_pipeline()
        self._notify("正在连接设备并采集；原始8通道EEG将自动保存为CSV")

    def _stop_integrated_collection(self) -> None:
        path = self._raw_csv_recorder.stop() if self._raw_csv_recorder.is_recording else None
        self._continuous_recording = False
        if self._pipeline_worker is not None:
            self._pipeline_worker.set_continuous_recording(False)
        self._disconnect_eeg_pipeline(stop_csv_recording=False)
        self._eeg_status = "采集已停止"
        self._notify(f"采集已停止，CSV已保存：{path}" if path else "采集已停止")

    def _toggle_collector_waveform(self) -> None:
        self._collector_waveform_paused = not self._collector_waveform_paused
        state = "已暂停波形刷新，CSV仍在保存" if self._collector_waveform_paused else "已恢复波形刷新"
        self._notify(state)
        self.update()

    def _open_collector_data_directory(self) -> None:
        directory = self._raw_csv_recorder.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self._notify(f"无法自动打开目录：{directory}")

    def _toggle_lsl_outlet(self) -> None:
        if self._lsl_outlet.active:
            self._lsl_outlet.stop()
            self._notify("LSL EEG 输出已停止")
            self.update()
            return
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            self._notify("请先在采集页启动 ExpandableEEG 数据流")
            return
        try:
            self._lsl_outlet.start(
                self._eeg_channel_names or tuple(f"CH{index + 1}" for index in range(8)),
                self._eeg_sample_rate or 250.0,
                self._eeg_channel_units or ("uV",) * 8,
            )
        except Exception as exc:
            self._notify(f"启动LSL输出失败：{exc}")
            return
        self._notify("LSL输出已启动：name=obci_eeg1，type=EEG，8通道")
        self.update()

    def _discover_lsl_collector_streams(self) -> None:
        stream = self._lsl_collector_stream
        discover = getattr(stream, "discover_streams", None)
        if not callable(discover):
            self._notify("当前容器没有独立LSL接收器")
            return
        if self._lsl_collector_discovery_in_progress:
            self._notify("正在搜索LSL EEG流，请稍候…")
            return
        if self._lsl_collector_worker is not None and self._lsl_collector_worker.isRunning():
            self._notify("请先停止LSL收集，再切换数据流")
            return
        self._lsl_collector_discovery_in_progress = True
        self._lsl_collector_status = "正在搜索 type=EEG 的LSL流…"

        def run_discovery() -> None:
            try:
                self.lsl_collector_discovery_completed.emit(list(discover()), "")
            except Exception as exc:
                self.lsl_collector_discovery_completed.emit([], str(exc))

        threading.Thread(target=run_discovery, name="LSL-Collector-Discovery", daemon=True).start()
        self.update()

    def _on_lsl_collector_discovery_completed(
        self, descriptors: list[EEGStreamDescriptor], error: str,
    ) -> None:
        self._lsl_collector_discovery_in_progress = False
        if error:
            self._lsl_collector_status = "LSL搜索失败"
            self._notify(f"LSL搜索失败：{error}")
            return
        if not descriptors:
            self._lsl_collector_status = "未发现LSL EEG流"
            self._notify("未发现流：请在‘采集 → Networking’启动LSL输出")
            return
        labels = [descriptor.display_name for descriptor in descriptors]
        selected, accepted = QInputDialog.getItem(
            self, "选择LSL EEG数据流", "发现的数据流", labels, 0, False,
        )
        if not accepted:
            return
        descriptor = descriptors[labels.index(selected)]
        try:
            self._lsl_collector_stream.select_stream(descriptor)
        except Exception as exc:
            self._notify(f"选择LSL流失败：{exc}")
            return
        self._lsl_collector_channel_names = descriptor.channel_names or tuple(
            f"CH{index + 1}" for index in range(descriptor.channel_count)
        )
        history_size = max(1, round((descriptor.nominal_sample_rate_hz or 250.0) * 5))
        self._lsl_collector_history = [
            deque(maxlen=history_size) for _ in range(descriptor.channel_count)
        ]
        self._lsl_collector_status = f"已选择 {descriptor.display_name}"
        self._lsl_collector_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已选择 {descriptor.display_name}"
        )
        self.update()

    def _start_lsl_collection(self) -> None:
        if self._lsl_collector_stream is None:
            self._notify("当前容器没有独立LSL接收器")
            return
        if self._lsl_collector_worker is not None and self._lsl_collector_worker.isRunning():
            self._notify("LSL收集已经在运行")
            return
        if self._lsl_collector_recorder.is_recording:
            self._lsl_collector_recorder.stop()
        subject_id = self.user.uid or self.user.username if self.user is not None else ""
        worker = LSLRecorderWorker(
            self._lsl_collector_stream,
            self._lsl_collector_recorder,
            self._experiment_session_id,
            subject_id,
            parent=self,
        )
        worker.frame_received.connect(self._on_lsl_collector_frame)
        worker.status_changed.connect(self._on_lsl_collector_status)
        worker.error_occurred.connect(self._on_lsl_collector_error)
        worker.recording_stopped.connect(self._on_lsl_collection_stopped)
        self._lsl_collector_worker = worker
        self._lsl_collector_waveform_paused = False
        self._lsl_collector_status = "正在连接LSL EEG流…"
        worker.start()
        self.update()

    def _stop_lsl_collection(self) -> None:
        worker = self._lsl_collector_worker
        if worker is not None and worker.isRunning():
            worker.stop()
        elif self._lsl_collector_recorder.is_recording:
            self._lsl_collector_recorder.stop()
        self._lsl_collector_status = "LSL收集已停止"
        self.update()

    def _toggle_lsl_collector_waveform(self) -> None:
        self._lsl_collector_waveform_paused = not self._lsl_collector_waveform_paused
        self._notify(
            "已暂停LSL波形，CSV仍持续保存"
            if self._lsl_collector_waveform_paused
            else "已恢复LSL波形"
        )
        self.update()

    def _open_lsl_collector_directory(self) -> None:
        directory = self._lsl_collector_recorder.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self._notify(f"无法自动打开目录：{directory}")

    def _on_lsl_collector_frame(self, frame: EEGFrame) -> None:
        if len(self._lsl_collector_history) != len(frame.samples):
            self._lsl_collector_history = [deque(maxlen=1250) for _ in frame.samples]
        if not self._lsl_collector_waveform_paused:
            for history, values in zip(self._lsl_collector_history, frame.samples):
                history.extend(float(value) for value in values)
        self._lsl_collector_channel_names = tuple(frame.channel_names)
        self._lsl_collector_sample_rate = float(frame.sample_rate_hz)
        self.update()

    def _on_lsl_collector_status(self, status: str) -> None:
        self._lsl_collector_status = status
        self._lsl_collector_events.appendleft(f"{datetime.now():%H:%M:%S}  {status}")
        self.update()

    def _on_lsl_collector_error(self, error: str) -> None:
        self._lsl_collector_events.appendleft(f"{datetime.now():%H:%M:%S}  ERROR  {error}")
        self.update()

    def _on_lsl_collection_stopped(self, path: str, sample_count: int) -> None:
        message = f"LSL收集结束：{sample_count}个样本"
        if path:
            message += f" · {path}"
        self._lsl_collector_events.appendleft(f"{datetime.now():%H:%M:%S}  {message}")
        self._lsl_collector_status = message
        self.update()

    def _choose_csv_replay(self) -> None:
        selector = getattr(self.eeg_stream, "select_csv", None)
        if not callable(selector):
            self._notify("当前 EEG 容器不支持运行时 CSV 回放")
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入 OpenBCI / LSL EEG CSV",
            str(Path.home()),
            "EEG CSV (*.csv);;所有文件 (*)",
        )
        if not path:
            self._notify("已取消导入 CSV")
            return
        self._disconnect_eeg_pipeline(stop_csv_recording=True)
        try:
            selector(path)
        except Exception as exc:
            self._notify(f"CSV 导入失败：{exc}")
            return
        self._eeg_history = []
        self._eeg_channel_names = ()
        self._eeg_channel_units = ()
        self._latest_recognition = None
        self._csv_replay_paused = False
        self._build_eeg_worker(replace=True)
        self._start_eeg_pipeline()
        self.research_page = "实验"
        self._notify(f"已导入并开始回放：{Path(path).name}")

    def _csv_playback_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self.eeg_stream, "playback_snapshot", None)
        if callable(snapshot):
            try:
                return dict(snapshot())
            except Exception as exc:
                return {"backend": "error", "error": str(exc)}
        return {"backend": "unavailable"}

    def _toggle_csv_playback(self) -> None:
        if self._csv_playback_snapshot().get("backend") != "csv":
            self._notify("请先导入 CSV 文件")
            return
        running = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        if running:
            self._disconnect_eeg_pipeline(stop_csv_recording=False)
            self._csv_replay_paused = True
            self._notify("CSV 回放已暂停")
        else:
            self._build_eeg_worker(replace=True)
            self._start_eeg_pipeline()
            self._csv_replay_paused = False
            self._notify("CSV 回放已继续")

    def _rewind_csv_playback(self) -> None:
        rewind = getattr(self.eeg_stream, "rewind_csv", None)
        if not callable(rewind):
            self._notify("当前 EEG 容器不支持 CSV 回放")
            return
        was_running = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        if was_running:
            self._disconnect_eeg_pipeline(stop_csv_recording=False)
        try:
            rewind()
        except Exception as exc:
            self._notify(f"CSV 回到开头失败：{exc}")
            return
        self._eeg_history = [deque(maxlen=360) for _ in self._eeg_channel_names]
        if was_running:
            self._build_eeg_worker(replace=True)
            self._start_eeg_pipeline()
        self._notify("CSV 已回到开头")

    def _seek_csv_playback(self, fraction: float) -> None:
        seek = getattr(self.eeg_stream, "seek_csv", None)
        if not callable(seek):
            self._notify("当前 EEG 容器不支持 CSV 回放")
            return
        try:
            seek(fraction)
        except Exception as exc:
            self._notify(f"CSV 定位失败：{exc}")
            return
        self._eeg_history = [deque(maxlen=360) for _ in self._eeg_channel_names]
        self.update()

    def _set_csv_playback_speed(self, speed: float) -> None:
        setter = getattr(self.eeg_stream, "set_csv_playback_speed", None)
        if not callable(setter):
            self._notify("当前 EEG 容器不支持 CSV 回放")
            return
        try:
            setter(speed)
        except Exception as exc:
            self._notify(f"设置回放速度失败：{exc}")
            return
        self._notify(f"CSV 回放速度：{speed:g}x")

    def _toggle_raw_csv_recording(self) -> None:
        if self._raw_csv_recorder.is_recording:
            path = self._raw_csv_recorder.stop()
            self._continuous_recording = False
            if self._pipeline_worker is not None:
                self._pipeline_worker.set_continuous_recording(False)
            self._notify(f"原始 EEG CSV 已保存：{path}" if path else "CSV 采集已停止（尚未收到数据）")
            return
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            self._notify("EEG 采集未运行，不能开始 CSV 录制")
            return
        source = self._pipeline_worker.source_name
        if source.startswith("CSV"):
            self._notify("当前是 CSV 回放；原始采集请先在“采集”页连接真实设备")
            return
        subject_id = self.user.uid or self.user.username if self.user is not None else ""
        self._raw_csv_recorder.start(
            session_id=self._experiment_session_id,
            subject_id=subject_id,
            source=source,
        )
        self._continuous_recording = True
        self._pipeline_worker.set_continuous_recording(True)
        self._notify("已开始保存原始 EEG：CSV 全量记录 + 10 秒 NPZ 分块")

    def _on_lsl_discovery_completed(
        self, descriptors: list[EEGStreamDescriptor], error: str,
    ) -> None:
        self._lsl_discovery_in_progress = False
        if error:
            self._eeg_status = "LSL 搜索失败"
            self._notify(f"LSL 搜索失败：{error}")
            return
        if not descriptors:
            self._eeg_status = "未发现 LSL EEG 流"
            self._notify("未发现 EEG 流：请先在 OpenBCI GUI 中启动 Networking → LSL")
            return
        labels = [descriptor.display_name for descriptor in descriptors]
        selected_label, accepted = QInputDialog.getItem(
            self, "选择 LSL EEG 数据源", "发现的 EEG 流", labels, 0, False,
        )
        if not accepted:
            return
        descriptor = descriptors[labels.index(selected_label)]
        self._disconnect_eeg_pipeline()
        try:
            self.eeg_stream.select_stream(descriptor)  # type: ignore[attr-defined]
        except Exception as exc:
            self._notify(f"切换 LSL 流失败：{exc}")
            return
        self._eeg_channel_names = descriptor.channel_names
        self._eeg_channel_units = descriptor.channel_units
        self._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已选择 {descriptor.display_name}"
        )
        self._build_eeg_worker(replace=True)
        self._eeg_status = f"已选择 {descriptor.name} · 等待 Start Session"
        self._notify(f"已选择实时流：{descriptor.display_name}")

    def deactivate(self) -> None:
        if self._stimulus_window is not None:
            self._stimulus_window.close()
        if self._raw_csv_recorder.is_recording:
            self._raw_csv_recorder.stop()
        self._continuous_recording = False
        if self._pipeline_worker is not None:
            self._pipeline_worker.set_continuous_recording(False)
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            self._pipeline_worker.stop()
        if self._lsl_collector_worker is not None and self._lsl_collector_worker.isRunning():
            self._lsl_collector_worker.stop()
        self._lsl_outlet.stop()

    def _on_eeg_frame(self, frame: EEGFrame) -> None:
        if len(self._eeg_history) != len(frame.samples):
            self._eeg_history = [deque(maxlen=1250) for _ in frame.samples]
        if not self._collector_waveform_paused:
            for history, values in zip(self._eeg_history, frame.samples):
                history.extend(float(value) for value in values)
        self._eeg_sample_rate = frame.sample_rate_hz
        self._eeg_channel_names = tuple(frame.channel_names)
        self._eeg_channel_units = tuple(frame.channel_units)
        if self._lsl_outlet.active:
            try:
                self._lsl_outlet.push_frame(frame)
            except Exception as exc:
                self._lsl_outlet.stop()
                self._eeg_events.appendleft(
                    f"{datetime.now():%H:%M:%S}  ERROR  LSL输出已停止：{exc}"
                )
        if self._raw_csv_recorder.is_recording:
            try:
                self._raw_csv_recorder.append(frame)
            except Exception as exc:
                self._raw_csv_recorder.stop()
                self._continuous_recording = False
                self._eeg_events.appendleft(
                    f"{datetime.now():%H:%M:%S}  ERROR  CSV 写入失败：{exc}"
                )
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

    def _on_eeg_worker_finished(self, worker: EEGWorker) -> None:
        if self._pipeline_worker is not worker:
            return
        if self._raw_csv_recorder.is_recording:
            path = self._raw_csv_recorder.stop()
            self._eeg_events.appendleft(
                f"{datetime.now():%H:%M:%S}  采集线程结束，CSV已安全关闭：{path or '无数据'}"
            )
        self._continuous_recording = False
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
