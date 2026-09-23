"""Framework-independent models for one-UAV inspection missions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from src.domain.models import Position


@dataclass(frozen=True)
class TargetPoint:
    target_id: str
    name: str
    position: Position


@dataclass(frozen=True)
class MissionStep:
    action: str
    description: str
    parameters: Mapping[str, float | str] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionPolicy:
    """由高层任务语义决定到达后的行为，而不是由目标点决定。"""

    task_type: str
    target_id: str
    arrival_action: str = "HOLD"
    observe_seconds: float = 0.0
    return_policy: str = "NONE"
    landing_policy: str = "NONE"


@dataclass(frozen=True)
class MissionPlan:
    mission_id: str
    task_label: str
    policy: MissionPolicy
    target: TargetPoint
    uav_id: str
    start_position: Position
    cruise_start: Position
    waypoints: tuple[Position, ...]
    outbound_distance_m: float
    total_distance_m: float
    estimated_duration_s: float
    steps: tuple[MissionStep, ...]
