"""Automatic SSVEP experiment lifecycle and worker coordination."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from src.bci.config import SAMPLE_RATE_HZ, SSVEP_CHANNEL_NAMES
from src.views.automatic_ssvep_experiment_window import AutomaticSSVEPExperimentWindow


class ExperimentHost(Protocol):
    actions: object
    acquisition: object
    experiment_recorder: object | None
    _pipeline_worker: object | None
    _eeg_channel_names: tuple[str, ...]
    _eeg_original_channel_names: tuple[str, ...]
    _eeg_sample_rate: float
    _eeg_events: object

    def _notify(self, message: str) -> None: ...
    def _on_eeg_status(self, status: str) -> None: ...


class ExperimentController:
    """Own the automatic experiment window and its capture protocol."""

    def __init__(self, host: ExperimentHost) -> None:
        self.host = host
        self.automatic_window: AutomaticSSVEPExperimentWindow | None = None

    @property
    def running(self) -> bool:
        return bool(self.automatic_window is not None and self.automatic_window.running)

    def start_automatic(self) -> None:
        worker = self.host._pipeline_worker
        if worker is None or not worker.isRunning():
            self.host._notify("自动采集无法开始：请先在“采集”页启动实时 EEG 数据流")
            return
        last_frame = self.host.acquisition.last_frame_monotonic
        if last_frame <= 0.0 or time.monotonic() - last_frame > 2.0:
            self.host._notify("自动采集无法开始：最近 2 秒没有收到实时 EEG，请检查电极与数据流")
            return
        source = worker.source_name.strip()
        if source.lower().startswith(("csv", "mock", "demo")):
            self.host._notify("自动采集拒绝回放/模拟源：请选择实时硬件或 LSL EEG")
            return
        actual_channels = tuple(str(value) for value in self.host._eeg_channel_names)
        if actual_channels != tuple(SSVEP_CHANNEL_NAMES):
            self.host._notify(
                "自动采集无法开始：通道顺序必须为 "
                f"{', '.join(SSVEP_CHANNEL_NAMES)}；实际为 "
                f"{', '.join(actual_channels) or '未知'}"
            )
            return
        actual_rate = float(self.host._eeg_sample_rate or 0.0)
        if abs(actual_rate - float(SAMPLE_RATE_HZ)) > 5.0:
            self.host._notify(
                f"自动采集无法开始：需要约 {SAMPLE_RATE_HZ:g} Hz 实时 EEG；"
                f"实际为 {actual_rate:g} Hz"
            )
            return
        if not self._confirm_generic_channel_mapping(actual_channels):
            return
        if self.automatic_window is not None:
            self.automatic_window.showFullScreen()
            self.automatic_window.raise_()
            self.automatic_window.activateWindow()
            return

        worker.set_paradigm_type("SSVEP")
        window = AutomaticSSVEPExperimentWindow(
            self.host.actions.automatic_experiment_config,
            parent=self.host if isinstance(self.host, QWidget) else None,
        )
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.capture_requested.connect(self._capture_trial)
        window.phase_marker.connect(self._record_phase)
        window.status_changed.connect(self.host._on_eeg_status)
        window.experiment_finished.connect(self._finish)
        window.destroyed.connect(self._window_destroyed)
        self.automatic_window = window
        window.showFullScreen()
        window.raise_()
        window.activateWindow()
        window.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, window.start_experiment)

    def deactivate(self) -> None:
        window, self.automatic_window = self.automatic_window, None
        if window is None:
            return
        worker = self.host._pipeline_worker
        if window.running and worker is not None:
            worker.cancel_experiment_trial("dashboard_deactivated")
        window.abort_immediately("dashboard_deactivated")

    def notify_error(self, message: str) -> None:
        if self.running and self.automatic_window is not None:
            self.automatic_window.notify_trial_save_error(message)

    def notify_trial_saved(self, label: str, path: str) -> None:
        if self.automatic_window is not None:
            self.automatic_window.notify_trial_saved(label, path, self._quality_status(path))

    def handle_worker_finished(self) -> None:
        if self.running and self.automatic_window is not None:
            self.automatic_window.notify_trial_save_error("实时 EEG 采集线程已停止")

    def _confirm_generic_channel_mapping(self, actual_channels: tuple[str, ...]) -> bool:
        original_channels = tuple(
            str(value) for value in self.host._eeg_original_channel_names
        )
        if not original_channels or original_channels == actual_channels:
            return True
        answer = QMessageBox.question(
            self.host if isinstance(self.host, QWidget) else None,
            "确认通用通道与电极接线",
            "当前 LSL 流只提供通用标签："
            f"{', '.join(original_channels)}。软件只能按位置映射，无法从信号判断真实电极。\n\n"
            "请确认物理接线依次为：\n"
            "CH1=O1，CH2=Oz，CH3=O2，CH4=PO3，CH5=POz，CH6=PO4，"
            "CH7=P3，CH8=P4。\n\n确认无误后才开始本次科研采集。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return True
        self.host._notify("自动采集已取消：尚未确认通用通道对应的真实电极位置")
        return False

    def _capture_trial(self, payload: object) -> None:
        worker = self.host._pipeline_worker
        window = self.automatic_window
        if worker is None or window is None or not isinstance(payload, dict):
            return
        try:
            worker.capture_experiment_trial(
                str(payload.get("true_label", "")),
                float(payload.get("frequency_hz", 0.0) or 0.0),
                float(payload.get("marker_timestamp", time.time())),
                duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
                discard_seconds=float(payload.get("discard_seconds", 0.0) or 0.0),
                metadata=payload,
            )
        except Exception as exc:
            window.notify_trial_save_error(str(exc))
            self.host._notify(f"自动试次启动失败：{exc}")

    def _record_phase(self, name: str, timestamp: float, metadata: object) -> None:
        worker = self.host._pipeline_worker
        if worker is None:
            return
        context = metadata if isinstance(metadata, dict) else {}
        phase = str(name).removeprefix("auto_ssvep_")
        try:
            if phase in {"cue", "stimulus", "analysis", "rest"}:
                worker.record_trial_phase(phase, timestamp, context, timestamp_domain="auto")
            else:
                worker.record_experiment_marker(
                    str(name), timestamp, context, timestamp_domain="auto"
                )
        except Exception as exc:
            self.host._eeg_events.appendleft(
                f"{datetime.now():%H:%M:%S}  ERROR  实验标记写入失败：{exc}"
            )

    def _finish(self, status: str, summary: object) -> None:
        if status == "aborted" and self.host._pipeline_worker is not None:
            self.host._pipeline_worker.cancel_experiment_trial("automatic_protocol_aborted")
        values = summary if isinstance(summary, dict) else {}
        self.host._notify(
            f"自动采集{'完成' if status == 'completed' else '中止'}："
            f"已保存 {values.get('saved_trials', 0)}，"
            f"有效 {values.get('valid_trials', 0)}，"
            f"剔除 {values.get('rejected_trials', 0)}"
        )

    def _window_destroyed(self, *_args) -> None:
        self.automatic_window = None

    def _quality_status(self, path: str) -> str:
        recorder = self.host.experiment_recorder
        if not path or recorder is None:
            return "unknown"
        trial_path = Path(path)
        if not trial_path.is_absolute():
            trial_path = recorder.root / trial_path
        try:
            with np.load(trial_path, allow_pickle=False) as payload:
                raw = str(np.asarray(payload["signal_quality"]).item())
            quality = json.loads(raw)
            return (
                str(quality.get("status", "unknown"))
                if isinstance(quality, dict)
                else "unknown"
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return "unknown"


__all__ = ["ExperimentController"]
