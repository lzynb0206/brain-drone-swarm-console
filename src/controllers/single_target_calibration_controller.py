"""Isolated raw-EEG recorder for the single-target calibration page."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

from src.bci.config import (
    PERSONAL_TEMPLATE_METADATA_PATH,
    PERSONAL_TEMPLATE_MODEL_PATH,
    SAMPLE_RATE_HZ,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
)
from src.bci.preprocessing import assess_signal_quality
from src.domain import EEGFrame
from src.ml.personal_template_model import train_personal_template_model
from src.services.single_target_calibration_protocol import SingleTargetCalibrationConfig
from src.views.single_target_calibration_window import SingleTargetCalibrationWindow


class SingleTargetHost(Protocol):
    acquisition: object
    experiment_recorder: object | None
    _pipeline_worker: object | None
    _eeg_channel_names: tuple[str, ...]
    _eeg_original_channel_names: tuple[str, ...]
    _eeg_channel_units: tuple[str, ...]
    _eeg_sample_rate: float
    _experiment_session_id: str
    intent_decoder: object
    actions: object
    user: object | None

    def _notify(self, message: str) -> None: ...
    def _on_eeg_status(self, status: str) -> None: ...
    def update(self) -> None: ...


class SingleTargetCalibrationController:
    """Capture directly from live frames; never calls the online decoder."""

    def __init__(self, host: SingleTargetHost) -> None:
        self.host = host
        self.config = SingleTargetCalibrationConfig()
        self.window: SingleTargetCalibrationWindow | None = None
        self.status = "尚未开始"
        self.progress = f"0/{self.config.trial_count}"
        self.latest_run_id = ""
        self.latest_run_directory = ""
        self.latest_accuracy: float | None = None
        self.latest_report: dict[str, object] = {}
        self._run_directory: Path | None = None
        self._manifest: list[dict[str, object]] = []
        self._active_payload: dict[str, object] | None = None
        self._active_values: list[np.ndarray] = []
        self._active_timestamps: list[np.ndarray] = []
        self._required_samples = 0
        self._saving = False
        self._mode = "isolated"

    @property
    def running(self) -> bool:
        return bool(self.window is not None and self.window.running)

    def start(self, mode: str = "isolated") -> None:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"isolated", "guided"}:
            raise ValueError("calibration mode must be isolated or guided")
        display_name = "六目标引导校准" if normalized_mode == "guided" else "单目标个人模板采集"
        worker = self.host._pipeline_worker
        if worker is None or not worker.isRunning():
            self.host._notify(f"{display_name}无法开始：请先在“采集”页启动实时 EEG 数据流")
            return
        last_frame = float(getattr(self.host.acquisition, "last_frame_monotonic", 0.0))
        if last_frame <= 0.0 or time.monotonic() - last_frame > 2.0:
            self.host._notify(f"{display_name}无法开始：最近2秒没有收到实时 EEG")
            return
        if tuple(self.host._eeg_channel_names) != tuple(SSVEP_CHANNEL_NAMES):
            self.host._notify(f"{display_name}无法开始：当前8通道顺序不是 O1/Oz/O2/PO3/POz/PO4/P3/P4")
            return
        if abs(float(self.host._eeg_sample_rate) - float(SAMPLE_RATE_HZ)) > 5.0:
            self.host._notify(f"{display_name}无法开始：采样率需要约250 Hz")
            return
        recorder = self.host.experiment_recorder
        if recorder is None:
            self.host._notify(f"{display_name}无法开始：实验记录器不可用")
            return
        if self.window is not None:
            self.window.showFullScreen()
            self.window.raise_()
            return
        window = SingleTargetCalibrationWindow(
            self.config,
            parent=self.host if isinstance(self.host, QWidget) else None,
            mode=normalized_mode,
        )
        self._mode = normalized_mode
        self._run_directory = recorder.root / "single_target_calibration" / window.run_id
        self._run_directory.mkdir(parents=True, exist_ok=False)
        self._manifest = []
        self.latest_run_id = window.run_id
        self.latest_run_directory = str(self._run_directory)
        self.latest_accuracy = None
        self.latest_report = {}
        self.progress = f"0/{self.config.trial_count}"
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.capture_requested.connect(self._begin_capture)
        window.status_changed.connect(self._set_status)
        window.run_finished.connect(self._finish)
        window.destroyed.connect(self._window_destroyed)
        self.window = window
        window.showFullScreen()
        window.raise_()
        window.activateWindow()
        window.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, window.start_run)

    def ingest_frame(self, frame: EEGFrame) -> None:
        if self._active_payload is None or self._saving:
            return
        values = np.asarray(frame.samples, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] != len(SSVEP_CHANNEL_NAMES):
            return
        sample_count = values.shape[1]
        timestamps = np.asarray(frame.sample_timestamps, dtype=np.float64)
        if timestamps.size != sample_count:
            fs = float(frame.sample_rate_hz or SAMPLE_RATE_HZ)
            end = float(frame.timestamp or time.time())
            timestamps = end - np.arange(sample_count - 1, -1, -1) / fs
        self._active_values.append(values.copy())
        self._active_timestamps.append(timestamps.copy())
        total = sum(item.shape[1] for item in self._active_values)
        if total >= self._required_samples:
            self._save_active(float(frame.sample_rate_hz or SAMPLE_RATE_HZ))

    def analyze_latest(self) -> None:
        if not self.latest_run_directory:
            self.host._notify("还没有单目标校准数据")
            return
        try:
            metadata = self._build_runtime_model()
        except Exception as exc:
            self.status = f"个人模板生成失败：{exc}"
            self.host._notify(self.status)
            self.host.update()
            return
        self.status = f"个人模板已生成：{metadata['trial_count']}段，待六目标在线测试"
        self.host._notify(self.status)
        self.host.update()

    def deactivate(self) -> None:
        self._active_payload = None
        self._active_values = []
        self._active_timestamps = []
        if self.window is not None:
            self.window.abort("工作台关闭")

    def handle_worker_finished(self) -> None:
        if self.running and self.window is not None:
            self.window.abort("实时 EEG 数据流已停止")

    def _begin_capture(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._active_payload = dict(payload)
        self._active_values = []
        self._active_timestamps = []
        fs = float(self.host._eeg_sample_rate or SAMPLE_RATE_HZ)
        self._required_samples = max(1, round(float(payload["duration_seconds"]) * fs))
        self._saving = False

    def _save_active(self, sample_rate_hz: float) -> None:
        if self._active_payload is None or self._run_directory is None or self._saving:
            return
        self._saving = True
        payload = dict(self._active_payload)
        values = np.concatenate(self._active_values, axis=1)[:, :self._required_samples]
        timestamps = np.concatenate(self._active_timestamps)[:self._required_samples]
        try:
            quality = dict(assess_signal_quality(values, sample_rate_hz))
        except Exception as exc:
            quality = {"status": "unknown", "reason": str(exc)}
        trial_index = int(payload["trial_index"])
        path = self._run_directory / f"trial-{trial_index:03d}-{payload['true_label']}.npz"
        metadata = {
            **payload,
            "sample_rate_hz": float(sample_rate_hz),
            "channels": list(self.host._eeg_channel_names),
            "original_channels": list(self.host._eeg_original_channel_names),
            "channel_units": list(self.host._eeg_channel_units),
            "signal_quality": quality,
            "window_start_timestamp": float(timestamps[0]) if timestamps.size else None,
            "window_end_timestamp": float(timestamps[-1]) if timestamps.size else None,
            "saved_unix": time.time(),
        }
        np.savez_compressed(
            path,
            eeg=values,
            sample_timestamps=timestamps,
            fs=np.asarray(sample_rate_hz, dtype=np.float32),
            true_label=np.asarray(str(payload["true_label"])),
            frequency_hz=np.asarray(float(payload["frequency_hz"]), dtype=np.float32),
            discard_seconds=np.asarray(float(payload["discard_seconds"]), dtype=np.float32),
            channel_names=np.asarray(self.host._eeg_channel_names),
            stimulus_layout=np.asarray(str(payload["stimulus_layout"])),
            calibration_condition=np.asarray(str(payload["calibration_condition"])),
            visible_target_count=np.asarray(int(payload["visible_target_count"]), dtype=np.int32),
            signal_quality=np.asarray(json.dumps(quality, ensure_ascii=False, sort_keys=True)),
            metadata=np.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
        )
        self._manifest.append({**metadata, "path": path.name})
        self._write_manifest("running")
        self.progress = f"{len(self._manifest)}/{self.config.trial_count}"
        window = self.window
        self._active_payload = None
        self._active_values = []
        self._active_timestamps = []
        self._saving = False
        if window is not None:
            window.notify_trial_saved(
                str(payload["true_label"]), str(path), str(quality.get("status", "unknown")),
            )
        self.host.update()

    def _finish(self, status: str, summary: object) -> None:
        self._active_payload = None
        self._active_values = []
        self._active_timestamps = []
        self._write_manifest(status, summary if isinstance(summary, dict) else {})
        if status == "completed":
            try:
                metadata = self._build_runtime_model()
                consistency = float(metadata["leave_one_trial_consistency"]) * 100.0
                channels = "/".join(str(value) for value in metadata["channel_names"])
                self.status = (
                    f"单目标采集完成；个人模板+FBCCA已启用（内部一致性 {consistency:.1f}%；"
                    f"稳定通道 {channels}），待六目标独立测试"
                )
            except Exception as exc:
                self.status = f"采集完成，但拒绝启用坏模板：{exc}"
        else:
            name = "六目标引导校准" if self._mode == "guided" else "单目标个人模板采集"
            self.status = f"{name}已中止：{dict(summary).get('reason', '') if isinstance(summary, dict) else ''}"
        self.host._notify(self.status)
        self.host.update()

    def _build_runtime_model(self) -> dict[str, object]:
        metadata = train_personal_template_model(
            self.latest_run_directory,
            PERSONAL_TEMPLATE_MODEL_PATH,
            PERSONAL_TEMPLATE_METADATA_PATH,
        )
        reloader = getattr(self.host.intent_decoder, "reload", None)
        if callable(reloader):
            reloader("个人模板-SSVEP")
        selector = getattr(self.host.intent_decoder, "select", None)
        if callable(selector):
            selector("个人模板-SSVEP")
        self.host.actions.algorithm = "个人模板-SSVEP"
        self.host.actions.default_algorithm = "个人模板-SSVEP"
        self.host.actions.loaded_algorithms.add("个人模板-SSVEP")
        self.latest_accuracy = None
        self.latest_report = {}
        return metadata

    def _write_manifest(self, status: str, summary: dict[str, object] | None = None) -> None:
        if self._run_directory is None:
            return
        payload = {
            "run_id": self.latest_run_id,
            "status": str(status),
            "layout": SSVEP_STIMULUS_LAYOUT,
            "calibration_mode": self._mode,
            "config": {
                "repetitions_per_command": self.config.repetitions_per_command,
                "cue_seconds": self.config.cue_seconds,
                "stimulus_seconds": self.config.stimulus_seconds,
                "discard_seconds": self.config.discard_seconds,
                "rest_seconds": self.config.rest_seconds,
                "random_seed": self.config.random_seed,
            },
            "trials": list(self._manifest),
            "summary": dict(summary or {}),
        }
        (self._run_directory / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _set_status(self, status: str) -> None:
        self.status = str(status)
        self.host._on_eeg_status(self.status)
        self.host.update()

    def _window_destroyed(self, *_args) -> None:
        self.window = None


__all__ = ["SingleTargetCalibrationController"]
