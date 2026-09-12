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


class CrazyflieBackend(Protocol):
    def connect(self, uris: tuple[str, ...], telemetry: TelemetryCallback, event: EventCallback) -> None: ...
    def disconnect(self) -> None: ...
    def takeoff_all(self, height_m: float, duration_s: float) -> None: ...
    def land_all(self, duration_s: float) -> None: ...
    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def hover_all(self, duration_s: float) -> None: ...
    def emergency_stop_all(self) -> None: ...
    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None: ...
    def go_to_positions(self, stages: Sequence[dict[str, Position]], duration_s: float) -> None: ...
    def set_led_all(self, red: int, green: int, blue: int, duration_s: float) -> None: ...
    def hover_one_by_one(
        self, height_m: float, height_offsets_m: dict[str, float], hover_s: float, duration_s: float,
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
    default_height_m: float = 0.5
    move_distance_m: float = 0.25
    move_speed_mps: float = 0.15
    minimum_battery_voltage: float = 3.6
    minimum_spacing_m: float = 0.55
    formation_spacing_m: float = 0.8
    formation_max_error_m: float = 0.15
    telemetry_timeout_s: float = 1.5
    lighthouse_ready_status: int = 2
    lighthouse_failure_samples: int = 3
    # Explicitly constructed swarm configurations retain the positioned
    # default; from_environment() selects bare mode for this deployment.
    require_lighthouse: bool = True
    cache_dir: Path = Path(".cache/cflib")
    height_offsets_m: dict[str, float] = field(default_factory=dict)
    positioning_mode: str = "lighthouse"

    @property
    def bare_mode(self) -> bool:
        """Whether this is a single Crazyflie without external positioning."""

        return self.positioning_mode == "lighthouse" and not self.require_lighthouse

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
            default_height_m=float(os.getenv("BDS_SWARM_TAKEOFF_HEIGHT", "0.5")),
            move_distance_m=float(os.getenv("BDS_SWARM_MOVE_DISTANCE", "0.25")),
            move_speed_mps=float(os.getenv("BDS_SWARM_MOVE_SPEED", "0.15")),
            minimum_battery_voltage=float(os.getenv("BDS_SWARM_MIN_VBAT", "3.6")),
            minimum_spacing_m=float(os.getenv("BDS_SWARM_MIN_SPACING", "0.55")),
            formation_spacing_m=float(os.getenv("BDS_SWARM_FORMATION_SPACING", "0.8")),
            formation_max_error_m=float(os.getenv("BDS_SWARM_FORMATION_MAX_ERROR", "0.15")),
            lighthouse_failure_samples=int(os.getenv("BDS_LIGHTHOUSE_FAILURE_SAMPLES", "3")),
            require_lighthouse=(
                mode == "lighthouse"
                and os.getenv("BDS_REQUIRE_LIGHTHOUSE", "false").strip().lower() not in {"0", "false", "no"}
            ),
            cache_dir=Path(os.getenv("BDS_CFLIB_CACHE", ".cache/cflib")).expanduser(),
            height_offsets_m=height_offsets,
            positioning_mode=mode,
        )


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
        self._backend = backend or CflibBackend(config.cache_dir, config.lighthouse_ready_status)
        self._lock = threading.RLock()
        self._commands: queue.Queue[SwarmCommand | None] = queue.Queue(maxsize=20)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
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
        if self.config.bare_mode and len(self.config.uris) != 1:
            raise RuntimeError("无定位模式只允许连接一架 Crazyflie")
        if self.config.positioning_mode == "flowdeck" and len(self.config.uris) != 1:
            raise RuntimeError("Flow Deck 模式只允许配置一架 Crazyflie")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._emergency = False
            self._armed = False
            self._state = "connecting"
            self._message = "正在连接 Crazyradio/Crazyflie…"
            self._thread = threading.Thread(target=self._run, name="Crazyflie-Swarm-Gateway", daemon=True)
            self._thread.start()

    def disconnect(self) -> None:
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
            backend = "bare" if self.config.bare_mode else (
                "flowdeck" if self.config.positioning_mode == "flowdeck" else "crazyflie"
            )
            return SwarmStatus(
                backend=backend,
                state=self._state,
                connected=self._connected,
                armed=self._armed,
                control_enabled=self.config.control_enabled,
                message=self._message,
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
            self._validate_ready(require_bounds=not self.config.bare_mode)
            self._armed = True
            self._message = "实机控制已解锁"
            self._record("INFO", "实机控制已由操作员显式解锁")

    def disarm(self) -> None:
        with self._lock:
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
            if action not in {"land", "led_test"} and not self._armed:
                raise RuntimeError("实机控制尚未解锁")
            self._validate_command(command)
        try:
            self._commands.put_nowait(command)
        except queue.Full as exc:
            raise RuntimeError("飞行指令队列已满，请停止继续操作") from exc

    def emergency_stop(self) -> None:
        with self._lock:
            self._emergency = True
            self._armed = False
            self._state = "emergency"
            self._message = "急停指令已发送；需要现场确认设备状态"
            connected = self._connected
            self._record("ERROR", "操作员触发蜂群急停")
        if connected:
            threading.Thread(target=self._emergency_backend, name="Crazyflie-Emergency", daemon=True).start()
        else:
            raise RuntimeError("蜂群未连接，无法获得硬件急停回执")

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
                self._message = "遥测已连接；实机控制仍处于锁定状态"
            self._record("INFO", f"已连接 {len(self.config.uris)} 架 Crazyflie")
            while not self._stop_event.is_set():
                try:
                    command = self._commands.get(timeout=0.2)
                except queue.Empty:
                    continue
                if command is None:
                    break
                try:
                    self._execute(command)
                    self._notify_command("COMPLETED", "硬件动作执行完成", command)
                except Exception as exc:
                    with self._lock:
                        self._armed = False
                        self._message = f"指令失败并锁定控制：{exc}"
                    self._record("ERROR", self._message)
                    self._notify_command("FAILED", str(exc), command)
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

    def _execute(self, command: SwarmCommand) -> None:
        with self._lock:
            self._validate_command(command)
        action = command.action.strip().lower()
        if action == "led_test":
            self._backend.set_led_all(0, 80, 255, float(command.parameters.get("duration_s", 1.0)))
        elif action == "hover_one_by_one":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            hover_s = max(0.5, float(command.parameters.get("hover_s", 2.0)))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
            with self._lock:
                self._airborne = True
            try:
                self._backend.hover_one_by_one(
                    height, self.config.height_offsets_m, hover_s, duration,
                )
            except Exception:
                # A partially completed acceptance run must never leave its state
                # ambiguous.  Ask every connected aircraft to land before failing.
                self._backend.land_all(3.0)
                raise
            finally:
                with self._lock:
                    self._airborne = False
                    self._armed = False
        elif action == "hover_sync_test":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            hover_s = max(0.5, float(command.parameters.get("hover_s", 2.0)))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
            with self._lock:
                self._airborne = True
            try:
                takeoff_with_offsets = getattr(self._backend, "takeoff_all_with_offsets", None)
                if self.config.height_offsets_m and callable(takeoff_with_offsets):
                    takeoff_with_offsets(height, self.config.height_offsets_m, duration)
                else:
                    self._backend.takeoff_all(height, duration)
                self._backend.hover_all(hover_s)
                self._backend.land_all(duration)
            except Exception:
                self._backend.land_all(3.0)
                raise
            finally:
                with self._lock:
                    self._airborne = False
                    self._armed = False
        elif action == "takeoff":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
            takeoff_with_offsets = getattr(self._backend, "takeoff_all_with_offsets", None)
            if self.config.height_offsets_m and callable(takeoff_with_offsets):
                takeoff_with_offsets(height, self.config.height_offsets_m, duration)
            else:
                self._backend.takeoff_all(height, duration)
            with self._lock:
                self._airborne = True
        elif action == "land":
            self._backend.land_all(3.0)
            with self._lock:
                self._airborne = False
                self._armed = False
        elif action == "hover":
            self._backend.hover_all(0.5)
        elif action in {"formation", "switch_formation"}:
            name = str(command.parameters.get("name", ""))
            if action == "switch_formation":
                name = self._FORMATIONS[(self._FORMATIONS.index(self._formation_mode) + 1) % len(self._FORMATIONS)]
            stages, targets = self._plan_formation(name)
            duration = self._duration_for_positions(targets)
            self._backend.go_to_positions(stages, duration)
            self._verify_position_targets(targets)
            self._formation_mode = name
        elif action == "relative_square":
            distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            for dx, dy in ((distance, 0.0), (0.0, distance), (-distance, 0.0), (0.0, -distance)):
                self._backend.move_all_relative(dx, dy, 0.0, duration)
        elif action == "relative_forward_back":
            distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
            duration = max(1.0, distance / max(0.05, self.config.move_speed_mps))
            self._backend.move_all_relative(distance, 0.0, 0.0, duration)
            self._backend.move_all_relative(-distance, 0.0, 0.0, duration)
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
        self._record("INFO", f"飞行指令完成：{action}")

    def _validate_command(self, command: SwarmCommand) -> None:
        action = command.action.strip().lower()
        if self.config.bare_mode and action in {
            "formation", "switch_formation", "relative_square", "relative_forward_back",
            "hover_one_by_one", "hover_sync_test",
        }:
            raise RuntimeError("无定位单机模式禁止蜂群、队形和自动测试动作")
        if action in {"land", "led_test"}:
            return
        self._validate_ready(require_bounds=action != "hover" and not self.config.bare_mode)
        if action in {"takeoff", "hover_one_by_one", "hover_sync_test"}:
            if self._airborne:
                raise RuntimeError("无人机已经处于飞行状态")
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            for uav in self._uavs.values():
                uri = self._resolve_uri(uav.uav_id)
                target_height = height + self.config.height_offsets_m.get(uri, 0.0)
                if not self.config.bare_mode and (
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
        if not self.config.bare_mode and (
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
        )
        positioning_ok = lighthouse_ok or local_flow_ok
        name = uri.rsplit("/", 1)[-1]
        should_trip = False
        with self._lock:
            self._uavs[uri] = UAVTelemetry(
                uav_id=f"CF-{name}", battery=battery, speed_mps=speed, altitude_m=position.z,
                task="飞行" if self._airborne else "待命",
                status=DeviceStatus.NORMAL if positioning_ok else DeviceStatus.WARNING,
                position=position, battery_voltage=voltage, positioning_ok=positioning_ok,
                last_update_timestamp=float(timestamp) if timestamp > 1_000_000_000 else time.time(),
            )
            # Flow Deck has no external absolute-reference health flag; its mode
            # is restricted to one aircraft and cannot be enabled for BCI control.
            if self._airborne and self.config.require_lighthouse:
                self._lighthouse_failure_counts[uri] = 0 if lighthouse_ok else self._lighthouse_failure_counts.get(uri, 0) + 1
                should_trip = self._lighthouse_failure_counts[uri] >= self.config.lighthouse_failure_samples
            else:
                self._lighthouse_failure_counts[uri] = 0
        if should_trip:
            self._trip_emergency(f"{name} Lighthouse 连续 {self.config.lighthouse_failure_samples} 次未就绪")

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

    def __init__(self, cache_dir: Path, lighthouse_ready_status: int = 2) -> None:
        self.cache_dir = cache_dir
        self.lighthouse_ready_status = lighthouse_ready_status
        self._swarm = None
        self._logs: list[object] = []
        self._telemetry_cache: dict[str, dict[str, float]] = {}
        self._callbacks: list[tuple[object, object]] = []
        self._last_quality_warning: dict[str, float] = {}
        self._event: EventCallback = lambda _level, _message: None

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
        # Do not use Swarm.reset_estimators() here.  cflib waits indefinitely
        # for Kalman position variance to converge, which means a bare
        # Crazyflie (no Lighthouse/Flow deck) never reaches the connected
        # state even though its radio link is healthy.  Reset the estimator
        # without waiting; flight readiness is still checked separately by
        # the gateway before arming or accepting a movement command.
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
            health_log.add_variable("lighthouse.status", "uint8_t")
            health_log.add_variable("lighthouse.bsActive", "uint16_t")
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

    @staticmethod
    def _configure_single(scf) -> None:
        scf.cf.param.set_value("stabilizer.estimator", "2")
        scf.cf.param.set_value("commander.enHighLevel", "1")

    @staticmethod
    def _reset_estimator_single(scf) -> None:
        scf.cf.param.set_value("kalman.resetEstimation", "1")
        time.sleep(0.1)
        scf.cf.param.set_value("kalman.resetEstimation", "0")

    def disconnect(self) -> None:
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

    def takeoff_all(self, height_m: float, duration_s: float) -> None:
        self._require_swarm().parallel_safe(self._takeoff_single, self._args(height_m, duration_s))
        time.sleep(duration_s + 0.5)

    def takeoff_all_with_offsets(self, height_m: float, offsets: dict[str, float], duration_s: float) -> None:
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
        swarm = self._require_swarm()
        swarm.parallel_safe(self._land_single, self._args(duration_s))
        time.sleep(duration_s + 0.5)
        swarm.parallel_safe(self._stop_single)

    @staticmethod
    def _land_single(scf, duration_s: float) -> None:
        scf.cf.high_level_commander.land(0.0, duration_s)

    @staticmethod
    def _stop_single(scf) -> None:
        scf.cf.high_level_commander.stop()

    def move_all_relative(self, dx: float, dy: float, dz: float, duration_s: float) -> None:
        self._require_swarm().parallel_safe(self._move_single, self._args(dx, dy, dz, duration_s))
        time.sleep(duration_s + 0.2)

    def move_one_relative(self, uri: str, dx: float, dy: float, dz: float, duration_s: float) -> None:
        swarm = self._require_swarm()
        if uri not in swarm._cfs:
            raise RuntimeError(f"Crazyflie 未连接：{uri}")
        self._move_single(swarm._cfs[uri], dx, dy, dz, duration_s)
        time.sleep(duration_s + 0.2)

    @staticmethod
    def _move_single(scf, dx: float, dy: float, dz: float, duration_s: float) -> None:
        scf.cf.high_level_commander.go_to(dx, dy, dz, 0.0, duration_s, relative=True)

    def hover_all(self, duration_s: float) -> None:
        self._require_swarm().parallel_safe(self._hover_single, self._args(duration_s))
        time.sleep(duration_s)

    def go_to_positions(self, stages: Sequence[dict[str, Position]], duration_s: float) -> None:
        swarm = self._require_swarm()
        for stage in stages:
            args = {
                uri: [position.x, position.y, position.z, duration_s]
                for uri, position in stage.items()
            }
            swarm.parallel_safe(self._go_absolute_single, args)
            time.sleep(duration_s + 0.25)

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
    ) -> None:
        """Run a sequential takeoff/hover/land acceptance test.

        Only one aircraft is commanded airborne at a time.  The caller still
        performs all normal geofence, battery, positioning and arming checks.
        """

        swarm = self._require_swarm()
        for uri in tuple(swarm._cfs):
            scf = swarm._cfs[uri]
            target_height = height_m + float(height_offsets_m.get(uri, 0.0))
            self._takeoff_single(scf, target_height, duration_s)
            time.sleep(duration_s + 0.3)
            self._hover_single(scf, hover_s)
            time.sleep(hover_s)
            self._land_single(scf, duration_s)
            time.sleep(duration_s + 0.3)
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
        self._require_swarm().parallel_safe(self._emergency_single)

    @staticmethod
    def _emergency_single(scf) -> None:
        supervisor = getattr(scf.cf, "supervisor", None)
        if supervisor is None or not hasattr(supervisor, "send_emergency_stop"):
            raise RuntimeError("当前 Crazyflie 固件/库不支持 supervisor emergency stop")
        supervisor.send_emergency_stop()

    def _args(self, *values: float) -> dict[str, list[float]]:
        return {uri: list(values) for uri in self._require_swarm()._cfs}

    def _require_swarm(self):
        if self._swarm is None:
            raise RuntimeError("Crazyflie swarm is not connected")
        return self._swarm
