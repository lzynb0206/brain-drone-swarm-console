from __future__ import annotations

from typing import Protocol, Sequence

from src.domain import SwarmCommand, UAVTelemetry


class SwarmGateway(Protocol):
    """无人机蜂群遥测与控制边界。

    【真实接口接入点】正式版可新增 ``CrazyflieSwarmGateway``，内部使用
    cflib/无线电或独立控制服务的 WebSocket；界面只通过本协议获取遥测、
    下发已经通过安全仲裁的指令。急停必须走独立高优先级通道，不能只改 UI。
    """

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        """返回各机位置、电量、速度、高度、任务和在线状态。"""
        ...

    def send_command(self, command: SwarmCommand) -> None:
        """下发 L1/L2/L3 指令；正式实现前必须加入权限和安全仲裁。"""
        ...

    def emergency_stop(self) -> None:
        """立即悬停/停机；正式实现需要硬件确认、超时和审计日志。"""
        ...
