"""Flight-command dispatch separated from gateway lifecycle and transport."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from src.adapters.real.crazyflie_formation_state import formation_targets
from src.domain import SwarmCommand
from src.services.formation_service import FormationPlanningError

if TYPE_CHECKING:
    from src.adapters.real.crazyflie_swarm_gateway import CrazyflieSwarmGateway


class CrazyflieCommandExecutor:
    """Execute one validated command against a gateway state owner."""

    def __init__(self, gateway: CrazyflieSwarmGateway) -> None:
        self.gateway = gateway

    def execute(self, command: SwarmCommand, generation: int) -> None:
        gateway = self.gateway
        gateway._raise_if_cancelled(generation)
        with gateway._lock:
            gateway._validate_command(command)
        action = command.action.strip().lower()
        targets = gateway._command_uris(command) if action != "led_test" else tuple(gateway.config.uris)
        # A command without uav_id is a group command even when the current
        # group happens to contain one aircraft.  This keeps group and targeted
        # execution semantics stable as aircraft are added or removed.
        one_uri = targets[0] if command.uav_id and len(targets) == 1 else None
        if action == "led_test":
            gateway._backend.set_led_all(0, 80, 255, float(command.parameters.get("duration_s", 1.0)))
        elif action == "takeoff":
            height = float(command.parameters.get("height_m", gateway.config.default_height_m))
            speed = max(0.05, gateway.config.move_speed_mps)
            duration = max(2.0, height / speed)
            with gateway._lock:
                needs_arming = tuple(uri for uri in targets if uri not in gateway._armed_uris)
            if len(needs_arming) == len(gateway.config.uris) and set(needs_arming) == set(gateway.config.uris):
                confirmed = gateway._backend.arm_all()
            else:
                confirmations = [gateway._backend.arm_one(uri) for uri in needs_arming]
                confirmed = False if any(value is False for value in confirmations) else None
            if confirmed is False:
                raise RuntimeError("飞控拒绝解锁，请检查电池、机体姿态和飞控锁定状态")
            with gateway._lock:
                gateway._armed_uris.update(targets)
                gateway._taking_off_uris.update(targets)
                gateway._airborne_uris.difference_update(targets)
                gateway._armed = bool(gateway._armed_uris)
                gateway._airborne = bool(gateway._airborne_uris)
                gateway._refresh_cached_flight_states()
                gateway._state = "taking_off"
                gateway._message = f"{len(targets)} 架无人机正在起飞并进入保持状态…"
            if one_uri is not None:
                gateway._backend.takeoff_one(one_uri, height, duration)
            else:
                gateway._backend.takeoff_all(height, duration)
            gateway._raise_if_cancelled(generation)
            gateway._wait_for_takeoff_height(
                height, generation, target_uris=targets, timeout_s=duration + 2.0,
            )
            with gateway._lock:
                gateway._taking_off_uris.difference_update(targets)
                gateway._airborne_uris.update(targets)
                gateway._horizontal_ready_uris.update(targets)
                gateway._refresh_cached_flight_states()
            if one_uri is not None:
                gateway._backend.hover_one(one_uri, 0.4)
            else:
                gateway._backend.hover_all(0.4)
            gateway._raise_if_cancelled(generation)
            with gateway._lock:
                gateway._airborne = bool(gateway._airborne_uris)
                gateway._state = "airborne"
                gateway._message = (
                    f"{len(targets)} 架无人机已到达目标高度；"
                    "可手动切换 Lighthouse 位置保持"
                )
        elif action == "land":
            with gateway._lock:
                gateway._state = "landing"
                gateway._message = f"{len(targets)} 架无人机正在受控降落…"
            if one_uri is not None:
                gateway._backend.land_one(one_uri, 3.0)
            else:
                gateway._backend.land_all(3.0)
            gateway._raise_if_cancelled(generation)
            with gateway._lock:
                if not gateway._emergency:
                    gateway._airborne_uris.difference_update(targets)
                    gateway._armed_uris.difference_update(targets)
                    gateway._taking_off_uris.difference_update(targets)
                    gateway._horizontal_ready_uris.difference_update(targets)
                    gateway._lighthouse_hold_uris.difference_update(targets)
                    for uri in targets:
                        gateway._lighthouse_targets_by_uri.pop(uri, None)
                    for uri in targets:
                        gateway._formation_slots_by_uri.pop(uri, None)
                    if not gateway._formation_slots_by_uri:
                        gateway._formation_plan = None
                    gateway._airborne = bool(gateway._airborne_uris)
                    gateway._armed = bool(gateway._armed_uris)
                    gateway._refresh_cached_flight_states()
                    gateway._state = "airborne" if gateway._airborne else "connected"
                    gateway._message = (
                        f"{len(targets)} 架无人机已受控降落；曾检测到低电压（仅提示）"
                        if gateway._telemetry_safety.battery_low_latched
                        else f"{len(targets)} 架无人机已受控降落；可再次解锁起飞"
                    )
        elif action == "lighthouse_hold":
            gateway._activate_lighthouse_hold(targets, generation)
            with gateway._lock:
                gateway._state = "lighthouse_hold"
                gateway._message = f"{len(targets)} 架无人机已切换至 Lighthouse 绝对位置保持"
        elif action in {"form_line", "form_triangle"}:
            gateway._activate_lighthouse_hold(targets, generation)
            shape = "line" if action == "form_line" else "triangle"
            spacing = float(command.parameters.get("spacing_m", gateway.config.formation_spacing_m))
            with gateway._lock:
                selected_uavs = tuple(gateway._uavs[uri] for uri in targets)
                altitude = sum(uav.position.z for uav in selected_uavs) / len(selected_uavs)
                gateway._state = "forming"
                gateway._message = f"正在生成{('直线' if shape == 'line' else '三角')}编队…"
            try:
                plan = gateway._formation_service.plan(
                    selected_uavs, shape=shape, spacing_m=spacing, altitude_m=altitude,
                )
            except FormationPlanningError as exc:
                raise RuntimeError(str(exc)) from exc
            # First formation is deliberately formed one aircraft at a time.
            # It prevents three initially unordered trajectories from crossing.
            for assignment in sorted(plan.assignments, key=lambda item: item.distance_m):
                gateway._raise_if_cancelled(generation)
                uri = gateway._resolve_uri(assignment.uav_id)
                yaw = gateway._uavs[uri].yaw_deg
                duration = max(1.0, assignment.distance_m / max(0.05, gateway.config.move_speed_mps))
                target = assignment.slot.position
                gateway._backend.move_one_absolute(uri, target.x, target.y, target.z, yaw, duration)
            with gateway._lock:
                gateway._formation_plan = plan
                gateway._formation_slots_by_uri = {
                    gateway._resolve_uri(item.uav_id): item.slot.slot_id for item in plan.assignments
                }
                gateway._last_formation_manual_at = time.monotonic()
                gateway._refresh_cached_flight_states()
                gateway._state = "formation_hold"
                gateway._message = (
                    f"{len(plan.assignments)} 架无人机已形成"
                    f"{('直线' if shape == 'line' else '三角')}编队，间距 {spacing:.2f}m"
                )
        elif action == "hover":
            duration = float(command.parameters.get("duration_s", 0.5))
            if one_uri is not None:
                gateway._backend.hover_one(one_uri, duration)
            else:
                gateway._backend.hover_all(duration)
        else:
            if action == "move_relative":
                dx = float(command.parameters.get("dx_m", 0.0))
                dy = float(command.parameters.get("dy_m", 0.0))
                dz = float(command.parameters.get("dz_m", 0.0))
                distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            else:
                distance = float(command.parameters.get("distance_m", gateway.config.move_distance_m))
                vectors = {
                    "forward": (distance, 0.0, 0.0),
                    "backward": (-distance, 0.0, 0.0),
                    "left": (0.0, distance, 0.0),
                    "right": (0.0, -distance, 0.0),
                }
                dx, dy, dz = vectors[action]
            if math.hypot(dx, dy) > 1e-6:
                with gateway._lock:
                    pending_takeoff = tuple(
                        uri for uri in targets if uri not in gateway._horizontal_ready_uris
                    )
                if pending_takeoff:
                    gateway._wait_for_takeoff_height(
                        gateway.config.default_height_m,
                        generation,
                        target_uris=pending_takeoff,
                        timeout_s=4.0,
                    )
                    with gateway._lock:
                        gateway._horizontal_ready_uris.update(pending_takeoff)
                gateway._wait_for_takeoff_height(
                    gateway.config.flow_min_height_m,
                    generation,
                    target_uris=targets,
                    timeout_s=4.0,
                )
            duration = max(1.0, distance / max(0.05, gateway.config.move_speed_mps))
            with gateway._lock:
                lighthouse_group = (
                    one_uri is None
                    and bool(targets)
                    and set(targets).issubset(gateway._lighthouse_hold_uris)
                )
                plan = gateway._formation_plan
            if lighthouse_group:
                if plan is not None:
                    moved_plan = plan.translated(dx, dy, dz)
                    with gateway._lock:
                        absolute_targets = formation_targets(
                            moved_plan,
                            gateway._uavs,
                            gateway._resolve_uri,
                        )
                else:
                    with gateway._lock:
                        absolute_targets = {
                            uri: (
                                gateway._uavs[uri].position.x + dx,
                                gateway._uavs[uri].position.y + dy,
                                gateway._uavs[uri].position.z + dz,
                                gateway._uavs[uri].yaw_deg,
                            )
                            for uri in targets
                        }
                    moved_plan = None
                gateway._backend.move_all_absolute(absolute_targets, duration)
                with gateway._lock:
                    if moved_plan is not None:
                        gateway._formation_plan = moved_plan
                    gateway._state = "formation_hold" if moved_plan is not None else "lighthouse_hold"
            elif one_uri is not None:
                gateway._backend.move_one_relative(one_uri, dx, dy, dz, duration)
            else:
                gateway._backend.move_all_relative(dx, dy, dz, duration)
            gateway._raise_if_cancelled(generation)
        gateway._raise_if_cancelled(generation)
        gateway._record("INFO", f"飞行指令完成：{action}")



__all__ = ["CrazyflieCommandExecutor"]
