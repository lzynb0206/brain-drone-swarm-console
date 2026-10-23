"""Soldier live-BCI chain orchestration and authorization safety."""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QTimer

from src.bci.config import SOLDIER_EEG_PORT, SSVEP_CHANNEL_NAMES
from src.domain import EEGFrame
from src.workers import EEGLSLBridgeWorker


class SoldierBCIHost(Protocol):
    eeg_stream: object
    swarm_gateway: object | None
    actions: object
    acquisition: object
    _pipeline_worker: object | None
    _latest_recognition: object | None
    _eeg_history: list
    _eeg_channel_names: tuple[str, ...]
    _eeg_channel_units: tuple[str, ...]
    _eeg_sample_rate: float
    _eeg_quality: dict[str, object]
    _eeg_health: dict[str, object]
    _eeg_status: str
    _eeg_events: deque[str]

    def _disconnect_eeg_pipeline(self, stop_csv_recording: bool = True) -> None: ...
    def _build_eeg_worker(self, replace: bool = False) -> None: ...
    def _start_eeg_pipeline(self) -> None: ...
    def _show_ssvep_window(self, mode: str) -> None: ...
    def _notify(self, message: str) -> None: ...
    def update(self, *args) -> None: ...


class SoldierBCIController:
    """Own the hardware → LSL → decoder chain used by soldier mode."""

    def __init__(self, host: SoldierBCIHost) -> None:
        self.host = host
        self.bridge_worker: EEGLSLBridgeWorker | None = None
        self.start_pending = False
        self.open_when_ready = False
        self.chain_active = False
        self.outlet_started_here = False

    def live_chain_ready(self) -> bool:
        worker = self.host._pipeline_worker
        worker_running = bool(worker is not None and worker.isRunning())
        last_frame = self.host.acquisition.last_frame_monotonic
        fresh = last_frame > 0.0 and time.monotonic() - last_frame <= 2.0
        return bool(
            self.chain_active
            and worker_running
            and fresh
            and self.host.acquisition.outlet.active
        )

    def start(self, *, open_control_when_ready: bool) -> bool:
        if self.start_pending:
            self.open_when_ready |= bool(open_control_when_ready)
            self.host._notify("SSVEP实时链路正在启动，请等待首帧脑电确认…")
            return True
        if self.live_chain_ready():
            worker = self.host._pipeline_worker
            source = worker.source_name if worker is not None else ""
            if not self.host.actions.bci_control_enabled:
                self.host._notify(self.host.actions.set_bci_control_enabled(True, source))
            if open_control_when_ready and self.host.actions.bci_control_enabled:
                self.host._show_ssvep_window("control")
            return True

        gateway = self.host.swarm_gateway
        gateway_status = gateway.status() if gateway is not None else None
        if gateway_status is None or not gateway_status.connected:
            self.host._notify("SSVEP启动失败：请先连接实机")
            return False
        if not gateway_status.control_enabled:
            self.host._notify("SSVEP启动失败：实机当前为只读遥测模式")
            return False
        if gateway_status.emergency:
            self.host._notify("SSVEP启动失败：系统处于急停锁定状态")
            return False

        discover = getattr(self.host.eeg_stream, "discover_hardware_devices", None)
        bridge_configurer = getattr(self.host.eeg_stream, "configure_hardware_bridge", None)
        lsl_selector = getattr(self.host.eeg_stream, "select_lsl", None)
        if not (callable(discover) and callable(bridge_configurer) and callable(lsl_selector)):
            self.host._notify("SSVEP启动失败：当前程序未配置 ExpandableEEG 直接采集后端")
            return False

        self.host.actions.set_bci_control_enabled(False, "")
        self.host._disconnect_eeg_pipeline(stop_csv_recording=True)
        self._reset_eeg_state()

        try:
            devices = list(discover())
            if not devices:
                raise RuntimeError("未发现已配对的 ExpandableEEG 蓝牙/串口设备")
            selected = self._select_device(devices)
            hardware_stream = bridge_configurer(selected)
            lsl_selector(self.host.acquisition.outlet.source_id)
        except Exception as exc:
            self.host._eeg_status = "SSVEP实时链路启动失败"
            self.host._notify(f"SSVEP启动失败：{exc}")
            return False

        self.host.acquisition.source_mode = "direct"
        self.host.acquisition.selected_device = str(selected.port)
        self.start_pending = True
        self.open_when_ready = bool(open_control_when_ready)
        self.chain_active = False
        self.outlet_started_here = False
        self.host._eeg_status = f"正在连接 {selected.port} 并等待首帧脑电…"
        bridge = EEGLSLBridgeWorker(
            hardware_stream,
            self.host.acquisition.outlet,
            parent=self.host,
        )
        bridge.status_changed.connect(self._on_bridge_status)
        bridge.error_occurred.connect(self._on_bridge_error)
        bridge.finished.connect(
            lambda active_worker=bridge: self._on_bridge_finished(active_worker)
        )
        self.bridge_worker = bridge
        self.host._build_eeg_worker(replace=True)
        if self.host._pipeline_worker is not None:
            self.host._pipeline_worker.set_continuous_recording(True)
        bridge.start()
        self.host._start_eeg_pipeline()
        self.host._notify("正在一键启动：蓝牙采集 → LSL obci_eeg1 → FBCCA…")
        self.host.update()
        return True

    def complete_after_frame(self, _frame: EEGFrame) -> None:
        """Authorize flight control only after a real frame crosses the LSL chain."""

        if not self.start_pending:
            return
        if not self.host.acquisition.outlet.active:
            self.start_pending = False
            self.open_when_ready = False
            self.chain_active = False
            self.host._notify("SSVEP启动失败：收到EEG数据时LSL发布器已停止")
            return
        self.outlet_started_here = True

        worker = self.host._pipeline_worker
        source = worker.source_name if worker is not None else ""
        message = self.host.actions.set_bci_control_enabled(True, source)
        if not self.host.actions.bci_control_enabled:
            self.start_pending = False
            self.open_when_ready = False
            self.chain_active = False
            self.host.acquisition.outlet.stop()
            self.host._notify(message)
            return

        open_window = self.open_when_ready
        self.start_pending = False
        self.open_when_ready = False
        self.chain_active = True
        self.host._eeg_status = "实时链路已就绪 · ExpandableEEG → LSL → FBCCA"
        self.host._notify("SSVEP链路已就绪：实时8通道EEG、LSL和FBCCA均已启动")
        if open_window:
            QTimer.singleShot(0, lambda: self.host._show_ssvep_window("control"))

    def stop_bridge(self) -> None:
        worker, self.bridge_worker = self.bridge_worker, None
        if worker is not None and worker.isRunning():
            worker.stop()
        self.host.acquisition.outlet.stop()

    def stop(self, message: str = "SSVEP实时链路已停止") -> None:
        worker = self.host._pipeline_worker
        source = worker.source_name if worker is not None else ""
        self.host.actions.set_bci_control_enabled(False, source)
        if worker is not None:
            worker.set_continuous_recording(False)
        self.reset_state()
        self.host._disconnect_eeg_pipeline(stop_csv_recording=True)
        self.host._eeg_status = message
        self.host._notify(message)

    def toggle(self) -> None:
        if self.start_pending or self.chain_active or self.host.actions.bci_control_enabled:
            self.stop("脑控授权、实时采集和LSL输出均已关闭")
            return
        self.start(open_control_when_ready=False)

    def reset_state(self) -> None:
        self.start_pending = False
        self.open_when_ready = False
        self.chain_active = False
        self.outlet_started_here = False
        self.host.acquisition.last_frame_monotonic = 0.0

    def handle_worker_finished(self) -> bool:
        """Close authorization after the shared EEG worker stops."""

        if not (self.start_pending or self.chain_active):
            return False
        self.host.actions.bci.disable()
        self.reset_state()
        self.stop_bridge()
        self.host._eeg_status = "实时EEG链路已断开，脑控授权已自动关闭"
        return True

    def stop_if_stale(self) -> bool:
        last_frame = self.host.acquisition.last_frame_monotonic
        if self.chain_active and last_frame > 0.0 and time.monotonic() - last_frame > 2.0:
            self.stop("实时EEG超过2秒无数据：采集、LSL和脑控授权已自动关闭")
            return True
        return False

    def _select_device(self, devices):
        preferred = str(
            SOLDIER_EEG_PORT or self.host.acquisition.selected_device
        ).strip().lower()
        selected = next(
            (item for item in devices if preferred and str(item.port).lower() == preferred),
            None,
        )
        if selected is None:
            matches = [
                item
                for item in devices
                if "expandableeeg"
                in " ".join(
                    (
                        str(getattr(item, "port", "")),
                        str(getattr(item, "description", "")),
                        str(getattr(item, "hardware_id", "")),
                    )
                ).lower()
            ]
            selected = matches[0] if len(matches) == 1 else None
        if selected is None and len(devices) == 1:
            selected = devices[0]
        if selected is None:
            ports = "、".join(str(item.port) for item in devices)
            raise RuntimeError(f"发现多个串口（{ports}），请设置 BDS_SOLDIER_EEG_PORT 指定采集盒")
        return selected

    def _reset_eeg_state(self) -> None:
        self.host._latest_recognition = None
        self.host._eeg_history = [deque(maxlen=1250) for _ in range(8)]
        self.host._eeg_channel_names = tuple(SSVEP_CHANNEL_NAMES)
        self.host._eeg_channel_units = ("uV",) * 8
        self.host._eeg_sample_rate = 0.0
        self.host._eeg_quality = {}
        self.host._eeg_health = {}
        self.host.acquisition.last_frame_monotonic = 0.0

    def _on_bridge_status(self, status: str) -> None:
        self.host._eeg_status = status
        self.host._eeg_events.appendleft(f"{datetime.now():%H:%M:%S}  {status}")
        self.host.update()

    def _on_bridge_error(self, message: str) -> None:
        self.host._eeg_events.appendleft(
            f"{datetime.now():%H:%M:%S}  ERROR  蓝牙→LSL：{message}"
        )
        self.host._notify(f"SSVEP实时链路失败：{message}")

    def _on_bridge_finished(self, worker: EEGLSLBridgeWorker) -> None:
        if self.bridge_worker is not worker:
            return
        self.bridge_worker = None
        if self.start_pending or self.chain_active:
            self.host.actions.bci.disable()
            self.reset_state()
            self.host._disconnect_eeg_pipeline(stop_csv_recording=True)
            self.host._eeg_status = "蓝牙采集或LSL输出已断开，脑控授权已关闭"
            self.host._notify(self.host._eeg_status)
        self.host.update()


__all__ = ["SoldierBCIController"]
