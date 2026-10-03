"""Continuous Flow/Lighthouse setpoint control isolated from radio setup."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

@dataclass
class _FlowAircraftState:
    """Latest Flow Hover target for one connected Crazyflie."""

    scf: object
    max_height_m: float
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vertical_speed: float = 0.0
    target_height_m: float = 0.0
    takeoff_target_m: float | None = None
    position_target: tuple[float, float, float, float] | None = None
    control_mode: str = "FLOW_HOVER"
    landing: bool = False
    error: Exception | None = None


class FlowControlMixin:
    """Reusable flight-control loop; concrete backends provide cflib sessions."""

    def takeoff_all(self, height_m: float, duration_s: float) -> None:
        self._start_flow_control(height_m)

    def takeoff_one(self, uri: str, height_m: float, duration_s: float) -> None:
        self._start_flow_control_one(uri, height_m)

    def land_all(self, duration_s: float) -> None:
        self._land_flow(tuple(self._require_swarm()._cfs), duration_s)

    def land_one(self, uri: str, duration_s: float) -> None:
        self._land_flow((uri,), duration_s)

    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None:
        uris = tuple(self._require_swarm()._cfs)
        if self._uses_lighthouse_position(uris):
            targets = self._translated_lighthouse_targets(uris, dx, dy, dz)
            self.move_all_absolute(targets, duration_s)
        else:
            self._move_flow_relative(uris, dx, dy, dz, duration_s)

    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None:
        self._scf(uri)
        if self._uses_lighthouse_position((uri,)):
            x, y, z, yaw = self._translated_lighthouse_targets((uri,), dx, dy, dz)[uri]
            self.move_one_absolute(uri, x, y, z, yaw, duration_s)
        else:
            self._move_flow_relative((uri,), dx, dy, dz, duration_s)

    def start_lighthouse_hold(
        self, targets: Mapping[str, tuple[float, float, float, float]],
    ) -> None:
        if not targets:
            raise RuntimeError("Lighthouse 位置保持目标不能为空")
        self._raise_flow_error(tuple(targets))
        with self._flow_control_lock:
            for uri, target in targets.items():
                state = self._flow_states.get(uri)
                if state is None:
                    raise RuntimeError(f"{uri} Flow 起飞控制流尚未启动")
                x, y, z, yaw = (float(value) for value in target)
                state.position_target = (x, y, self._clamp_flow_height(z, uri), yaw)
                state.control_mode = "LIGHTHOUSE_HOLD"
                state.velocity = (0.0, 0.0, 0.0)
                state.vertical_speed = 0.0
                state.takeoff_target_m = None
                state.target_height_m = state.position_target[2]

    def set_lighthouse_targets(
        self, targets: Mapping[str, tuple[float, float, float, float]],
    ) -> None:
        self._set_lighthouse_targets(targets, cancel_motion=True)

    def _set_lighthouse_targets(
        self,
        targets: Mapping[str, tuple[float, float, float, float]],
        *,
        cancel_motion: bool,
    ) -> None:
        if cancel_motion:
            with self._flow_control_lock:
                self._position_motion_generation += 1
        self._raise_flow_error(tuple(targets))
        with self._flow_control_lock:
            for uri, target in targets.items():
                state = self._flow_states.get(uri)
                if state is None or state.position_target is None:
                    raise RuntimeError(f"{uri} 尚未切换到 Lighthouse 位置保持")
                x, y, z, yaw = (float(value) for value in target)
                state.position_target = (x, y, self._clamp_flow_height(z, uri), yaw)

    def move_all_absolute(
        self,
        targets: Mapping[str, tuple[float, float, float, float]],
        duration_s: float,
    ) -> None:
        self._move_lighthouse_absolute(dict(targets), duration_s)

    def move_one_absolute(
        self, uri: str, x: float, y: float, z: float, yaw: float, duration_s: float,
    ) -> None:
        self._move_lighthouse_absolute({uri: (x, y, z, yaw)}, duration_s)

    def hover_all(self, duration_s: float) -> None:
        uris = tuple(self._require_swarm()._cfs)
        self._set_flow_motion(uris, 0.0, 0.0, 0.0, 0.0)
        self._wait_flow(duration_s, uris)

    def hover_one(self, uri: str, duration_s: float) -> None:
        self._set_flow_motion((uri,), 0.0, 0.0, 0.0, 0.0)
        self._wait_flow(duration_s, (uri,))

    def stop_motion_all(self) -> None:
        uris = tuple(self._active_flow_uris())
        with self._flow_control_lock:
            lighthouse_uris = tuple(
                uri for uri in uris
                if self._flow_states[uri].position_target is not None
            )
        flow_uris = tuple(uri for uri in uris if uri not in lighthouse_uris)
        if lighthouse_uris:
            self._hold_current_lighthouse_positions(lighthouse_uris)
        if flow_uris:
            self._set_flow_motion(flow_uris, 0.0, 0.0, 0.0, 0.0)

    def stop_motion_one(self, uri: str) -> None:
        if self._uses_lighthouse_position((uri,)):
            self._hold_current_lighthouse_positions((uri,))
        else:
            self._set_flow_motion((uri,), 0.0, 0.0, 0.0, 0.0)

    def start_manual_control(self, target_height_m: float) -> None:
        self._start_flow_control(target_height_m)

    def start_manual_control_one(self, uri: str, target_height_m: float) -> None:
        self._start_flow_control_one(uri, target_height_m)

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        self._start_flow_control(self.config.default_height_m)
        self._set_manual_input(tuple(self._active_flow_uris()), vertical, forward, lateral)

    def set_manual_input_one(
        self, uri: str, vertical: float, forward: float, lateral: float,
    ) -> None:
        self._start_flow_control_one(uri, self.config.default_height_m)
        self._set_manual_input((uri,), vertical, forward, lateral)

    def _set_manual_input(
        self, uris: Sequence[str], vertical: float, forward: float, lateral: float,
    ) -> None:
        dead_zone = 0.08
        vertical = 0.0 if abs(vertical) < dead_zone else max(-1.0, min(1.0, vertical))
        forward = 0.0 if abs(forward) < dead_zone else max(-1.0, min(1.0, forward))
        lateral = 0.0 if abs(lateral) < dead_zone else max(-1.0, min(1.0, lateral))
        xy_speed = max(0.05, self.config.flow_max_horizontal_speed_mps)
        z_speed = max(0.05, self.config.flow_max_vertical_speed_mps)
        # Official body-frame convention: +X forward, +Y left.
        self._set_flow_motion(
            uris,
            forward * xy_speed,
            -lateral * xy_speed,
            vertical * z_speed,
            0.0,
        )

    def stop_motors_all(self) -> None:
        self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._stop_motors_single)

    def stop_motors_one(self, uri: str) -> None:
        self._shutdown_flow_control((uri,))
        self._stop_motors_single(self._scf(uri))
    def _start_flow_control(self, target_height_m: float) -> None:
        """Activate every aircraft and refresh them from one shared scheduler."""
        for uri in tuple(self._require_swarm()._cfs):
            self._activate_flow_aircraft(uri, target_height_m)
        self._ensure_flow_control_loop()

    def _start_flow_control_one(self, uri: str, target_height_m: float) -> None:
        self._activate_flow_aircraft(uri, target_height_m)
        self._ensure_flow_control_loop()

    def _activate_flow_aircraft(self, uri: str, target_height_m: float) -> None:
        scf = self._scf(uri)
        with self._flow_control_lock:
            existing = self._flow_states.get(uri)
            if existing is not None:
                if existing.error is not None:
                    raise RuntimeError(f"{uri} Flow Hover 控制流失败：{existing.error}")
                return
            target_height = self._clamp_flow_height(target_height_m, uri)
            self._flow_states[uri] = _FlowAircraftState(
                scf=scf,
                max_height_m=self._flow_max_heights.get(uri, self.config.flow_max_height_m),
                vertical_speed=min(0.2, self.config.flow_max_vertical_speed_mps),
                target_height_m=0.0,
                takeoff_target_m=target_height,
            )
        scf.cf.commander.send_notify_setpoint_stop()

    def _ensure_flow_control_loop(self) -> None:
        with self._flow_control_lock:
            thread = self._flow_control_thread
            if thread is not None and thread.is_alive():
                return
            if thread is not None:
                raise RuntimeError("蜂群 Flow Hover 调度器已停止；请停止电机后重新启动操控")
            self._flow_control_stop.clear()
            self._flow_control_thread = threading.Thread(
                target=self._flow_control_loop,
                name="Crazyflie-Swarm-Flow-Hover",
                daemon=True,
            )
            self._flow_control_thread.start()

    def _flow_control_loop(self) -> None:
        # Official cflib MotionCommander refreshes its Hover setpoint every 0.2 s.
        period_s = 1.0 / max(5.0, min(100.0, self.config.flow_setpoint_rate_hz))
        previous = time.monotonic()
        while not self._flow_control_stop.is_set():
            now = time.monotonic()
            elapsed = min(0.25, max(0.0, now - previous))
            previous = now
            commands: list[
                tuple[str, object, str, tuple[float, float, float, float]]
            ] = []
            with self._flow_control_lock:
                for uri, state in self._flow_states.items():
                    if state.error is not None:
                        continue
                    if state.position_target is not None:
                        commands.append((uri, state.scf, "position", state.position_target))
                        continue
                    vx, vy, yawrate = state.velocity
                    minimum = (
                        0.0 if state.takeoff_target_m is not None else
                        0.05 if state.landing else self.config.flow_min_height_m
                    )
                    maximum = max(minimum, state.max_height_m)
                    state.target_height_m = max(
                        minimum,
                        min(maximum, state.target_height_m + state.vertical_speed * elapsed),
                    )
                    if (
                        state.takeoff_target_m is not None
                        and state.target_height_m >= state.takeoff_target_m
                    ):
                        state.target_height_m = state.takeoff_target_m
                        state.takeoff_target_m = None
                        state.vertical_speed = 0.0
                    commands.append((
                        uri, state.scf, "hover",
                        (vx, vy, yawrate, state.target_height_m),
                    ))
            for uri, scf, mode, setpoint in commands:
                error: Exception | None = None
                try:
                    # Keep removal and the final radio write mutually exclusive:
                    # once targeted landing removes a state, the scheduler must
                    # never send a stale Hover setpoint that can restart motors.
                    with self._flow_control_lock:
                        current = self._flow_states.get(uri)
                        if current is None or current.scf is not scf:
                            continue
                        if mode == "position":
                            scf.cf.commander.send_position_setpoint(*setpoint)
                        else:
                            scf.cf.commander.send_hover_setpoint(*setpoint)
                except Exception as exc:
                    error = exc
                    with self._flow_control_lock:
                        state = self._flow_states.get(uri)
                        if state is not None:
                            state.error = exc
                if error is not None:
                    self._event("SAFETY", f"{uri} 飞行控制流中断：{error}")
            self._flow_control_stop.wait(period_s)

    def _set_flow_motion(
        self,
        uris: Sequence[str],
        velocity_x: float,
        velocity_y: float,
        vertical_speed: float,
        yawrate: float,
    ) -> None:
        selected = tuple(uris)
        self._raise_flow_error(selected)
        max_xy = max(0.05, self.config.flow_max_horizontal_speed_mps)
        max_z = max(0.05, self.config.flow_max_vertical_speed_mps)
        with self._flow_control_lock:
            for uri in selected:
                state = self._flow_states.get(uri)
                if state is None:
                    raise RuntimeError(f"{uri} Flow Hover 尚未启动")
                if state.position_target is not None:
                    raise RuntimeError(f"{uri} 正处于 Lighthouse 位置保持，不能发送 Flow 速度指令")
                state.velocity = (
                    max(-max_xy, min(max_xy, float(velocity_x))),
                    max(-max_xy, min(max_xy, float(velocity_y))),
                    float(yawrate),
                )
                state.vertical_speed = max(-max_z, min(max_z, float(vertical_speed)))
                if abs(vertical_speed) > 1e-6:
                    state.takeoff_target_m = None

    def _move_flow_relative(
        self,
        uris: Sequence[str],
        dx: float,
        dy: float,
        dz: float,
        duration_s: float,
    ) -> None:
        selected = tuple(uris)
        duration = max(0.1, float(duration_s))
        self._set_flow_motion(
            selected, dx / duration, dy / duration, dz / duration, 0.0,
        )
        try:
            self._wait_flow(duration, selected)
        finally:
            self._set_flow_motion(selected, 0.0, 0.0, 0.0, 0.0)

    def _move_lighthouse_absolute(
        self,
        targets: Mapping[str, tuple[float, float, float, float]],
        duration_s: float,
    ) -> None:
        selected = tuple(targets)
        self._raise_flow_error(selected)
        duration = max(0.1, float(duration_s))
        with self._flow_control_lock:
            starts: dict[str, tuple[float, float, float, float]] = {}
            self._position_motion_generation += 1
            generation = self._position_motion_generation
            for uri in selected:
                state = self._flow_states.get(uri)
                if state is None or state.position_target is None:
                    raise RuntimeError(f"{uri} 尚未切换到 Lighthouse 位置保持")
                starts[uri] = state.position_target
        steps = max(1, int(duration * max(10.0, self.config.flow_setpoint_rate_hz)))
        for step in range(1, steps + 1):
            with self._flow_control_lock:
                if generation != self._position_motion_generation:
                    return
            ratio = step / steps
            intermediate = {
                uri: tuple(
                    starts[uri][axis] + (float(targets[uri][axis]) - starts[uri][axis]) * ratio
                    for axis in range(4)
                )
                for uri in selected
            }
            self._set_lighthouse_targets(intermediate, cancel_motion=False)
            self._wait_flow(duration / steps, selected)

    def _uses_lighthouse_position(self, uris: Sequence[str]) -> bool:
        selected = tuple(uris)
        if not selected:
            return False
        with self._flow_control_lock:
            return all(
                uri in self._flow_states and self._flow_states[uri].position_target is not None
                for uri in selected
            )

    def _translated_lighthouse_targets(
        self, uris: Sequence[str], dx: float, dy: float, dz: float,
    ) -> dict[str, tuple[float, float, float, float]]:
        with self._flow_control_lock:
            return {
                uri: (
                    self._flow_states[uri].position_target[0] + dx,
                    self._flow_states[uri].position_target[1] + dy,
                    self._flow_states[uri].position_target[2] + dz,
                    self._flow_states[uri].position_target[3],
                )
                for uri in uris
                if uri in self._flow_states and self._flow_states[uri].position_target is not None
            }

    def _hold_current_lighthouse_positions(self, uris: Sequence[str]) -> None:
        targets: dict[str, tuple[float, float, float, float]] = {}
        with self._flow_control_lock:
            self._position_motion_generation += 1
            for uri in uris:
                state = self._flow_states.get(uri)
                if state is None or state.position_target is None:
                    continue
                measured = self._telemetry_cache.get(uri, {})
                targets[uri] = (
                    float(measured.get("stateEstimate.x", state.position_target[0])),
                    float(measured.get("stateEstimate.y", state.position_target[1])),
                    float(measured.get("stateEstimate.z", state.position_target[2])),
                    float(measured.get("stateEstimate.yaw", state.position_target[3])),
                )
                state.position_target = targets[uri]

    def _land_flow(self, uris: Sequence[str], duration_s: float) -> None:
        # A landing may only take over an already-running Flow Hover stream.
        # Starting a new stream here can spin the motors again after touchdown,
        # especially when a duplicate landing command was queued.
        selected = tuple(uris)
        self._raise_flow_error(selected)
        with self._flow_control_lock:
            for uri in selected:
                state = self._flow_states.get(uri)
                if state is None:
                    raise RuntimeError(f"{uri} Flow Hover 控制流未运行，拒绝执行降落")
                if state.position_target is not None:
                    state.target_height_m = state.position_target[2]
                    state.position_target = None
                    state.control_mode = "FLOW_HOVER"
                state.velocity = (0.0, 0.0, 0.0)
                state.landing = True
                state.takeoff_target_m = None
                state.vertical_speed = -max(
                    0.08,
                    (state.target_height_m - 0.05) / max(0.5, float(duration_s)),
                )
        self._wait_flow(max(0.5, float(duration_s)), selected)
        # Keep a low, stationary hover setpoint briefly so the estimator can
        # observe the touchdown before handing control back to the supervisor.
        self._set_flow_motion(selected, 0.0, 0.0, 0.0, 0.0)
        self._wait_flow(0.35, selected)
        self._shutdown_flow_control(selected, notify_setpoint_stop=False)
        swarm = self._require_swarm()
        if set(selected) == set(swarm._cfs):
            swarm.parallel_safe(self._finish_controlled_landing_single)
        else:
            for uri in selected:
                self._finish_controlled_landing_single(self._scf(uri))
        self._wait_for_landed_confirmation(selected)

    @classmethod
    def _finish_controlled_landing_single(cls, scf) -> None:
        """Finish a normal landing without invoking the latching emergency stop."""
        deadline = time.monotonic() + cls._LANDING_SETTLE_S
        while time.monotonic() < deadline:
            scf.cf.commander.send_stop_setpoint()
            time.sleep(0.1)
        scf.cf.commander.send_notify_setpoint_stop()
        platform = getattr(scf.cf, "platform", None)
        if platform is not None and hasattr(platform, "send_arming_request"):
            platform.send_arming_request(False)

    def _wait_for_landed_confirmation(
        self, target_uris: Sequence[str] | None = None,
    ) -> bool | None:
        values = self._supervisor_values(target_uris)
        if values is None:
            return None
        deadline = time.monotonic() + self._LANDING_CONFIRM_TIMEOUT_S
        while True:
            values = self._supervisor_values(target_uris)
            if values is None:
                return None
            self._raise_for_supervisor_fault(values)
            landed = all(not (value & (1 << 1)) and not (value & (1 << 4)) for value in values)
            if landed:
                return True
            if time.monotonic() >= deadline:
                raise RuntimeError("降落已停止电机，但飞控尚未确认已经落地；请勿接触桨叶")
            time.sleep(0.05)

    def _wait_flow(self, duration_s: float, uris: Sequence[str] | None = None) -> bool:
        stopped = self._flow_control_stop.wait(max(0.0, float(duration_s)))
        self._raise_flow_error(uris)
        return stopped

    def _raise_flow_error(self, uris: Sequence[str] | None = None) -> None:
        with self._flow_control_lock:
            selected = tuple(uris or self._flow_states)
            errors = [
                (uri, self._flow_states[uri].error)
                for uri in selected
                if uri in self._flow_states and self._flow_states[uri].error is not None
            ]
        if errors:
            uri, error = errors[0]
            raise RuntimeError(f"{uri} Flow Hover 控制流失败：{error}") from error

    def _shutdown_flow_control(
        self,
        target_uris: Sequence[str] | None = None,
        *,
        notify_setpoint_stop: bool = True,
    ) -> None:
        with self._flow_control_lock:
            selected = tuple(target_uris or self._flow_states)
            states = [
                self._flow_states.pop(uri) for uri in selected if uri in self._flow_states
            ]
            stop_scheduler = not self._flow_states
            thread = self._flow_control_thread
            if stop_scheduler:
                self._flow_control_stop.set()
        if stop_scheduler and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if stop_scheduler:
            with self._flow_control_lock:
                self._flow_control_thread = None
        if notify_setpoint_stop:
            for state in states:
                try:
                    state.scf.cf.commander.send_notify_setpoint_stop()
                except Exception:
                    pass

    def _clamp_flow_height(self, height_m: float, uri: str | None = None) -> float:
        minimum = max(0.05, self.config.flow_min_height_m)
        detected = self._flow_max_heights.get(uri, self._flow_detected_max_height)
        maximum = max(minimum, min(self.config.flow_max_height_m, detected))
        return max(minimum, min(maximum, float(height_m)))

    def _active_flow_uris(self) -> tuple[str, ...]:
        with self._flow_control_lock:
            return tuple(self._flow_states)

    @property
    def _flow_velocity(self) -> tuple[float, float, float]:
        with self._flow_control_lock:
            state = next(iter(self._flow_states.values()), None)
            return state.velocity if state is not None else (0.0, 0.0, 0.0)

    @property
    def _flow_vertical_speed(self) -> float:
        with self._flow_control_lock:
            state = next(iter(self._flow_states.values()), None)
            return state.vertical_speed if state is not None else 0.0

    @property
    def flow_target_height(self) -> float:
        with self._flow_control_lock:
            state = next(iter(self._flow_states.values()), None)
            return state.target_height_m if state is not None else 0.0



__all__ = ["FlowControlMixin"]
