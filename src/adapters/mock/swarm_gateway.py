from __future__ import annotations

from collections.abc import Sequence

from src.domain import DeviceStatus, Position, SwarmCommand, UAVTelemetry


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

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        return self._uavs

    def send_command(self, command: SwarmCommand) -> None:
        if self.emergency_stopped:
            raise RuntimeError("Swarm is emergency-stopped")
        self.command_log.append(command)

    def emergency_stop(self) -> None:
        self.emergency_stopped = True
        self.command_log.append(SwarmCommand("emergency_stop"))
