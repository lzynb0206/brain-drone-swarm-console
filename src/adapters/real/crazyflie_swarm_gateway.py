"""Application-facing Crazyflie swarm state machine and command scheduler."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from typing import Callable, Sequence

from src.domain import (
    DeviceStatus,
    FormationPlan,
    SwarmCommand,
    SwarmStatus,
    UAVTelemetry,
)
from src.services.formation_service import FormationService
from src.adapters.real.crazyflie_contracts import CrazyflieBackend, CrazyflieSwarmConfig
from src.adapters.real.crazyflie_cflib_backend import CflibBackend
from src.adapters.real.crazyflie_command_executor import CrazyflieCommandExecutor
from src.adapters.real.crazyflie_command_queue import CrazyflieCommandQueue
from src.adapters.real.crazyflie_event_journal import CrazyflieEventJournal
from src.adapters.real.crazyflie_emergency import CrazyflieEmergencyCoordinator
from src.adapters.real.crazyflie_formation_state import (
    formation_targets,
    lighthouse_targets_from_telemetry,
    rebase_formation_plan,
)
from src.adapters.real.crazyflie_safety import CrazyflieSafetyPolicy
from src.adapters.real.crazyflie_telemetry import CrazyflieTelemetryMapper
from src.adapters.real.crazyflie_telemetry_safety import CrazyflieTelemetrySafetyMonitor

class _CommandCancelled(RuntimeError):
    """Internal signal used when a high-priority motor stop cancels queued work."""


class CrazyflieSwarmGateway:
    """Queue-based real Flow Deck gateway; no synthetic aircraft are created."""

    _ALLOWED_ACTIONS = {
        "takeoff", "land", "forward", "backward", "left", "right", "move_relative",
        "hover", "led_test", "lighthouse_hold", "form_line", "form_triangle",
    }

    def __init__(self, config: CrazyflieSwarmConfig, backend: CrazyflieBackend | None = None) -> None:
        self.config = config
        self._backend = backend or CflibBackend(config)
        self._telemetry_mapper = CrazyflieTelemetryMapper()
        self._safety_policy = CrazyflieSafetyPolicy()
        self._lock = threading.RLock()
        self._command_queue = CrazyflieCommandQueue(maxsize=20)
        self._command_executor = CrazyflieCommandExecutor(self)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._command_idle = threading.Event()
        self._command_idle.set()
        self._motor_stop_in_progress = False
        self._motor_stop_backend_complete = False
        self._motor_stop_error: Exception | None = None
        self._landing_requested = False
        self._telemetry_safety = CrazyflieTelemetrySafetyMonitor()
        self._emergency_coordinator = CrazyflieEmergencyCoordinator(self)
        self._state = "disconnected"
        self._message = "尚未连接"
        self._connected = False
        self._armed = False
        self._airborne = False
        self._armed_uris: set[str] = set()
        self._taking_off_uris: set[str] = set()
        self._airborne_uris: set[str] = set()
        self._horizontal_ready_uris: set[str] = set()
        self._landing_uris: set[str] = set()
        self._lighthouse_hold_uris: set[str] = set()
        self._lighthouse_targets_by_uri: dict[str, tuple[float, float, float, float]] = {}
        self._formation_slots_by_uri: dict[str, str] = {}
        self._formation_plan: FormationPlan | None = None
        self._formation_service = FormationService()
        self._last_formation_manual_at = time.monotonic()
        self._last_lighthouse_manual_at = time.monotonic()
        self._emergency = False
        self._uavs: dict[str, UAVTelemetry] = {}
        self._journal = CrazyflieEventJournal()

    @property
    def _command_generation(self) -> int:
        return self._command_queue.generation

    @_command_generation.setter
    def _command_generation(self, value: int) -> None:
        self._command_queue.generation = int(value)

    def connect(self) -> None:
        if not self.config.uris:
            raise RuntimeError("未配置 BDS_CRAZYFLIE_URIS，不能连接 Crazyflie")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._emergency:
                raise RuntimeError("急停锁仍未复位；请先给无人机断电重启，再点击急停复位")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机停止操作尚未完成，请稍候")
            self._stop_event.clear()
            self._command_queue.reset()
            self._command_idle.set()
            self._armed = False
            self._airborne = False
            self._armed_uris.clear()
            self._taking_off_uris.clear()
            self._airborne_uris.clear()
            self._horizontal_ready_uris.clear()
            self._lighthouse_hold_uris.clear()
            self._lighthouse_targets_by_uri.clear()
            self._landing_uris.clear()
            self._formation_slots_by_uri.clear()
            self._formation_plan = None
            self._landing_requested = False
            self._uavs.clear()
            self._telemetry_safety.reset()
            self._state = "connecting"
            self._message = f"正在连接 {len(self.config.uris)} 架 Crazyflie…"
            self._thread = threading.Thread(target=self._run, name="Crazyflie-Swarm-Gateway", daemon=True)
            self._thread.start()

    def disconnect(self) -> None:
        should_hold = False
        with self._lock:
            self._command_generation += 1
            self._discard_pending_commands("连接断开")
            should_hold = self._connected and self._airborne and not self._emergency
        if should_hold:
            try:
                self._backend.stop_motion_all()
            except Exception as exc:
                self._record("WARN", f"断开前停止移动失败：{exc}")
        self._stop_event.set()
        self._command_queue.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=8.0)

    def status(self) -> SwarmStatus:
        with self._lock:
            has_lighthouse = bool(self.config.uris) and all(
                uri in self._uavs and self._uavs[uri].lighthouse_deck_present
                for uri in self.config.uris
            )
            return SwarmStatus(
                backend="lighthouse_flow" if has_lighthouse else "flowdeck",
                state=self._state,
                connected=self._connected,
                armed=self._armed,
                control_enabled=self.config.control_enabled,
                message=self._message,
                airborne=self._airborne,
                emergency=self._emergency,
            )

    def configure_uris(self, uris: Sequence[str]) -> None:
        normalized = tuple(str(uri).strip() for uri in uris if str(uri).strip())
        if not normalized or len(set(normalized)) != len(normalized):
            raise RuntimeError("URI 不能为空且不能重复")
        with self._lock:
            if self._connected or (self._thread is not None and self._thread.is_alive()):
                raise RuntimeError("请先断开蜂群，再修改 URI")
            self.config = replace(self.config, uris=normalized)
            self._uavs.clear()
            self._message = f"已配置 {len(normalized)} 架 Crazyflie，尚未连接"

    def add_event_listener(self, listener: Callable[[str, str], None]) -> None:
        self._journal.add_event_listener(listener)

    def add_command_listener(self, listener: Callable[[str, str, SwarmCommand], None]) -> None:
        self._journal.add_command_listener(listener)

    def arm(self) -> None:
        with self._lock:
            if not self.config.control_enabled:
                raise RuntimeError("当前为只读遥测模式；请显式设置 BDS_HARDWARE_CONTROL_ENABLED=true")
            if not self._connected:
                raise RuntimeError("蜂群尚未连接")
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态，必须重新连接后才能解锁")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机停止操作尚未完成，请稍候再解锁")
            if self._landing_requested:
                raise RuntimeError("正在受控降落，不能重复启动 Flow Hover")
            if self._taking_off_uris:
                raise RuntimeError("Flow Hover 正在起飞，请等待高度确认")
            self._validate_ready()
            confirmed = self._backend.arm_all()
            if confirmed is False:
                raise RuntimeError("飞控拒绝解锁，请检查电池、机体姿态和飞控锁定状态")
            try:
                self._backend.start_manual_control(self.config.default_height_m)
            except Exception:
                # Never leave the firmware armed when the continuous
                # setpoint stream could not be established.
                self._backend.disarm_all()
                raise
            self._armed = True
            self._airborne = False
            self._armed_uris = set(self.config.uris)
            self._taking_off_uris = set(self.config.uris)
            self._airborne_uris.clear()
            self._horizontal_ready_uris.difference_update(self.config.uris)
            self._refresh_cached_flight_states()
            self._state = "taking_off"
            count = len(self.config.uris)
            self._message = (
                f"{count} 架 Crazyflie 正在渐升至 {self.config.default_height_m:.2f}m；"
                "松杆保持悬停"
            )
            self._record("INFO", f"{count} 架实机控制已由操作员显式解锁")
            generation = self._command_generation
        threading.Thread(
            target=self._monitor_flow_takeoff,
            args=(tuple(self.config.uris), self.config.default_height_m, generation),
            name="Crazyflie-Flow-Takeoff-Monitor",
            daemon=True,
        ).start()

    def takeoff_height_m(self) -> float:
        with self._lock:
            return self.config.default_height_m

    def set_takeoff_height(self, height_m: float) -> None:
        height = float(height_m)
        if not math.isfinite(height):
            raise RuntimeError("目标高度必须是有效数字")
        with self._lock:
            if self._armed or self._airborne:
                raise RuntimeError("Flow Hover 已启动，不能修改启动目标高度")
            minimum = self.config.flow_min_height_m
            maximum = self.config.flow_max_height_m
            if not minimum <= height <= maximum:
                raise RuntimeError(f"目标高度必须在 {minimum:.2f}–{maximum:.2f}m 之间")
            self.config = replace(self.config, default_height_m=height)
            self._message = f"启动目标高度已设置为 {height:.2f}m"
        self._record("INFO", f"Flow Hover 启动目标高度设置为 {height:.2f}m")

    def move_distance_m(self) -> float:
        with self._lock:
            return self.config.move_distance_m

    def set_move_distance(self, distance_m: float) -> None:
        distance = float(distance_m)
        if not math.isfinite(distance) or not 0.01 <= distance <= 1.0:
            raise RuntimeError("单次移动距离必须在 0.01–1.00m 之间")
        with self._lock:
            self.config = replace(self.config, move_distance_m=distance)
        self._record("INFO", f"SSVEP 单次移动距离设置为 {distance:.2f}m")

    def disarm(self) -> None:
        with self._lock:
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态，请先重启无人机并复位")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机停止操作尚未完成")
            if self._airborne:
                raise RuntimeError("无人机仍在飞行；请先降落，必要时使用电机立即停转")
            self._backend.disarm_all()
            self._armed_uris.clear()
            self._taking_off_uris.clear()
            self._airborne_uris.clear()
            self._horizontal_ready_uris.clear()
            self._lighthouse_hold_uris.clear()
            self._lighthouse_targets_by_uri.clear()
            self._armed = False
            self._airborne = False
            self._refresh_cached_flight_states()
            self._message = "实机控制已锁定"
            self._record("INFO", "实机控制已锁定")

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        with self._lock:
            return tuple(self._uavs[uri] for uri in self.config.uris if uri in self._uavs)

    def send_command(self, command: SwarmCommand) -> None:
        action = command.action.strip().lower()
        if action not in self._ALLOWED_ACTIONS:
            raise RuntimeError(f"不支持的蜂群动作：{command.action}")
        with self._lock:
            if not self._connected:
                raise RuntimeError("蜂群尚未连接")
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机正在停止，暂不接受飞行指令")
            if action != "led_test" and not self.config.control_enabled:
                raise RuntimeError("当前为只读遥测模式；请显式设置 BDS_HARDWARE_CONTROL_ENABLED=true")
            targets = self._command_uris(command) if action != "led_test" else tuple(self.config.uris)
            landing_targets = set(targets) & self._landing_uris
            if landing_targets:
                raise RuntimeError("目标无人机降落已在进行，暂不接受新的飞行指令")
            if action == "land":
                if not targets or any(uri not in self._airborne_uris for uri in targets):
                    raise RuntimeError("Flow Hover 尚未启动，不能执行受控降落")
            elif action not in {"led_test", "takeoff"} and any(
                uri not in self._armed_uris for uri in targets
            ):
                raise RuntimeError("实机控制尚未解锁")
            self._validate_command(command)
            if action == "land":
                self._landing_uris.update(targets)
                self._landing_requested = bool(self._landing_uris)
                self._refresh_cached_flight_states()
        try:
            self._command_queue.enqueue(command)
        except RuntimeError:
            if action == "land":
                with self._lock:
                    self._landing_uris.difference_update(targets)
                    self._landing_requested = bool(self._landing_uris)
                    self._refresh_cached_flight_states()
            raise

    def stop_motion(self) -> None:
        with self._lock:
            if not self._connected:
                raise RuntimeError("蜂群尚未连接")
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态")
            if not self._armed or not self._airborne:
                raise RuntimeError("无人机尚未起飞并解锁")
            self._command_generation += 1
            self._discard_pending_commands("操作员停止移动")
        try:
            self._backend.stop_motion_all()
        except Exception as exc:
            with self._lock:
                self._armed = False
                self._state = "flight_error"
                self._message = f"停止移动未确认：{exc}；请立即现场处置"
            raise RuntimeError(self._message) from exc
        with self._lock:
            if not self._emergency:
                self._lighthouse_targets_by_uri.update(
                    lighthouse_targets_from_telemetry(
                        self._lighthouse_hold_uris,
                        self._uavs,
                    )
                )
                self._last_lighthouse_manual_at = time.monotonic()
                self._formation_plan = rebase_formation_plan(
                    self._formation_plan,
                    self._uavs,
                    self._resolve_uri,
                )
                if self._telemetry_safety.battery_low_latched:
                    voltage = self._telemetry_safety.last_low_voltage or 0.0
                    self._state = "battery_low"
                    self._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
                else:
                    self._state = "airborne"
                    self._message = "已停止移动并保持当前 Flow Hover 高度"
        self._record("INFO", "操作员停止移动并进入保持状态")

    def stop_motors(self) -> None:
        self._emergency_coordinator.stop_motors()

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        """Drive Flow velocity or Lighthouse absolute targets with one stick API."""
        values = tuple(max(-1.0, min(1.0, float(value))) for value in (vertical, forward, lateral))
        with self._lock:
            if not self._connected:
                raise RuntimeError("无人机尚未连接")
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态")
            if not self._armed:
                raise RuntimeError("请先点击“启动操控”")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机正在停止")
            if self._landing_requested:
                raise RuntimeError("正在受控降落，摇杆输入已禁用")
            if self._taking_off_uris:
                raise RuntimeError("正在等待 Flow 起飞高度确认，暂不接受摇杆输入")
            if not self._airborne_uris:
                raise RuntimeError("无人机尚未确认离地")
            if math.hypot(values[1], values[2]) > 1e-6:
                not_ready = []
                for uri in self._airborne_uris:
                    telemetry = self._uavs.get(uri)
                    target = (
                        self.config.flow_min_height_m
                        if uri in self._horizontal_ready_uris
                        else self.config.default_height_m
                    )
                    tolerance = max(0.02, min(0.05, target * 0.1))
                    if telemetry is None or telemetry.position.z < target - tolerance:
                        not_ready.append(telemetry.uav_id if telemetry is not None else uri)
                    else:
                        self._horizontal_ready_uris.add(uri)
                if not_ready:
                    raise RuntimeError(
                        "等待起飞高度达到目标范围：" + ", ".join(not_ready)
                    )
            formation_plan = self._formation_plan
            formation_mode = (
                formation_plan is not None
                and self._airborne_uris
                and self._airborne_uris.issubset(self._lighthouse_hold_uris)
            )
            lighthouse_mode = (
                not formation_mode
                and bool(self._airborne_uris)
                and self._airborne_uris.issubset(self._lighthouse_hold_uris)
            )
            if formation_mode:
                now = time.monotonic()
                elapsed = max(0.0, min(0.10, now - self._last_formation_manual_at))
                self._last_formation_manual_at = now
                speed_xy = self.config.flow_max_horizontal_speed_mps
                speed_z = self.config.flow_max_vertical_speed_mps
                dx = values[1] * speed_xy * elapsed
                dy = -values[2] * speed_xy * elapsed
                dz = values[0] * speed_z * elapsed
                minimum_z = min(item.slot.position.z + dz for item in formation_plan.assignments)
                maximum_z = max(item.slot.position.z + dz for item in formation_plan.assignments)
                if minimum_z < self.config.flow_min_height_m or maximum_z > self.config.flow_max_height_m:
                    dz = 0.0
                translated_plan = formation_plan.translated(dx, dy, dz)
                targets = formation_targets(
                    translated_plan,
                    self._uavs,
                    self._resolve_uri,
                )
                self._formation_plan = translated_plan
            elif lighthouse_mode:
                now = time.monotonic()
                elapsed = max(0.0, min(0.10, now - self._last_lighthouse_manual_at))
                self._last_lighthouse_manual_at = now
                speed_xy = self.config.flow_max_horizontal_speed_mps
                speed_z = self.config.flow_max_vertical_speed_mps
                targets = {}
                for uri in self._airborne_uris:
                    telemetry = self._uavs.get(uri)
                    if telemetry is None:
                        raise RuntimeError(f"{uri} 尚未收到 Lighthouse 位置遥测")
                    current = self._lighthouse_targets_by_uri.get(uri, (
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
                        self.config.flow_min_height_m,
                        min(self.config.flow_max_height_m, z + values[0] * speed_z * elapsed),
                    )
                    targets[uri] = (x + dx, y + dy, z, yaw)
                self._lighthouse_targets_by_uri.update(targets)
            else:
                targets = None
        if targets is not None:
            self._backend.set_lighthouse_targets(targets)
        else:
            self._backend.set_manual_input(*values)
        with self._lock:
            self._airborne = bool(self._airborne_uris)
            if self._telemetry_safety.battery_low_latched:
                voltage = self._telemetry_safety.last_low_voltage or 0.0
                self._state = "battery_low"
                self._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
            else:
                self._state = (
                    "formation_hold" if formation_mode else
                    "lighthouse_hold" if lighthouse_mode else
                    "manual_flight"
                )
                self._message = (
                    (
                        "Lighthouse 编队：" if formation_mode else
                        "Lighthouse 单机：" if lighthouse_mode else
                        "Flow Hover："
                    )
                    + "升降 {:+.0f}% / 前后 {:+.0f}% / 左右 {:+.0f}%"
                    .format(values[0] * 100, values[1] * 100, values[2] * 100)
                )

    def emergency_stop(self) -> None:
        self._emergency_coordinator.emergency_stop()

    def reset_after_emergency_reboot(self) -> None:
        self._emergency_coordinator.reset_after_reboot()

    def events(self, level: str | None = None) -> list[str]:
        return self._journal.formatted_events(level)

    def _run(self) -> None:
        try:
            self._backend.connect(self.config.uris, self._on_telemetry, self._on_backend_event)
            with self._lock:
                self._connected = True
                self._state = "connected"
                count = len(self.config.uris)
                if count == 1:
                    self._message = "1 架 Crazyflie 遥测已连接；Flow Hover 尚未启动"
                else:
                    self._message = f"{count} 架 Crazyflie 遥测已连接；等待启动蜂群"
            self._record("INFO", f"已连接 {len(self.config.uris)} 架 Crazyflie")
            while not self._stop_event.is_set():
                try:
                    command = self._command_queue.get(timeout=0.2)
                except TimeoutError:
                    continue
                if command is None:
                    break
                generation, command = command
                self._command_idle.clear()
                try:
                    self._execute(command, generation)
                    self._notify_command("COMPLETED", "硬件动作执行完成", command)
                except _CommandCancelled:
                    self._notify_command("CANCELLED", "指令被停止操作取消", command)
                except Exception as exc:
                    with self._lock:
                        if not self._emergency:
                            self._state = "flight_error"
                        self._message = f"指令执行失败：{exc}"
                    self._record("ERROR", self._message)
                    self._notify_command("FAILED", str(exc), command)
                finally:
                    if command.action.strip().lower() == "land":
                        with self._lock:
                            self._landing_uris.difference_update(self._command_uris(command))
                            self._landing_requested = bool(self._landing_uris)
                            self._refresh_cached_flight_states()
                    self._command_idle.set()
                    self._try_finalize_motor_stop()
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._message = f"Crazyflie 连接失败：{exc}"
            self._record("ERROR", self._message)
        finally:
            if self._airborne and not self._emergency:
                try:
                    with self._lock:
                        airborne_uris = tuple(
                            uri for uri in self.config.uris if uri in self._airborne_uris
                        )
                    if set(airborne_uris) == set(self.config.uris):
                        self._backend.land_all(3.0)
                    else:
                        for uri in airborne_uris:
                            self._backend.land_one(uri, 3.0)
                except Exception as exc:
                    self._record("ERROR", f"断开前自动降落失败：{exc}")
            try:
                self._backend.disconnect()
            except Exception as exc:
                self._record("WARN", f"关闭 Crazyflie 链路失败：{exc}")
            with self._lock:
                self._connected = False
                self._armed = False
                self._airborne = False
                self._armed_uris.clear()
                self._taking_off_uris.clear()
                self._airborne_uris.clear()
                self._horizontal_ready_uris.clear()
                self._landing_uris.clear()
                self._lighthouse_hold_uris.clear()
                self._lighthouse_targets_by_uri.clear()
                self._formation_slots_by_uri.clear()
                self._formation_plan = None
                self._landing_requested = False
                self._refresh_cached_flight_states()
                if not self._emergency and self._state != "error":
                    self._state = "disconnected"
                    self._message = "蜂群连接已断开"

    def _execute(self, command: SwarmCommand, generation: int) -> None:
        self._command_executor.execute(command, generation)

    def _raise_if_cancelled(self, generation: int) -> None:
        with self._lock:
            cancelled = generation != self._command_generation or self._emergency
        if cancelled:
            raise _CommandCancelled()

    def _wait_for_takeoff_height(
        self,
        target_height_m: float,
        generation: int,
        *,
        target_uris: Sequence[str] | None = None,
        timeout_s: float,
    ) -> None:
        """等待实时高度进入目标容差，防止贴地发送水平位移。"""
        target = max(self.config.flow_min_height_m, float(target_height_m))
        tolerance = max(0.02, min(0.05, target * 0.1))
        required_height = target - tolerance
        deadline = time.monotonic() + max(0.5, float(timeout_s))
        last_height: float | None = None
        selected = tuple(target_uris or self.config.uris)
        while time.monotonic() < deadline:
            self._raise_if_cancelled(generation)
            with self._lock:
                heights = [
                    self._uavs[uri].position.z for uri in selected if uri in self._uavs
                ]
            if heights:
                last_height = min(heights)
                if last_height >= required_height:
                    return
            time.sleep(0.05)
        detail = "无遥测" if last_height is None else f"当前 {last_height:.2f}m"
        raise RuntimeError(
            f"起飞高度未达到目标范围：目标 {target:.2f}m，{detail}"
        )

    def _activate_lighthouse_hold(self, targets: Sequence[str], generation: int) -> None:
        with self._lock:
            missing = [self._uavs[uri].uav_id for uri in targets if not self._uavs[uri].lighthouse_ready]
        if missing:
            raise RuntimeError("Lighthouse 尚未定位：" + ", ".join(missing))
        self._wait_for_takeoff_height(
            self.config.default_height_m,
            generation,
            target_uris=targets,
            timeout_s=4.0,
        )
        with self._lock:
            absolute_targets = {
                uri: (
                    self._uavs[uri].position.x,
                    self._uavs[uri].position.y,
                    self._uavs[uri].position.z,
                    self._uavs[uri].yaw_deg,
                )
                for uri in targets
            }
        self._backend.start_lighthouse_hold(absolute_targets)
        with self._lock:
            self._lighthouse_hold_uris.update(targets)
            self._lighthouse_targets_by_uri.update(absolute_targets)
            self._last_lighthouse_manual_at = time.monotonic()
            self._refresh_cached_flight_states()

    def _try_activate_lighthouse_hold(
        self, targets: Sequence[str], generation: int,
    ) -> bool:
        with self._lock:
            if set(targets).issubset(self._lighthouse_hold_uris):
                return True
            ready = bool(targets) and all(
                uri in self._uavs and self._uavs[uri].lighthouse_ready for uri in targets
            )
        if not ready:
            return False
        self._activate_lighthouse_hold(targets, generation)
        return True

    def _monitor_flow_takeoff(
        self, targets: Sequence[str], target_height_m: float, generation: int,
    ) -> None:
        """Confirm real lift-off before enabling flight safety and manual motion.

        Flow quality can briefly fall to zero while the aircraft is still on
        the floor and the motors are spinning.  Treating that interval as
        normal flight previously fired the latching emergency stop before the
        height setpoint had time to reach the requested value.
        """
        try:
            self._wait_for_takeoff_height(
                target_height_m,
                generation,
                target_uris=targets,
                timeout_s=max(5.0, target_height_m / 0.2 + 3.0),
            )
            with self._lock:
                self._raise_if_cancelled(generation)
                self._taking_off_uris.difference_update(targets)
                self._airborne_uris.update(targets)
                self._horizontal_ready_uris.update(targets)
                self._airborne = bool(self._airborne_uris)
                self._refresh_cached_flight_states()
                self._state = "airborne"
                self._message = (
                    f"Flow Hover 已到达 {target_height_m:.2f}m；"
                    "可点击 Lighthouse保持切换定位模式"
                )
            self._record("INFO", "Flow 起飞高度已由实时遥测确认")
        except _CommandCancelled:
            return
        except RuntimeError as exc:
            self._record("ERROR", f"Flow 起飞未完成：{exc}")
            try:
                if set(targets) == set(self.config.uris):
                    self._backend.stop_motors_all()
                else:
                    for uri in targets:
                        self._backend.stop_motors_one(uri)
            except Exception as stop_exc:
                with self._lock:
                    self._state = "stop_error"
                    self._message = f"起飞失败且电机停止未确认：{stop_exc}"
                self._record("ERROR", self._message)
                return
            with self._lock:
                self._taking_off_uris.difference_update(targets)
                self._armed_uris.difference_update(targets)
                self._airborne_uris.difference_update(targets)
                self._horizontal_ready_uris.difference_update(targets)
                self._armed = bool(self._armed_uris)
                self._airborne = bool(self._airborne_uris)
                self._refresh_cached_flight_states()
                self._state = "connected"
                self._message = f"起飞未达到目标高度，电机已停止：{exc}"

    def _discard_pending_commands(self, reason: str) -> None:
        discarded = self._command_queue.drain()
        with self._lock:
            for command in discarded:
                if command.action.strip().lower() == "land":
                    self._landing_uris.difference_update(self._command_uris(command))
            self._landing_requested = bool(self._landing_uris)
            self._refresh_cached_flight_states()
        for command in discarded:
            self._notify_command("CANCELLED", reason, command)

    def _validate_command(self, command: SwarmCommand) -> None:
        action = command.action.strip().lower()
        if action == "led_test":
            return
        targets = self._command_uris(command)
        if action == "land":
            self._validate_ready(targets)
            if any(uri not in self._airborne_uris for uri in targets):
                raise RuntimeError("Flow Hover 尚未启动，不能执行受控降落")
            return
        self._validate_ready(targets)
        if action == "takeoff":
            if any(uri in self._airborne_uris for uri in targets):
                raise RuntimeError("无人机已经处于飞行状态")
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            self._safety_policy.validate_takeoff_height(
                height, self.config.flow_min_height_m, self.config.flow_max_height_m,
            )
            return
        if action == "hover":
            if any(uri not in self._airborne_uris for uri in targets):
                raise RuntimeError("无人机尚未起飞")
            return
        if any(uri not in self._airborne_uris for uri in targets):
            raise RuntimeError("无人机尚未起飞")
        if action in {"lighthouse_hold", "form_line", "form_triangle"}:
            if action.startswith("form_") and command.uav_id:
                raise RuntimeError("编队命令必须发送给整个蜂群")
            not_ready = [self._uavs[uri].uav_id for uri in targets if not self._uavs[uri].lighthouse_ready]
            if not_ready:
                raise RuntimeError("Lighthouse Deck/基站定位尚未就绪：" + ", ".join(not_ready))
            if action.startswith("form_") and len(targets) < 2:
                raise RuntimeError("编队至少需要 2 架无人机")
            if action == "form_triangle" and len(targets) != 3:
                raise RuntimeError("三角编队要求恰好连接 3 架无人机")
            return
        if action == "move_relative":
            try:
                dx = float(command.parameters.get("dx_m", 0.0))
                dy = float(command.parameters.get("dy_m", 0.0))
                dz = float(command.parameters.get("dz_m", 0.0))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("相对位移参数必须是有效数字") from exc
            self._safety_policy.validate_relative_movement(dx, dy, dz)

    def _resolve_uri(self, uav_id: str) -> str:
        if uav_id in self.config.uris:
            return uav_id
        for uri, telemetry in self._uavs.items():
            if telemetry.uav_id == uav_id:
                return uri
        raise RuntimeError(f"找不到无人机：{uav_id}")

    def _refresh_cached_flight_states(self) -> None:
        for uri, telemetry in tuple(self._uavs.items()):
            control_mode = (
                "FORMATION" if uri in self._formation_slots_by_uri else
                "LIGHTHOUSE_HOLD" if uri in self._lighthouse_hold_uris else
                "TAKING_OFF" if uri in self._taking_off_uris else
                "FLOW_HOVER" if uri in self._airborne_uris else "GROUND"
            )
            self._uavs[uri] = replace(
                telemetry,
                task=(
                    "降落" if uri in self._landing_uris else
                    "起飞" if uri in self._taking_off_uris else
                    "飞行" if uri in self._airborne_uris else "待命"
                ),
                armed=uri in self._armed_uris,
                airborne=uri in self._airborne_uris,
                control_mode=control_mode,
                formation_slot_id=self._formation_slots_by_uri.get(uri, ""),
            )

    def _command_uris(self, command: SwarmCommand) -> tuple[str, ...]:
        if command.uav_id:
            return (self._resolve_uri(command.uav_id),)
        return tuple(self.config.uris)

    def _validate_ready(self, target_uris: Sequence[str] | None = None) -> None:
        selected = tuple(target_uris or self.config.uris)
        if any(uri not in self._uavs for uri in selected):
            raise RuntimeError("尚未收到 Crazyflie 遥测")
        now = time.time()
        for uri in selected:
            uav = self._uavs[uri]
            if now - uav.last_update_timestamp > self.config.telemetry_timeout_s:
                raise RuntimeError(f"{uav.uav_id} 遥测超时")
            if not uav.positioning_ok:
                raise RuntimeError(f"{uav.uav_id} 定位源尚未就绪（Flow/Lighthouse 均不可用）")

    def _on_telemetry(self, uri: str, timestamp: float, data: dict[str, float]) -> None:
        facts = self._telemetry_mapper.map(uri, timestamp, data)
        position = facts.position
        voltage = facts.voltage
        name = facts.name
        newly_locked = False
        with self._lock:
            uri_airborne = uri in self._airborne_uris
            decision = self._telemetry_safety.evaluate(
                uri,
                facts,
                airborne=uri_airborne,
                config=self.config,
            )
            control_mode = (
                "FORMATION" if uri in self._formation_slots_by_uri else
                "LIGHTHOUSE_HOLD" if uri in self._lighthouse_hold_uris else
                "TAKING_OFF" if uri in self._taking_off_uris else
                "FLOW_HOVER" if uri_airborne else "GROUND"
            )
            if decision.battery_low_active:
                self._state = "battery_low"
                self._message = (
                    decision.low_voltage_warning
                    or f"电量过低：{voltage:.2f}V（仅提示）"
                )

            self._uavs[uri] = UAVTelemetry(
                uav_id=f"CF-{name}", battery=facts.battery_percent,
                speed_mps=facts.speed_mps, altitude_m=position.z,
                task=(
                    "降落" if uri in self._landing_uris else
                    "起飞" if uri in self._taking_off_uris else
                    "飞行" if uri_airborne else "待命"
                ),
                status=DeviceStatus.NORMAL if facts.positioning_ok else DeviceStatus.WARNING,
                position=position, battery_voltage=voltage,
                positioning_ok=facts.positioning_ok,
                last_update_timestamp=facts.timestamp,
                armed=uri in self._armed_uris,
                airborne=uri_airborne,
                positioning_source=facts.positioning_source,
                flow_deck_present=facts.flow_deck_present,
                lighthouse_deck_present=facts.lighthouse_deck_present,
                lighthouse_ready=facts.lighthouse_ready,
                active_base_stations=facts.active_base_stations,
                yaw_deg=facts.yaw_deg,
                control_mode=control_mode,
                formation_slot_id=self._formation_slots_by_uri.get(uri, ""),
            )
            if decision.firmware_locked:
                newly_locked = self._emergency_coordinator.lock_from_firmware()
        if newly_locked:
            self._record("ERROR", f"{name} 飞控报告锁定状态，需要断电重启")
        if decision.low_voltage_warning is not None:
            self._record("WARN", decision.low_voltage_warning)
        if decision.positioning_failure_trip:
            self._trip_emergency(
                f"{name} Flow/Lighthouse 定位连续 {self.config.flow_failure_samples} 次未就绪"
            )

    def _on_backend_event(self, level: str, message: str) -> None:
        if level.upper() == "SAFETY":
            self._trip_emergency(message)
        else:
            self._record(level, message)

    def _trip_emergency(self, reason: str) -> None:
        self._emergency_coordinator.trip(reason)

    def _try_finalize_motor_stop(self) -> None:
        self._emergency_coordinator.try_finalize_motor_stop()

    def _record(self, level: str, message: str) -> None:
        self._journal.record(level, message)

    def _notify_command(self, status: str, message: str, command: SwarmCommand) -> None:
        self._journal.notify_command(status, message, command)

__all__ = ["CflibBackend", "CrazyflieBackend", "CrazyflieSwarmConfig", "CrazyflieSwarmGateway"]
