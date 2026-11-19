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
                              QComboBox, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QLabel)

from src.bci.config import SAMPLE_RATE_HZ, SSVEP_CHANNEL_NAMES
from src.services.subject_catalog import SubjectCatalog
from src.services.acquisition_identity import acquisition_directory
from src.views.automatic_ssvep_experiment_window import AutomaticSSVEPExperimentWindow
from src.views.hybrid_experiment_window import HybridExperimentWindow
from src.services.hybrid_experiment_protocol import MI_TASK_SETS
from src.services.collection_plans import REST_SCHEMES, collection_schemes, hybrid_collection_config
from src.bci.montage import HYBRID, LEGACY, MONTAGES
from src.services.acquisition_preflight import audit_hybrid_run
from src.services.external_recording import ExternalRecordingJournal


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
        self._marker_failure = False
        self.selected_subject_id = ""
        self.selected_subject_name = ""
        self._subject_root: Path | None = None
        self._run_context: dict[str, str] = {}
        self.external_journal = None

    @property
    def external_recording(self):
        return getattr(self.host.actions, "recording_mode", "local_eeg") == "external_bdf"

    def set_recording_mode(self, mode):
        if self.automatic_window is not None:
            self.host._notify("请先关闭实验窗口再切换记录方式")
            return
        if mode not in ("local_eeg", "external_bdf"):
            return
        self.host.actions.recording_mode = mode
        self.host._notify("独立呈现：无需本机 EEG；另一台电脑记录 BDF，本机保存实验日志" if self.external_recording else "本机 EEG：需要实时数据流和匹配的通道布局")
        self.host.update()

    def choose_subject(self, *, create: bool = False) -> None:
        if self.automatic_window is not None:
            self.host._notify("请先关闭当前实验窗口，再切换受试者")
            return
        recorder = self.host.experiment_recorder
        if recorder is None:
            self.host._notify("采集数据目录尚未就绪")
            return
        try:
            catalog = SubjectCatalog(recorder.root)
            parent = self.host if isinstance(self.host, QWidget) else None
            options = [(key, value["display_name"]) for key, value in catalog.subjects.items()
                       if key != "subject-other" and value.get("display_name") != "其他"]
            if create or not options:
                name, accepted = QInputDialog.getText(parent, "新增并确认采集受试者",
                    "输入受试者编号或名称；确认后本次采集将自动归入该受试者：",
                    text=catalog.next_default_name())
                if not accepted:
                    return
                subject_id = catalog.create_subject(name)
            else:
                labels = [f"{name}  ·  {key}" for key, name in options]
                current = next((i for i, (key, _) in enumerate(options)
                                if key == self.selected_subject_id), 0)
                choice, accepted = QInputDialog.getItem(parent, "确认采集受试者",
                    "选择本轮受试者；原始数据、SSVEP 与 MI 阶段均自动归入此人：",
                    labels, current, False)
                if not accepted:
                    return
                subject_id = options[labels.index(choice)][0]
            self.select_subject(subject_id)
        except (OSError, ValueError) as exc:
            self.host._notify(f"受试者选择失败：{exc}")

    def select_subject(self, subject_id: str) -> None:
        if self.automatic_window is not None:
            raise ValueError("请先关闭当前实验窗口，再切换受试者")
        recorder = self.host.experiment_recorder
        if recorder is None:
            raise ValueError("采集数据目录尚未就绪")
        catalog = SubjectCatalog(recorder.root)
        subject = catalog.subjects.get(subject_id)
        if not subject or subject.get("display_name") == "其他":
            raise ValueError("请选择具体受试者")
        root = Path(recorder.root).resolve()
        self.selected_subject_id = subject_id
        self.selected_subject_name = subject["display_name"]
        self._subject_root = root
        self.host._notify(f"已确认采集受试者：{self.selected_subject_name}（{subject_id}）")
        self.host.update()

    def _require_subject(self) -> bool:
        recorder = self.host.experiment_recorder
        if (recorder is None or not self.selected_subject_id
                or Path(recorder.root).resolve() != self._subject_root):
            self.host._notify("请先在数据采集页选择并确认本轮受试者")
            return False
        return True

    def _bind_run(self, window, paradigm: str, scheme: str) -> bool:
        try:
            catalog = SubjectCatalog(self.host.experiment_recorder.root)
            context = catalog.bind_acquisition(self.selected_subject_id, window.run_id,
                self._run_date(window.run_id), paradigm, scheme)
            acquisition_directory(self.host.experiment_recorder.raw_trial_dir, context).mkdir(parents=True, exist_ok=True)
            self.selected_subject_name = context["subject_name"]
            self._run_context = context
            self._marker_failure = False
            self.external_journal = None
            if self.external_recording:
                self.external_journal = ExternalRecordingJournal(
                    self.host.experiment_recorder.root, window.run_id, context, window.config)
            return True
        except (OSError, ValueError) as exc:
            window.deleteLater()
            self.host._notify(f"无法准备受试者采集目录：{exc}")
            return False

    @property
    def running(self) -> bool:
        return bool(self.automatic_window is not None and self.automatic_window.running)

    def select_collection_scheme(self, scheme: str) -> None:
        if self.running:
            self.host._notify("请先结束当前采集，再切换实验方案")
            return
        options = collection_schemes(self.host.actions.paradigm_type)
        selected = next((item for item in options if item.id == scheme), None)
        if selected is None:
            self.host._notify("当前范式不支持此采集方案")
            return
        self.host.actions.collection_schemes[self.host.actions.paradigm_type] = scheme
        self.host._notify(f"实验方案已切换：{selected.title}")

    def configure_collection(self) -> None:
        if self.running:
            self.host._notify("请先结束本次实验再修改采集计划")
            return
        if self.host.actions.paradigm_type == "SSVEP" and self.host.actions.collection_scheme == "formal":
            self.host._configure_automatic_ssvep_experiment()
        else:
            self.configure_hybrid()

    def start_collection(self) -> None:
        if self.host.actions.paradigm_type == "SSVEP" and self.host.actions.collection_scheme == "formal":
            self.start_automatic()
        else:
            self.start_hybrid(scheme=self.host.actions.collection_scheme)

    def start_automatic(self) -> None:
        if not self._require_subject():
            return
        worker = self.host._pipeline_worker
        if not self.external_recording:
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

        self.host.actions.bci_control_enabled = False
        if not self.external_recording:
            worker.set_paradigm_type("SSVEP")
        window = AutomaticSSVEPExperimentWindow(
            self.host.actions.automatic_experiment_config,
            parent=self.host if isinstance(self.host, QWidget) else None,
            external_recording=self.external_recording,
        )
        if not self._bind_run(window, "SSVEP", "formal"):
            return
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
        if self.running:
            self.host._notify("请先结束当前实验，再调整采集布局")
            return
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
        if montage.mi:
            if self.host.actions.paradigm_type != "MI":
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
        paradigm = self.host.actions.paradigm_type
        scheme = self.host.actions.collection_scheme
        serial = paradigm != "MI"
        rest_only = scheme in REST_SCHEMES
        pilot = scheme == "pilot"
        dialog = QDialog(self.host if isinstance(self.host, QWidget) else None)
        title = next(item.title for item in collection_schemes(paradigm) if item.id == scheme)
        dialog.setWindowTitle(f"{paradigm} · {title}")
        dialog.setMinimumWidth(480)
        layout = QFormLayout(dialog)
        tasks = QComboBox()
        for key, labels in MI_TASK_SETS.items():
            tasks.addItem(" / ".join(labels), key)
        tasks.setCurrentIndex(tasks.findData(config.task_set))
        if not rest_only:
            layout.addRow("MI 任务（与训练模型一致）", tasks)
        note = QLabel(
            "试跑固定为 2 组，不采专门静息；时长与任务设置和正式采集共用。"
            if pilot else "只运行所选的静息状态，默认 140 秒；到时提示音并自动结束，无需按空格。"
            if rest_only else "开始前短暂休息，随后自动运行任务；睁眼、闭眼静息请分别选择独立方案。"
        )
        note.setWordWrap(True)
        layout.addRow(note)
        fields = {}
        resting_fields = (("resting_open_seconds", "专门睁眼静息 / 秒"),) if scheme == "rest_open" else (
            (("resting_closed_seconds", "专门闭眼静息 / 秒"),) if scheme == "rest_closed" else ())
        for name, title in resting_fields:
            box = QSpinBox(); box.setRange(5, 240); box.setSingleStep(5); box.setValue(int(getattr(config, name)))
            fields[name] = box; layout.addRow(title, box)
        count_fields = []
        if not rest_only and not pilot:
            count_fields.append(("serial_pair_count", "正式采集总组数") if serial else ("repetitions", "每种 MI 动作次数"))
        for name, title in count_fields:
            box = QSpinBox()
            box.setRange(1, 600 if name == "serial_pair_count" else 50)
            box.setValue((config.serial_pair_count or replace(config, mode="serial").pair_count)
                         if name == "serial_pair_count" else getattr(config, name))
            fields[name] = box; layout.addRow(title, box)
        timing_fields = []
        if not rest_only:
            timing_fields.append(("baseline_seconds", "开始前休息 / 秒"))
            if serial:
                timing_fields.extend((("cue_seconds", "SSVEP 目标提示 / 秒"), ("ssvep_seconds", "SSVEP / 秒"),
                                      ("gap_seconds", "两阶段间隔 / 秒")))
            timing_fields.extend((("mi_cue_seconds", "MI 动作提示 / 秒"), ("mi_seconds", "MI 想象采集 / 秒"),
                                  ("rest_seconds", "休息 / 秒")))
        for name, title in timing_fields:
            box = QDoubleSpinBox(); box.setRange(2 if name in ("ssvep_seconds", "mi_seconds") else .5, 30)
            box.setSingleStep(.5); box.setValue(getattr(config, name)); fields[name] = box; layout.addRow(title, box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            try:
                values = {name: box.value() for name, box in fields.items()}
                if not rest_only:
                    values["task_set"] = tasks.currentData()
                updated = replace(config, **values).validate()
            except ValueError as exc:
                self.host._notify(str(exc))
                return
            self.host.actions.hybrid_experiment_config = updated
            self.host._notify(f"{paradigm} 采集计划已更新")

    def start_hybrid(self, mode=None, *, pilot=False, scheme=None):
        worker = self.host._pipeline_worker
        if self.automatic_window is not None:
            self.automatic_window.showFullScreen(); self.automatic_window.raise_(); return
        if not self._require_subject():
            return
        if not self.external_recording:
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
        paradigm = "MI" if mode == "mi" or self.host.actions.paradigm_type == "MI" else "SSVEP+MI混合"
        scheme = scheme or ("pilot" if pilot else "rest" if mode == "rest" else "formal")
        if scheme in REST_SCHEMES:
            paradigm = self.host.actions.paradigm_type
        config = hybrid_collection_config(
            self.host.actions.hybrid_experiment_config, paradigm, scheme,
        )
        self.host.actions.bci_control_enabled = False
        if not self.external_recording:
            worker.set_paradigm_type("SSVEP+MI混合")
        self._marker_failure = False
        window = HybridExperimentWindow(config, self.host if isinstance(self.host, QWidget) else None,
                                        external_recording=self.external_recording)
        if not self._bind_run(window, paradigm, scheme):
            return
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
        if window.running and worker is not None and not self.external_recording:
            worker.cancel_experiment_trial("dashboard_deactivated")
        window.abort_immediately("dashboard_deactivated")

    def notify_error(self, message: str) -> None:
        if self.external_recording:
            return
        if self.running and self.automatic_window is not None:
            if "没有可用 EEG 通道" in str(message):
                self.automatic_window.notify_quality_warning(str(message))
                return
            self.automatic_window.notify_trial_save_error(message)

    def notify_trial_saved(self, label: str, path: str) -> None:
        if self.external_recording:
            return
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
        if self.external_recording:
            return
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
        if self.external_recording and window is not None and isinstance(payload, dict):
            try:
                self.external_journal.capture({**payload, **self._run_context})
            except (OSError, ValueError, TypeError) as exc:
                self._external_log_error(exc)
            return
        if worker is None or window is None or not isinstance(payload, dict):
            return
        try:
            worker.capture_experiment_trial(
                str(payload.get("true_label", "")),
                float(payload.get("frequency_hz", 0.0) or 0.0),
                float(payload.get("marker_timestamp", time.time())),
                duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
                discard_seconds=float(payload.get("discard_seconds", 0.0) or 0.0),
                metadata={**payload, **self._run_context},
            )
        except Exception as exc:
            window.notify_trial_save_error(str(exc))
            self.host._notify(f"自动试次启动失败：{exc}")

    def _external_log_error(self, exc):
        if self._marker_failure:
            return
        self._marker_failure = True
        window = self.automatic_window
        if window is not None:
            window.external_log_failed = True
            if window.running:
                QTimer.singleShot(0, lambda: window.notify_trial_save_error(f"实验日志保存失败：{exc}"))
        self.host._notify(f"实验日志保存失败：{exc}")

    def _record_phase(self, name: str, timestamp: float, metadata: object) -> None:
        if self.external_recording:
            try:
                self.external_journal.phase(name, timestamp, dict(metadata or {}))
            except (OSError, ValueError, TypeError) as exc:
                self._external_log_error(exc)
            return
        worker = self.host._pipeline_worker
        if worker is None:
            return
        context = {**(metadata if isinstance(metadata, dict) else {}), **self._run_context}
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
        if self.external_recording:
            try:
                self.external_journal.finish(status, dict(summary or {}))
                self.host._notify(f"实验呈现{'完成' if status == 'completed' else '中止'}："
                    f"已记录 {self.external_journal.completed} 个完整阶段；"
                    f"未在本机保存 EEG。日志：{self.external_journal.path}；稍后导入 BDF 并确认时间对齐。")
            except (OSError, ValueError, TypeError) as exc:
                self.host._notify(f"实验汇总写入失败：{exc}；请保留已有日志")
            return
        if status == "aborted" and self.host._pipeline_worker is not None:
            self.host._pipeline_worker.cancel_experiment_trial("automatic_protocol_aborted")
        values = dict(summary) if isinstance(summary, dict) else {}
        if values.get("run_id") == self._run_context.get("run_id"):
            values.update(self._run_context)
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
                        if not audit["signal_quality_checks_passed"]:
                            assignment_message += "；试跑信号质量存在异常，详情见检查报告"
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
        """Refresh the analysis browser using the identity fixed before capture."""

        recorder = self.host.experiment_recorder
        run_id = str(values.get("run_id", "")).strip()
        saved_trials = int(values.get("saved_trials", 0) or 0)
        if recorder is None or not run_id or saved_trials <= 0:
            return ""
        try:
            catalog = SubjectCatalog(recorder.root)
        except (OSError, ValueError) as exc:
            return f"；受试者目录读取失败（{exc}，原始文件已保留）"
        assignment = catalog.assignment_for_run(run_id)
        if assignment is None:
            return "；缺少批次受试者归属，请在数据分析页核对"
        browser = getattr(self.host, "trial_analysis", None)
        if browser is not None:
            browser.load_project_data()
            browser.select_subject(assignment.subject_id)
            browser.select_date(assignment.acquisition_date)
            # Prefer a complete pair over the run's idle calibration/rest stages.
            records = [record for record in browser.records if record.experiment_id == run_id]
            if records:
                record = min(records, key=lambda row: (browser.paired_record(row) is None,
                    row.analysis_kind != "ssvep", row.trial_id))
                browser.select_record(browser.records.index(record))
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
        self._run_context = {}
        self.external_journal = None

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
