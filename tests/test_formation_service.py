from __future__ import annotations

import unittest

from src.domain import DeviceStatus, Position, UAVTelemetry
from src.services.formation_service import FormationPlanningError, FormationService


def uav(name: str, x: float, y: float) -> UAVTelemetry:
    return UAVTelemetry(name, 80.0, 0.0, 0.4, "飞行", DeviceStatus.NORMAL, Position(x, y, 0.4))


class FormationServiceTest(unittest.TestCase):
    def test_line_uses_unique_slots_and_minimizes_travel(self) -> None:
        plan = FormationService().plan(
            (uav("A", 0.0, 0.5), uav("B", 0.0, 0.0), uav("C", 0.0, -0.5)),
            shape="line", spacing_m=0.4,
        )

        assignments = {item.uav_id: item.slot for item in plan.assignments}
        self.assertEqual({"L1", "L2", "L3"}, {slot.slot_id for slot in assignments.values()})
        self.assertGreater(assignments["A"].position.y, assignments["B"].position.y)
        self.assertGreater(assignments["B"].position.y, assignments["C"].position.y)

    def test_triangle_requires_three_aircraft(self) -> None:
        with self.assertRaisesRegex(FormationPlanningError, "恰好连接 3 架"):
            FormationService().plan(
                (uav("A", 0.0, 0.0), uav("B", 0.1, 0.0)),
                shape="triangle", spacing_m=0.4,
            )

    def test_translating_plan_preserves_slot_offsets(self) -> None:
        plan = FormationService().plan(
            (uav("A", 0.0, 0.0), uav("B", 0.0, 0.4), uav("C", 0.0, -0.4)),
            shape="triangle", spacing_m=0.4,
        )
        moved = plan.translated(0.2, -0.1, 0.1)
        before = {item.slot.slot_id: item.slot.position for item in plan.assignments}
        after = {item.slot.slot_id: item.slot.position for item in moved.assignments}
        for slot_id in before:
            self.assertAlmostEqual(before[slot_id].x + 0.2, after[slot_id].x)
            self.assertAlmostEqual(before[slot_id].y - 0.1, after[slot_id].y)
            self.assertAlmostEqual(before[slot_id].z + 0.1, after[slot_id].z)


if __name__ == "__main__":
    unittest.main()
