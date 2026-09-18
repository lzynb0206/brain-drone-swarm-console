"""Crazyflie swarm adapter with explicit arming and safety gates.

The UI only enqueues commands.  All cflib work runs on one background thread so
Qt never waits for radio connection, estimator reset, or a timed movement.
"""

from __future__ import annotations

import math
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from itertools import permutations
from pathlib import Path
from typing import Callable, Protocol, Sequence

from src.domain import DeviceStatus, Position, SwarmCommand, SwarmStatus, UAVTelemetry


TelemetryCallback = Callable[[str, float, dict[str, float]], None]
EventCallback = Callable[[str, str], None]
CancelCheck = Callable[[], None]


class CrazyflieBackend(Protocol):
    def connect(self, uris: tuple[str, ...], telemetry: TelemetryCallback, event: EventCallback) -> None: ...
    def disconnect(self) -> None: ...
    def arm_all(self) -> bool | None: ...
    def disarm_all(self) -> None: ...
    def takeoff_all(self, height_m: float, duration_s: float) -> None: ...
    def land_all(self, duration_s: float) -> None: ...
    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def hover_all(self, duration_s: float) -> None: ...
    def stop_motion_all(self) -> None: ...
    def stop_motors_all(self) -> None: ...
    def start_manual_control(self) -> None: ...
    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None: ...
    def emergency_stop_all(self) -> None: ...
    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def go_to_positions(
        self, stages: Sequence[dict[str, Position]], duration_s: float,
        cancel_check: CancelCheck | None = None,
    ) -> None: ...
    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None: ...
    def hover_one_by_one(
        self, height_m: float, height_offsets_m: dict[str, float], hover_s: float, duration_s: float,
        cancel_check: CancelCheck | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class FlightBounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def contains(self, position: Position) -> bool:
        return (
            self.min_x <= position.x <= self.max_x
            and self.min_y <= position.y <= self.max_y
            and self.min_z <= position.z <= self.max_z
        )


@dataclass(frozen=True)
class CrazyflieSwarmConfig:
    uris: tuple[str, ...]
    control_enabled: bool = False
    bounds: FlightBounds | None = None
    default_height_m: float = 0.4
    move_distance_m: float = 0.25
    move_speed_mps: float = 0.15
    minimum_battery_voltage: float = 3.6
    minimum_spacing_m: float = 0.55
    formation_spacing_m: float = 0.8
    formation_max_error_m: float = 0.15
    telemetry_timeout_s: float = 1.5
    lighthouse_ready_status: int = 2
    lighthouse_failure_samples: int = 3
    cache_dir: Path = Path(".cache/cflib")
    height_offsets_m: dict[str, float] = field(default_factory=dict)
    positioning_mode: str = "lighthouse"
    flow_min_height_m: float = 0.2
    flow_max_height_m: float = 2.0
    flow_max_horizontal_speed_mps: float = 0.5
    flow_max_vertical_speed_mps: float = 0.5
    flow_setpoint_rate_hz: float = 5.0

    @classmethod
    def from_environment(cls, positioning_mode: str | None = None) -> "CrazyflieSwarmConfig":
        uris = tuple(value.strip() for value in os.getenv("BDS_CRAZYFLIE_URIS", "").split(",") if value.strip())
        bounds_text = os.getenv("BDS_SWARM_GEOFENCE", "").strip()
        bounds = None
        if bounds_text:
            values = [float(value.strip()) for value in bounds_text.split(",")]
            if len(values) != 6:
                raise ValueError("BDS_SWARM_GEOFENCE 必须是 minX,maxX,minY,maxY,minZ,maxZ")
            bounds = FlightBounds(*values)
        try:
            height_offsets = {
                str(key): float(value)
                for key, value in json.loads(os.getenv("BDS_HEIGHT_OFFSETS_JSON", "{}")).items()
            }
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("BDS_HEIGHT_OFFSETS_JSON 必须是 URI 到米制补偿值的 JSON 对象") from exc
        mode = (positioning_mode or os.getenv("BDS_POSITIONING_MODE", "lighthouse")).strip().lower()
        if mode not in {"lighthouse", "flowdeck"}:
            raise ValueError("BDS_POSITIONING_MODE 只能是 lighthouse 或 flowdeck")
        return cls(
            uris=uris,
            control_enabled=os.getenv("BDS_HARDWARE_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
            bounds=bounds,
            default_height_m=float(os.getenv("BDS_SWARM_TAKEOFF_HEIGHT", "0.4")),
            move_distance_m=float(os.getenv("BDS_SWARM_MOVE_DISTANCE", "0.25")),
            move_speed_mps=float(os.getenv("BDS_SWARM_MOVE_SPEED", "0.15")),
            minimum_battery_voltage=float(os.getenv("BDS_SWARM_MIN_VBAT", "3.6")),
            minimum_spacing_m=float(os.getenv("BDS_SWARM_MIN_SPACING", "0.55")),
            formation_spacing_m=float(os.getenv("BDS_SWARM_FORMATION_SPACING", "0.8")),
            formation_max_error_m=float(os.getenv("BDS_SWARM_FORMATION_MAX_ERROR", "0.15")),
            lighthouse_failure_samples=int(os.getenv("BDS_LIGHTHOUSE_FAILURE_SAMPLES", "3")),
            cache_dir=Path(os.getenv("BDS_CFLIB_CACHE", ".cache/cflib")).expanduser(),
            height_offsets_m=height_offsets,
            positioning_mode=mode,
            flow_min_height_m=float(os.getenv("BDS_FLOW_MIN_HEIGHT", "0.2")),
            flow_max_height_m=float(os.getenv("BDS_FLOW_MAX_HEIGHT", "2.0")),
            flow_max_horizontal_speed_mps=float(os.getenv("BDS_FLOW_MAX_XY_SPEED", "0.5")),
            flow_max_vertical_speed_mps=float(os.getenv("BDS_FLOW_MAX_Z_SPEED", "0.5")),
            flow_setpoint_rate_hz=float(os.getenv("BDS_FLOW_SETPOINT_RATE", "5")),
        )


class _CommandCancelled(RuntimeError):
    """Internal signal used when a high-priority motor stop cancels queued work."""


class CrazyflieSwarmGateway:
    """Queue-based real swarm gateway; no synthetic aircraft are ever created."""

    _ALLOWED_ACTIONS = {
        "takeoff", "land", "forward", "backward", "left", "right", "hover",
        "formation", "switch_formation", "led_test", "relative_square", "hover_one_by_one",
        "hover_sync_test", "relative_forward_back",
    }
    _FORMATIONS = ("line", "diamond", "square")

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
        self._state = "disconnected"
        self._message = "尚未连接"
        self._connected = False
        self._armed = False
        self._airborne = False
        self._emergency = False
        self._uavs: dict[str, UAVTelemetry] = {}
        self._lighthouse_failure_counts: dict[str, int] = {}
        self._events: list[tuple[str, str, float]] = []
        self._event_listeners: list[Callable[[str, str], None]] = []
        self._command_listeners: list[Callable[[str, str, SwarmCommand], None]] = []
        self._formation_mode = "line"

    def connect(self) -> None:
        if not self.config.uris:
            raise RuntimeError("未配置 BDS_CRAZYFLIE_URIS，不能连接 Crazyflie")
        if self.config.positioning_mode == "flowdeck" and len(self.config.uris) != 1:
            raise RuntimeError("Flow Deck 模式只允许配置一架 Crazyflie")
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
            self._state = "connecting"
            self._message = "正在连接 Crazyradio/Crazyflie…"
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
            backend = "flowdeck" if self.config.positioning_mode == "flowdeck" else "crazyflie"
            return SwarmStatus(
                backend=backend,
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
            self._validate_ready(require_bounds=self.config.positioning_mode == "lighthouse")
            confirmed = self._backend.arm_all()
            if confirmed is False:
                raise RuntimeError("飞控拒绝解锁，请检查电池、机体姿态和飞控锁定状态")
            if self.config.positioning_mode == "flowdeck":
                try:
                    self._backend.start_manual_control()
                except Exception:
                    # Never leave the firmware armed when the continuous
                    # setpoint stream could not be established.
                    self._backend.disarm_all()
                    raise
            self._armed = True
            self._airborne = self.config.positioning_mode == "flowdeck"
            self._state = "manual_flight" if self._airborne else "connected"
            self._message = (
                f"Flow Hover 正在渐升至 {self.config.default_height_m:.2f}m；松杆保持悬停"
                if self.config.positioning_mode == "flowdeck" else
                "实机控制已解锁" if confirmed is not None else
                "解锁请求已发送；当前固件未提供状态确认"
            )
            self._record("INFO", "实机控制已由操作员显式解锁")

    def disarm(self) -> None:
        with self._lock:
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态，请先重启无人机并复位")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机停止操作尚未完成")
            if self._airborne:
                raise RuntimeError("无人机仍在飞行；请先降落，必要时使用电机立即停转")
            self._backend.disarm_all()
            self._armed = False
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
            if action not in {"land", "led_test"} and not self._armed:
                raise RuntimeError("实机控制尚未解锁")
            self._validate_command(command)
        try:
            self._commands.put_nowait((self._command_generation, command))
        except queue.Full as exc:
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
            if self.config.positioning_mode != "flowdeck":
                raise RuntimeError("虚拟摇杆需要 Flow Deck Hover 模式")
            if self._motor_stop_in_progress:
                raise RuntimeError("电机正在停止")
        self._backend.set_manual_input(*values)
        with self._lock:
            self._airborne = True
            self._state = "manual_flight"
            self._message = (
                "Flow Hover：升降 {:+.0f}% / 前后 {:+.0f}% / 左右 {:+.0f}%"
                .format(values[0] * 100, values[1] * 100, values[2] * 100)
            )

    def emergency_stop(self) -> None:
        with self._lock:
            self._command_generation += 1
            self._discard_pending_commands("紧急停止")
            self._emergency = True
            self._armed = False
            self._airborne = False
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
                self._message = (
                    "Flow Deck、测距和光流遥测已连接；Hover 尚未启动"
                    if self.config.positioning_mode == "flowdeck"
                    else "遥测已连接；实机控制仍处于锁定状态"
                )
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
                        self._armed = False
                        if not self._emergency:
                            self._state = "flight_error"
                        self._message = f"指令失败并锁定控制：{exc}"
                    self._record("ERROR", self._message)
                    self._notify_command("FAILED", str(exc), command)
                finally:
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
                    self._backend.land_all(3.0)
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
                if not self._emergency and self._state != "error":
                    self._state = "disconnected"
                    self._message = "蜂群连接已断开"

    def _execute(self, command: SwarmCommand, generation: int) -> None:
        self._raise_if_cancelled(generation)
        with self._lock:
            self._validate_command(command)
        action = command.action.strip().lower()
        cancel_check = lambda: self._raise_if_cancelled(generation)
        if action == "led_test":
            self._backend.set_led_all(0, 80, 255, float(command.parameters.get("duration_s", 1.0)))
        elif action == "hover_one_by_one":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            hover_s = max(0.5, float(command.parameters.get("hover_s", 2.0)))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
            with self._lock:
                self._airborne = True
            landed = False
            try:
                self._backend.hover_one_by_one(
                    height, self.config.height_offsets_m, hover_s, duration, cancel_check,
                )
                landed = True
            except _CommandCancelled:
                raise
            except Exception:
                # A partially completed acceptance run must never leave its state
                # ambiguous.  Ask every connected aircraft to land before failing.
                self._raise_if_cancelled(generation)
                self._backend.land_all(3.0)
                landed = True
                raise
            finally:
                if landed:
                    with self._lock:
                        if generation == self._command_generation and not self._emergency:
                            self._airborne = False
                            self._armed = False
                            self._state = "connected"
                            self._message = "逐架悬停验收完成，电机已停止"
        elif action == "hover_sync_test":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            hover_s = max(0.5, float(command.parameters.get("hover_s", 2.0)))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
            with self._lock:
                self._airborne = True
            landed = False
            try:
                takeoff_with_offsets = getattr(self._backend, "takeoff_all_with_offsets", None)
                if self.config.height_offsets_m and callable(takeoff_with_offsets):
                    takeoff_with_offsets(height, self.config.height_offsets_m, duration)
                else:
                    self._backend.takeoff_all(height, duration)
                self._raise_if_cancelled(generation)
                self._backend.hover_all(hover_s)
                self._raise_if_cancelled(generation)
                self._backend.land_all(duration)
                self._raise_if_cancelled(generation)
                landed = True
            except _CommandCancelled:
                raise
            except Exception:
                self._raise_if_cancelled(generation)
                self._backend.land_all(3.0)
                landed = True
                raise
            finally:
                if landed:
                    with self._lock:
                        if generation == self._command_generation and not self._emergency:
                            self._airborne = False
                            self._armed = False
                            self._state = "connected"
                            self._message = "同步悬停验收完成，电机已停止"
        elif action == "takeoff":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            speed = max(0.05, self.config.move_speed_mps)
            duration = max(2.0, height / speed)
            with self._lock:
                self._airborne = True
                self._state = "taking_off"
                self._message = "正在起飞并进入保持状态…"
            takeoff_with_offsets = getattr(self._backend, "takeoff_all_with_offsets", None)
            if self.config.height_offsets_m and callable(takeoff_with_offsets):
                takeoff_with_offsets(height, self.config.height_offsets_m, duration)
            else:
                self._backend.takeoff_all(height, duration)
            self._raise_if_cancelled(generation)
            self._backend.hover_all(0.4)
            self._raise_if_cancelled(generation)
            with self._lock:
                self._airborne = True
                self._state = "airborne"
                self._message = "已起飞并保持目标位置"
        elif action == "land":
            self._backend.land_all(3.0)
            self._raise_if_cancelled(generation)
            with self._lock:
                if not self._emergency:
                    self._airborne = False
                    self._armed = False
                    self._state = "connected"
                    self._message = "已降落并停止电机"
        elif action == "hover":
            self._backend.hover_all(0.5)
        elif action in {"formation", "switch_formation"}:
            name = str(command.parameters.get("name", ""))
            if action == "switch_formation":
                name = self._FORMATIONS[(self._FORMATIONS.index(self._formation_mode) + 1) % len(self._FORMATIONS)]
            stages, targets = self._plan_formation(name)
            duration = self._duration_for_positions(targets)
            self._backend.go_to_positions(stages, duration, cancel_check)
            self._raise_if_cancelled(generation)
            self._verify_position_targets(targets)
            self._formation_mode = name
        elif action == "relative_square":
            distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            for dx, dy in ((distance, 0.0), (0.0, distance), (-distance, 0.0), (0.0, -distance)):
                self._raise_if_cancelled(generation)
                self._backend.move_all_relative(dx, dy, 0.0, duration)
                self._raise_if_cancelled(generation)
        elif action == "relative_forward_back":
            distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            self._backend.move_all_relative(distance, 0.0, 0.0, duration)
            self._raise_if_cancelled(generation)
            self._backend.move_all_relative(-distance, 0.0, 0.0, duration)
            self._raise_if_cancelled(generation)
        else:
            distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
            vectors = {
                "forward": (distance, 0.0, 0.0),
                "backward": (-distance, 0.0, 0.0),
                "left": (0.0, distance, 0.0),
                "right": (0.0, -distance, 0.0),
            }
            dx, dy, dz = vectors[action]
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            if command.uav_id:
                self._backend.move_one_relative(self._resolve_uri(command.uav_id), dx, dy, dz, duration)
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
        for command in discarded:
            self._notify_command("CANCELLED", reason, command)

    def _validate_command(self, command: SwarmCommand) -> None:
        action = command.action.strip().lower()
        if self.config.positioning_mode == "flowdeck" and action in {
            "formation", "switch_formation", "relative_square", "relative_forward_back",
            "hover_one_by_one", "hover_sync_test",
        }:
            raise RuntimeError("Flow Deck Hover 是单机相对定位模式，禁止蜂群和绝对队形动作")
        if action in {"land", "led_test"}:
            return
        self._validate_ready(
            require_bounds=action != "hover" and self.config.positioning_mode == "lighthouse"
        )
        if action in {"takeoff", "hover_one_by_one", "hover_sync_test"}:
            if self._airborne:
                raise RuntimeError("无人机已经处于飞行状态")
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            for uav in self._uavs.values():
                uri = self._resolve_uri(uav.uav_id)
                target_height = height + self.config.height_offsets_m.get(uri, 0.0)
                if self.config.positioning_mode == "lighthouse" and (
                    not self.config.bounds or not self.config.bounds.contains(
                        Position(uav.position.x, uav.position.y, target_height)
                    )
                ):
                    raise RuntimeError(f"{uav.uav_id} 起飞目标超出场地围栏")
            return
        if action == "hover":
            if not self._airborne:
                raise RuntimeError("无人机尚未起飞")
            return
        if action in {"formation", "switch_formation", "relative_square", "relative_forward_back"}:
            if not self._airborne:
                raise RuntimeError("无人机尚未起飞")
            if action in {"formation", "switch_formation"}:
                name = str(command.parameters.get("name", ""))
                if action == "formation" and name not in self._FORMATIONS:
                    raise RuntimeError(f"不支持的队形：{name}")
                if len(self.config.uris) != 4:
                    raise RuntimeError("一字、菱形、方形队形当前要求恰好 4 架无人机")
            if action == "relative_square":
                distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
                self._validate_relative_path(
                    ((distance, 0.0, 0.0), (0.0, distance, 0.0),
                     (-distance, 0.0, 0.0), (0.0, -distance, 0.0)),
                )
            elif action == "relative_forward_back":
                distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
                self._validate_relative_path(
                    ((distance, 0.0, 0.0), (-distance, 0.0, 0.0)),
                )
            return
        if not self._airborne:
            raise RuntimeError("无人机尚未起飞")
        distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
        dx, dy = {
            "forward": (distance, 0.0), "backward": (-distance, 0.0),
            "left": (0.0, distance), "right": (0.0, -distance),
        }[action]
        selected_uris = [self._resolve_uri(command.uav_id)] if command.uav_id else list(self._uavs)
        planned = {uri: telemetry.position for uri, telemetry in self._uavs.items()}
        for uri in selected_uris:
            current = self._uavs[uri].position
            planned[uri] = Position(current.x + dx, current.y + dy, current.z)
        targets = list(planned.values())
        if self.config.positioning_mode == "lighthouse" and (
            not self.config.bounds or any(not self.config.bounds.contains(target) for target in targets)
        ):
            raise RuntimeError("移动目标超出场地围栏")
        for index, first in enumerate(targets):
            for second in targets[index + 1:]:
                if math.dist((first.x, first.y, first.z), (second.x, second.y, second.z)) < self.config.minimum_spacing_m:
                    raise RuntimeError("目标位置小于最小机间距")

    def _validate_relative_path(self, vectors: Sequence[tuple[float, float, float]]) -> None:
        """Validate every waypoint of a synchronized relative test path."""

        planned = {uri: telemetry.position for uri, telemetry in self._uavs.items()}
        for dx, dy, dz in vectors:
            planned = {
                uri: Position(value.x + dx, value.y + dy, value.z + dz)
                for uri, value in planned.items()
            }
            self._validate_position_set(planned)

    def _resolve_uri(self, uav_id: str) -> str:
        if uav_id in self.config.uris:
            return uav_id
        for uri, telemetry in self._uavs.items():
            if telemetry.uav_id == uav_id:
                return uri
        raise RuntimeError(f"找不到无人机：{uav_id}")

    def _plan_formation(self, name: str) -> tuple[list[dict[str, Position]], dict[str, Position]]:
        if name not in self._FORMATIONS:
            raise RuntimeError(f"不支持的队形：{name}")
        ordered = [self._uavs[uri] for uri in self.config.uris]
        if len(ordered) != 4:
            raise RuntimeError("形成队形前必须收到 4 架无人机的实时位置")
        center_x = sum(item.position.x for item in ordered) / 4.0
        center_y = sum(item.position.y for item in ordered) / 4.0
        center_z = sum(item.position.z for item in ordered) / 4.0
        spacing = self.config.formation_spacing_m
        offsets = {
            "line": ((-1.5 * spacing, 0.0), (-0.5 * spacing, 0.0), (0.5 * spacing, 0.0), (1.5 * spacing, 0.0)),
            "diamond": ((-spacing, 0.0), (0.0, spacing), (spacing, 0.0), (0.0, -spacing)),
            "square": ((-spacing / 2, -spacing / 2), (-spacing / 2, spacing / 2),
                       (spacing / 2, spacing / 2), (spacing / 2, -spacing / 2)),
        }[name]
        current = {uri: self._uavs[uri].position for uri in self.config.uris}
        candidates: list[tuple[float, dict[str, Position]]] = []
        for assigned_offsets in permutations(offsets):
            candidate = {
                uri: Position(
                    center_x + dx,
                    center_y + dy,
                    center_z + self.config.height_offsets_m.get(uri, 0.0),
                )
                for uri, (dx, dy) in zip(self.config.uris, assigned_offsets)
            }
            try:
                self._validate_transition(current, candidate)
            except RuntimeError:
                continue
            distance = sum(
                math.dist(
                    (current[uri].x, current[uri].y, current[uri].z),
                    (candidate[uri].x, candidate[uri].y, candidate[uri].z),
                )
                for uri in self.config.uris
            )
            candidates.append((distance, candidate))
        if not candidates:
            raise RuntimeError("找不到同时满足围栏和机间距的队形转换路径")
        targets = min(candidates, key=lambda item: item[0])[1]
        midpoint = {
            uri: Position(
                (current[uri].x + targets[uri].x) / 2.0,
                (current[uri].y + targets[uri].y) / 2.0,
                (current[uri].z + targets[uri].z) / 2.0,
            )
            for uri in self.config.uris
        }
        self._validate_transition(current, midpoint)
        self._validate_transition(midpoint, targets)
        return [midpoint, targets], targets

    def _validate_transition(
        self, start: dict[str, Position], end: dict[str, Position], steps: int = 20,
    ) -> None:
        """Check the full linear transition, not only its endpoints."""

        for step in range(steps + 1):
            ratio = step / max(1, steps)
            positions = {
                uri: Position(
                    start[uri].x + (end[uri].x - start[uri].x) * ratio,
                    start[uri].y + (end[uri].y - start[uri].y) * ratio,
                    start[uri].z + (end[uri].z - start[uri].z) * ratio,
                )
                for uri in start
            }
            self._validate_position_set(positions)

    def _validate_position_set(self, positions: dict[str, Position]) -> None:
        if self.config.bounds is None or any(not self.config.bounds.contains(value) for value in positions.values()):
            raise RuntimeError("队形路径超出场地围栏")
        values = list(positions.values())
        for index, first in enumerate(values):
            for second in values[index + 1:]:
                if math.dist((first.x, first.y, first.z), (second.x, second.y, second.z)) < self.config.minimum_spacing_m:
                    raise RuntimeError("队形路径小于最小机间距")

    def _duration_for_positions(self, targets: dict[str, Position]) -> float:
        displacement = max(
            math.dist(
                (self._uavs[uri].position.x, self._uavs[uri].position.y, self._uavs[uri].position.z),
                (target.x, target.y, target.z),
            )
            for uri, target in targets.items()
        )
        return max(1.0, displacement / max(0.05, self.config.move_speed_mps))

    def _verify_position_targets(self, targets: dict[str, Position]) -> None:
        errors = {
            uri: math.dist(
                (self._uavs[uri].position.x, self._uavs[uri].position.y, self._uavs[uri].position.z),
                (target.x, target.y, target.z),
            )
            for uri, target in targets.items()
        }
        failed = {uri: error for uri, error in errors.items() if error > self.config.formation_max_error_m}
        if failed:
            details = ", ".join(f"{uri.rsplit('/', 1)[-1]}={error:.2f}m" for uri, error in failed.items())
            raise RuntimeError(f"队形坐标误差超限：{details}")

    def _validate_ready(self, require_bounds: bool) -> None:
        if require_bounds and self.config.bounds is None:
            raise RuntimeError("未配置 BDS_SWARM_GEOFENCE，实机飞行被禁止")
        if len(self._uavs) != len(self.config.uris):
            raise RuntimeError("尚未收到全部无人机遥测")
        now = time.time()
        for uav in self._uavs.values():
            if now - uav.last_update_timestamp > self.config.telemetry_timeout_s:
                raise RuntimeError(f"{uav.uav_id} 遥测超时")
            if uav.battery_voltage is None or uav.battery_voltage < self.config.minimum_battery_voltage:
                raise RuntimeError(f"{uav.uav_id} 电压不足")
            if not uav.positioning_ok:
                mode_label = "Flow Deck 测距/光流" if self.config.positioning_mode == "flowdeck" else "Lighthouse"
                raise RuntimeError(f"{uav.uav_id} {mode_label}尚未就绪")

    def _on_telemetry(self, uri: str, timestamp: float, data: dict[str, float]) -> None:
        position = Position(float(data.get("stateEstimate.x", 0.0)), float(data.get("stateEstimate.y", 0.0)),
                            float(data.get("stateEstimate.z", 0.0)))
        speed = math.sqrt(sum(float(data.get(key, 0.0)) ** 2 for key in ("stateEstimate.vx", "stateEstimate.vy", "stateEstimate.vz")))
        voltage = float(data.get("pm.vbat", 0.0))
        battery = max(0.0, min(100.0, (voltage - 3.0) / 1.2 * 100.0))
        lighthouse_ok = (
            int(data.get("lighthouse.status", 0)) == self.config.lighthouse_ready_status
            and int(data.get("lighthouse.bsActive", 0)) > 0
            and all(math.isfinite(value) for value in (position.x, position.y, position.z))
        )
        local_flow_ok = (
            self.config.positioning_mode == "flowdeck"
            and all(key in data for key in ("stateEstimate.x", "stateEstimate.y", "stateEstimate.z"))
            and all(math.isfinite(value) for value in (position.x, position.y, position.z))
            and 0.0 < float(data.get("range.zrange", 0.0)) < 4000.0
            and float(data.get("motion.squal", 0.0)) > 0.0
        )
        positioning_ok = lighthouse_ok or local_flow_ok
        supervisor_info = int(data.get("supervisor.info", 0))
        firmware_locked = bool(supervisor_info & (1 << 6))
        name = uri.rsplit("/", 1)[-1]
        should_trip = False
        newly_locked = False
        with self._lock:
            self._uavs[uri] = UAVTelemetry(
                uav_id=f"CF-{name}", battery=battery, speed_mps=speed, altitude_m=position.z,
                task="飞行" if self._airborne else "待命",
                status=DeviceStatus.NORMAL if positioning_ok else DeviceStatus.WARNING,
                position=position, battery_voltage=voltage, positioning_ok=positioning_ok,
                last_update_timestamp=float(timestamp) if timestamp > 1_000_000_000 else time.time(),
            )
            if self._airborne:
                current_positioning_ok = (
                    local_flow_ok if self.config.positioning_mode == "flowdeck" else lighthouse_ok
                )
                self._lighthouse_failure_counts[uri] = (
                    0 if current_positioning_ok else self._lighthouse_failure_counts.get(uri, 0) + 1
                )
                should_trip = self._lighthouse_failure_counts[uri] >= self.config.lighthouse_failure_samples
            else:
                self._lighthouse_failure_counts[uri] = 0
            if firmware_locked and not self._emergency:
                self._command_generation += 1
                self._discard_pending_commands("飞控进入锁定状态")
                self._emergency = True
                self._armed = False
                self._airborne = False
                self._state = "emergency"
                self._message = "飞控处于锁定状态；请给无人机断电重启后再复位"
                newly_locked = True
        if newly_locked:
            self._record("ERROR", f"{name} 飞控报告锁定状态，需要断电重启")
        if should_trip:
            source = "Flow Deck" if self.config.positioning_mode == "flowdeck" else "Lighthouse"
            self._trip_emergency(f"{name} {source} 连续 {self.config.lighthouse_failure_samples} 次未就绪")

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
                self._state = "connected"
                self._message = "电机已停止；重新起飞前需要再次解锁"
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


class CflibBackend:
    """Thin cflib implementation isolated for hardware-free unit testing."""

    def __init__(self, config: CrazyflieSwarmConfig) -> None:
        self.config = config
        self.cache_dir = config.cache_dir
        self.lighthouse_ready_status = config.lighthouse_ready_status
        self._swarm = None
        self._logs: list[object] = []
        self._telemetry_cache: dict[str, dict[str, float]] = {}
        self._callbacks: list[tuple[object, object]] = []
        self._last_quality_warning: dict[str, float] = {}
        self._event: EventCallback = lambda _level, _message: None
        self._flow_control_lock = threading.Lock()
        self._flow_control_stop = threading.Event()
        self._flow_control_thread: threading.Thread | None = None
        self._flow_control_error: Exception | None = None
        self._flow_scf = None
        self._flow_velocity = (0.0, 0.0, 0.0)
        self._flow_vertical_speed = 0.0
        self._flow_detected_max_height = config.flow_max_height_m
        self._flow_target_height = self._clamp_flow_height(config.default_height_m)
        self._flow_takeoff_target: float | None = None
        self._flow_landing = False
        self._detected_decks: dict[str, dict[str, bool]] = {}

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
        self._swarm.parallel_safe(self._configure_single)
        # Reset without the blocking convergence loop.  Readiness is decided
        # from live Flow/Lighthouse telemetry by the gateway.
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
            if self.config.positioning_mode == "flowdeck":
                for variable, variable_type in (
                    ("range.zrange", "uint16_t"),
                    ("motion.squal", "uint8_t"),
                    ("motion.deltaX", "int16_t"),
                    ("motion.deltaY", "int16_t"),
                ):
                    if self._log_variable_exists(scf, variable):
                        health_log.add_variable(variable, variable_type)
            if self._log_variable_exists(scf, "lighthouse.status"):
                health_log.add_variable("lighthouse.status", "uint8_t")
            if self._log_variable_exists(scf, "lighthouse.bsActive"):
                health_log.add_variable("lighthouse.bsActive", "uint16_t")
            if self._log_variable_exists(scf, "supervisor.info"):
                health_log.add_variable("supervisor.info", "uint16_t")
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
        values = getattr(scf.cf.param, "values", {})
        deck_values = values.get("deck", {}) if isinstance(values, dict) else {}

        def present(name: str) -> bool:
            try:
                return int(deck_values.get(name, 0)) != 0
            except (TypeError, ValueError):
                return False

        decks = {
            "flow_v1": present("bcFlow"),
            "flow_v2": present("bcFlow2"),
            "lighthouse": present("bcLighthouse4"),
            "ai": present("bcAI"),
        }
        self._detected_decks[uri] = decks
        if self.config.positioning_mode == "flowdeck" and not (decks["flow_v1"] or decks["flow_v2"]):
            raise RuntimeError("未检测到 Flow Deck（deck.bcFlow/deck.bcFlow2 均为 0）")
        if self.config.positioning_mode == "lighthouse" and not decks["lighthouse"]:
            raise RuntimeError("未检测到 Lighthouse Deck（deck.bcLighthouse4=0）")
        if self.config.positioning_mode == "flowdeck":
            missing_logs = [
                name for name in ("range.zrange", "motion.squal")
                if not self._log_variable_exists(scf, name)
            ]
            if missing_logs:
                raise RuntimeError(
                    "Flow Deck 固件遥测不完整，缺少：" + ", ".join(missing_logs)
                )
            official_limit = 2.0 if decks["flow_v2"] else 1.0
            self._flow_detected_max_height = min(self.config.flow_max_height_m, official_limit)
            self._event(
                "INFO",
                f"{uri} 已检测 Flow Deck {'V2' if decks['flow_v2'] else 'V1'}"
                f"；Hover 高度上限 {self._flow_detected_max_height:.1f}m"
                + ("；同时检测到 AI Deck" if decks["ai"] else ""),
            )
        elif decks["ai"]:
            self._event("WARN", f"{uri} 同时检测到 Lighthouse 与 AI Deck，请确认 UART1 冲突已处理")
        scf.cf.param.set_value("stabilizer.estimator", "2")

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
        if self.config.positioning_mode == "flowdeck":
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
        swarm.parallel_safe(self._arm_positioned_single)
        return self._wait_for_armed_confirmation()

    def _wait_for_armed_confirmation(self, timeout_s: float = 1.0) -> bool | None:
        swarm = self._require_swarm()
        uris = tuple(swarm._cfs)
        if not uris or not all("supervisor.info" in self._telemetry_cache.get(uri, {}) for uri in uris):
            return None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            values = [int(self._telemetry_cache.get(uri, {}).get("supervisor.info", 0)) for uri in uris]
            if any(value & (1 << 6) for value in values):
                return False
            if all(value & (1 << 1) for value in values):
                return True
            time.sleep(0.05)
        return False

    def disarm_all(self) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._disarm_single)

    @staticmethod
    def _arm_positioned_single(scf) -> None:
        scf.cf.commander.send_notify_setpoint_stop()
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
        if self.config.positioning_mode == "flowdeck":
            self._start_flow_control(height_m)
            return
        self._require_swarm().parallel_safe(self._takeoff_single, self._args(height_m, duration_s))
        time.sleep(duration_s + 0.5)

    def takeoff_all_with_offsets(self, height_m: float, offsets: dict[str, float], duration_s: float) -> None:
        if self.config.positioning_mode == "flowdeck":
            uri = next(iter(self._require_swarm()._cfs))
            self._start_flow_control(height_m + float(offsets.get(uri, 0.0)))
            return
        args = {
            uri: [height_m + float(offsets.get(uri, 0.0)), duration_s]
            for uri in self._require_swarm()._cfs
        }
        self._require_swarm().parallel_safe(self._takeoff_single, args)
        time.sleep(duration_s + 0.5)

    @staticmethod
    def _takeoff_single(scf, height_m: float, duration_s: float) -> None:
        scf.cf.high_level_commander.takeoff(height_m, duration_s)

    def land_all(self, duration_s: float) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._land_flow(duration_s)
            return
        swarm = self._require_swarm()
        swarm.parallel_safe(self._land_single, self._args(duration_s))
        time.sleep(duration_s + 0.5)
        swarm.parallel_safe(self._stop_motors_single)

    @staticmethod
    def _land_single(scf, duration_s: float) -> None:
        scf.cf.high_level_commander.land(0.0, duration_s)

    @staticmethod
    def _stop_single(scf) -> None:
        scf.cf.high_level_commander.stop()

    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._move_flow_relative(dx, dy, dz, duration_s)
            return
        self._require_swarm().parallel_safe(self._move_single, self._args(dx, dy, dz, duration_s))
        time.sleep(duration_s + 0.2)

    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None:
        swarm = self._require_swarm()
        if uri not in swarm._cfs:
            raise RuntimeError(f"Crazyflie 未连接：{uri}")
        if self.config.positioning_mode == "flowdeck":
            self._move_flow_relative(dx, dy, dz, duration_s)
            return
        self._move_single(swarm._cfs[uri], dx, dy, dz, duration_s)
        time.sleep(duration_s + 0.2)

    @staticmethod
    def _move_single(scf, dx: float, dy: float, dz: float, duration_s: float) -> None:
        scf.cf.high_level_commander.go_to(dx, dy, dz, 0.0, duration_s, relative=True)

    def hover_all(self, duration_s: float) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._set_flow_motion(0.0, 0.0, 0.0, 0.0)
            self._wait_flow(duration_s)
            return
        self._require_swarm().parallel_safe(self._hover_single, self._args(duration_s))
        time.sleep(duration_s)

    def stop_motion_all(self) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._set_flow_motion(0.0, 0.0, 0.0, 0.0)
            return
        self._require_swarm().parallel_safe(self._hover_single, self._args(0.5))

    def start_manual_control(self) -> None:
        if self.config.positioning_mode != "flowdeck":
            raise RuntimeError("Flow Hover 手动控制需要 Flow Deck 模式")
        self._start_flow_control(self.config.default_height_m)

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        if self.config.positioning_mode != "flowdeck":
            raise RuntimeError("Flow Hover 手动控制需要 Flow Deck 模式")
        self._start_flow_control(self.config.default_height_m)
        dead_zone = 0.08
        vertical = 0.0 if abs(vertical) < dead_zone else max(-1.0, min(1.0, vertical))
        forward = 0.0 if abs(forward) < dead_zone else max(-1.0, min(1.0, forward))
        lateral = 0.0 if abs(lateral) < dead_zone else max(-1.0, min(1.0, lateral))
        xy_speed = max(0.05, self.config.flow_max_horizontal_speed_mps)
        z_speed = max(0.05, self.config.flow_max_vertical_speed_mps)
        # Official body-frame convention: +X forward, +Y left.
        self._set_flow_motion(
            forward * xy_speed,
            -lateral * xy_speed,
            vertical * z_speed,
            0.0,
        )

    def stop_motors_all(self) -> None:
        if self.config.positioning_mode == "flowdeck":
            self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._stop_motors_single)

    def go_to_positions(
        self, stages: Sequence[dict[str, Position]], duration_s: float,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        swarm = self._require_swarm()
        for stage in stages:
            if cancel_check is not None:
                cancel_check()
            args = {
                uri: [position.x, position.y, position.z, duration_s]
                for uri, position in stage.items()
            }
            swarm.parallel_safe(self._go_absolute_single, args)
            time.sleep(duration_s + 0.25)
            if cancel_check is not None:
                cancel_check()

    @staticmethod
    def _go_absolute_single(scf, x: float, y: float, z: float, duration_s: float) -> None:
        scf.cf.high_level_commander.go_to(x, y, z, 0.0, duration_s, relative=False)

    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None:
        swarm = self._require_swarm()
        swarm.parallel_safe(self._set_led_single, self._args(red, green, blue))
        time.sleep(max(0.1, duration_s))
        swarm.parallel_safe(self._set_led_single, self._args(0, 0, 0))

    def hover_one_by_one(
        self, height_m: float, height_offsets_m: dict[str, float], hover_s: float, duration_s: float,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        """Run a sequential takeoff/hover/land acceptance test.

        Only one aircraft is commanded airborne at a time.  The caller still
        performs all normal geofence, battery, positioning and arming checks.
        """

        swarm = self._require_swarm()
        for uri in tuple(swarm._cfs):
            if cancel_check is not None:
                cancel_check()
            scf = swarm._cfs[uri]
            target_height = height_m + float(height_offsets_m.get(uri, 0.0))
            self._takeoff_single(scf, target_height, duration_s)
            time.sleep(duration_s + 0.3)
            if cancel_check is not None:
                cancel_check()
            self._hover_single(scf, hover_s)
            time.sleep(hover_s)
            if cancel_check is not None:
                cancel_check()
            self._land_single(scf, duration_s)
            time.sleep(duration_s + 0.3)
            if cancel_check is not None:
                cancel_check()
            self._stop_single(scf)

    @staticmethod
    def _set_led_single(scf, red: int, green: int, blue: int) -> None:
        scf.cf.param.set_value("ring.effect", "7")
        scf.cf.param.set_value("ring.solidRed", str(max(0, min(255, int(red)))))
        scf.cf.param.set_value("ring.solidGreen", str(max(0, min(255, int(green)))))
        scf.cf.param.set_value("ring.solidBlue", str(max(0, min(255, int(blue)))))

    @staticmethod
    def _hover_single(scf, duration_s: float) -> None:
        scf.cf.high_level_commander.go_to(0.0, 0.0, 0.0, 0.0, duration_s, relative=True)

    def emergency_stop_all(self) -> None:
        if self.config.positioning_mode == "flowdeck":
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
        """Start the same body-velocity/absolute-height stream used by cfclient Hover."""
        thread = self._flow_control_thread
        if thread is not None:
            self._raise_flow_error()
            if thread.is_alive():
                return
            raise RuntimeError("Flow Hover 控制流已停止；请停止电机后重新启动操控")
        scf = self._only_scf()
        target_height = self._clamp_flow_height(target_height_m)
        scf.cf.commander.send_notify_setpoint_stop()
        with self._flow_control_lock:
            self._flow_scf = scf
            self._flow_velocity = (0.0, 0.0, 0.0)
            # cflib MotionCommander starts at ground distance 0 and integrates
            # a vertical velocity until the requested take-off height is reached.
            self._flow_vertical_speed = min(0.2, self.config.flow_max_vertical_speed_mps)
            self._flow_target_height = 0.0
            self._flow_takeoff_target = target_height
            self._flow_control_error = None
            self._flow_landing = False
        self._flow_control_stop.clear()
        self._flow_control_thread = threading.Thread(
            target=self._flow_control_loop,
            name="Crazyflie-Flow-Hover",
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
            with self._flow_control_lock:
                scf = self._flow_scf
                vx, vy, yawrate = self._flow_velocity
                minimum = (
                    0.0 if self._flow_takeoff_target is not None else
                    0.05 if self._flow_landing else self.config.flow_min_height_m
                )
                maximum = max(minimum, self._flow_detected_max_height)
                self._flow_target_height = max(
                    minimum,
                    min(maximum, self._flow_target_height + self._flow_vertical_speed * elapsed),
                )
                if (
                    self._flow_takeoff_target is not None
                    and self._flow_target_height >= self._flow_takeoff_target
                ):
                    self._flow_target_height = self._flow_takeoff_target
                    self._flow_takeoff_target = None
                    self._flow_vertical_speed = 0.0
                target_height = self._flow_target_height
            try:
                if scf is None:
                    return
                scf.cf.commander.send_hover_setpoint(vx, vy, yawrate, target_height)
            except Exception as exc:
                with self._flow_control_lock:
                    self._flow_control_error = exc
                self._flow_control_stop.set()
                self._event("SAFETY", f"Flow Hover 控制流中断：{exc}")
                return
            self._flow_control_stop.wait(period_s)

    def _set_flow_motion(
        self, velocity_x: float, velocity_y: float, vertical_speed: float, yawrate: float,
    ) -> None:
        self._raise_flow_error()
        max_xy = max(0.05, self.config.flow_max_horizontal_speed_mps)
        max_z = max(0.05, self.config.flow_max_vertical_speed_mps)
        with self._flow_control_lock:
            self._flow_velocity = (
                max(-max_xy, min(max_xy, float(velocity_x))),
                max(-max_xy, min(max_xy, float(velocity_y))),
                float(yawrate),
            )
            self._flow_vertical_speed = max(-max_z, min(max_z, float(vertical_speed)))
            if abs(vertical_speed) > 1e-6:
                self._flow_takeoff_target = None

    def _move_flow_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None:
        self._start_flow_control(self.config.default_height_m)
        duration = max(0.1, float(duration_s))
        self._set_flow_motion(dx / duration, dy / duration, dz / duration, 0.0)
        try:
            self._wait_flow(duration)
        finally:
            self._set_flow_motion(0.0, 0.0, 0.0, 0.0)

    def _land_flow(self, duration_s: float) -> None:
        self._start_flow_control(self.config.default_height_m)
        with self._flow_control_lock:
            current_height = self._flow_target_height
            self._flow_velocity = (0.0, 0.0, 0.0)
            self._flow_landing = True
            self._flow_takeoff_target = None
            self._flow_vertical_speed = -max(
                0.08,
                (current_height - 0.05) / max(0.5, float(duration_s)),
            )
        self._wait_flow(max(0.5, float(duration_s)))
        self._shutdown_flow_control()
        self._require_swarm().parallel_safe(self._stop_motors_single)

    def _wait_flow(self, duration_s: float) -> bool:
        stopped = self._flow_control_stop.wait(max(0.0, float(duration_s)))
        self._raise_flow_error()
        return stopped

    def _raise_flow_error(self) -> None:
        with self._flow_control_lock:
            error = self._flow_control_error
        if error is not None:
            raise RuntimeError(f"Flow Hover 控制流失败：{error}") from error

    def _shutdown_flow_control(self) -> None:
        self._flow_control_stop.set()
        thread = self._flow_control_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        with self._flow_control_lock:
            scf = self._flow_scf
            self._flow_scf = None
            self._flow_control_thread = None
            self._flow_velocity = (0.0, 0.0, 0.0)
            self._flow_vertical_speed = 0.0
            self._flow_takeoff_target = None
            self._flow_landing = False
        if scf is not None:
            try:
                scf.cf.commander.send_notify_setpoint_stop()
            except Exception:
                pass

    def _clamp_flow_height(self, height_m: float) -> float:
        minimum = max(0.05, self.config.flow_min_height_m)
        maximum = max(minimum, min(self.config.flow_max_height_m, self._flow_detected_max_height))
        return max(minimum, min(maximum, float(height_m)))

    @property
    def flow_target_height(self) -> float:
        with self._flow_control_lock:
            return self._flow_target_height

    def _only_scf(self):
        swarm = self._require_swarm()
        if len(swarm._cfs) != 1:
            raise RuntimeError("Flow Hover 只支持一架 Crazyflie")
        return next(iter(swarm._cfs.values()))

    def _args(self, *values: float) -> dict[str, list[float]]:
        return {uri: list(values) for uri in self._require_swarm()._cfs}

    def _require_swarm(self):
        if self._swarm is None:
            raise RuntimeError("Crazyflie swarm is not connected")
        return self._swarm
