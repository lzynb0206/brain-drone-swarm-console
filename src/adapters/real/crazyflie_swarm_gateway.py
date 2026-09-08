"""Crazyflie swarm adapter with explicit arming and safety gates.

The UI only enqueues commands.  All cflib work runs on one background thread so
Qt never waits for radio connection, estimator reset, or a timed movement.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from dataclasses import dataclass
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
    telemetry_timeout_s: float = 1.5
    lighthouse_ready_status: int = 2
    lighthouse_failure_samples: int = 3
    require_lighthouse: bool = True
    cache_dir: Path = Path(".cache/cflib")
    arm_phrase: str = "启用实机控制"

    @classmethod
    def from_environment(cls) -> "CrazyflieSwarmConfig":
        uris = tuple(value.strip() for value in os.getenv("BDS_CRAZYFLIE_URIS", "").split(",") if value.strip())
        bounds_text = os.getenv("BDS_SWARM_GEOFENCE", "").strip()
        bounds = None
        if bounds_text:
            values = [float(value.strip()) for value in bounds_text.split(",")]
            if len(values) != 6:
                raise ValueError("BDS_SWARM_GEOFENCE 必须是 minX,maxX,minY,maxY,minZ,maxZ")
            bounds = FlightBounds(*values)
        return cls(
            uris=uris,
            control_enabled=os.getenv("BDS_HARDWARE_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
            bounds=bounds,
            default_height_m=float(os.getenv("BDS_SWARM_TAKEOFF_HEIGHT", "0.5")),
            move_distance_m=float(os.getenv("BDS_SWARM_MOVE_DISTANCE", "0.25")),
            move_speed_mps=float(os.getenv("BDS_SWARM_MOVE_SPEED", "0.15")),
            minimum_battery_voltage=float(os.getenv("BDS_SWARM_MIN_VBAT", "3.6")),
            minimum_spacing_m=float(os.getenv("BDS_SWARM_MIN_SPACING", "0.55")),
            lighthouse_failure_samples=int(os.getenv("BDS_LIGHTHOUSE_FAILURE_SAMPLES", "3")),
            require_lighthouse=os.getenv("BDS_REQUIRE_LIGHTHOUSE", "true").strip().lower() not in {"0", "false", "no"},
            cache_dir=Path(os.getenv("BDS_CFLIB_CACHE", ".cache/cflib")).expanduser(),
        )


class CrazyflieSwarmGateway:
    """Queue-based real swarm gateway; no synthetic aircraft are ever created."""

    _ALLOWED_ACTIONS = {"takeoff", "land", "forward", "backward", "left", "right", "hover"}

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

    def connect(self) -> None:
        if not self.config.uris:
            raise RuntimeError("未配置 BDS_CRAZYFLIE_URIS，不能连接 Crazyflie")
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
            return SwarmStatus(
                backend="crazyflie",
                state=self._state,
                connected=self._connected,
                armed=self._armed,
                control_enabled=self.config.control_enabled,
                message=self._message,
            )

    def arm(self, confirmation: str) -> None:
        with self._lock:
            if not self.config.control_enabled:
                raise RuntimeError("当前为只读遥测模式；请显式设置 BDS_HARDWARE_CONTROL_ENABLED=true")
            if confirmation != self.config.arm_phrase:
                raise RuntimeError(f"确认文字不正确，请完整输入：{self.config.arm_phrase}")
            if not self._connected:
                raise RuntimeError("蜂群尚未连接")
            if self._emergency:
                raise RuntimeError("系统处于急停锁定状态，必须重新连接后才能解锁")
            self._validate_ready(require_bounds=True)
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
            if action != "land" and not self._armed:
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
                except Exception as exc:
                    with self._lock:
                        self._armed = False
                        self._message = f"指令失败并锁定控制：{exc}"
                    self._record("ERROR", self._message)
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
        if action == "takeoff":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            duration = max(2.0, height / max(0.05, self.config.move_speed_mps))
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
            self._backend.move_all_relative(dx, dy, dz, duration)
        self._record("INFO", f"飞行指令完成：{action}")

    def _validate_command(self, command: SwarmCommand) -> None:
        action = command.action.strip().lower()
        if action == "land":
            return
        self._validate_ready(require_bounds=action != "hover")
        if action == "takeoff":
            height = float(command.parameters.get("height_m", self.config.default_height_m))
            for uav in self._uavs.values():
                if not self.config.bounds or not self.config.bounds.contains(Position(uav.position.x, uav.position.y, height)):
                    raise RuntimeError(f"{uav.uav_id} 起飞目标超出场地围栏")
            return
        if action == "hover":
            return
        if not self._airborne:
            raise RuntimeError("无人机尚未起飞")
        distance = float(command.parameters.get("distance_m", self.config.move_distance_m))
        dx, dy = {
            "forward": (distance, 0.0), "backward": (-distance, 0.0),
            "left": (0.0, distance), "right": (0.0, -distance),
        }[action]
        targets = [Position(u.position.x + dx, u.position.y + dy, u.position.z) for u in self._uavs.values()]
        if not self.config.bounds or any(not self.config.bounds.contains(target) for target in targets):
            raise RuntimeError("移动目标超出场地围栏")
        for index, first in enumerate(targets):
            for second in targets[index + 1:]:
                if math.dist((first.x, first.y, first.z), (second.x, second.y, second.z)) < self.config.minimum_spacing_m:
                    raise RuntimeError("目标位置小于最小机间距")

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
            if self.config.require_lighthouse and not uav.positioning_ok:
                raise RuntimeError(f"{uav.uav_id} Lighthouse 定位未就绪")

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
        name = uri.rsplit("/", 1)[-1]
        should_trip = False
        with self._lock:
            self._uavs[uri] = UAVTelemetry(
                uav_id=f"CF-{name}", battery=battery, speed_mps=speed, altitude_m=position.z,
                task="飞行" if self._airborne else "待命",
                status=DeviceStatus.NORMAL if lighthouse_ok else DeviceStatus.WARNING,
                position=position, battery_voltage=voltage, positioning_ok=lighthouse_ok,
                last_update_timestamp=float(timestamp) if timestamp > 1_000_000_000 else time.time(),
            )
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
        self._swarm.reset_estimators()
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

    @staticmethod
    def _move_single(scf, dx: float, dy: float, dz: float, duration_s: float) -> None:
        scf.cf.high_level_commander.go_to(dx, dy, dz, 0.0, duration_s, relative=True)

    def hover_all(self, duration_s: float) -> None:
        self._require_swarm().parallel_safe(self._hover_single, self._args(duration_s))
        time.sleep(duration_s)

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
