"""cflib connection, deck discovery, logging and firmware commands."""

from __future__ import annotations

import threading
import time
from typing import Sequence

from src.adapters.real.crazyflie_contracts import (
    CrazyflieSwarmConfig,
    EventCallback,
    TelemetryCallback,
)
from src.adapters.real.crazyflie_flow_control import FlowControlMixin, _FlowAircraftState

class CflibBackend(FlowControlMixin):
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
            lost_callback = lambda _uri, message, current_uri=uri: event(  # noqa: E731
                "SAFETY", f"{current_uri} 无线电链路丢失：{message}",
            )
            scf.cf.connection_lost.add_callback(lost_callback)
            self._callbacks.append((scf.cf.connection_lost, lost_callback))
            quality_callback = lambda quality, current_uri=uri: self._handle_link_quality(  # noqa: E731
                current_uri, quality,
            )
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
        # Match cflib MotionCommander: let the Kalman estimator converge
        # before the operator can start the motors.
        time.sleep(2.0)

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


__all__ = ["CflibBackend"]
