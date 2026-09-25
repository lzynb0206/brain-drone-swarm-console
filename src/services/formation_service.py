"""Generate formation slots and assign aircraft with minimum total travel."""

from __future__ import annotations

import math
from typing import Sequence

from src.domain import Position, UAVTelemetry
from src.domain.formation import FormationAssignment, FormationPlan, FormationSlot


class FormationPlanningError(RuntimeError):
    pass


class FormationService:
    def plan(
        self,
        uavs: Sequence[UAVTelemetry],
        *,
        shape: str,
        spacing_m: float,
        altitude_m: float | None = None,
    ) -> FormationPlan:
        aircraft = tuple(uavs)
        if not aircraft:
            raise FormationPlanningError("没有可用于编队的无人机")
        if len({uav.uav_id for uav in aircraft}) != len(aircraft):
            raise FormationPlanningError("无人机编号重复，不能分配编队槽位")
        spacing = float(spacing_m)
        if not math.isfinite(spacing) or not 0.15 <= spacing <= 2.0:
            raise FormationPlanningError("编队间距必须在 0.15–2.00m 之间")

        center = Position(
            sum(uav.position.x for uav in aircraft) / len(aircraft),
            sum(uav.position.y for uav in aircraft) / len(aircraft),
            float(altitude_m) if altitude_m is not None else
            sum(uav.position.z for uav in aircraft) / len(aircraft),
        )
        slots = self._slots(shape.strip().lower(), len(aircraft), center, spacing)
        costs = [
            [self._distance(uav.position, slot.position) for slot in slots]
            for uav in aircraft
        ]
        assignment = self._hungarian(costs)
        results = tuple(
            FormationAssignment(aircraft[row].uav_id, slots[col], costs[row][col])
            for row, col in enumerate(assignment)
        )
        return FormationPlan(shape.strip().lower(), center, spacing, results)

    @staticmethod
    def _slots(shape: str, count: int, center: Position, spacing: float) -> tuple[FormationSlot, ...]:
        if shape == "line":
            return tuple(
                FormationSlot(
                    f"L{index + 1}",
                    Position(center.x, center.y + (index - (count - 1) / 2) * spacing, center.z),
                )
                for index in range(count)
            )
        if shape == "triangle":
            if count != 3:
                raise FormationPlanningError("三角编队当前要求恰好连接 3 架无人机")
            height = math.sqrt(3.0) * spacing / 2.0
            return (
                FormationSlot("T1", Position(center.x + 2.0 * height / 3.0, center.y, center.z)),
                FormationSlot("T2", Position(center.x - height / 3.0, center.y + spacing / 2.0, center.z)),
                FormationSlot("T3", Position(center.x - height / 3.0, center.y - spacing / 2.0, center.z)),
            )
        raise FormationPlanningError(f"不支持的编队形状：{shape}")

    @staticmethod
    def _distance(left: Position, right: Position) -> float:
        return math.sqrt(
            (left.x - right.x) ** 2 +
            (left.y - right.y) ** 2 +
            (left.z - right.z) ** 2
        )

    @staticmethod
    def _hungarian(costs: Sequence[Sequence[float]]) -> tuple[int, ...]:
        """Return the minimum-cost column for every row (square matrix)."""
        size = len(costs)
        if any(len(row) != size for row in costs):
            raise FormationPlanningError("编队代价矩阵必须为方阵")
        u = [0.0] * (size + 1)
        v = [0.0] * (size + 1)
        p = [0] * (size + 1)
        way = [0] * (size + 1)
        for row in range(1, size + 1):
            p[0] = row
            column0 = 0
            minimum = [math.inf] * (size + 1)
            used = [False] * (size + 1)
            while True:
                used[column0] = True
                current_row = p[column0]
                delta = math.inf
                column1 = 0
                for column in range(1, size + 1):
                    if used[column]:
                        continue
                    reduced = costs[current_row - 1][column - 1] - u[current_row] - v[column]
                    if reduced < minimum[column]:
                        minimum[column] = reduced
                        way[column] = column0
                    if minimum[column] < delta:
                        delta = minimum[column]
                        column1 = column
                for column in range(size + 1):
                    if used[column]:
                        u[p[column]] += delta
                        v[column] -= delta
                    else:
                        minimum[column] -= delta
                column0 = column1
                if p[column0] == 0:
                    break
            while True:
                column1 = way[column0]
                p[column0] = p[column1]
                column0 = column1
                if column0 == 0:
                    break
        result = [-1] * size
        for column in range(1, size + 1):
            if p[column]:
                result[p[column] - 1] = column - 1
        return tuple(result)
