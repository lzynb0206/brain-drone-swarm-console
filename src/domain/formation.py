"""Framework-independent formation planning results."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.models import Position


@dataclass(frozen=True)
class FormationSlot:
    slot_id: str
    position: Position


@dataclass(frozen=True)
class FormationAssignment:
    uav_id: str
    slot: FormationSlot
    distance_m: float


@dataclass(frozen=True)
class FormationPlan:
    shape: str
    center: Position
    spacing_m: float
    assignments: tuple[FormationAssignment, ...]

    @property
    def max_distance_m(self) -> float:
        return max((item.distance_m for item in self.assignments), default=0.0)

    def translated(self, dx: float, dy: float, dz: float) -> "FormationPlan":
        assignments = tuple(
            FormationAssignment(
                item.uav_id,
                FormationSlot(
                    item.slot.slot_id,
                    Position(
                        item.slot.position.x + dx,
                        item.slot.position.y + dy,
                        item.slot.position.z + dz,
                    ),
                ),
                (dx * dx + dy * dy + dz * dz) ** 0.5,
            )
            for item in self.assignments
        )
        return FormationPlan(
            self.shape,
            Position(self.center.x + dx, self.center.y + dy, self.center.z + dz),
            self.spacing_m,
            assignments,
        )
