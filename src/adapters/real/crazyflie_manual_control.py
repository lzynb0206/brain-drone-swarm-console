"""Manual Flow/Lighthouse target updates extracted from the gateway state machine."""

from __future__ import annotations

import math
import time

from src.adapters.real.crazyflie_formation_state import formation_targets


class CrazyflieManualControlCoordinator:
    def __init__(self, gateway: object) -> None:
        self.gateway = gateway

    def set_input(self, vertical: float, forward: float, lateral: float) -> None:
        """Drive Flow velocity or Lighthouse absolute targets with one stick API."""
        host = self.gateway
        values = tuple(max(-1.0, min(1.0, float(value))) for value in (vertical, forward, lateral))
        with host._lock:
            if not host._connected:
                raise RuntimeError("无人机尚未连接")
            if host._emergency:
                raise RuntimeError("系统处于急停锁定状态")
            if not host._armed:
                raise RuntimeError("请先点击“启动操控”")
            if host._motor_stop_in_progress:
                raise RuntimeError("电机正在停止")
            if host._landing_requested:
                raise RuntimeError("正在受控降落，摇杆输入已禁用")
            if host._taking_off_uris:
                raise RuntimeError("正在等待 Flow 起飞高度确认，暂不接受摇杆输入")
            if not host._airborne_uris:
                raise RuntimeError("无人机尚未确认离地")
            if math.hypot(values[1], values[2]) > 1e-6:
                not_ready = []
                for uri in host._airborne_uris:
                    telemetry = host._uavs.get(uri)
                    target = (
                        host.config.flow_min_height_m
                        if uri in host._horizontal_ready_uris
                        else host.config.default_height_m
                    )
                    tolerance = max(0.02, min(0.05, target * 0.1))
                    if telemetry is None or telemetry.position.z < target - tolerance:
                        not_ready.append(telemetry.uav_id if telemetry is not None else uri)
                    else:
                        host._horizontal_ready_uris.add(uri)
                if not_ready:
                    raise RuntimeError(
                        "等待起飞高度达到目标范围：" + ", ".join(not_ready)
                    )
            formation_plan = host._formation_plan
            formation_mode = (
                formation_plan is not None
                and host._airborne_uris
                and host._airborne_uris.issubset(host._lighthouse_hold_uris)
            )
            lighthouse_mode = (
                not formation_mode
                and bool(host._airborne_uris)
                and host._airborne_uris.issubset(host._lighthouse_hold_uris)
            )
            if formation_mode:
                now = time.monotonic()
                elapsed = max(0.0, min(0.10, now - host._last_formation_manual_at))
                host._last_formation_manual_at = now
                speed_xy = host.config.flow_max_horizontal_speed_mps
                speed_z = host.config.flow_max_vertical_speed_mps
                dx = values[1] * speed_xy * elapsed
                dy = -values[2] * speed_xy * elapsed
                dz = values[0] * speed_z * elapsed
                minimum_z = min(item.slot.position.z + dz for item in formation_plan.assignments)
                maximum_z = max(item.slot.position.z + dz for item in formation_plan.assignments)
                if minimum_z < host.config.flow_min_height_m or maximum_z > host.config.flow_max_height_m:
                    dz = 0.0
                translated_plan = formation_plan.translated(dx, dy, dz)
                targets = formation_targets(
                    translated_plan,
                    host._uavs,
                    host._resolve_uri,
                )
                host._formation_plan = translated_plan
            elif lighthouse_mode:
                now = time.monotonic()
                elapsed = max(0.0, min(0.10, now - host._last_lighthouse_manual_at))
                host._last_lighthouse_manual_at = now
                speed_xy = host.config.flow_max_horizontal_speed_mps
                speed_z = host.config.flow_max_vertical_speed_mps
                targets = {}
                for uri in host._airborne_uris:
                    telemetry = host._uavs.get(uri)
                    if telemetry is None:
                        raise RuntimeError(f"{uri} 尚未收到 Lighthouse 位置遥测")
                    current = host._lighthouse_targets_by_uri.get(uri, (
                        telemetry.position.x,
                        telemetry.position.y,
                        telemetry.position.z,
                        telemetry.yaw_deg,
                    ))
                    x, y, z, yaw = current
                    yaw_rad = math.radians(yaw)
                    # Keep the same body-frame convention as Flow Hover:
                    # +forward follows the nose and +lateral moves right.
                    dx = (
                        values[1] * math.cos(yaw_rad)
                        + values[2] * math.sin(yaw_rad)
                    ) * speed_xy * elapsed
                    dy = (
                        values[1] * math.sin(yaw_rad)
                        - values[2] * math.cos(yaw_rad)
                    ) * speed_xy * elapsed
                    z = max(
                        host.config.flow_min_height_m,
                        min(host.config.flow_max_height_m, z + values[0] * speed_z * elapsed),
                    )
                    targets[uri] = (x + dx, y + dy, z, yaw)
                host._lighthouse_targets_by_uri.update(targets)
            else:
                targets = None
        if targets is not None:
            host._backend.set_lighthouse_targets(targets)
        else:
            host._backend.set_manual_input(*values)
        with host._lock:
            host._airborne = bool(host._airborne_uris)
            if host._telemetry_safety.battery_low_latched:
                voltage = host._telemetry_safety.last_low_voltage or 0.0
                host._state = "battery_low"
                host._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
            else:
                host._state = (
                    "formation_hold" if formation_mode else
                    "lighthouse_hold" if lighthouse_mode else
                    "manual_flight"
                )
                host._message = (
                    (
                        "Lighthouse 编队：" if formation_mode else
                        "Lighthouse 单机：" if lighthouse_mode else
                        "Flow Hover："
                    )
                    + "升降 {:+.0f}% / 前后 {:+.0f}% / 左右 {:+.0f}%"
                    .format(values[0] * 100, values[1] * 100, values[2] * 100)
                )


__all__ = ["CrazyflieManualControlCoordinator"]

