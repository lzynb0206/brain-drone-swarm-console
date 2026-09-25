"""Crazyflie Flow Deck adapter with multi-aircraft telemetry and safety gates.

The UI only enqueues commands.  All cflib work runs on one background thread so
Qt never waits for radio connection, estimator reset, or a timed movement.

Multiple Crazyflies can be connected and monitored at the same time.  One
shared scheduler refreshes an independent Flow Hover target for every active
aircraft, supporting both group commands and commands addressed by UAV id.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from src.domain import (
    DeviceStatus,
    FormationAssignment,
    FormationPlan,
    FormationSlot,
    Position,
    SwarmCommand,
    SwarmStatus,
    UAVTelemetry,
)
from src.services.formation_service import FormationPlanningError, FormationService


TelemetryCallback = Callable[[str, float, dict[str, float]], None]
EventCallback = Callable[[str, str], None]
class CrazyflieBackend(Protocol):
    def connect(self, uris: tuple[str, ...], telemetry: TelemetryCallback, event: EventCallback) -> None: ...
    def disconnect(self) -> None: ...
    def arm_all(self) -> bool | None: ...
    def arm_one(self, uri: str) -> bool | None: ...
    def disarm_all(self) -> None: ...
    def disarm_one(self, uri: str) -> None: ...
    def takeoff_all(self, height_m: float, duration_s: float) -> None: ...
    def takeoff_one(self, uri: str, height_m: float, duration_s: float) -> None: ...
    def land_all(self, duration_s: float) -> None: ...
    def land_one(self, uri: str, duration_s: float) -> None: ...
    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def hover_all(self, duration_s: float) -> None: ...
    def hover_one(self, uri: str, duration_s: float) -> None: ...
    def stop_motion_all(self) -> None: ...
    def stop_motion_one(self, uri: str) -> None: ...
    def stop_motors_all(self) -> None: ...
    def stop_motors_one(self, uri: str) -> None: ...
    def start_manual_control(self, target_height_m: float) -> None: ...
    def start_manual_control_one(self, uri: str, target_height_m: float) -> None: ...
    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None: ...
    def set_manual_input_one(self, uri: str, vertical: float, forward: float, lateral: float) -> None: ...
    def emergency_stop_all(self) -> None: ...
    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def start_lighthouse_hold(self, targets: Mapping[str, tuple[float, float, float, float]]) -> None: ...
    def set_lighthouse_targets(self, targets: Mapping[str, tuple[float, float, float, float]]) -> None: ...
    def move_all_absolute(self, targets: Mapping[str, tuple[float, float, float, float]], duration_s: float) -> None: ...
    def move_one_absolute(self, uri: str, x: float, y: float, z: float, yaw: float, duration_s: float) -> None: ...
    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None: ...


@dataclass(frozen=True)
class CrazyflieSwarmConfig:
    uris: tuple[str, ...]
    control_enabled: bool = False
    default_height_m: float = 0.4
    move_distance_m: float = 0.25
    move_speed_mps: float = 0.15
    minimum_battery_voltage: float = 3.2
    battery_low_confirm_s: float = 1.0
    telemetry_timeout_s: float = 1.5
    flow_failure_samples: int = 3
    cache_dir: Path = Path(".cache/cflib")
    flow_min_height_m: float = 0.2
    flow_max_height_m: float = 2.0
    flow_max_horizontal_speed_mps: float = 0.5
    flow_max_vertical_speed_mps: float = 0.5
    flow_setpoint_rate_hz: float = 5.0
    formation_spacing_m: float = 0.4

    @classmethod
    def from_environment(cls) -> "CrazyflieSwarmConfig":
        uris = tuple(value.strip() for value in os.getenv("BDS_CRAZYFLIE_URIS", "").split(",") if value.strip())
        return cls(
            uris=uris,
            control_enabled=os.getenv("BDS_HARDWARE_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
            default_height_m=float(os.getenv("BDS_SWARM_TAKEOFF_HEIGHT", "0.4")),
            move_distance_m=float(os.getenv("BDS_SWARM_MOVE_DISTANCE", "0.25")),
            move_speed_mps=float(os.getenv("BDS_SWARM_MOVE_SPEED", "0.15")),
            minimum_battery_voltage=float(os.getenv("BDS_SWARM_MIN_VBAT", "3.2")),
            battery_low_confirm_s=float(os.getenv("BDS_SWARM_LOW_VOLTAGE_CONFIRM_S", "1.0")),
            flow_failure_samples=int(os.getenv("BDS_FLOW_FAILURE_SAMPLES", "3")),
            cache_dir=Path(os.getenv("BDS_CFLIB_CACHE", ".cache/cflib")).expanduser(),
            flow_min_height_m=float(os.getenv("BDS_FLOW_MIN_HEIGHT", "0.2")),
            flow_max_height_m=float(os.getenv("BDS_FLOW_MAX_HEIGHT", "2.0")),
            flow_max_horizontal_speed_mps=float(os.getenv("BDS_FLOW_MAX_XY_SPEED", "0.5")),
            flow_max_vertical_speed_mps=float(os.getenv("BDS_FLOW_MAX_Z_SPEED", "0.5")),
            flow_setpoint_rate_hz=float(os.getenv("BDS_FLOW_SETPOINT_RATE", "5")),
            formation_spacing_m=float(os.getenv("BDS_FORMATION_SPACING", "0.4")),
        )


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
        self._lock = threading.RLock()
        self._commands: queue.Queue[tuple[int, SwarmCommand] | None] = queue.Queue(maxsize=20)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._command_idle = threading.Event()
        self._command_idle.set()
        self._command_generation = 0
        self._motor_stop_in_progress = False
        self._motor_stop_backend_complete = False
        self._motor_stop_error: Exception | None = None
        self._landing_requested = False
        self._battery_low_latched = False
        self._battery_low_since: dict[str, float] = {}
        self._last_low_voltage: float | None = None
        self._state = "disconnected"
        self._message = "尚未连接"
        self._connected = False
        self._armed = False
        self._airborne = False
        self._armed_uris: set[str] = set()
        self._airborne_uris: set[str] = set()
        self._horizontal_ready_uris: set[str] = set()
        self._landing_uris: set[str] = set()
        self._lighthouse_hold_uris: set[str] = set()
        self._formation_slots_by_uri: dict[str, str] = {}
        self._formation_plan: FormationPlan | None = None
        self._formation_service = FormationService()
        self._last_formation_manual_at = time.monotonic()
        self._emergency = False
        self._uavs: dict[str, UAVTelemetry] = {}
        self._flow_failure_counts: dict[str, int] = {}
        self._events: list[tuple[str, str, float]] = []
        self._event_listeners: list[Callable[[str, str], None]] = []
        self._command_listeners: list[Callable[[str, str, SwarmCommand], None]] = []

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
            self._commands = queue.Queue(maxsize=20)
            self._command_idle.set()
            self._armed = False
            self._airborne = False
            self._armed_uris.clear()
            self._airborne_uris.clear()
            self._horizontal_ready_uris.clear()
            self._landing_uris.clear()
            self._lighthouse_hold_uris.clear()
            self._formation_slots_by_uri.clear()
            self._formation_plan = None
            self._landing_requested = False
            self._uavs.clear()
            self._flow_failure_counts.clear()
            self._battery_low_latched = False
            self._battery_low_since.clear()
            self._last_low_voltage = None
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
        try:
            self._commands.put_nowait(None)
        except queue.Full:
            pass
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
        with self._lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def add_command_listener(self, listener: Callable[[str, str, SwarmCommand], None]) -> None:
        with self._lock:
            if listener not in self._command_listeners:
                self._command_listeners.append(listener)

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
            self._airborne = True
            self._armed_uris = set(self.config.uris)
            self._airborne_uris = set(self.config.uris)
            self._horizontal_ready_uris.difference_update(self.config.uris)
            self._refresh_cached_flight_states()
            self._state = "manual_flight"
            count = len(self.config.uris)
            self._message = (
                f"{count} 架 Crazyflie 正在渐升至 {self.config.default_height_m:.2f}m；"
                "松杆保持悬停"
            )
            self._record("INFO", f"{count} 架实机控制已由操作员显式解锁")
            generation = self._command_generation
        threading.Thread(
            target=self._auto_lighthouse_after_takeoff,
            args=(tuple(self.config.uris), self.config.default_height_m, generation),
            name="Crazyflie-Auto-Lighthouse-Transition",
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
            self._airborne_uris.clear()
            self._horizontal_ready_uris.clear()
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
            self._commands.put_nowait((self._command_generation, command))
        except queue.Full as exc:
            if action == "land":
                with self._lock:
                    self._landing_uris.difference_update(targets)
                    self._landing_requested = bool(self._landing_uris)
                    self._refresh_cached_flight_states()
            raise RuntimeError("飞行指令队列已满，请停止继续操作") from exc

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
                self._rebase_formation_to_telemetry()
                if self._battery_low_latched:
                    voltage = self._last_low_voltage or 0.0
                    self._state = "battery_low"
                    self._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
                else:
                    self._state = "airborne"
                    self._message = "已停止移动并保持当前 Flow Hover 高度"
        self._record("INFO", "操作员停止移动并进入保持状态")

    def stop_motors(self) -> None:
        with self._lock:
            if not self._connected:
                raise RuntimeError("蜂群尚未连接")
            if self._emergency:
                raise RuntimeError("当前已处于硬件急停锁定状态")
            if self._motor_stop_in_progress:
                return
            self._command_generation += 1
            self._discard_pending_commands("操作员停止电机")
            self._armed = False
            self._armed_uris.clear()
            self._motor_stop_in_progress = True
            self._motor_stop_backend_complete = False
            self._motor_stop_error = None
            self._state = "stopping_motors"
            self._message = "正在发送电机停止指令…"
            self._record("WARN", "操作员触发可恢复的电机立即停转")
        threading.Thread(target=self._stop_motors_backend, name="Crazyflie-Motor-Stop", daemon=True).start()

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        """Feed normalized virtual-stick values to the official Flow Hover stream."""
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
                targets = self._formation_targets(translated_plan)
                self._formation_plan = translated_plan
            else:
                targets = None
        if targets is not None:
            self._backend.set_lighthouse_targets(targets)
        else:
            self._backend.set_manual_input(*values)
        with self._lock:
            self._airborne = True
            self._airborne_uris.update(self._armed_uris)
            if self._battery_low_latched:
                voltage = self._last_low_voltage or 0.0
                self._state = "battery_low"
                self._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
            else:
                self._state = "formation_hold" if targets is not None else "manual_flight"
                self._message = (
                    ("Lighthouse 编队：" if targets is not None else "Flow Hover：")
                    + "升降 {:+.0f}% / 前后 {:+.0f}% / 左右 {:+.0f}%"
                    .format(values[0] * 100, values[1] * 100, values[2] * 100)
                )

    def emergency_stop(self) -> None:
        with self._lock:
            self._command_generation += 1
            self._discard_pending_commands("紧急停止")
            self._emergency = True
            self._armed = False
            self._airborne = False
            self._armed_uris.clear()
            self._airborne_uris.clear()
            self._horizontal_ready_uris.clear()
            self._landing_uris.clear()
            self._lighthouse_hold_uris.clear()
            self._formation_slots_by_uri.clear()
            self._formation_plan = None
            self._landing_requested = False
            self._refresh_cached_flight_states()
            self._state = "emergency"
            self._message = "急停指令已发送；需要现场确认设备状态"
            connected = self._connected
            self._record("ERROR", "操作员触发蜂群急停")
        if connected:
            threading.Thread(target=self._emergency_backend, name="Crazyflie-Emergency", daemon=True).start()
        else:
            raise RuntimeError("蜂群未连接，无法获得硬件急停回执")

    def reset_after_emergency_reboot(self) -> None:
        with self._lock:
            if self._connected or (self._thread is not None and self._thread.is_alive()):
                raise RuntimeError("请先断开链路，并先给无人机断电重启")
            self._emergency = False
            if self._motor_stop_in_progress:
                raise RuntimeError("电机停止线程尚未结束，请稍候再复位")
            self._state = "disconnected"
            self._message = "急停锁已复位；等待重新连接"
            self._record("INFO", "操作员确认无人机已重启，上位机急停锁复位")

    def events(self, level: str | None = None) -> list[str]:
        with self._lock:
            values = list(self._events)
        if level:
            values = [value for value in values if value[0] == level]
        return [f"{time.strftime('%H:%M:%S', time.localtime(stamp))}  {kind}  {message}" for kind, message, stamp in values]

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
                    command = self._commands.get(timeout=0.2)
                except queue.Empty:
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
                self._airborne_uris.clear()
                self._horizontal_ready_uris.clear()
                self._landing_uris.clear()
                self._lighthouse_hold_uris.clear()
                self._formation_slots_by_uri.clear()
                self._formation_plan = None
                self._landing_requested = False
                self._refresh_cached_flight_states()
                if not self._emergency and self._state != "error":
                    self._state = "disconnected"
                    self._message = "蜂群连接已断开"

    def _execute(self, command: SwarmCommand, generation: int) -> None:
        self._raise_if_cancelled(generation)
        with self._lock:
            self._validate_command(command)
        action = command.action.strip().lower()
        targets = self._command_uris(command) if action != "led_test" else tuple(self.config.uris)
        # A command without uav_id is a group command even when the current
        # group happens to contain one aircraft.  This keeps group and targeted
        # execution semantics stable as aircraft are added or removed.
        one_uri = targets[0] if command.uav_id and len(targets) == 1 else None
        if action == "led_test":
            self._backend.set_led_all(0, 80, 255, float(command.parameters.get("duration_s", 1.0)))
        elif action == "takeoff":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            speed = max(0.05, self.config.move_speed_mps)
            duration = max(2.0, height / speed)
            with self._lock:
                needs_arming = tuple(uri for uri in targets if uri not in self._armed_uris)
            if len(needs_arming) == len(self.config.uris) and set(needs_arming) == set(self.config.uris):
                confirmed = self._backend.arm_all()
            else:
                confirmations = [self._backend.arm_one(uri) for uri in needs_arming]
                confirmed = False if any(value is False for value in confirmations) else None
            if confirmed is False:
                raise RuntimeError("飞控拒绝解锁，请检查电池、机体姿态和飞控锁定状态")
            with self._lock:
                self._armed_uris.update(targets)
                self._airborne_uris.update(targets)
                self._armed = bool(self._armed_uris)
                self._airborne = bool(self._airborne_uris)
                self._refresh_cached_flight_states()
                self._state = "taking_off"
                self._message = f"{len(targets)} 架无人机正在起飞并进入保持状态…"
            if one_uri is not None:
                self._backend.takeoff_one(one_uri, height, duration)
            else:
                self._backend.takeoff_all(height, duration)
            self._raise_if_cancelled(generation)
            self._wait_for_takeoff_height(
                height, generation, target_uris=targets, timeout_s=duration + 2.0,
            )
            with self._lock:
                self._horizontal_ready_uris.update(targets)
            if one_uri is not None:
                self._backend.hover_one(one_uri, 0.4)
            else:
                self._backend.hover_all(0.4)
            self._raise_if_cancelled(generation)
            transitioned = self._try_activate_lighthouse_hold(targets, generation)
            with self._lock:
                self._airborne_uris.update(targets)
                self._airborne = bool(self._airborne_uris)
                self._state = "lighthouse_hold" if transitioned else "airborne"
                self._message = (
                    f"{len(targets)} 架无人机已起飞并切换至 Lighthouse 位置保持"
                    if transitioned else f"{len(targets)} 架无人机已起飞并保持目标位置"
                )
        elif action == "land":
            with self._lock:
                self._state = "landing"
                self._message = f"{len(targets)} 架无人机正在受控降落…"
            if one_uri is not None:
                self._backend.land_one(one_uri, 3.0)
            else:
                self._backend.land_all(3.0)
            self._raise_if_cancelled(generation)
            with self._lock:
                if not self._emergency:
                    self._airborne_uris.difference_update(targets)
                    self._armed_uris.difference_update(targets)
                    self._horizontal_ready_uris.difference_update(targets)
                    self._lighthouse_hold_uris.difference_update(targets)
                    for uri in targets:
                        self._formation_slots_by_uri.pop(uri, None)
                    if not self._formation_slots_by_uri:
                        self._formation_plan = None
                    self._airborne = bool(self._airborne_uris)
                    self._armed = bool(self._armed_uris)
                    self._refresh_cached_flight_states()
                    self._state = "airborne" if self._airborne else "connected"
                    self._message = (
                        f"{len(targets)} 架无人机已受控降落；曾检测到低电压（仅提示）"
                        if self._battery_low_latched
                        else f"{len(targets)} 架无人机已受控降落；可再次解锁起飞"
                    )
        elif action == "lighthouse_hold":
            self._activate_lighthouse_hold(targets, generation)
            with self._lock:
                self._state = "lighthouse_hold"
                self._message = f"{len(targets)} 架无人机已切换至 Lighthouse 绝对位置保持"
        elif action in {"form_line", "form_triangle"}:
            self._activate_lighthouse_hold(targets, generation)
            shape = "line" if action == "form_line" else "triangle"
            spacing = float(command.parameters.get("spacing_m", self.config.formation_spacing_m))
            with self._lock:
                selected_uavs = tuple(self._uavs[uri] for uri in targets)
                altitude = sum(uav.position.z for uav in selected_uavs) / len(selected_uavs)
                self._state = "forming"
                self._message = f"正在生成{('直线' if shape == 'line' else '三角')}编队…"
            try:
                plan = self._formation_service.plan(
                    selected_uavs, shape=shape, spacing_m=spacing, altitude_m=altitude,
                )
            except FormationPlanningError as exc:
                raise RuntimeError(str(exc)) from exc
            # First formation is deliberately formed one aircraft at a time.
            # It prevents three initially unordered trajectories from crossing.
            for assignment in sorted(plan.assignments, key=lambda item: item.distance_m):
                self._raise_if_cancelled(generation)
                uri = self._resolve_uri(assignment.uav_id)
                yaw = self._uavs[uri].yaw_deg
                duration = max(1.0, assignment.distance_m / max(0.05, self.config.move_speed_mps))
                target = assignment.slot.position
                self._backend.move_one_absolute(uri, target.x, target.y, target.z, yaw, duration)
            with self._lock:
                self._formation_plan = plan
                self._formation_slots_by_uri = {
                    self._resolve_uri(item.uav_id): item.slot.slot_id for item in plan.assignments
                }
                self._last_formation_manual_at = time.monotonic()
                self._refresh_cached_flight_states()
                self._state = "formation_hold"
                self._message = (
                    f"{len(plan.assignments)} 架无人机已形成"
                    f"{('直线' if shape == 'line' else '三角')}编队，间距 {spacing:.2f}m"
                )
        elif action == "hover":
            duration = float(command.parameters.get("duration_s", 0.5))
            if one_uri is not None:
                self._backend.hover_one(one_uri, duration)
            else:
                self._backend.hover_all(duration)
        else:
            if action == "move_relative":
                dx = float(command.parameters.get("dx_m", 0.0))
                dy = float(command.parameters.get("dy_m", 0.0))
                dz = float(command.parameters.get("dz_m", 0.0))
                distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            else:
                distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
                vectors = {
                    "forward": (distance, 0.0, 0.0),
                    "backward": (-distance, 0.0, 0.0),
                    "left": (0.0, distance, 0.0),
                    "right": (0.0, -distance, 0.0),
                }
                dx, dy, dz = vectors[action]
            if math.hypot(dx, dy) > 1e-6:
                with self._lock:
                    pending_takeoff = tuple(
                        uri for uri in targets if uri not in self._horizontal_ready_uris
                    )
                if pending_takeoff:
                    self._wait_for_takeoff_height(
                        self.config.default_height_m,
                        generation,
                        target_uris=pending_takeoff,
                        timeout_s=4.0,
                    )
                    with self._lock:
                        self._horizontal_ready_uris.update(pending_takeoff)
                self._wait_for_takeoff_height(
                    self.config.flow_min_height_m,
                    generation,
                    target_uris=targets,
                    timeout_s=4.0,
                )
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            with self._lock:
                lighthouse_group = (
                    one_uri is None
                    and bool(targets)
                    and set(targets).issubset(self._lighthouse_hold_uris)
                )
                plan = self._formation_plan
            if lighthouse_group:
                if plan is not None:
                    moved_plan = plan.translated(dx, dy, dz)
                    absolute_targets = self._formation_targets(moved_plan)
                else:
                    with self._lock:
                        absolute_targets = {
                            uri: (
                                self._uavs[uri].position.x + dx,
                                self._uavs[uri].position.y + dy,
                                self._uavs[uri].position.z + dz,
                                self._uavs[uri].yaw_deg,
                            )
                            for uri in targets
                        }
                    moved_plan = None
                self._backend.move_all_absolute(absolute_targets, duration)
                with self._lock:
                    if moved_plan is not None:
                        self._formation_plan = moved_plan
                    self._state = "formation_hold" if moved_plan is not None else "lighthouse_hold"
            elif one_uri is not None:
                self._backend.move_one_relative(one_uri, dx, dy, dz, duration)
            else:
                self._backend.move_all_relative(dx, dy, dz, duration)
            self._raise_if_cancelled(generation)
        self._raise_if_cancelled(generation)
        self._record("INFO", f"飞行指令完成：{action}")

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
            self.config.flow_min_height_m,
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

    def _auto_lighthouse_after_takeoff(
        self, targets: Sequence[str], target_height_m: float, generation: int,
    ) -> None:
        try:
            self._wait_for_takeoff_height(
                target_height_m,
                generation,
                target_uris=targets,
                timeout_s=max(5.0, target_height_m / max(0.05, self.config.move_speed_mps) + 3.0),
            )
            if not self._try_activate_lighthouse_hold(targets, generation):
                self._record("INFO", "Lighthouse 尚未就绪，继续使用 Flow Hover")
                return
            with self._lock:
                if not self._emergency:
                    self._horizontal_ready_uris.update(targets)
                    self._state = "lighthouse_hold"
                    self._message = f"{len(targets)} 架无人机已自动切换至 Lighthouse 位置保持"
            self._record("INFO", "Flow 起飞完成，已自动切换至 Lighthouse 绝对位置控制")
        except (_CommandCancelled, RuntimeError) as exc:
            if not isinstance(exc, _CommandCancelled):
                self._record("WARN", f"自动切换 Lighthouse 未完成：{exc}")

    def _formation_targets(
        self, plan: FormationPlan,
    ) -> dict[str, tuple[float, float, float, float]]:
        with self._lock:
            return {
                self._resolve_uri(item.uav_id): (
                    item.slot.position.x,
                    item.slot.position.y,
                    item.slot.position.z,
                    self._uavs[self._resolve_uri(item.uav_id)].yaw_deg,
                )
                for item in plan.assignments
            }

    def _rebase_formation_to_telemetry(self) -> None:
        plan = self._formation_plan
        if plan is None:
            return
        assignments: list[FormationAssignment] = []
        positions: list[Position] = []
        for item in plan.assignments:
            uri = self._resolve_uri(item.uav_id)
            position = self._uavs[uri].position
            positions.append(position)
            assignments.append(FormationAssignment(
                item.uav_id,
                FormationSlot(item.slot.slot_id, position),
                0.0,
            ))
        center = Position(
            sum(position.x for position in positions) / len(positions),
            sum(position.y for position in positions) / len(positions),
            sum(position.z for position in positions) / len(positions),
        )
        self._formation_plan = FormationPlan(
            plan.shape, center, plan.spacing_m, tuple(assignments),
        )

    def _discard_pending_commands(self, reason: str) -> None:
        sentinel_seen = False
        discarded: list[SwarmCommand] = []
        while True:
            try:
                item = self._commands.get_nowait()
            except queue.Empty:
                break
            if item is None:
                sentinel_seen = True
            else:
                _generation, command = item
                discarded.append(command)
        if sentinel_seen:
            try:
                self._commands.put_nowait(None)
            except queue.Full:
                pass
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
            if not self.config.flow_min_height_m <= height <= self.config.flow_max_height_m:
                raise RuntimeError(
                    f"Flow Hover 目标高度必须在 {self.config.flow_min_height_m:.1f}–"
                    f"{self.config.flow_max_height_m:.1f}m 之间"
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
            if not all(math.isfinite(value) for value in (dx, dy, dz)):
                raise RuntimeError("相对位移参数必须是有效数字")
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance <= 1e-6:
                raise RuntimeError("相对位移距离不能为 0")
            if distance > 5.0:
                raise RuntimeError("单次相对位移不能超过 5.0m")

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
                "FLOW_HOVER" if uri in self._airborne_uris else "GROUND"
            )
            self._uavs[uri] = replace(
                telemetry,
                task=(
                    "降落" if uri in self._landing_uris else
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
        position = Position(float(data.get("stateEstimate.x", 0.0)), float(data.get("stateEstimate.y", 0.0)),
                            float(data.get("stateEstimate.z", 0.0)))
        speed = math.sqrt(sum(float(data.get(key, 0.0)) ** 2 for key in ("stateEstimate.vx", "stateEstimate.vy", "stateEstimate.vz")))
        voltage = float(data.get("pm.vbat", 0.0))
        battery = max(0.0, min(100.0, (voltage - 3.0) / 1.2 * 100.0))
        local_flow_ok = (
            all(key in data for key in ("stateEstimate.x", "stateEstimate.y", "stateEstimate.z"))
            and all(math.isfinite(value) for value in (position.x, position.y, position.z))
            and 0.0 < float(data.get("range.zrange", 0.0)) < 4000.0
            and float(data.get("motion.squal", 0.0)) > 0.0
        )
        flow_deck_present = bool(int(data.get("deck.bcFlow", 0.0))) or bool(
            int(data.get("deck.bcFlow2", 0.0))
        )
        lighthouse_deck_present = bool(int(data.get("deck.bcLighthouse4", 0.0)))
        lighthouse_status = int(data.get("lighthouse.status", 0.0))
        base_station_mask = int(data.get("lighthouse.bsActive", 0.0))
        active_base_stations = base_station_mask.bit_count()
        lighthouse_ready = (
            lighthouse_deck_present and lighthouse_status == 2 and active_base_stations > 0
        )
        positioning_ok = lighthouse_ready or local_flow_ok
        positioning_source = (
            "lighthouse" if lighthouse_ready else "flow" if local_flow_ok else "unavailable"
        )
        supervisor_info = int(data.get("supervisor.info", 0))
        firmware_locked = bool(supervisor_info & (1 << 6))
        name = uri.rsplit("/", 1)[-1]
        should_trip = False
        newly_locked = False
        low_voltage_warning: str | None = None
        with self._lock:
            now = time.monotonic()
            threshold = self.config.minimum_battery_voltage
            uri_airborne = uri in self._airborne_uris
            control_mode = (
                "FORMATION" if uri in self._formation_slots_by_uri else
                "LIGHTHOUSE_HOLD" if uri in self._lighthouse_hold_uris else
                "FLOW_HOVER" if uri_airborne else "GROUND"
            )
            if uri_airborne and voltage < threshold:
                started = self._battery_low_since.setdefault(uri, now)
                confirmed = now - started >= max(0.0, self.config.battery_low_confirm_s)
                if confirmed and not self._battery_low_latched:
                    self._battery_low_latched = True
                    self._last_low_voltage = voltage
                    self._state = "battery_low"
                    self._message = f"电量过低：{voltage:.2f}V，请立即受控降落"
                    low_voltage_warning = self._message
                elif self._battery_low_latched:
                    self._last_low_voltage = min(self._last_low_voltage or voltage, voltage)
                    self._state = "battery_low"
                    self._message = f"电量过低：{voltage:.2f}V（仅提示）"
            elif voltage >= threshold + 0.05:
                # Require a fresh continuous low interval after voltage recovers
                # instead of triggering on one transient motor-load sample.
                self._battery_low_since.pop(uri, None)

            self._uavs[uri] = UAVTelemetry(
                uav_id=f"CF-{name}", battery=battery, speed_mps=speed, altitude_m=position.z,
                task=(
                    "降落" if uri in self._landing_uris else
                    "飞行" if uri_airborne else "待命"
                ),
                status=DeviceStatus.NORMAL if positioning_ok else DeviceStatus.WARNING,
                position=position, battery_voltage=voltage, positioning_ok=positioning_ok,
                last_update_timestamp=float(timestamp) if timestamp > 1_000_000_000 else time.time(),
                armed=uri in self._armed_uris,
                airborne=uri_airborne,
                positioning_source=positioning_source,
                flow_deck_present=flow_deck_present,
                lighthouse_deck_present=lighthouse_deck_present,
                lighthouse_ready=lighthouse_ready,
                active_base_stations=active_base_stations,
                yaw_deg=float(data.get("stateEstimate.yaw", 0.0)),
                control_mode=control_mode,
                formation_slot_id=self._formation_slots_by_uri.get(uri, ""),
            )
            if uri_airborne:
                self._flow_failure_counts[uri] = (
                    0 if (local_flow_ok or lighthouse_ready) else self._flow_failure_counts.get(uri, 0) + 1
                )
                should_trip = self._flow_failure_counts[uri] >= self.config.flow_failure_samples
            else:
                self._flow_failure_counts[uri] = 0
            if firmware_locked and not self._emergency:
                self._command_generation += 1
                self._discard_pending_commands("飞控进入锁定状态")
                self._emergency = True
                self._armed = False
                self._airborne = False
                self._armed_uris.clear()
                self._airborne_uris.clear()
                self._horizontal_ready_uris.clear()
                self._landing_uris.clear()
                self._lighthouse_hold_uris.clear()
                self._formation_slots_by_uri.clear()
                self._formation_plan = None
                self._landing_requested = False
                self._refresh_cached_flight_states()
                self._state = "emergency"
                self._message = "飞控处于锁定状态；请给无人机断电重启后再复位"
                newly_locked = True
        if newly_locked:
            self._record("ERROR", f"{name} 飞控报告锁定状态，需要断电重启")
        if low_voltage_warning is not None:
            self._record("WARN", low_voltage_warning)
        if should_trip:
            self._trip_emergency(
                f"{name} Flow/Lighthouse 定位连续 {self.config.flow_failure_samples} 次未就绪"
            )

    def _on_backend_event(self, level: str, message: str) -> None:
        if level.upper() == "SAFETY":
            self._trip_emergency(message)
        else:
            self._record(level, message)

    def _trip_emergency(self, reason: str) -> None:
        with self._lock:
            if self._emergency or not self._airborne:
                self._record("WARN", f"安全事件（未在飞行）：{reason}")
                return
        self._record("ERROR", f"安全门控触发急停：{reason}")
        try:
            self.emergency_stop()
        except RuntimeError as exc:
            self._record("ERROR", f"自动急停未送达：{exc}")

    def _emergency_backend(self) -> None:
        try:
            self._backend.emergency_stop_all()
            self._record("ERROR", "硬件急停调用已完成；请现场确认电机状态")
        except Exception as exc:
            self._record("ERROR", f"硬件急停调用失败：{exc}")

    def _stop_motors_backend(self) -> None:
        error: Exception | None = None
        try:
            self._backend.stop_motors_all()
            self._record("WARN", "电机停止调用已完成")
        except Exception as exc:
            error = exc
            self._record("ERROR", f"电机停止调用失败：{exc}")
        finally:
            with self._lock:
                self._motor_stop_backend_complete = True
                self._motor_stop_error = error
            self._try_finalize_motor_stop()

    def _try_finalize_motor_stop(self) -> None:
        with self._lock:
            if (
                not self._motor_stop_in_progress
                or not self._motor_stop_backend_complete
                or not self._command_idle.is_set()
            ):
                return
            error = self._motor_stop_error
            self._motor_stop_in_progress = False
            self._motor_stop_backend_complete = False
            self._motor_stop_error = None
            if self._emergency:
                return
            if error is None:
                self._airborne = False
                self._armed_uris.clear()
                self._airborne_uris.clear()
                self._horizontal_ready_uris.clear()
                self._landing_uris.clear()
                self._lighthouse_hold_uris.clear()
                self._formation_slots_by_uri.clear()
                self._formation_plan = None
                self._landing_requested = False
                self._refresh_cached_flight_states()
                self._state = "connected"
                self._message = (
                    "电机已停止；曾检测到低电压（仅提示）"
                    if self._battery_low_latched
                    else "电机已停止；重新起飞前需要再次解锁"
                )
            else:
                self._state = "stop_error"
                self._message = f"电机停止未确认：{error}；请立即现场处置"

    def _record(self, level: str, message: str) -> None:
        with self._lock:
            self._events.insert(0, (level.upper(), message, time.time()))
            del self._events[200:]
            listeners = tuple(self._event_listeners)
        for listener in listeners:
            try:
                listener(level.upper(), message)
            except Exception:
                pass

    def _notify_command(self, status: str, message: str, command: SwarmCommand) -> None:
        with self._lock:
            listeners = tuple(self._command_listeners)
        for listener in listeners:
            try:
                listener(status, message, command)
            except Exception:
                pass


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


class CflibBackend:
    """Thin cflib implementation isolated for hardware-free unit testing."""

    _PARAM_DOWNLOAD_TIMEOUT_S = 12.0
    _LANDING_SETTLE_S = 1.0
    _LANDING_CONFIRM_TIMEOUT_S = 5.0

    def __init__(self, config: CrazyflieSwarmConfig) -> None:
        self.config = config
        self.cache_dir = config.cache_dir
        self._swarm = None
        self._logs: list[object] = []
        self._telemetry_cache: dict[str, dict[str, float]] = {}
        self._callbacks: list[tuple[object, object]] = []
        self._last_quality_warning: dict[str, float] = {}
        self._event: EventCallback = lambda _level, _message: None
        self._flow_control_lock = threading.RLock()
        self._flow_control_stop = threading.Event()
        self._flow_control_thread: threading.Thread | None = None
        self._flow_states: dict[str, _FlowAircraftState] = {}
        self._flow_max_heights: dict[str, float] = {}
        self._flow_detected_max_height = config.flow_max_height_m
        self._detected_decks: dict[str, dict[str, bool]] = {}
        self._supervisor_info_reliable = False
        self._position_motion_generation = 0

    def connect(self, uris: tuple[str, ...], telemetry: TelemetryCallback, event: EventCallback) -> None:
        try:
            import cflib.crtp
            from cflib.crazyflie.log import LogConfig
            from cflib.crazyflie.swarm import CachedCfFactory, Swarm
        except ImportError as exc:
            raise RuntimeError("未安装 cflib；请在 Python 3.10+ 环境执行 pip install cflib") from exc
        self._event = event
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cflib.crtp.init_drivers()
        self._swarm = Swarm(set(uris), factory=CachedCfFactory(rw_cache=str(self.cache_dir)))
        self._swarm.open_links()
        self._supervisor_info_reliable = self._detect_supervisor_info_support()
        if not self._supervisor_info_reliable:
            event(
                "WARN",
                "Crazyflie 固件协议早于 v7 或版本未知；解锁使用官方兼容请求，"
                "不等待不可靠的 supervisor.can_be_armed 状态位",
            )
        try:
            self._swarm.parallel_safe(self._configure_single)
        except Exception as exc:
            # cflib.parallel_safe wraps the useful device error in a generic
            # "One or more threads..." exception. Surface the original cause
            # so the operator can see which hardware check actually failed.
            cause = exc.__cause__
            if cause is not None:
                raise RuntimeError(str(cause)) from cause
            raise
        # Reset without the blocking convergence loop. Readiness is decided
        # from live Flow Deck telemetry by the gateway.
        self._swarm.parallel_safe(self._reset_estimator_single)
        for uri, scf in self._swarm._cfs.items():  # cflib Swarm currently exposes connected CFs here.
            lost_callback = lambda _uri, message, current_uri=uri: event(
                "SAFETY", f"{current_uri} 无线电链路丢失：{message}",
            )
            scf.cf.connection_lost.add_callback(lost_callback)
            self._callbacks.append((scf.cf.connection_lost, lost_callback))
            quality_callback = lambda quality, current_uri=uri: self._handle_link_quality(current_uri, quality)
            scf.cf.link_statistics.link_quality_updated.add_callback(quality_callback)
            self._callbacks.append((scf.cf.link_statistics.link_quality_updated, quality_callback))
            pose_log = LogConfig(name=f"pose-{uri[-2:]}", period_in_ms=100)
            for variable in ("stateEstimate.x", "stateEstimate.y", "stateEstimate.z",
                             "stateEstimate.vx", "stateEstimate.vy", "stateEstimate.vz"):
                pose_log.add_variable(variable, "float")
            health_log = LogConfig(name=f"health-{uri[-2:]}", period_in_ms=200)
            health_log.add_variable("pm.vbat", "float")
            health_log.add_variable("stateEstimate.yaw", "float")
            for variable, variable_type in (
                ("range.zrange", "uint16_t"),
                ("motion.squal", "uint8_t"),
                ("motion.deltaX", "int16_t"),
                ("motion.deltaY", "int16_t"),
            ):
                if self._log_variable_exists(scf, variable):
                    health_log.add_variable(variable, variable_type)
            if self._log_variable_exists(scf, "supervisor.info"):
                health_log.add_variable("supervisor.info", "uint16_t")
            for variable, variable_type in (
                ("lighthouse.status", "uint8_t"),
                ("lighthouse.bsActive", "uint16_t"),
            ):
                if self._log_variable_exists(scf, variable):
                    health_log.add_variable(variable, variable_type)
            for log in (pose_log, health_log):
                log.data_received_cb.add_callback(
                    lambda timestamp, data, _log, current_uri=uri: self._handle_log(
                        current_uri, timestamp, data, telemetry,
                    )
                )
                log.error_cb.add_callback(
                    lambda _log, message, current_uri=uri: event("ERROR", f"{current_uri} 遥测错误：{message}")
                )
                scf.cf.log.add_config(log)
                log.start()
                self._logs.append(log)

    def _handle_log(self, uri: str, timestamp_ms: float, data: dict[str, float], telemetry: TelemetryCallback) -> None:
        cache = self._telemetry_cache.setdefault(uri, {})
        cache.update({key: float(value) for key, value in data.items()})
        telemetry(uri, timestamp_ms / 1000.0, dict(cache))

    def _handle_link_quality(self, uri: str, quality: object) -> None:
        try:
            value = float(quality)
        except (TypeError, ValueError):
            return
        now = time.monotonic()
        if value < 30.0 and now - self._last_quality_warning.get(uri, 0.0) >= 5.0:
            self._last_quality_warning[uri] = now
            self._event("WARN", f"{uri} 无线电链路质量偏低：{value:.0f}%")

    def _configure_single(self, scf) -> None:
        uri = str(getattr(scf.cf, "link_uri", "Crazyflie"))
        deck_values = self._wait_for_deck_values(scf)

        def present(name: str) -> bool:
            try:
                return int(deck_values.get(name, 0)) != 0
            except (TypeError, ValueError):
                return False

        decks = {
            "flow_v1": present("bcFlow"),
            "flow_v2": present("bcFlow2"),
            "ai": present("bcAI"),
            "lighthouse": present("bcLighthouse4"),
            "loco": present("bcLoco"),
        }
        self._detected_decks[uri] = decks
        self._telemetry_cache.setdefault(uri, {}).update({
            "deck.bcFlow": float(int(decks["flow_v1"])),
            "deck.bcFlow2": float(int(decks["flow_v2"])),
            "deck.bcAI": float(int(decks["ai"])),
            "deck.bcLighthouse4": float(int(decks["lighthouse"])),
            "deck.bcLoco": float(int(decks["loco"])),
        })
        if not (decks["flow_v1"] or decks["flow_v2"]):
            raise RuntimeError("未检测到 Flow Deck（deck.bcFlow/deck.bcFlow2 均为 0）")
        missing_logs = [
            name for name in ("range.zrange", "motion.squal")
            if not self._log_variable_exists(scf, name)
        ]
        if missing_logs:
            raise RuntimeError(
                "Flow Deck 固件遥测不完整，缺少：" + ", ".join(missing_logs)
            )
        official_limit = 2.0 if decks["flow_v2"] else 1.0
        detected_max_height = min(self.config.flow_max_height_m, official_limit)
        with self._flow_control_lock:
            self._flow_max_heights[uri] = detected_max_height
            self._flow_detected_max_height = min(self._flow_max_heights.values())
        self._event(
            "INFO",
            f"{uri} 已检测 Flow Deck {'V2' if decks['flow_v2'] else 'V1'}"
            f"；Hover 高度上限 {detected_max_height:.1f}m"
            + ("；同时检测到 AI Deck" if decks["ai"] else "")
            + ("；检测到 Lighthouse Deck" if decks["lighthouse"] else "")
            + ("；检测到 Loco/UWB Deck" if decks["loco"] else ""),
        )
        scf.cf.param.set_value("stabilizer.estimator", "2")

    @classmethod
    def _wait_for_deck_values(cls, scf) -> dict[str, object]:
        """Wait for cflib's asynchronous parameter download to finish.

        ``Swarm.open_links()`` may return after the radio link and parameter
        TOC are ready but before all parameter *values* have arrived. Reading
        ``param.values`` immediately in that small window incorrectly reports
        an installed Flow Deck V2 as missing.  In particular, cflib can expose
        ``bcFlow``/``bcFlow2`` with their temporary zero values (and even mark
        the parameter table updated) before the deck driver publishes the
        detected value.  A zero is therefore not a final result during this
        startup window: keep polling until either Flow variant becomes nonzero
        or the full detection timeout expires.
        """
        param = scf.cf.param
        deadline = time.monotonic() + cls._PARAM_DOWNLOAD_TIMEOUT_S
        last_deck_values: dict[str, object] = {}
        next_refresh_at = 0.0
        flow_detected_at: float | None = None

        def flow_detected(values: dict[str, object]) -> bool:
            for name in ("bcFlow", "bcFlow2"):
                try:
                    if int(values.get(name, 0)) != 0:
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        while time.monotonic() < deadline:
            values = getattr(param, "values", {})
            if isinstance(values, dict):
                deck_values = values.get("deck", {})
                if isinstance(deck_values, dict):
                    last_deck_values = dict(deck_values)
                    if flow_detected(last_deck_values):
                        if flow_detected_at is None:
                            flow_detected_at = time.monotonic()
                        elif time.monotonic() - flow_detected_at >= 0.25:
                            return last_deck_values

            # Deck detection may finish after cflib's initial parameter-value
            # download. Read-only parameter changes are not guaranteed to be
            # pushed to the client, so actively request fresh values instead
            # of relying on another manual reconnect to fetch them.
            now = time.monotonic()
            request_update = getattr(param, "request_param_update", None)
            if callable(request_update) and now >= next_refresh_at:
                for complete_name in (
                    "deck.bcFlow", "deck.bcFlow2", "deck.bcAI",
                    "deck.bcLighthouse4", "deck.bcLoco",
                ):
                    try:
                        request_update(complete_name)
                    except Exception:
                        # The parameter TOC itself can still be arriving. The
                        # next polling cycle will retry once it is available.
                        pass
                next_refresh_at = now + 0.25

            # Do not finish merely because ``param.is_updated`` is true. On
            # real hardware that flag may precede Flow Deck enumeration by a
            # few seconds, which previously forced the operator to click the
            # connection button several times.
            time.sleep(0.05)

        if last_deck_values:
            return last_deck_values
        raise RuntimeError(
            "Crazyflie 参数下载超时，暂时无法确认 Flow Deck；"
            "请关闭其他 Crazyflie 客户端后重新连接"
        )

    @staticmethod
    def _log_variable_exists(scf, complete_name: str) -> bool:
        group, name = complete_name.split(".", 1)
        toc = getattr(getattr(scf.cf.log, "toc", None), "toc", {})
        return group in toc and name in toc[group]

    @staticmethod
    def _reset_estimator_single(scf) -> None:
        scf.cf.param.set_value("kalman.resetEstimation", "1")
        time.sleep(0.1)
        scf.cf.param.set_value("kalman.resetEstimation", "0")

    def disconnect(self) -> None:
        try:
            self._shutdown_flow_control()
        except Exception:
            pass
        if self._swarm is not None:
            try:
                self._swarm.parallel_safe(self._stop_motors_single)
            except Exception:
                pass
        for caller, callback in self._callbacks:
            try:
                caller.remove_callback(callback)
            except (ValueError, AttributeError):
                pass
        self._callbacks.clear()
        for log in self._logs:
            try:
                log.stop()
            except Exception:
                pass
        self._logs.clear()
        self._telemetry_cache.clear()
        self._last_quality_warning.clear()
        if self._swarm is not None:
            self._swarm.close_links()
            self._swarm = None

    def arm_all(self) -> bool | None:
        swarm = self._require_swarm()
        values = self._supervisor_values()
        if values is not None:
            self._raise_for_supervisor_fault(values)
        swarm.parallel_safe(self._arm_positioned_single)
        if self._supervisor_info_reliable:
            return self._wait_for_armed_confirmation()
        return None

    def arm_one(self, uri: str) -> bool | None:
        scf = self._scf(uri)
        values = self._supervisor_values((uri,))
        if values is not None:
            self._raise_for_supervisor_fault(values)
        self._arm_positioned_single(scf)
        if self._supervisor_info_reliable:
            return self._wait_for_armed_confirmation(target_uris=(uri,))
        return None

    def _detect_supervisor_info_support(self) -> bool:
        swarm = self._require_swarm()
        versions: list[int] = []
        for scf in swarm._cfs.values():
            platform = getattr(scf.cf, "platform", None)
            getter = getattr(platform, "get_protocol_version", None)
            if not callable(getter):
                return False
            try:
                versions.append(int(getter()))
            except (TypeError, ValueError):
                return False
        return bool(versions) and all(version >= 7 for version in versions)

    def _wait_for_armed_confirmation(
        self,
        timeout_s: float = 1.0,
        target_uris: Sequence[str] | None = None,
    ) -> bool | None:
        values = self._supervisor_values(target_uris)
        if values is None:
            return None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            values = self._supervisor_values(target_uris)
            if values is None:
                return None
            self._raise_for_supervisor_fault(values)
            if all(value & (1 << 1) for value in values):
                return True
            time.sleep(0.05)
        return False

    def _supervisor_values(self, target_uris: Sequence[str] | None = None) -> list[int] | None:
        swarm = self._require_swarm()
        uris = tuple(target_uris or swarm._cfs)
        if not uris or not all("supervisor.info" in self._telemetry_cache.get(uri, {}) for uri in uris):
            return None
        return [int(self._telemetry_cache[uri]["supervisor.info"]) for uri in uris]

    @staticmethod
    def _raise_for_supervisor_fault(values: Sequence[int]) -> None:
        if any(value & (1 << 6) for value in values):
            raise RuntimeError("飞控处于急停锁定状态，必须给无人机断电重启")
        if any(value & (1 << 5) for value in values):
            raise RuntimeError("飞控检测到机体翻倒，请断电检查并将无人机水平放置")
        if any(value & (1 << 7) for value in values):
            raise RuntimeError("飞控保留坠机状态，请断电检查机体后重新启动")
        if any(value & (1 << 11) for value in values):
            raise RuntimeError("飞控报告扩展板故障，请检查 Flow Deck 连接")

    def disarm_all(self) -> None:
        self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._disarm_single)

    def disarm_one(self, uri: str) -> None:
        self._shutdown_flow_control((uri,))
        self._disarm_single(self._scf(uri))

    @staticmethod
    def _arm_positioned_single(scf) -> None:
        scf.cf.commander.send_notify_setpoint_stop()
        supervisor = getattr(scf.cf, "supervisor", None)
        if supervisor is not None and hasattr(supervisor, "send_arming_request"):
            supervisor.send_arming_request(True)
            return
        platform = getattr(scf.cf, "platform", None)
        if platform is not None and hasattr(platform, "send_arming_request"):
            platform.send_arming_request(True)

    @staticmethod
    def _disarm_single(scf) -> None:
        scf.cf.commander.send_stop_setpoint()
        platform = getattr(scf.cf, "platform", None)
        if platform is not None and hasattr(platform, "send_arming_request"):
            platform.send_arming_request(False)

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

    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None:
        swarm = self._require_swarm()
        swarm.parallel_safe(self._set_led_single, self._args(red, green, blue))
        time.sleep(max(0.1, duration_s))
        swarm.parallel_safe(self._set_led_single, self._args(0, 0, 0))


    @staticmethod
    def _set_led_single(scf, red: int, green: int, blue: int) -> None:
        scf.cf.param.set_value("ring.effect", "7")
        scf.cf.param.set_value("ring.solidRed", str(max(0, min(255, int(red)))))
        scf.cf.param.set_value("ring.solidGreen", str(max(0, min(255, int(green)))))
        scf.cf.param.set_value("ring.solidBlue", str(max(0, min(255, int(blue)))))

    def emergency_stop_all(self) -> None:
        self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._emergency_single)

    @staticmethod
    def _emergency_single(scf) -> None:
        supervisor = getattr(scf.cf, "supervisor", None)
        if supervisor is not None and hasattr(supervisor, "send_emergency_stop"):
            supervisor.send_emergency_stop()
            return
        # cflib 0.1.28 exposes the firmware-compatible fallback as cf.loc.
        localization = getattr(scf.cf, "loc", None)
        if localization is not None and hasattr(localization, "send_emergency_stop"):
            localization.send_emergency_stop()
            return
        scf.cf.commander.send_stop_setpoint()
        raise RuntimeError("当前 cflib/固件不支持锁存急停，已退化为电机停止")

    @staticmethod
    def _stop_motors_single(scf) -> None:
        try:
            scf.cf.high_level_commander.stop()
        finally:
            scf.cf.commander.send_stop_setpoint()
            platform = getattr(scf.cf, "platform", None)
            if platform is not None and hasattr(platform, "send_arming_request"):
                platform.send_arming_request(False)

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

    def _scf(self, uri: str):
        swarm = self._require_swarm()
        if uri not in swarm._cfs:
            raise RuntimeError(f"Crazyflie 未连接：{uri}")
        return swarm._cfs[uri]

    def _only_scf(self):
        swarm = self._require_swarm()
        if len(swarm._cfs) != 1:
            raise RuntimeError("当前操作要求只连接一架 Crazyflie")
        return next(iter(swarm._cfs.values()))

    def _args(self, *values: float) -> dict[str, list[float]]:
        return {uri: list(values) for uri in self._require_swarm()._cfs}

    def _require_swarm(self):
        if self._swarm is None:
            raise RuntimeError("Crazyflie swarm is not connected")
        return self._swarm
