"""Automatic SSVEP experiment lifecycle and worker coordination."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QInputDialog, QMessageBox, QWidget, QDialog, QFormLayout,
                              QComboBox, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QCheckBox, QLabel)

from src.bci.config import SAMPLE_RATE_HZ, SSVEP_CHANNEL_NAMES
from src.services.subject_catalog import SubjectCatalog
from src.views.automatic_ssvep_experiment_window import AutomaticSSVEPExperimentWindow
from src.views.hybrid_experiment_window import HybridExperimentWindow
from src.services.hybrid_experiment_protocol import MI_TASK_SETS
from src.bci.montage import HYBRID, LEGACY, MONTAGES
from src.views.acquisition_preflight_dialog import AcquisitionPreflightDialog
from src.services.acquisition_preflight import audit_hybrid_run


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
        self.preflight = None
        self._preflight_worker = None
        self._preflight_time = 0.
        self._marker_failure = False
        self.pilot_audit = None

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

    def configure_montage(self, montage_id):
        worker = self.host._pipeline_worker
        if worker is not None and worker.isRunning():
            self.host._notify("请先停止采集数据流，按接线图调整电极后再应用布局")
            return
        montage = MONTAGES[montage_id]
        configure = getattr(self.host.acquisition.stream, "configure_montage", None)
        if not callable(configure):
            self.host._notify("当前数据源不支持切换实时通道布局")
            return
        try:
            configure(montage.names)
        except (RuntimeError, ValueError) as exc:
            self.host._notify(str(exc))
            return
        self.host.actions.montage_id = montage.id
        self.preflight = None
        if montage.mi:
            self.host.actions.paradigm_type = "SSVEP+MI混合"
            self.host.actions.bci_control_enabled = False
        else:
            self.host.actions.paradigm_type = "SSVEP"
        if worker is not None:
            worker.set_paradigm_type(self.host.actions.paradigm_type)
        self.host._eeg_channel_names = montage.names
        self.host._notify("已应用采集接线：" + "，".join(f"CH{i+1}={n}" for i,n in enumerate(montage.names)))

    def configure_hybrid(self):
        if self.running:
            self.host._notify("请先结束本次实验再修改采集计划")
            return
        config = self.host.actions.hybrid_experiment_config
        dialog = QDialog(self.host if isinstance(self.host, QWidget) else None)
        dialog.setWindowTitle("MI / 串行配对采集计划")
        layout = QFormLayout(dialog)
        tasks = QComboBox()
        for key, labels in MI_TASK_SETS.items():
            tasks.addItem(" / ".join(labels), key)
        tasks.setCurrentIndex(tasks.findData(config.task_set))
        layout.addRow("MI 任务（与训练模型一致）", tasks)
        include_rest = QCheckBox("本轮先采专门静息（睁眼＋闭眼）")
        include_rest.setChecked(config.include_dedicated_rest)
        layout.addRow(include_rest)
        note = QLabel("专门静息合计 3–5 分钟；闭眼结束后按空格确认睁眼。\n取消勾选时本轮仅有 5 秒短基线，不会标记为完成专门静息。")
        note.setWordWrap(True)
        layout.addRow(note)
        fields = {}
        for name, title in (("resting_open_seconds", "专门睁眼静息 / 秒"), ("resting_closed_seconds", "专门闭眼静息 / 秒")):
            box = QSpinBox(); box.setRange(60, 240); box.setSingleStep(30); box.setValue(int(getattr(config, name)))
            fields[name] = box; layout.addRow(title, box)
        for name, title in (("repetitions", "每种目标×动作组合次数"), ("idle_trials", "起始空闲窗口数")):
            box = QSpinBox(); box.setRange(1, 30); box.setValue(getattr(config, name))
            fields[name] = box; layout.addRow(title, box)
        for name, title in (("cue_seconds", "提示 / 秒"), ("ssvep_seconds", "SSVEP / 秒"),
                            ("gap_seconds", "两阶段间隔 / 秒"), ("mi_seconds", "MI / 秒"), ("rest_seconds", "休息 / 秒")):
            box = QDoubleSpinBox(); box.setRange(2 if name in ("ssvep_seconds", "mi_seconds") else .5, 30)
            box.setSingleStep(.5); box.setValue(getattr(config, name)); fields[name] = box; layout.addRow(title, box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            try:
                updated = replace(config, task_set=tasks.currentData(), include_dedicated_rest=include_rest.isChecked(),
                    **{name: box.value() for name,box in fields.items()}).validate()
            except ValueError as exc:
                self.host._notify(str(exc))
                return
            self.host.actions.hybrid_experiment_config = updated
            self.host._notify("MI / 串行采集计划已更新")

    def run_preflight(self):
        if self.running:
            self.host._notify("请结束当前实验后再运行试采检查")
            return
        worker = self.host._pipeline_worker
        if worker is None or not worker.isRunning() or self.host.experiment_recorder is None:
            self.host._notify("先应用候选 5+3 布局并启动实时数据流，再运行试采检查")
            return
        if worker.source_name.strip().lower().startswith(("csv", "mock", "demo")):
            self.host._notify("正式采集检查需要真实硬件或 LSL，回放／模拟不能通过")
            return
        if self.host.actions.montage_id != HYBRID.id or tuple(self.host._eeg_channel_names) != HYBRID.names:
            self.host._notify("先停流并应用候选布局：" + ", ".join(HYBRID.names))
            return
        self.preflight = None
        self.host.actions.bci_control_enabled = False
        worker.set_paradigm_type("SSVEP+MI混合")
        dialog = AcquisitionPreflightDialog(worker, self.host.experiment_recorder.root,
                                            self.host if isinstance(self.host, QWidget) else None)
        dialog.exec()
        if dialog.probe_result is not None:
            self.preflight = dialog.probe_result
            self._preflight_worker, self._preflight_time = worker, time.monotonic()
            self.host._notify("试采检查通过；物理显示延迟仍需独立实测" if self.preflight["formal_ready"] else "试采检查未通过，请查看检查报告并修正")

    def preflight_ready(self):
        return bool(self.preflight and self.preflight.get("formal_ready")
                    and self._preflight_worker is self.host._pipeline_worker
                    and time.monotonic() - self._preflight_time <= 900
                    and self.host.actions.montage_id == self.preflight.get("montage_id")
                    and tuple(self.host._eeg_channel_names) == tuple(self.preflight.get("channel_names", ()))
                    and tuple(self.host._eeg_original_channel_names or self.host._eeg_channel_names)
                        == tuple(self.preflight.get("original_channel_names") or ()))

    def pilot_signature(self, config):
        return (config.task_set, config.cue_seconds, config.ssvep_seconds, config.gap_seconds,
                config.mi_seconds, config.rest_seconds, config.baseline_seconds, self.host.actions.montage_id,
                (self.preflight or {}).get("id"))

    def start_hybrid(self, mode=None, *, pilot=False):
        worker = self.host._pipeline_worker
        if self.automatic_window is not None:
            self.automatic_window.showFullScreen(); self.automatic_window.raise_(); return
        if worker is None or not worker.isRunning() or time.monotonic() - self.host.acquisition.last_frame_monotonic > 2:
            self.host._notify("请应用 5+3 布局，然后启动实时 EEG 数据流")
            return
        if worker.source_name.strip().lower().startswith(("csv", "mock", "demo")):
            self.host._notify("配对采集需要实时硬件或 LSL EEG；回放数据不能充当新采集")
            return
        if self.host.actions.montage_id != HYBRID.id or tuple(self.host._eeg_channel_names) != HYBRID.names:
            self.host._notify("需要候选 5+3 实际接线：O1 Oz O2 P3 P4 C3 Cz C4；请停流后应用布局")
            return
        if abs(float(self.host._eeg_sample_rate) - SAMPLE_RATE_HZ) > 5:
            self.host._notify("当前采样率与 250 Hz 配对采集配置不符")
            return
        if not self.preflight_ready():
            self.host._notify("本次数据流尚未通过试采检查，或检查已超过 15 分钟；请先点“硬件／时间检查”")
            return
        config = replace(self.host.actions.hybrid_experiment_config,
                         mode="serial" if pilot else mode or ("mi" if self.host.actions.paradigm_type == "MI" else "serial"))
        if pilot:
            config = replace(config, purpose="pilot", repetitions=1, idle_trials=2, include_dedicated_rest=False)
            self.pilot_audit = None
        elif config.mode != "rest" and (not self.pilot_audit or not self.pilot_audit.get("passed")
                or self.pilot_audit.get("signature") != self.pilot_signature(config)):
            self.host._notify("先运行“流程试跑（2组）”并通过软件时序检查，再开始正式任务；试跑数据不进入正式分析")
            return
        self.host.actions.bci_control_enabled = False
        worker.set_paradigm_type("SSVEP+MI混合")
        self._marker_failure = False
        window = HybridExperimentWindow(config, self.host if isinstance(self.host, QWidget) else None,
                                        preflight_id=self.preflight["id"])
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.capture_requested.connect(self._capture_trial)
        window.phase_marker.connect(self._record_phase)
        window.status_changed.connect(self.host._on_eeg_status)
        window.experiment_finished.connect(self._finish)
        window.destroyed.connect(self._window_destroyed)
        self.automatic_window = window
        window.showFullScreen(); window.raise_(); window.activateWindow(); window.setFocus(Qt.OtherFocusReason)
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
            if "没有可用 EEG 通道" in str(message):
                self.automatic_window.notify_quality_warning(str(message))
                return
            self.automatic_window.notify_trial_save_error(message)

    def notify_trial_saved(self, label: str, path: str) -> None:
        if self.automatic_window is not None:
            if isinstance(self.automatic_window, HybridExperimentWindow):
                try:
                    evidence = self._ssvep_evidence(path)
                except (OSError, ValueError, KeyError) as exc:
                    self.automatic_window.notify_trial_save_error(f"配对证据读取失败：{exc}")
                    return
                self.automatic_window.notify_trial_saved(label, path, self._quality_status(path), evidence)
            else:
                self.automatic_window.notify_trial_saved(label, path, self._quality_status(path))

    def _ssvep_evidence(self, path):
        trial_path = Path(path)
        if not trial_path.is_absolute():
            trial_path = self.host.experiment_recorder.root / trial_path
        with np.load(trial_path, allow_pickle=False) as p:
            quality = json.loads(str(p["signal_quality"].item()))
            metadata = json.loads(str(p["metadata"].item()))
        valid = bool(quality.get("p_ssvep_valid", False))
        return {"p_ssvep": quality.get("p_ssvep") if valid else None,
                "p_ssvep_valid": valid, "p_ssvep_is_probability": False,
                "ssvep_prediction": metadata.get("candidate_label"),
                "ssvep_idle_windows": quality.get("bn_idle_windows", 0)}

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
            + "，".join(f"CH{i+1}={name}" for i,name in enumerate(actual_channels))
            + "。\n\n确认无误后才开始本次科研采集。",
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
            window = self.automatic_window
            if isinstance(window, HybridExperimentWindow) and window.running and not self._marker_failure:
                self._marker_failure = True
                QTimer.singleShot(0, lambda: window.notify_trial_save_error("时间标记保存失败，已中止本次采集"))

    def _finish(self, status: str, summary: object) -> None:
        if status == "aborted" and self.host._pipeline_worker is not None:
            self.host._pipeline_worker.cancel_experiment_trial("automatic_protocol_aborted")
        values = summary if isinstance(summary, dict) else {}
        assignment_message = self._label_completed_run(values)
        if "completed_pairs" in values:
            recorder = self.host.experiment_recorder
            if recorder is not None:
                try:
                    directory = recorder.root / "hybrid_runs"
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / f"{values['run_id']}.json").write_text(
                        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    audit = audit_hybrid_run(recorder.root, values)
                    (directory / f"{values['run_id']}-timing-audit.json").write_text(
                        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    assignment_message += "；软件时序检查" + ("通过" if audit["software_timing_checks_passed"] else "有异常，请查看 timing-audit 报告")
                    if values.get("config", {}).get("purpose") == "pilot":
                        from src.services.hybrid_experiment_protocol import HybridExperimentConfig
                        self.pilot_audit = {"passed": audit["run_completed"] and audit["software_timing_checks_passed"] and audit["signal_quality_checks_passed"],
                            "signature": self.pilot_signature(HybridExperimentConfig(**values["config"])),
                            "run_id": values["run_id"]}
                        if not audit["signal_quality_checks_passed"]:
                            assignment_message += "；试跑信号质量未通过，请检查电极后重试"
                except OSError as exc:
                    assignment_message += f"；汇总保存失败：{exc}（原始试次文件仍保留）"
            self.host._notify(f"配对采集{'完成' if status == 'completed' else '中止'}："
                f"已保存 {values.get('saved_trials', 0)} 段，完成 {values['completed_pairs']}/"
                f"{values.get('planned_pairs', 0)} 组；"
                + ("试跑数据不计入正式分析" if values.get("config", {}).get("purpose") == "pilot" else "低置信度试次仍保留")
                + assignment_message)
            return
        self.host._notify(
            f"自动采集{'完成' if status == 'completed' else '中止'}："
            f"已保存 {values.get('saved_trials', 0)}，"
            f"有效 {values.get('valid_trials', 0)}，"
            f"剔除 {values.get('rejected_trials', 0)}"
            f"{assignment_message}"
        )

    def _label_completed_run(self, values: dict) -> str:
        """Ask once at run end and persist protected participant metadata."""

        recorder = self.host.experiment_recorder
        run_id = str(values.get("run_id", "")).strip()
        saved_trials = int(values.get("saved_trials", 0) or 0)
        if recorder is None or not run_id or saved_trials <= 0:
            return ""
        try:
            catalog = SubjectCatalog(recorder.root)
        except (OSError, ValueError) as exc:
            return f"；受试者标签未保存（{exc}）"
        default_name = catalog.next_default_name()
        existing = catalog.assignment_for_run(run_id)
        if existing is not None:
            default_name = existing.display_name
        parent = self.automatic_window
        name, accepted = QInputDialog.getText(
            parent,
            "保存采集数据标签",
            "一级标签：受试者姓名或编号\n"
            "二级标签将按本次采集日期自动生成；输入已有姓名会归入同一受试者：",
            text=default_name,
        )
        chosen_name = str(name).strip() if accepted else default_name
        acquisition_date = self._run_date(run_id)
        try:
            assignment = catalog.assign_run(
                run_id,
                acquisition_date,
                chosen_name or default_name,
                assignment_source="acquisition-finish-dialog",
            )
        except (OSError, ValueError) as exc:
            return f"；受试者标签未保存（{exc}）"
        browser = getattr(self.host, "trial_analysis", None)
        if browser is not None and getattr(browser, "source_path", None) is not None:
            browser.load_project_data()
        return (
            f"；已归档到 {assignment.display_name} / "
            f"{assignment.acquisition_date}"
        )

    @staticmethod
    def _run_date(run_id: str) -> str:
        try:
            return datetime.strptime(run_id.split("-", 3)[1], "%Y%m%d").strftime(
                "%Y-%m-%d"
            )
        except (IndexError, ValueError):
            return datetime.now().strftime("%Y-%m-%d")

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
