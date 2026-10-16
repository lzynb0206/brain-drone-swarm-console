from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from src.adapters.mock import MockSwarmGateway, MockSystemMonitor
from src.adapters.real import CrazyflieSwarmConfig, CrazyflieSwarmGateway
from src.bci.config import COMMANDS
from src.domain import IntentResult, Role, User
from src.services import ExperimentRecorder, WorkbenchService
from src.views.main_view import MainView
from src.widgets.scifi_canvas import Canvas, RESEARCH_NAV_ITEMS


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

    def test_ssvep_move_distance_can_be_changed_while_control_is_armed(self) -> None:
        message = self.service.set_swarm_move_distance(0.12)
        self.service.send_swarm_command("takeoff")
        self.service.send_swarm_command("forward")

        self.assertIn("12cm", message)
        self.assertEqual(0.12, self.service.swarm_move_distance_m())
        self.assertEqual(0.12, self.gateway.command_log[-1].parameters["distance_m"])

    def test_csv_replay_cannot_enable_hardware_bci_control(self) -> None:
        message = self.service.set_bci_control_enabled(True, "CSV回放 · start-1.csv")
        self.assertIn("只有实时 LSL", message)
        self.assertFalse(self.service.bci_control_enabled)

    def test_expandable_native_source_can_enable_hardware_bci_control(self) -> None:
        self.gateway.disarm()

        message = self.service.set_bci_control_enabled(
            True,
            "ExpandableEEG Native · /dev/cu.ExpandableEEGBT",
        )

        self.assertIn("保持地面静止", message)
        self.assertTrue(self.service.bci_control_enabled)
        self.assertFalse(self.gateway.status().airborne)

    def test_decoder_verified_intent_is_not_double_gated_by_execution_service(self) -> None:
        self.gateway.connect()
        self.gateway.arm()
        self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")
        self.service.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        before = len(self.gateway.command_log)

        self.service.handle_intent(
            "起飞",
            True,
            "LSL · obci_eeg1",
            safety_verified=True,
        )

        self.assertEqual(before + 1, len(self.gateway.command_log))
        self.assertEqual("takeoff", self.gateway.command_log[-1].action)

    def test_legacy_decoder_intent_still_requires_execution_confirmation(self) -> None:
        self.gateway.connect()
        self.gateway.arm()
        self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")
        self.service.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        before = len(self.gateway.command_log)

        first = self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.assertIn("二次确认", first)
        self.assertEqual(before, len(self.gateway.command_log))
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")

        self.assertEqual(before + 1, len(self.gateway.command_log))
        self.assertEqual("takeoff", self.gateway.command_log[-1].action)

    def test_grounded_bci_authorization_does_not_take_off_until_intent_arrives(self) -> None:
        self.gateway.disarm()

        message = self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")

        self.assertIn("保持地面静止", message)
        self.assertFalse(self.gateway.status().airborne)
        self.assertEqual([], self.gateway.command_log)

        self.service.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")

        self.assertTrue(self.gateway.status().airborne)
        self.assertEqual("takeoff", self.gateway.command_log[-1].action)

    def test_sustained_fixation_cannot_repeat_command_until_idle_release(self) -> None:
        self.gateway.disarm()
        self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")
        self.service.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        self.service.handle_intent(
            "起飞", True, "LSL · obci_eeg1", safety_verified=True,
        )
        command_count = len(self.gateway.command_log)

        message = self.service.handle_intent(
            "前进", True, "LSL · obci_eeg1", safety_verified=True,
        )

        self.assertIn("移开视线", message)
        self.assertEqual(command_count, len(self.gateway.command_log))

        self.service.handle_intent("空闲", False, "LSL · obci_eeg1")
        self.service.bci._last_command_time -= 3.0
        self.service.handle_intent(
            "前进", True, "LSL · obci_eeg1", safety_verified=True,
        )

        self.assertEqual(command_count + 1, len(self.gateway.command_log))
        self.assertEqual("forward", self.gateway.command_log[-1].action)

    def test_fresh_window_wait_blocks_old_cooldown_evidence_without_locking_control(self) -> None:
        self.gateway.disarm()
        self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")
        self.service.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        self.service.bci.begin_fresh_window_wait(2.0)

        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.assertEqual([], self.gateway.command_log)

        self.service.bci._session_ready_at -= 3.0
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.service.handle_intent("起飞", True, "LSL · obci_eeg1")
        self.assertEqual("takeoff", self.gateway.command_log[-1].action)

    def test_authorization_alone_never_executes_without_fullscreen_session(self) -> None:
        self.gateway.disarm()
        self.service.set_bci_control_enabled(True, "LSL · obci_eeg1")

        self.service.handle_intent(
            "起飞", True, "LSL · obci_eeg1", safety_verified=True,
        )

        self.assertEqual([], self.gateway.command_log)
        self.assertFalse(self.service.bci.session_active)

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

    @staticmethod
    def _render_page(page, method_name: str) -> set[str]:
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        page._regions = []
        try:
            getattr(page, method_name)(Canvas(painter, 1280, 720))
        finally:
            painter.end()
        return {action for _rect, action in page._regions}

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

    def test_research_acquisition_tabs_are_stateful(self) -> None:
        self.view._activate("research:采集")
        self.view._activate("acquisition-tab:LSL")
        self.assertEqual("采集", self.view.research_page)
        self.assertEqual("LSL", self.view.acquisition_tab)

    def test_research_navigation_exposes_hardware_free_ssvep_command_test(self) -> None:
        actions = self._render_page(self.view.research_widget, "_paint_research")

        self.assertIn("ssvep-command-test", actions)

    def test_ssvep_command_test_action_is_routed_to_test_window(self) -> None:
        with patch.object(self.view, "_open_ssvep_test") as opener:
            self.view._activate("ssvep-command-test")

        opener.assert_called_once_with()

    def test_test_window_recognition_never_reaches_swarm_gateway(self) -> None:
        result = IntentResult(
            timestamp=1.0,
            label="起飞",
            confidence=0.91,
            margin=0.25,
            accepted=True,
            reason="",
            frequency_hz=8.0,
            scores={"起飞": 0.91},
            sample_rate_hz=250.0,
            sample_count=500,
            used_channels_1based=(1, 2, 3),
            safety_verified=True,
        )
        test_window = MagicMock()
        test_window.test_mode = True
        test_window.blocks_hardware_execution = True
        test_window.running = True
        test_window.stimuli_active = True
        test_window.calibrating_idle = False
        test_window.process_simulated_control_result.return_value = True
        self.view._stimulus_window = test_window
        self.gateway.connect()
        self.gateway.arm()
        self.view.actions.set_bci_control_enabled(True, "LSL · obci_eeg1")
        self.view.actions.bci.start_session("LSL · obci_eeg1", warmup_seconds=0.0)
        command_count = len(self.gateway.command_log)

        self.view._on_recognition(result)

        test_window.process_simulated_control_result.assert_called_once_with(result)
        self.assertEqual(command_count, len(self.gateway.command_log))

    def test_research_navigation_uses_one_contiguous_layout_grid(self) -> None:
        for current, following in zip(RESEARCH_NAV_ITEMS, RESEARCH_NAV_ITEMS[1:]):
            self.assertEqual(current[1] + current[2], following[1])

    def test_acquisition_setup_keeps_primary_actions_available(self) -> None:
        self.view.acquisition_stage = "setup"
        actions = self._render_page(self.view.research_widget, "_research_acquisition")
        self.assertIn("openbci-serial-autoconnect", actions)
        self.assertIn("openbci-start-session", actions)
        self.assertIn("openbci-guide", actions)

    def test_lsl_collector_is_a_distinct_research_navigation_page(self) -> None:
        self.view._activate("research:LSL收集")

        self.assertEqual("LSL收集", self.view.research_page)

        actions = self._render_page(self.view.research_widget, "_research_lsl_collector")
        self.assertIn("lsl-collector-refresh", actions)
        self.assertIn("lsl-collector-start", actions)
        self.assertIn("lsl-collector-stop", actions)

    def test_acquisition_networking_is_an_lsl_output_not_input(self) -> None:
        self.view.acquisition_stage = "runtime"
        self.view.acquisition_tab = "LSL"
        actions = self._render_page(self.view.research_widget, "_research_acquisition")
        self.assertIn("lsl-outlet-toggle", actions)
        self.assertNotIn("lsl-refresh", actions)

    def test_acquisition_setup_uses_fixed_hardware_profile(self) -> None:
        self.assertEqual(250, self.view.acquisition.sample_rate)
        self.assertEqual(8, len(self.view.acquisition.hardware_channel_settings))

    def test_flight_stop_actions_are_routed_to_distinct_gateway_operations(self) -> None:
        self.view._activate("swarm-command:takeoff")
        self.view._activate("swarm-stop-motion")
        self.assertEqual("stop_motion", self.gateway.command_log[-1].action)
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes):
            self.view._activate("swarm-motor-stop")
        self.assertEqual("motor_stop", self.gateway.command_log[-1].action)

    def test_emergency_stop_closes_active_soldier_eeg_chain(self) -> None:
        self.view.soldier_bci.chain_active = True

        with patch.object(self.view.soldier_bci, "stop") as stop_chain:
            self.view._activate("swarm-emergency-stop")

        stop_chain.assert_called_once()
        self.assertTrue(self.gateway.status().emergency)

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
        self.view.actions.algorithm = "SVM"
        self.view.show()
        self.app.processEvents()

        QTest.mouseClick(self.view.research_widget, Qt.LeftButton, pos=QPoint(100, 485))

        self.assertEqual("FBCCA", self.view.actions.algorithm)

    def test_unconnected_flow_page_renders_completely(self) -> None:
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        view = MainView(swarm_gateway=gateway)
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            view.soldier_widget._regions = []
            view.soldier_widget._soldier_brain(Canvas(painter, 1280, 720))
        finally:
            painter.end()
            view.close()
        self.assertGreaterEqual(len(view.soldier_widget._regions), 10)

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

    def test_move_distance_dialog_updates_gateway_in_centimeters(self) -> None:
        self.view._show_move_distance_dialog()
        dialog = self.view._distance_dialog
        distance_input = self.view._distance_input
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(distance_input)
        distance_input.setValue(12.0)

        dialog.accept()
        self.app.processEvents()

        self.assertIsNone(self.view._distance_dialog)
        self.assertIsNone(self.view._distance_input)
        self.assertEqual(0.12, self.gateway.move_distance_m())

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

    def test_simulated_structured_mission_runs_goto_after_confirmation(self) -> None:
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.view._activate("mission-simulate")

        self.assertIsNotNone(self.view._latest_recognition)
        self.assertEqual("去A点", self.view._latest_recognition.label)
        self.assertEqual(
            ["takeoff", "move_relative"],
            [command.action for command in self.gateway.command_log],
        )

    def test_brain_control_page_paints_structured_mission_button(self) -> None:
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            self.view.soldier_widget._regions = []
            self.view.soldier_widget._soldier_brain(Canvas(painter, 1280, 720))
        finally:
            painter.end()

        actions = [action for _rect, action in self.view.soldier_widget._regions]
        self.assertIn("mission-simulate", actions)
        self.assertIn("swarm-move-distance-custom", actions)
        # Lighthouse switching is deliberately unavailable until real height
        # and positioning telemetry confirm that the aircraft is airborne.
        self.assertNotIn("swarm-lighthouse-hold", actions)
        self.assertIn("swarm-form-line", actions)
        self.assertIn("swarm-form-triangle", actions)

    def test_soldier_ssvep_entry_appears_when_live_control_is_authorized(self) -> None:
        worker = MagicMock()
        worker.isRunning.return_value = True
        worker.source_name = "LSL · obci_eeg1"
        self.view._pipeline_worker = worker
        self.view.actions.bci.enabled = True
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            self.view.soldier_widget._regions = []
            self.view.soldier_widget._soldier_brain(Canvas(painter, 1280, 720))
        finally:
            painter.end()

        actions = [action for _rect, action in self.view.soldier_widget._regions]
        self.assertIn("soldier-ssvep-control", actions)

    def test_soldier_ssvep_action_opens_operational_window(self) -> None:
        with patch.object(self.view, "_open_ssvep_control") as opener:
            self.view._activate("soldier-ssvep-control")

        opener.assert_called_once_with()

    def test_soldier_one_click_configures_hardware_bridge_and_lsl_decoder(self) -> None:
        eeg_stream = MagicMock()
        device = SimpleNamespace(
            port="/dev/cu.ExpandableEEGBT",
            description="ExpandableEEG",
            hardware_id="ESP32SPP",
        )
        hardware_stream = MagicMock()
        eeg_stream.discover_hardware_devices.return_value = [device]
        eeg_stream.configure_hardware_bridge.return_value = hardware_stream
        self.view.eeg_stream = eeg_stream

        with (
            patch.object(self.view, "_disconnect_eeg_pipeline") as disconnect,
            patch.object(self.view, "_build_eeg_worker") as build_worker,
            patch.object(self.view, "_start_eeg_pipeline") as start_pipeline,
            patch("src.controllers.soldier_bci_controller.EEGLSLBridgeWorker") as bridge_class,
        ):
            bridge = bridge_class.return_value
            started = self.view.soldier_bci.start(open_control_when_ready=True)

        self.assertTrue(started)
        disconnect.assert_called_once_with(stop_csv_recording=True)
        eeg_stream.configure_hardware_bridge.assert_called_once_with(device)
        eeg_stream.select_lsl.assert_called_once_with(self.view.acquisition.outlet.source_id)
        bridge_class.assert_called_once_with(
            hardware_stream, self.view.acquisition.outlet, parent=self.view,
        )
        bridge.start.assert_called_once_with()
        build_worker.assert_called_once_with(replace=True)
        start_pipeline.assert_called_once_with()
        self.assertTrue(self.view.soldier_bci.start_pending)
        self.assertTrue(self.view.soldier_bci.open_when_ready)
        self.assertFalse(self.view.actions.bci_control_enabled)

    def test_soldier_chain_authorizes_only_after_lsl_frame_arrives(self) -> None:
        worker = MagicMock()
        worker.source_name = "LSL · obci_eeg1"
        worker.isRunning.return_value = True
        self.view._pipeline_worker = worker
        self.view.acquisition.outlet = MagicMock()
        self.view.acquisition.outlet.active = True
        self.view.soldier_bci.start_pending = True

        self.view.soldier_bci.complete_after_frame(MagicMock())

        self.assertTrue(self.view.actions.bci_control_enabled)
        self.assertTrue(self.view.soldier_bci.chain_active)
        self.assertFalse(self.view.soldier_bci.start_pending)

    def test_soldier_chain_never_authorizes_when_lsl_publisher_is_down(self) -> None:
        worker = MagicMock()
        worker.source_name = "LSL · obci_eeg1"
        self.view._pipeline_worker = worker
        self.view.acquisition.outlet = MagicMock()
        self.view.acquisition.outlet.active = False
        self.view.soldier_bci.start_pending = True

        self.view.soldier_bci.complete_after_frame(MagicMock())

        self.assertFalse(self.view.actions.bci_control_enabled)
        self.assertFalse(self.view.soldier_bci.chain_active)
        self.assertFalse(self.view.soldier_bci.start_pending)

    def test_cancelling_inspection_confirmation_sends_no_command(self) -> None:
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.view._activate("mission-simulate")

        self.assertEqual([], self.gateway.command_log)
        self.assertIn("已取消任务", self.view._toast)


if __name__ == "__main__":
    unittest.main()
