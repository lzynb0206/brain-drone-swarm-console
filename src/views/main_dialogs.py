"""Dialogs and modal interactions used by the main dashboard."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


class MainDialogsMixin:
    """Own the dashboard's confirmation and configuration dialogs."""
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
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            self._notify("请先确保 EEG 数据源已经连接")
            return
        if self._stimulus_window is None:
            self._stimulus_window = SSVEPStimulusWindow(parent=self)
            self._stimulus_window.status_changed.connect(self._on_eeg_status)
            self._stimulus_window.trial_started.connect(self._pipeline_worker.capture_stimulus_trial)
            self._stimulus_window.event_marker.connect(self._pipeline_worker.record_stimulus_marker)
            self._stimulus_window.emergency_requested.connect(
                lambda: self._notify(self.actions.emergency_stop())
            )
            self._stimulus_window.trial_stopped.connect(
                lambda label, path: self._notify(
                    f"刺激试次已结束：{label}" + (f"，测频记录：{path}" if path else "")
                )
            )
        self._stimulus_window.showFullScreen()
        self._stimulus_window.raise_()
        self._stimulus_window.activateWindow()
        self._stimulus_window.setFocus(Qt.OtherFocusReason)

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


