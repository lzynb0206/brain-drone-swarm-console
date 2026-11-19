from __future__ import annotations

import time
import unittest

from PySide6.QtWidgets import QApplication, QWidget

from src.adapters.mock import MockSwarmGateway
from src.adapters.real.crazyflie_command_queue import CrazyflieCommandQueue
from src.adapters.real.crazyflie_telemetry import CrazyflieTelemetryMapper
from src.controllers import (
    CSVReplayController,
    EEGAcquisitionController,
    ExperimentController,
    LSLCollectionController,
    SoldierBCIController,
)
from src.domain import Role, SwarmCommand, User
from src.services import WorkbenchService
from src.services.bci_execution_service import BCIExecutionService
from src.services.research_workbench_service import ResearchWorkbenchService
from src.services.swarm_control_service import SwarmControlService
from src.views.main_view import MainView


class RefactoredBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_workbench_facade_composes_focused_services(self) -> None:
        service = WorkbenchService(MockSwarmGateway())

        self.assertIsInstance(service.research, ResearchWorkbenchService)
        self.assertIsInstance(service.swarm, SwarmControlService)
        self.assertIsInstance(service.bci, BCIExecutionService)
        service.paradigm_frequency = 15.0
        self.assertEqual(15.0, service.research.paradigm_frequency)

    def test_role_pages_are_real_widgets_and_emit_semantic_actions(self) -> None:
        view = MainView(swarm_gateway=MockSwarmGateway())
        try:
            self.assertIsInstance(view.research_widget, QWidget)
            self.assertIsInstance(view.soldier_widget, QWidget)
            self.assertIsInstance(view.acquisition, EEGAcquisitionController)
            self.assertIsInstance(view.csv_replay, CSVReplayController)
            self.assertIsInstance(view.lsl_collector, LSLCollectionController)
            self.assertIsInstance(view.soldier_bci, SoldierBCIController)
            self.assertIsInstance(view.experiment, ExperimentController)
            view.research_widget.action_requested.emit("acquisition-profile:hybrid8", 0.0, 0.0)
            self.assertEqual("hybrid8", view.actions.acquisition_profile.id)
        finally:
            view.close()

    def test_only_supported_roles_have_workspaces_and_registration_choices(self) -> None:
        from src.views.login_view import ROLE_CHOICES

        self.assertEqual({role.value for role in Role}, {"soldier", "commander", "researcher"})
        self.assertEqual({role for _, role in ROLE_CHOICES}, set(Role))
        view = MainView(swarm_gateway=MockSwarmGateway())
        try:
            self.assertEqual(view._role_stack.count(), 2)
            for role in Role:
                view.set_user(User(role.value, role.label, role))
                expected = view.research_widget if role == Role.RESEARCHER else view.soldier_widget
                self.assertIs(view._role_stack.currentWidget(), expected)
        finally:
            view.close()

    def test_command_queue_owns_generation_and_pending_commands(self) -> None:
        commands = CrazyflieCommandQueue(maxsize=2)
        command = SwarmCommand("takeoff")
        commands.enqueue(command)
        generation, queued = commands.get(timeout=0.0)
        self.assertEqual(0, generation)
        self.assertEqual(command, queued)
        self.assertEqual(1, commands.invalidate())

    def test_telemetry_mapper_has_no_gateway_side_effects(self) -> None:
        facts = CrazyflieTelemetryMapper().map(
            "radio://test/01",
            time.time(),
            {
                "stateEstimate.x": 0.1,
                "stateEstimate.y": 0.2,
                "stateEstimate.z": 0.3,
                "stateEstimate.vx": 0.0,
                "stateEstimate.vy": 0.0,
                "stateEstimate.vz": 0.0,
                "pm.vbat": 3.9,
                "range.zrange": 300.0,
                "motion.squal": 50.0,
                "deck.bcFlow2": 1.0,
            },
        )

        self.assertTrue(facts.positioning_ok)
        self.assertEqual("flow", facts.positioning_source)
        self.assertTrue(facts.flow_deck_present)


if __name__ == "__main__":
    unittest.main()
