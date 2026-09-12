from __future__ import annotations

from collections.abc import Sequence

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
        self._listeners = []
        self._command_listeners = []

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._armed = False

    def status(self) -> SwarmStatus:
        return SwarmStatus("mock", "connected" if self._connected else "disconnected", self._connected,
                           self._armed, True, "仅供测试")

    def arm(self) -> None:
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        return self._uavs

    def send_command(self, command: SwarmCommand) -> None:
        if self.emergency_stopped:
            raise RuntimeError("Swarm is emergency-stopped")
        self.command_log.append(command)
        for listener in tuple(self._listeners):
            listener("INFO", f"飞行指令完成：{command.action}")
        for listener in tuple(self._command_listeners):
            listener("COMPLETED", "测试适配器已执行", command)

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
        self.command_log.append(SwarmCommand("emergency_stop"))
