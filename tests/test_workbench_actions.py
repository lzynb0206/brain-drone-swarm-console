from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.adapters.mock import MockSwarmGateway, MockSystemMonitor
from src.bci.config import COMMANDS
from src.domain import Role, User
from src.services import ExperimentRecorder, WorkbenchService
from src.views.main_view import MainView


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
        self.service.send_swarm_command("forward")
        self.assertEqual("forward", self.gateway.command_log[-1].action)
        self.service.emergency_stop()
        self.assertEqual("emergency_stop", self.gateway.command_log[-1].action)
        before = len(self.gateway.command_log)
        message = self.service.send_swarm_command("left")
        self.assertIn("拒绝", message)
        self.assertEqual(before, len(self.gateway.command_log))

    def test_csv_replay_cannot_enable_hardware_bci_control(self) -> None:
        message = self.service.set_bci_control_enabled(True, "CSV回放 · start-1.csv")
        self.assertIn("只有实时 LSL", message)
        self.assertFalse(self.service.bci_control_enabled)

    def test_command_audit_correlates_submission_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = MockSwarmGateway()
            recorder = ExperimentRecorder(directory)
            service = WorkbenchService(gateway, MockSystemMonitor(), recorder)
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
        self.view._activate("swarm-command:right")

        self.assertEqual(15, self.view.actions.paradigm_frequency)
        self.assertEqual("LSTM", self.view.actions.algorithm)
        self.assertIn("LSTM", self.view.actions.loaded_algorithms)
        self.assertEqual(100, self.view.actions.controller_values["Kp"])
        self.assertEqual("数据浏览", self.view.actions.data_tab)
        self.assertEqual("right", self.gateway.command_log[-1].action)

    def test_real_mouse_click_hits_painted_algorithm_card(self) -> None:
        self.view.resize(1280, 720)
        self.view.set_user(User("researcher", "测试科研员", Role.RESEARCHER))
        self.view.research_page = "算法"
        self.view.show()
        self.app.processEvents()

        QTest.mouseClick(self.view, Qt.LeftButton, pos=QPoint(100, 485))

        self.assertEqual("FBCCA", self.view.actions.algorithm)


if __name__ == "__main__":
    unittest.main()
