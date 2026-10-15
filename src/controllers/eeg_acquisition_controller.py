"""EEG source selection, acquisition recording and LSL publishing workflow."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QWidget

from src.bci.config import SSVEP_CHANNEL_NAMES
from src.domain import EEGFrame, EEGStreamDescriptor
from src.services import LSLEEGOutlet, RawEEGCsvRecorder


class EEGAcquisitionHost(Protocol):
    _pipeline_worker: object | None
    _eeg_history: list
    _eeg_sample_rate: float
    _eeg_channel_names: tuple[str, ...]
    _eeg_original_channel_names: tuple[str, ...]
    _eeg_channel_units: tuple[str, ...]
    _eeg_health: dict[str, object]
    _eeg_status: str
    _eeg_events: deque[str]
    _latest_recognition: object | None
    acquisition_stage: str
    acquisition_tab: str
    research_page: str
    user: object | None
    _experiment_session_id: str
    csv_replay: object

    def _build_eeg_worker(self, replace: bool = False) -> None: ...
    def _start_eeg_pipeline(self) -> None: ...
    def _stop_soldier_lsl_bridge(self) -> None: ...
    def _notify(self, message: str) -> None: ...
    def update(self, *args) -> None: ...


class EEGAcquisitionController(QObject):
    """Own acquisition-facing state and coordinate the shared EEG worker."""

    lsl_discovery_completed = Signal(object, str)

    def __init__(
        self,
        stream,
        raw_recorder: RawEEGCsvRecorder,
        outlet: LSLEEGOutlet,
        host: EEGAcquisitionHost,
    ) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.stream = stream
        self.raw_recorder = raw_recorder
        self.outlet = outlet
        self.host = host
        self.continuous_recording = False
        self.source_mode = "direct"
        self.selected_device = ""
        self.waveform_paused = False
        self.last_frame_monotonic = 0.0
        self.sample_rate = 250
        self.hardware_channel_settings = [
            {"gain": "x24", "input": "Normal", "bias": "Yes", "srb2": "On", "srb1": "Off"}
            for _ in range(8)
        ]
        self._lsl_discovery_in_progress = False
        self.lsl_discovery_completed.connect(self._finish_lsl_discovery)

    def prepare_session(self) -> None:
        """Prepare the fixed eight-channel direct-hardware profile."""

        if self.host.csv_replay.snapshot().get("backend") != "hardware":
            if not self.discover_direct_devices():
                return
        self._reset_live_state()
        self.host.acquisition_stage = "runtime"
        self.host.acquisition_tab = "Dashboard"
        self.host._eeg_status = "设备已选择 · 点击 Start Data Stream 开始采集"
        self.host._build_eeg_worker(replace=True)
        self.host._notify("ExpandableEEG会话已建立；点击 Start Data Stream 开始8通道采集")
        self.host.update()

    def toggle_stream(self) -> None:
        worker = self.host._pipeline_worker
        running = worker is not None and worker.isRunning()
        if running:
            self.disconnect_pipeline(stop_csv_recording=True)
            self.host._eeg_status = "Data stream stopped"
            self.host._notify("OpenBCI 实时数据流已停止")
            return
        if self.host.csv_replay.snapshot().get("backend") == "csv":
            self.host._notify("采集页不回放 CSV；请返回采集设置并选择真实设备")
            return
        self.host._build_eeg_worker(replace=True)
        self.host._start_eeg_pipeline()
        self.host._notify("正在连接 ExpandableEEG 并读取 8 通道 EEG…")

    def return_to_setup(self) -> None:
        self.disconnect_pipeline(stop_csv_recording=True)
        self.host.acquisition_stage = "setup"
        self.host.acquisition_tab = "Dashboard"
        self.host._eeg_status = "等待配置实时采集会话"
        self.host.update()

    def select_source(self, mode: str) -> None:
        worker = self.host._pipeline_worker
        if worker is not None and worker.isRunning():
            self.host._notify("请先停止当前采集，再切换数据来源")
            return
        if mode not in {"direct", "lsl"}:
            return
        self.source_mode = mode
        if mode == "lsl":
            selector = getattr(self.stream, "select_lsl", None)
            if callable(selector):
                try:
                    selector()
                    self.host._eeg_status = "LSL输入已选择 · 请刷新数据流"
                except Exception as exc:
                    self.host._notify(f"切换LSL输入失败：{exc}")
                    return
        else:
            self.host._eeg_status = (
                f"已选择 {self.selected_device} · 等待开始采集"
                if self.selected_device
                else "等待搜索 ExpandableEEG 蓝牙COM/串口"
            )
        self.host.update()

    def discover_direct_devices(self) -> bool:
        discover = getattr(self.stream, "discover_hardware_devices", None)
        selector = getattr(self.stream, "select_hardware", None)
        if not callable(discover) or not callable(selector):
            self.host._notify("当前容器未配置直接硬件采集后端")
            return False
        worker = self.host._pipeline_worker
        if worker is not None and worker.isRunning():
            self.host._notify("请先停止当前采集，再搜索设备")
            return False
        self.host._eeg_status = "正在搜索蓝牙COM/串口设备…"
        self.host.update()
        try:
            devices = list(discover())
        except Exception as exc:
            self.host._eeg_status = "设备搜索失败"
            self.host._notify(f"设备搜索失败：{exc}")
            return False
        if not devices:
            self.host._eeg_status = "未发现串口设备"
            self.host._notify("未发现设备：请先在系统蓝牙中配对 ExpandableEEG，使其生成COM/串口")
            return False
        labels = [device.display_name for device in devices]
        selected, accepted = QInputDialog.getItem(
            self.host if isinstance(self.host, QWidget) else None,
            "选择 ExpandableEEG",
            "蓝牙COM/串口设备",
            labels,
            0,
            False,
        )
        if not accepted:
            self.host._eeg_status = "已取消设备选择"
            return False
        device = devices[labels.index(selected)]
        self.disconnect_pipeline(stop_csv_recording=True)
        try:
            selector(device)
        except Exception as exc:
            self.host._notify(f"选择设备失败：{exc}")
            return False
        self.source_mode = "direct"
        self.selected_device = device.port
        self.host._eeg_status = f"已选择 {device.port} · 8通道 / 250 Hz"
        self.host._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已选择 ExpandableEEG：{device.display_name}"
        )
        self.host._build_eeg_worker(replace=True)
        self.host.update()
        return True

    def refresh_source(self) -> None:
        if self.source_mode == "direct":
            self.discover_direct_devices()
        else:
            self.discover_lsl_streams()

    def discover_lsl_streams(self) -> None:
        discover = getattr(self.stream, "discover_streams", None)
        if not callable(discover):
            self.host._notify("当前 EEG 数据源不是 LSL；请将 BDS_EEG_BACKEND 配置为 lsl")
            return
        if self._lsl_discovery_in_progress:
            self.host._notify("正在搜索 LSL EEG 流，请稍候…")
            return
        self._lsl_discovery_in_progress = True
        self.host._eeg_status = "正在搜索 LSL EEG 流…"

        def run_discovery() -> None:
            try:
                self.lsl_discovery_completed.emit(list(discover()), "")
            except Exception as exc:
                self.lsl_discovery_completed.emit([], str(exc))

        threading.Thread(target=run_discovery, name="LSL-Discovery", daemon=True).start()
        self.host.update()

    def start_integrated_collection(self) -> None:
        worker = self.host._pipeline_worker
        if worker is not None and worker.isRunning():
            self.host._notify("采集已经在运行")
            return
        snapshot = self.host.csv_replay.snapshot()
        if self.source_mode == "direct":
            if snapshot.get("backend") != "hardware" and not self.discover_direct_devices():
                return
        else:
            selector = getattr(self.stream, "select_lsl", None)
            if snapshot.get("backend") != "lsl" and callable(selector):
                try:
                    selector()
                except Exception as exc:
                    self.host._notify(f"切换LSL输入失败：{exc}")
                    return
        self._reset_live_state()
        self.waveform_paused = False
        self.host._build_eeg_worker(replace=True)
        source = str(getattr(self.stream, "source_name", "EEG"))
        user = self.host.user
        subject_id = user.uid or user.username if user is not None else ""
        if not self.raw_recorder.is_recording:
            self.raw_recorder.start(
                session_id=self.host._experiment_session_id,
                subject_id=subject_id,
                source=source,
            )
        self.continuous_recording = True
        if self.host._pipeline_worker is not None:
            self.host._pipeline_worker.set_continuous_recording(True)
        self.host._start_eeg_pipeline()
        self.host._notify("正在连接设备并采集；原始8通道EEG将自动保存为CSV")

    def stop_integrated_collection(self) -> None:
        path = self.raw_recorder.stop() if self.raw_recorder.is_recording else None
        self.continuous_recording = False
        if self.host._pipeline_worker is not None:
            self.host._pipeline_worker.set_continuous_recording(False)
        self.disconnect_pipeline(stop_csv_recording=False)
        self.host._eeg_status = "采集已停止"
        self.host._notify(f"采集已停止，CSV已保存：{path}" if path else "采集已停止")

    def toggle_waveform(self) -> None:
        self.waveform_paused = not self.waveform_paused
        self.host._notify(
            "已暂停波形刷新，CSV仍在保存" if self.waveform_paused else "已恢复波形刷新"
        )
        self.host.update()

    def open_data_directory(self) -> None:
        directory = self.raw_recorder.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.host._notify(f"无法自动打开目录：{directory}")

    def toggle_outlet(self) -> None:
        if self.outlet.active:
            self.outlet.stop()
            self.host._notify("LSL EEG 输出已停止")
            self.host.update()
            return
        worker = self.host._pipeline_worker
        if worker is None or not worker.isRunning():
            self.host._notify("请先在采集页启动 ExpandableEEG 数据流")
            return
        try:
            self.outlet.start(
                self.host._eeg_channel_names or tuple(f"CH{index + 1}" for index in range(8)),
                self.host._eeg_sample_rate or 250.0,
                self.host._eeg_channel_units or ("uV",) * 8,
            )
        except Exception as exc:
            self.host._notify(f"启动LSL输出失败：{exc}")
            return
        self.host._notify("LSL输出已启动：name=obci_eeg1，type=EEG，8通道")
        self.host.update()

    def toggle_raw_recording(self) -> None:
        if self.raw_recorder.is_recording:
            path = self.raw_recorder.stop()
            self.continuous_recording = False
            if self.host._pipeline_worker is not None:
                self.host._pipeline_worker.set_continuous_recording(False)
            self.host._notify(
                f"原始 EEG CSV 已保存：{path}" if path else "CSV 采集已停止（尚未收到数据）"
            )
            return
        worker = self.host._pipeline_worker
        if worker is None or not worker.isRunning():
            self.host._notify("EEG 采集未运行，不能开始 CSV 录制")
            return
        source = worker.source_name
        if source.startswith("CSV"):
            self.host._notify("当前是 CSV 回放；原始采集请先在“采集”页连接真实设备")
            return
        user = self.host.user
        subject_id = user.uid or user.username if user is not None else ""
        self.raw_recorder.start(
            session_id=self.host._experiment_session_id,
            subject_id=subject_id,
            source=source,
        )
        self.continuous_recording = True
        worker.set_continuous_recording(True)
        self.host._notify("已开始保存原始 EEG：CSV 全量记录 + 10 秒 NPZ 分块")

    def disconnect_pipeline(self, stop_csv_recording: bool = True) -> None:
        if stop_csv_recording and self.raw_recorder.is_recording:
            self.raw_recorder.stop()
        self.continuous_recording = self.raw_recorder.is_recording
        worker = self.host._pipeline_worker
        if worker is not None:
            if stop_csv_recording:
                worker.set_continuous_recording(False)
            if worker.isRunning():
                worker.stop()
        self.host._stop_soldier_lsl_bridge()
        self.outlet.stop()
        self.host._eeg_status = "已由用户断开"
        self.host._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已由用户断开 EEG 数据源"
        )
        self.host.update()

    def handle_frame(self, frame: EEGFrame, *, publish_outlet: bool) -> None:
        self.last_frame_monotonic = time.monotonic()
        if len(self.host._eeg_history) != len(frame.samples):
            self.host._eeg_history = [deque(maxlen=1250) for _ in frame.samples]
        if not self.waveform_paused:
            for history, values in zip(self.host._eeg_history, frame.samples):
                history.extend(float(value) for value in values)
        self.host._eeg_sample_rate = frame.sample_rate_hz
        self.host._eeg_channel_names = tuple(frame.channel_names)
        self.host._eeg_original_channel_names = tuple(frame.original_channel_names)
        self.host._eeg_channel_units = tuple(frame.channel_units)
        if self.outlet.active and publish_outlet:
            try:
                self.outlet.push_frame(frame)
            except Exception as exc:
                self.outlet.stop()
                self.host._eeg_events.appendleft(
                    f"{datetime.now():%H:%M:%S}  ERROR  LSL输出已停止：{exc}"
                )
        if self.raw_recorder.is_recording:
            try:
                self.raw_recorder.append(frame)
            except Exception as exc:
                self.raw_recorder.stop()
                self.continuous_recording = False
                self.host._eeg_events.appendleft(
                    f"{datetime.now():%H:%M:%S}  ERROR  CSV 写入失败：{exc}"
                )

    def close_recording_after_worker(self) -> None:
        if self.raw_recorder.is_recording:
            path = self.raw_recorder.stop()
            self.host._eeg_events.appendleft(
                f"{datetime.now():%H:%M:%S}  采集线程结束，CSV已安全关闭：{path or '无数据'}"
            )
        self.continuous_recording = False

    def _finish_lsl_discovery(
        self,
        descriptors: list[EEGStreamDescriptor],
        error: str,
    ) -> None:
        self._lsl_discovery_in_progress = False
        if error:
            self.host._eeg_status = "LSL 搜索失败"
            self.host._notify(f"LSL 搜索失败：{error}")
            return
        if not descriptors:
            self.host._eeg_status = "未发现 LSL EEG 流"
            self.host._notify("未发现 EEG 流：请先在 OpenBCI GUI 中启动 Networking → LSL")
            return
        labels = [descriptor.display_name for descriptor in descriptors]
        selected_label, accepted = QInputDialog.getItem(
            self.host if isinstance(self.host, QWidget) else None,
            "选择 LSL EEG 数据源",
            "发现的 EEG 流",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        descriptor = descriptors[labels.index(selected_label)]
        self.disconnect_pipeline()
        try:
            self.stream.select_stream(descriptor)
        except Exception as exc:
            self.host._notify(f"切换 LSL 流失败：{exc}")
            return
        self.host._eeg_channel_names = descriptor.channel_names
        self.host._eeg_channel_units = descriptor.channel_units
        self.host._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  已选择 {descriptor.display_name}"
        )
        self.host._build_eeg_worker(replace=True)
        self.host._eeg_status = f"已选择 {descriptor.name} · 等待 Start Session"
        self.host._notify(f"已选择实时流：{descriptor.display_name}")

    def _reset_live_state(self) -> None:
        self.host._eeg_history = [deque(maxlen=1250) for _ in range(8)]
        self.host._eeg_sample_rate = 0.0
        self.host._eeg_channel_names = tuple(SSVEP_CHANNEL_NAMES)
        self.host._eeg_channel_units = ("uV",) * 8
        self.host._eeg_health = {}
        self.host._latest_recognition = None


__all__ = ["EEGAcquisitionController"]
