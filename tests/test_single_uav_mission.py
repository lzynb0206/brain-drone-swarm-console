from __future__ import annotations

import math
import time
import unittest
from dataclasses import replace

from src.adapters.mock import MockSwarmGateway
from src.domain import IntentResult
from src.services import MissionPlanningError, SingleUAVMissionService


def mission_intent(label: str = "巡检A点", *, accepted: bool = True) -> IntentResult:
    return IntentResult(
        timestamp=time.time(),
        label=label,
        confidence=0.96,
        margin=0.32,
        accepted=accepted,
        reason="测试意图",
        frequency_hz=0.0,
        scores={label: 0.96},
        sample_rate_hz=250.0,
        sample_count=1000,
        used_channels_1based=(1, 2, 3, 4),
    )


def structured_goto_intent() -> IntentResult:
    return replace(
        mission_intent("解码任务-001"),
        task_type="GOTO",
        target_id="A",
        arrival_action="HOLD",
        return_policy="NONE",
        landing_policy="NONE",
    )


class SingleUAVMissionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = MockSwarmGateway()
        self.service = SingleUAVMissionService(self.gateway)

    def test_prepare_builds_an_obstacle_free_shortest_line(self) -> None:
        plan = self.service.prepare(mission_intent())

        self.assertEqual("UAV-01", plan.uav_id)
        self.assertEqual("A", plan.target.target_id)
        self.assertEqual(plan.target.position, plan.waypoints[-1])
        expected = math.hypot(1.0 / 3.0 - 0.15, 0.5 / 3.0 - 0.70)
        self.assertAlmostEqual(expected, plan.outbound_distance_m)
        self.assertAlmostEqual(expected * 2.0, plan.total_distance_m)
        self.assertEqual(
            ["takeoff", "move_relative", "hover", "move_relative", "land"],
            [step.action for step in plan.steps],
        )

        dx = plan.target.position.x - plan.cruise_start.x
        dy = plan.target.position.y - plan.cruise_start.y
        for waypoint in plan.waypoints:
            waypoint_dx = waypoint.x - plan.cruise_start.x
            waypoint_dy = waypoint.y - plan.cruise_start.y
            self.assertAlmostEqual(0.0, dx * waypoint_dy - dy * waypoint_dx)

    def test_execute_waits_for_each_completion_and_finishes_at_start(self) -> None:
        plan = self.service.prepare(mission_intent())

        self.service.execute(plan)

        self.assertEqual("COMPLETED", self.service.last_status)
        self.assertEqual(
            ["takeoff", "move_relative", "hover", "move_relative", "land"],
            [command.action for command in self.gateway.command_log],
        )
        aircraft = self.gateway.list_uavs()[0]
        self.assertAlmostEqual(plan.start_position.x, aircraft.position.x)
        self.assertAlmostEqual(plan.start_position.y, aircraft.position.y)
        self.assertEqual(0.0, aircraft.position.z)

    def test_rejected_decoder_result_cannot_create_a_mission(self) -> None:
        with self.assertRaisesRegex(MissionPlanningError, "未通过安全门控"):
            self.service.prepare(mission_intent(accepted=False))

    def test_goto_ends_at_target_without_hover_return_or_landing(self) -> None:
        plan = self.service.prepare(structured_goto_intent())

        self.assertEqual("GOTO", plan.policy.task_type)
        self.assertEqual("HOLD", plan.policy.arrival_action)
        self.assertEqual("NONE", plan.policy.return_policy)
        self.assertEqual("NONE", plan.policy.landing_policy)
        self.assertEqual(
            ["takeoff", "move_relative"],
            [step.action for step in plan.steps],
        )
        self.assertAlmostEqual(plan.outbound_distance_m, plan.total_distance_m)

        self.service.execute(plan)

        self.assertEqual(["takeoff", "move_relative"], [c.action for c in self.gateway.command_log])
        self.assertTrue(self.gateway.status().airborne)
        aircraft = self.gateway.list_uavs()[0]
        self.assertAlmostEqual(plan.target.position.x, aircraft.position.x)
        self.assertAlmostEqual(plan.target.position.y, aircraft.position.y)
        self.assertAlmostEqual(plan.target.position.z, aircraft.position.z)

    def test_zero_reported_battery_does_not_block_mission_planning(self) -> None:
        self.gateway._uavs = tuple(
            replace(aircraft, battery=0.0, battery_voltage=2.5)
            for aircraft in self.gateway._uavs
        )

        plan = self.service.prepare(structured_goto_intent())

        self.assertEqual("GOTO", plan.policy.task_type)


if __name__ == "__main__":
    unittest.main()
