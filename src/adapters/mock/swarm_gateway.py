from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.domain import DeviceStatus, Position, SwarmCommand, SwarmStatus, UAVTelemetry


class MockSwarmGateway:
    """内存中的演示蜂群，不会向无人机发送无线电指令。

    【真实接口接入点】另建 ``CrazyflieSwarmGateway`` 实现同一协议，再在
    container.py 替换本类；真实急停必须有硬件回执和失效保护。
    """

    def __init__(self) -> None:
        self.command_log: list[SwarmCommand] = []
        self.emergency_stopped = False
        self._uavs = (
            UAVTelemetry("UAV-01", 82, 2.1, 1.4, "侦察", DeviceStatus.NORMAL, Position(0.15, 0.70, 1.4)),
            UAVTelemetry("UAV-02", 75, 1.8, 1.5, "编队", DeviceStatus.NORMAL, Position(0.25, 0.25, 1.5)),
            UAVTelemetry("UAV-03", 78, 1.8, 1.2, "编队", DeviceStatus.WARNING, Position(0.55, 0.22, 1.2)),
            UAVTelemetry("UAV-04", 86, 2.0, 1.6, "警戒", DeviceStatus.NORMAL, Position(0.65, 0.55, 1.6)),
        )
        self._connected = True
        self._armed = True
        self._airborne = False
        self._listeners = []
        self._command_listeners = []
        self.manual_input = (0.0, 0.0, 0.0)
        self._takeoff_height_m = 0.4

    def connect(self) -> None:
        if self.emergency_stopped:
            raise RuntimeError("急停锁仍未复位")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._armed = False
        self._airborne = False
        self._uavs = tuple(
            replace(uav, armed=False, airborne=False) for uav in self._uavs
        )

    def status(self) -> SwarmStatus:
        state = "emergency" if self.emergency_stopped else ("connected" if self._connected else "disconnected")
        return SwarmStatus(
            "mock", state, self._connected, self._armed, True, "仅供测试",
            airborne=self._airborne, emergency=self.emergency_stopped,
        )

    def arm(self) -> None:
        if not self._connected:
            raise RuntimeError("蜂群尚未连接")
        if self.emergency_stopped:
            raise RuntimeError("系统处于急停锁定状态")
        self._armed = True
        self._uavs = tuple(replace(uav, armed=True) for uav in self._uavs)

    def disarm(self) -> None:
        if self._airborne:
            raise RuntimeError("飞行中不能只锁定控制，请先降落或停止电机")
        self._armed = False
        self._uavs = tuple(replace(uav, armed=False) for uav in self._uavs)

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        return self._uavs

    def send_command(self, command: SwarmCommand) -> None:
        if not self._connected:
            raise RuntimeError("蜂群尚未连接")
        if self.emergency_stopped:
            raise RuntimeError("Swarm is emergency-stopped")
        if command.action not in {"takeoff", "land", "led_test"} and not self._armed:
            raise RuntimeError("实机控制尚未解锁")
        if command.action in {"forward", "backward", "left", "right", "move_relative", "hover"} and not self._airborne:
            raise RuntimeError("无人机尚未起飞")
        self.command_log.append(command)
        if command.action == "takeoff":
            self._armed = True
            self._airborne = True
            height = float(command.parameters.get("height_m", self._takeoff_height_m))
            self._update_positions(
                command.uav_id, dz=height, absolute_z=True, armed=True, airborne=True,
            )
        elif command.action == "move_relative":
            self._update_positions(
                command.uav_id,
                dx=float(command.parameters.get("dx_m", 0.0)),
                dy=float(command.parameters.get("dy_m", 0.0)),
                dz=float(command.parameters.get("dz_m", 0.0)),
            )
        elif command.action == "land":
            self._update_positions(
                command.uav_id, dz=0.0, absolute_z=True, armed=False, airborne=False,
            )
            self._airborne = any(item.airborne for item in self._uavs)
            self._armed = any(item.armed for item in self._uavs)
        for listener in tuple(self._listeners):
            listener("INFO", f"飞行指令完成：{command.action}")
        for listener in tuple(self._command_listeners):
            listener("COMPLETED", "测试适配器已执行", command)

    def _update_positions(
        self,
        uav_id: str | None,
        *,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        absolute_z: bool = False,
        armed: bool | None = None,
        airborne: bool | None = None,
    ) -> None:
        updated = []
        for uav in self._uavs:
            if uav_id is not None and uav.uav_id != uav_id:
                updated.append(uav)
                continue
            z = dz if absolute_z else uav.position.z + dz
            position = Position(uav.position.x + dx, uav.position.y + dy, z)
            updated.append(replace(
                uav,
                position=position,
                altitude_m=z,
                armed=uav.armed if armed is None else armed,
                airborne=uav.airborne if airborne is None else airborne,
            ))
        self._uavs = tuple(updated)

    def stop_motion(self) -> None:
        if not self._airborne:
            raise RuntimeError("无人机尚未起飞")
        command = SwarmCommand("stop_motion")
        self.command_log.append(command)
        for listener in tuple(self._listeners):
            listener("INFO", "操作员停止移动并进入保持状态")
        for listener in tuple(self._command_listeners):
            listener("COMPLETED", "测试适配器已停止移动", command)

    def stop_motors(self) -> None:
        if not self._connected:
            raise RuntimeError("蜂群尚未连接")
        command = SwarmCommand("motor_stop")
        self.command_log.append(command)
        self._airborne = False
        self._armed = False
        self._uavs = tuple(
            replace(uav, armed=False, airborne=False) for uav in self._uavs
        )
        for listener in tuple(self._listeners):
            listener("WARN", "电机停止调用已完成")
        for listener in tuple(self._command_listeners):
            listener("COMPLETED", "测试适配器已停止电机", command)

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        if not self._connected:
            raise RuntimeError("蜂群尚未连接")
        if not self._armed:
            raise RuntimeError("实机控制尚未解锁")
        values = tuple(max(-1.0, min(1.0, float(value))) for value in (vertical, forward, lateral))
        self.manual_input = values
        if values[0] > 0.08:
            self._airborne = True
            self._uavs = tuple(
                replace(uav, armed=True, airborne=True) for uav in self._uavs
            )

    def takeoff_height_m(self) -> float:
        return self._takeoff_height_m

    def set_takeoff_height(self, height_m: float) -> None:
        height = float(height_m)
        if not 0.2 <= height <= 2.0:
            raise RuntimeError("目标高度必须在 0.20–2.00m 之间")
        if self._armed or self._airborne:
            raise RuntimeError("Flow Hover 已启动，不能修改启动目标高度")
        self._takeoff_height_m = height

    def configure_uris(self, uris) -> None:
        if self._connected:
            raise RuntimeError("测试网关已连接，不能修改 URI")
        normalized = [str(uri).strip() for uri in uris if str(uri).strip()]
        if not normalized or len(set(normalized)) != len(normalized):
            raise RuntimeError("URI 不能为空且不能重复")

    def add_event_listener(self, listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def add_command_listener(self, listener) -> None:
        if listener not in self._command_listeners:
            self._command_listeners.append(listener)

    def emergency_stop(self) -> None:
        self.emergency_stopped = True
        self._airborne = False
        self._armed = False
        self._uavs = tuple(
            replace(uav, armed=False, airborne=False) for uav in self._uavs
        )
        self.command_log.append(SwarmCommand("emergency_stop"))

    def reset_after_emergency_reboot(self) -> None:
        if self._connected:
            raise RuntimeError("请先断开连接")
        self.emergency_stopped = False
