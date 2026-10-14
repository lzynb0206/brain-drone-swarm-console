"""Pure formation-state transformations for the Crazyflie gateway."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from src.domain import (
    FormationAssignment,
    FormationPlan,
    FormationSlot,
    Position,
    UAVTelemetry,
)


def formation_targets(
    plan: FormationPlan,
    telemetry_by_uri: Mapping[str, UAVTelemetry],
    resolve_uri: Callable[[str], str],
) -> dict[str, tuple[float, float, float, float]]:
    """Translate a domain formation plan into backend absolute setpoints."""

    targets: dict[str, tuple[float, float, float, float]] = {}
    for assignment in plan.assignments:
        uri = resolve_uri(assignment.uav_id)
        position = assignment.slot.position
        targets[uri] = (
            position.x,
            position.y,
            position.z,
            telemetry_by_uri[uri].yaw_deg,
        )
    return targets


def rebase_formation_plan(
    plan: FormationPlan | None,
    telemetry_by_uri: Mapping[str, UAVTelemetry],
    resolve_uri: Callable[[str], str],
) -> FormationPlan | None:
    """Re-anchor a formation to its latest measured aircraft positions."""

    if plan is None:
        return None
    assignments: list[FormationAssignment] = []
    positions: list[Position] = []
    for item in plan.assignments:
        position = telemetry_by_uri[resolve_uri(item.uav_id)].position
        positions.append(position)
        assignments.append(
            FormationAssignment(
                item.uav_id,
                FormationSlot(item.slot.slot_id, position),
                0.0,
            )
        )
    center = Position(
        sum(position.x for position in positions) / len(positions),
        sum(position.y for position in positions) / len(positions),
        sum(position.z for position in positions) / len(positions),
    )
    return FormationPlan(plan.shape, center, plan.spacing_m, tuple(assignments))


def lighthouse_targets_from_telemetry(
    held_uris: Iterable[str],
    telemetry_by_uri: Mapping[str, UAVTelemetry],
) -> dict[str, tuple[float, float, float, float]]:
    """Build continuous Lighthouse hold targets from current telemetry."""

    targets: dict[str, tuple[float, float, float, float]] = {}
    for uri in held_uris:
        telemetry = telemetry_by_uri.get(uri)
        if telemetry is None:
            continue
        targets[uri] = (
            telemetry.position.x,
            telemetry.position.y,
            telemetry.position.z,
            telemetry.yaw_deg,
        )
    return targets


__all__ = [
    "formation_targets",
    "lighthouse_targets_from_telemetry",
    "rebase_formation_plan",
]
