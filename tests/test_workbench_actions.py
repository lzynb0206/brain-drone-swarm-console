from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from src.adapters.mock import MockSwarmGateway, MockSystemMonitor
from src.adapters.real import CrazyflieSwarmConfig, CrazyflieSwarmGateway
from src.bci.config import COMMANDS
from src.domain import Role, User
from src.services import ExperimentRecorder, WorkbenchService
from src.views.main_view import MainView
from src.widgets.scifi_canvas import Canvas


class WorkbenchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = MockSwarmGateway()
        self.service = WorkbenchService(self.gateway, MockSystemMonitor())

    def test_algorithm_controller_and_mapping_are_stateful(self) -> None:
        self.service.select_algorithm("SVM")
        self.service.load_algorithm()
        self.service.set_default_algorithm()
        self.assertEqual("SVM", self.service.default_algorithm)
        self.assertIn("SVM", self.service.loaded_algorithms)

        self.service.controller_values["Kp"] = 84
        self.assertIn("Kp=0.84", self.service.apply_controller())

        self.service.add_mapping("18Hz", "悬停", "0m/s")
        self.assertEqual("18Hz", self.service.mappings[-1].frequency)
        added_index = len(self.service.mappings) - 1
        self.service.update_mapping(added_index, "后退", "1m/s")
        self.assertEqual("后退", self.service.mappings[-1].action)
        self.service.delete_mapping(added_index)
        self.assertEqual(len(COMMANDS), len(self.service.mappings))

    def test_swarm_commands_and_emergency_stop_use_gateway(self) -> None:
        self.service.send_swarm_command("takeoff")
        self.service.send_swarm_command("forward")
        self.assertEqual("forward", self.gateway.command_log[-1].action)
        self.service.emergency_stop()
        self.assertEqual("emergency_stop", self.gateway.command_log[-1].action)
        before = len(self.gateway.command_log)
        message = self.service.send_swarm_command("left")
        self.assertIn("拒绝", message)
        self.assertEqual(before, len(self.gateway.command_log))

    def test_stop_motion_motor_stop_and_emergency_recovery_are_distinct(self) -> None:
        self.service.send_swarm_command("takeoff")
        message = self.service.stop_swarm_motion()
        self.assertIn("停止水平移动", message)
        self.assertEqual("stop_motion", self.gateway.command_log[-1].action)

        message = self.service.stop_swarm_motors()
        self.assertIn("重新起飞需要再次解锁", message)
        self.assertEqual("motor_stop", self.gateway.command_log[-1].action)
        self.assertFalse(self.gateway.status().emergency)

        self.gateway.arm()
        self.service.emergency_stop()
        self.assertTrue(self.service.emergency_stopped)
        message = self.service.reset_emergency_after_reboot()
        self.assertIn("重新连接并解锁", message)
        self.assertFalse(self.service.emergency_stopped)
        self.assertFalse(self.gateway.status().connected)

    def test_virtual_sticks_update_gateway_without_creating_discrete_commands(self) -> None:
        before = len(self.gateway.command_log)
        self.assertIsNone(self.service.set_manual_control(0.7, -0.3, 0.2))
        self.assertEqual((0.7, -0.3, 0.2), self.gateway.manual_input)
        self.assertEqual(before, len(self.gateway.command_log))

    def test_takeoff_height_can_be_adjusted_before_flow_hover_starts(self) -> None:
        self.gateway.disarm()
        message = self.service.adjust_swarm_takeoff_height(0.1)
        self.assertIn("0.50m", message)
        self.assertEqual(0.5, self.gateway.takeoff_height_m())

    def test_csv_replay_cannot_enable_hardware_bci_control(self) -> None:
        message = self.service.set_bci_control_enabled(True, "CSV回放 · start-1.csv")
        self.assertIn("只有实时 LSL", message)
        self.assertFalse(self.service.bci_control_enabled)

    def test_command_audit_correlates_submission_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = MockSwarmGateway()
            recorder = ExperimentRecorder(directory)
            service = WorkbenchService(gateway, MockSystemMonitor(), recorder)
            service.send_swarm_command("takeoff")
            service.send_swarm_command("forward")
            rows = recorder.command_log_path.read_text(encoding="utf-8-sig").splitlines()
        command_id = gateway.command_log[-1].command_id
        self.assertTrue(command_id)
        self.assertTrue(any(command_id in row and "COMPLETED" in row for row in rows))
        self.assertTrue(any(command_id in row and "SUBMITTED" in row for row in rows))

    def test_report_export_writes_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.service.export_soldier_report(Path(directory))
            payload = json.loads(path.read_text(encoding="utf-8"))
            config_path = self.service.export_workspace_config(Path(directory))
            config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual("S-07", payload["soldier_id"])
        self.assertIn("exported_at", payload)
        self.assertEqual("SSVEP", config["paradigm"]["type"])
        self.assertEqual("FBCCA", config["algorithm"])


class MainViewActionRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.gateway = MockSwarmGateway()
        self.view = MainView(swarm_gateway=self.gateway, system_monitor=MockSystemMonitor())

    def tearDown(self) -> None:
        self.view.close()

    def test_painted_control_actions_change_application_state(self) -> None:
        self.view._activate("paradigm-frequency:15")
        self.view._activate("algorithm-select:LSTM")
        self.view._activate("algorithm-load")
        self.view._activate("controller-slider:Kp", x=910)
        self.view._activate("data-tab:数据浏览")
        self.view._activate("swarm-command:takeoff")
        self.view._activate("swarm-command:right")

        self.assertEqual(15, self.view.actions.paradigm_frequency)
        self.assertEqual("LSTM", self.view.actions.algorithm)
        self.assertIn("LSTM", self.view.actions.loaded_algorithms)
        self.assertEqual(100, self.view.actions.controller_values["Kp"])
        self.assertEqual("数据浏览", self.view.actions.data_tab)
        self.assertEqual("right", self.gateway.command_log[-1].action)

    def test_flight_stop_actions_are_routed_to_distinct_gateway_operations(self) -> None:
        self.view._activate("swarm-command:takeoff")
        self.view._activate("swarm-stop-motion")
        self.assertEqual("stop_motion", self.gateway.command_log[-1].action)
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes):
            self.view._activate("swarm-motor-stop")
        self.assertEqual("motor_stop", self.gateway.command_log[-1].action)

    def test_lighthouse_and_formation_controls_route_to_swarm_commands(self) -> None:
        self.view._activate("swarm-lighthouse-hold")
        self.view._activate("swarm-form-line")
        self.view._activate("swarm-form-triangle")

        self.assertEqual(
            ["lighthouse_hold", "form_line", "form_triangle"],
            [command.action for command in self.gateway.command_log],
        )
        self.assertEqual(0.4, self.gateway.command_log[-1].parameters["spacing_m"])

    def test_virtual_joystick_helpers_update_and_center_axes(self) -> None:
        self.view._update_manual_stick("manual-stick-height", 470, 304)
        self.assertEqual(1.0, self.gateway.manual_input[0])
        self.view._update_manual_stick("manual-stick-direction", 842, 304)
        self.assertAlmostEqual(2 ** -0.5, self.gateway.manual_input[1])
        self.assertAlmostEqual(2 ** -0.5, self.gateway.manual_input[2])
        self.view._center_joysticks(send=True)
        self.assertEqual((0.0, 0.0, 0.0), self.gateway.manual_input)

    def test_real_mouse_click_hits_painted_algorithm_card(self) -> None:
        self.view.resize(1280, 720)
        self.view.set_user(User("researcher", "测试科研员", Role.RESEARCHER))
        self.view.research_page = "算法"
        self.view.show()
        self.app.processEvents()

        QTest.mouseClick(self.view, Qt.LeftButton, pos=QPoint(100, 485))

        self.assertEqual("FBCCA", self.view.actions.algorithm)

    def test_unconnected_flow_page_renders_completely(self) -> None:
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        view = MainView(swarm_gateway=gateway)
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            view._soldier_brain(Canvas(painter, 1280, 720))
        finally:
            painter.end()
            view.close()
        self.assertGreaterEqual(len(view._regions), 10)

    def test_uri_dialog_cancel_returns_to_main_view(self) -> None:
        self.view._configure_swarm_uris_dialog()
        dialog = self.view._uri_dialog
        self.assertIsNotNone(dialog)
        self.app.processEvents()
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(buttons)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.assertIsNotNone(cancel)

        QTest.mouseClick(cancel, Qt.LeftButton)
        self.app.processEvents()

        self.assertIsNone(self.view._uri_dialog)
        self.assertEqual("已取消 URI 配置", self.view._toast)

    def test_custom_takeoff_height_dialog_updates_gateway(self) -> None:
        self.gateway.disarm()
        self.view._show_takeoff_height_dialog()
        dialog = self.view._height_dialog
        height_input = self.view._height_input
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(height_input)
        height_input.setValue(0.55)

        dialog.accept()
        self.app.processEvents()

        self.assertIsNone(self.view._height_dialog)
        self.assertIsNone(self.view._height_input)
        self.assertEqual(0.55, self.gateway.takeoff_height_m())

    def test_long_toast_starts_smooth_scroll_animation(self) -> None:
        message = "硬件急停未送达：蜂群未连接，无法获得硬件急停回执；请断电重启无人机后再复位"
        self.view._notify(message)
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            self.view._paint_toast(Canvas(painter, 1280, 720))
        finally:
            painter.end()

        self.assertTrue(self.view._toast_scroll_timer.isActive())
        self.assertEqual(message, self.view._toast)

    def test_temporary_task_button_runs_fixed_goto_after_confirmation(self) -> None:
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.view._activate("mission-test")

        self.assertIsNotNone(self.view._latest_recognition)
        self.assertEqual("去A点", self.view._latest_recognition.label)
        self.assertEqual(
            ["takeoff", "move_relative"],
            [command.action for command in self.gateway.command_log],
        )

    def test_brain_control_page_paints_temporary_task_button(self) -> None:
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            self.view._regions = []
            self.view._soldier_brain(Canvas(painter, 1280, 720))
        finally:
            painter.end()

        actions = [action for _rect, action in self.view._regions]
        self.assertIn("mission-test", actions)
        # Lighthouse switching is deliberately unavailable until real height
        # and positioning telemetry confirm that the aircraft is airborne.
        self.assertNotIn("swarm-lighthouse-hold", actions)
        self.assertIn("swarm-form-line", actions)
        self.assertIn("swarm-form-triangle", actions)

    def test_cancelling_inspection_confirmation_sends_no_command(self) -> None:
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.view._activate("mission-test")

        self.assertEqual([], self.gateway.command_log)
        self.assertIn("已取消任务", self.view._toast)


if __name__ == "__main__":
    unittest.main()
