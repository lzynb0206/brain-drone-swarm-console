from __future__ import annotations

from typing import Callable, Protocol, Sequence

from src.domain import SwarmCommand, SwarmStatus, UAVTelemetry


class SwarmGateway(Protocol):
    """无人机蜂群遥测与控制边界。

    【真实接口接入点】正式版可新增 ``CrazyflieSwarmGateway``，内部使用
    cflib/无线电或独立控制服务的 WebSocket；界面只通过本协议获取遥测、
    下发已经通过安全仲裁的指令。急停必须走独立高优先级通道，不能只改 UI。
    """

    def connect(self) -> None:
        """在后台建立设备连接；不得阻塞 Qt 主线程。"""
        ...

    def disconnect(self) -> None:
        """安全结束连接。若仍在飞行，应先尝试降落。"""
        ...

    def status(self) -> SwarmStatus:
        """返回缓存状态，不执行网络或无线电 I/O。"""
        ...

    def arm(self, confirmation: str) -> None:
        """显式解锁实机控制；实现必须检查硬件与场地安全条件。"""
        ...

    def disarm(self) -> None:
        """锁定普通控制指令；降落和急停仍应可用。"""
        ...

    def list_uavs(self) -> Sequence[UAVTelemetry]:
        """返回各机位置、电量、速度、高度、任务和在线状态。"""
        ...

    def send_command(self, command: SwarmCommand) -> None:
        """提交经过权限与安全仲裁的串行控制指令。"""
        ...

    def emergency_stop(self) -> None:
        """立即悬停/停机；正式实现需要硬件确认、超时和审计日志。"""
        ...

    def configure_uris(self, uris: Sequence[str]) -> None:
        """在未连接状态更新设备 URI；实现必须拒绝重复或空 URI。"""
        ...

    def add_event_listener(self, listener: Callable[[str, str], None]) -> None:
        """订阅后台指令完成、失败和安全事件。"""
        ...

    def add_command_listener(self, listener: Callable[[str, str, SwarmCommand], None]) -> None:
        """订阅包含 command_id 的命令完成或失败回执。"""
        ...
