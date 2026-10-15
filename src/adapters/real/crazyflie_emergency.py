"""Emergency lock and recoverable motor-stop coordination."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.real.crazyflie_swarm_gateway import CrazyflieSwarmGateway


class CrazyflieEmergencyCoordinator:
    """Coordinate queue invalidation, backend calls and terminal flight state."""

    def __init__(self, gateway: CrazyflieSwarmGateway) -> None:
        self.gateway = gateway

    def stop_motors(self) -> None:
        gateway = self.gateway
        with gateway._lock:
            if not gateway._connected:
                raise RuntimeError("蜂群尚未连接")
            if gateway._emergency:
                raise RuntimeError("当前已处于硬件急停锁定状态")
            if gateway._motor_stop_in_progress:
                return
            gateway._command_generation += 1
            gateway._discard_pending_commands("操作员停止电机")
            gateway._armed = False
            gateway._armed_uris.clear()
            gateway._motor_stop_in_progress = True
            gateway._motor_stop_backend_complete = False
            gateway._motor_stop_error = None
            gateway._state = "stopping_motors"
            gateway._message = "正在发送电机停止指令…"
            gateway._record("WARN", "操作员触发可恢复的电机立即停转")
        threading.Thread(
            target=self._stop_motors_backend,
            name="Crazyflie-Motor-Stop",
            daemon=True,
        ).start()

    def emergency_stop(self) -> None:
        gateway = self.gateway
        with gateway._lock:
            gateway._command_generation += 1
            gateway._discard_pending_commands("紧急停止")
            gateway._emergency = True
            self._clear_flight_state()
            gateway._refresh_cached_flight_states()
            gateway._state = "emergency"
            gateway._message = "急停指令已发送；需要现场确认设备状态"
            connected = gateway._connected
            gateway._record("ERROR", "操作员触发蜂群急停")
        if connected:
            threading.Thread(
                target=self._emergency_backend,
                name="Crazyflie-Emergency",
                daemon=True,
            ).start()
            return
        raise RuntimeError("蜂群未连接，无法获得硬件急停回执")

    def reset_after_reboot(self) -> None:
        gateway = self.gateway
        with gateway._lock:
            if gateway._connected or (
                gateway._thread is not None and gateway._thread.is_alive()
            ):
                raise RuntimeError("请先断开链路，并先给无人机断电重启")
            if gateway._motor_stop_in_progress:
                raise RuntimeError("电机停止线程尚未结束，请稍候再复位")
            gateway._emergency = False
            gateway._state = "disconnected"
            gateway._message = "急停锁已复位；等待重新连接"
            gateway._record("INFO", "操作员确认无人机已重启，上位机急停锁复位")

    def lock_from_firmware(self) -> bool:
        gateway = self.gateway
        with gateway._lock:
            if gateway._emergency:
                return False
            gateway._command_generation += 1
            gateway._discard_pending_commands("飞控进入锁定状态")
            gateway._emergency = True
            self._clear_flight_state()
            gateway._refresh_cached_flight_states()
            gateway._state = "emergency"
            gateway._message = "飞控处于锁定状态；请给无人机断电重启后再复位"
            return True

    def trip(self, reason: str) -> None:
        gateway = self.gateway
        with gateway._lock:
            if gateway._emergency or not gateway._airborne:
                gateway._record("WARN", f"安全事件（未在飞行）：{reason}")
                return
        gateway._record("ERROR", f"安全门控触发急停：{reason}")
        try:
            self.emergency_stop()
        except RuntimeError as exc:
            gateway._record("ERROR", f"自动急停未送达：{exc}")

    def _emergency_backend(self) -> None:
        gateway = self.gateway
        try:
            gateway._backend.emergency_stop_all()
            gateway._record("ERROR", "硬件急停调用已完成；请现场确认电机状态")
        except Exception as exc:
            gateway._record("ERROR", f"硬件急停调用失败：{exc}")

    def _stop_motors_backend(self) -> None:
        gateway = self.gateway
        error: Exception | None = None
        try:
            gateway._backend.stop_motors_all()
            gateway._record("WARN", "电机停止调用已完成")
        except Exception as exc:
            error = exc
            gateway._record("ERROR", f"电机停止调用失败：{exc}")
        finally:
            with gateway._lock:
                gateway._motor_stop_backend_complete = True
                gateway._motor_stop_error = error
            self.try_finalize_motor_stop()

    def try_finalize_motor_stop(self) -> None:
        gateway = self.gateway
        with gateway._lock:
            if (
                not gateway._motor_stop_in_progress
                or not gateway._motor_stop_backend_complete
                or not gateway._command_idle.is_set()
            ):
                return
            error = gateway._motor_stop_error
            gateway._motor_stop_in_progress = False
            gateway._motor_stop_backend_complete = False
            gateway._motor_stop_error = None
            if gateway._emergency:
                return
            if error is None:
                self._clear_flight_state()
                gateway._refresh_cached_flight_states()
                gateway._state = "connected"
                gateway._message = (
                    "电机已停止；曾检测到低电压（仅提示）"
                    if gateway._telemetry_safety.battery_low_latched
                    else "电机已停止；重新起飞前需要再次解锁"
                )
            else:
                gateway._state = "stop_error"
                gateway._message = f"电机停止未确认：{error}；请立即现场处置"

    def _clear_flight_state(self) -> None:
        gateway = self.gateway
        gateway._armed = False
        gateway._airborne = False
        gateway._armed_uris.clear()
        gateway._taking_off_uris.clear()
        gateway._airborne_uris.clear()
        gateway._horizontal_ready_uris.clear()
        gateway._landing_uris.clear()
        gateway._lighthouse_hold_uris.clear()
        gateway._lighthouse_targets_by_uri.clear()
        gateway._formation_slots_by_uri.clear()
        gateway._formation_plan = None
        gateway._landing_requested = False


__all__ = ["CrazyflieEmergencyCoordinator"]
