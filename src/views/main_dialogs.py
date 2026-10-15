"""Dialogs and modal interactions used by the main dashboard."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.bci.config import (
    COMMANDS,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.services.ssvep_experiment_protocol import AutomaticExperimentConfig
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


class MainDialogsMixin:
    """Own the dashboard's confirmation and configuration dialogs."""

    def _configure_automatic_ssvep_experiment(self) -> None:
        """Edit the visible, reproducible acquisition protocol."""

        current = self.actions.automatic_experiment_config
        dialog = QDialog(self)
        dialog.setWindowTitle("SSVEP 自动随机采集配置")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumWidth(470)
        outer = QVBoxLayout(dialog)
        description = QLabel(
            "每个重复块都包含一次空闲和六个动作，块内按随机种子打乱。\n"
            "真实标签来自实验计划；不会把 FBCCA/Transformer 预测当作标签。",
            dialog,
        )
        description.setWordWrap(True)
        outer.addWidget(description)
        form = QFormLayout()

        repetitions = QSpinBox(dialog)
        repetitions.setRange(1, 200)
        repetitions.setValue(current.repetitions_per_label)
        repetitions.setSuffix(" 次/类")
        form.addRow("每类次数", repetitions)

        def seconds_box(value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
            box = QDoubleSpinBox(dialog)
            box.setRange(minimum, maximum)
            box.setDecimals(2)
            box.setSingleStep(0.1)
            box.setValue(float(value))
            box.setSuffix(" s")
            return box

        cue = seconds_box(current.cue_seconds, 0.1, 30.0)
        stimulus = seconds_box(current.stimulus_seconds, 0.5, 60.0)
        discard = seconds_box(current.discard_seconds, 0.0, 59.5)
        rest = seconds_box(current.rest_seconds, 0.1, 60.0)
        form.addRow("提示时长", cue)
        form.addRow("刺激/原始采集时长", stimulus)
        form.addRow("分析时丢弃起始段", discard)
        form.addRow("试次间休息", rest)

        seed = QSpinBox(dialog)
        seed.setRange(0, 2_147_483_647)
        seed.setValue(int(current.random_seed))
        form.addRow("随机种子", seed)
        outer.addLayout(form)
        total = QLabel(dialog)
        outer.addWidget(total)

        def refresh_total() -> None:
            available = max(0.0, stimulus.value() - discard.value())
            valid = available >= WINDOW_SECONDS
            total.setText(
                f"总试次：{repetitions.value() * len(current.labels)}  "
                f"｜ 每试次可分析：{available:.2f} s"
                + ("" if valid else f"  ｜ 当前模型至少需要 {WINDOW_SECONDS:g} s")
            )

        for widget in (repetitions, stimulus, discard):
            widget.valueChanged.connect(lambda _value, refresh=refresh_total: refresh())
        refresh_total()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        self._release_mouse_capture_for_dialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = AutomaticExperimentConfig(
            repetitions_per_label=repetitions.value(),
            cue_seconds=cue.value(),
            stimulus_seconds=stimulus.value(),
            discard_seconds=discard.value(),
            rest_seconds=rest.value(),
            random_seed=seed.value(),
            labels=tuple(current.labels),
        )
        try:
            candidate.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "配置无效", str(exc))
            return
        self.actions.automatic_experiment_config = candidate
        self._notify(
            f"自动采集配置已更新：{candidate.trial_count} 试次，seed={candidate.random_seed}"
        )

    def _show_openbci_guide(self) -> None:
        QMessageBox.information(
            self,
            "采集 → LSL收集 → CSV实验",
            "1. 先在系统蓝牙设置中配对 ExpandableEEG，确认生成 COM 或 /dev/cu.* 串口。\n"
            "2. 关闭其他占用该串口的 OpenBCI GUI，再在“采集”页选择设备并启动数据流。\n"
            "3. 检查采样率、通道数和电极标签；MI 建议重点核对 C3/Cz/C4。\n"
            "4. 需要实时转发时，在采集页 Networking 启动 LSL Stream。\n"
            "5. 在“LSL收集”页刷新并选择流，开始接收、显示并保存 CSV。\n"
            "6. 在“实验”页导入 CSV 做回放和算法复盘。\n\n"
            "macOS 检测到 ExpandableEEGBT 时会自动绕过失效的 /dev/cu 伪串口，"
            "直连 ESP32SPP 的 RFCOMM channel 1。若仍提示0字节，再用Type-C数据线和 "
            "scripts/expandable_eeg_config.py 确认设备处于Bluetooth模式。\n\n"
            "说明：LSL 负责实时传输 EEG 和时间戳；FBCCA 只用于 SSVEP。"
            "MI 模式当前只采集和标注，不生成飞行指令。",
        )

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
        self._show_ssvep_window("experiment")

    def _open_ssvep_control(self) -> None:
        if not self.soldier_bci.live_chain_ready() or not self.actions.bci_control_enabled:
            self.soldier_bci.start(open_control_when_ready=True)
            return
        self._show_ssvep_window("control")

    def _show_ssvep_window(self, mode: str) -> None:
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            self._notify("请先确保 EEG 数据源已经连接")
            return
        if self._stimulus_window is not None and self._stimulus_worker is not self._pipeline_worker:
            self._stimulus_window.close()
            self._stimulus_window.deleteLater()
            self._stimulus_window = None
            self._stimulus_worker = None
        if self._stimulus_window is None:
            self._stimulus_window = SSVEPStimulusWindow(parent=self)
            self._stimulus_worker = self._pipeline_worker
            self._stimulus_window.status_changed.connect(self._on_eeg_status)
            self._stimulus_window.trial_started.connect(self._pipeline_worker.capture_stimulus_trial)
            self._stimulus_window.event_marker.connect(self._pipeline_worker.record_stimulus_marker)
            self._stimulus_window.emergency_requested.connect(self._handle_ssvep_emergency)
            self._stimulus_window.control_started.connect(self._on_ssvep_control_started)
            self._stimulus_window.control_stopped.connect(self._on_ssvep_control_stopped)
            self._stimulus_window.fresh_window_requested.connect(
                self._on_ssvep_fresh_window_requested
            )
            self._stimulus_window.trial_stopped.connect(
                self._on_ssvep_window_stopped
            )
        self._stimulus_window.set_control_profile(
            move_distance_m=self.actions.swarm_move_distance_m(),
            command_cooldown_seconds=self.actions.bci.command_cooldown_seconds,
        )
        self._stimulus_window.set_mode(mode)
        self._stimulus_window.showFullScreen()
        self._stimulus_window.raise_()
        self._stimulus_window.activateWindow()
        self._stimulus_window.setFocus(Qt.OtherFocusReason)

    def _on_ssvep_window_stopped(self, label: str, path: str) -> None:
        prefix = "SSVEP 在线驾驶已结束" if label == "在线控制" else f"刺激试次已结束：{label}"
        self._notify(prefix + (f"，测频记录：{path}" if path else ""))

    def _on_ssvep_control_started(self) -> None:
        source = self._pipeline_worker.source_name if self._pipeline_worker is not None else ""
        begin_calibration = getattr(self.intent_decoder, "begin_idle_calibration", None)
        if callable(begin_calibration):
            begin_calibration()
        self._notify(self.actions.start_bci_control_session(source))

    def _on_ssvep_fresh_window_requested(self, seconds: float, previous_phase: str) -> None:
        if self._pipeline_worker is not None:
            self._pipeline_worker.reset_online_decoding("冷却结束，开始新的完整 EEG 窗口")
        self.actions.bci.begin_fresh_window_wait(seconds)
        if previous_phase == "空闲校准":
            summary = getattr(self.intent_decoder, "idle_calibration_summary", None)
            if callable(summary):
                self._notify(str(summary()))

    def _on_ssvep_control_stopped(self) -> None:
        if not (
            self.actions.bci_control_enabled
            or self.soldier_bci.start_pending
            or self.soldier_bci.chain_active
        ):
            return
        self.soldier_bci.stop(
            "SSVEP在线驾驶已暂停：采集、LSL和脑控授权均已关闭"
        )

    def _handle_ssvep_emergency(self) -> None:
        """Keep the hardware emergency path independent of EEG teardown."""

        if (
            self.actions.bci_control_enabled
            or self.soldier_bci.start_pending
            or self.soldier_bci.chain_active
        ):
            self.soldier_bci.stop(
                "SSVEP链路已关闭，正在执行无人机紧急停止"
            )
        self._notify(self.actions.emergency_stop())

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
        layout.addWidget(QLabel("每行填写一个 Crazyflie URI，可配置多架（必须先断开）：", dialog))
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

    def _show_move_distance_dialog(self) -> None:
        if self._distance_dialog is not None:
            self._distance_dialog.raise_()
            self._distance_dialog.activateWindow()
            return
        self._release_mouse_capture_for_dialog()
        dialog = QDialog(self)
        dialog.setWindowTitle("设置 SSVEP 单次移动距离")
        dialog.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("输入后续前进、后退、左移、右移指令的移动距离：", dialog))
        distance_input = QDoubleSpinBox(dialog)
        distance_input.setRange(1.0, 100.0)
        distance_input.setDecimals(1)
        distance_input.setSingleStep(1.0)
        distance_input.setSuffix(" cm")
        distance_input.setValue(self.actions.swarm_move_distance_m() * 100.0)
        layout.addWidget(distance_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._distance_dialog = dialog
        self._distance_input = distance_input
        dialog.accepted.connect(
            lambda: self._apply_move_distance_cm(distance_input.value())
        )
        dialog.rejected.connect(lambda: self._notify("已取消移动距离设置"))
        dialog.finished.connect(lambda _result: self._finish_move_distance_dialog(dialog))
        QTimer.singleShot(0, lambda: self._open_move_distance_dialog_if_current(dialog))

    def _open_move_distance_dialog_if_current(self, dialog: QDialog) -> None:
        if self._distance_dialog is dialog:
            dialog.open()

    def _apply_move_distance_cm(self, distance_cm: float) -> None:
        message = self.actions.set_swarm_move_distance(float(distance_cm) / 100.0)
        if self._stimulus_window is not None:
            self._stimulus_window.set_control_profile(
                move_distance_m=self.actions.swarm_move_distance_m(),
                command_cooldown_seconds=self.actions.bci.command_cooldown_seconds,
            )
        self._notify(message)

    def _finish_move_distance_dialog(self, dialog: QDialog) -> None:
        if self._distance_dialog is dialog:
            self._distance_dialog = None
            self._distance_input = None
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
        labels = ("起飞", "前进", "后退", "左移", "右移", "悬停", "受控降落")
        action_map = {
            "起飞": "takeoff",
            "前进": "forward",
            "后退": "backward",
            "左移": "left",
            "右移": "right",
            "悬停": "hover",
            "受控降落": "land",
        }
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
