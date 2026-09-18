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

    def arm(self) -> None:
        """通过一次按钮点击解锁；实现仍须检查硬件安全条件。"""
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

    def stop_motion(self) -> None:
        """停止当前水平动作并保持飞行；无定位模式只能保持姿态/高度，不能定点。"""
        ...

    def stop_motors(self) -> None:
        """立即关闭电机但不触发固件急停锁；空中使用会直接坠落。"""
        ...

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> None:
        """更新虚拟摇杆输入，三个轴均为 -1.0 到 1.0。"""
        ...

    def emergency_stop(self) -> None:
        """触发固件锁存急停；恢复前必须重启无人机。"""
        ...

    def reset_after_emergency_reboot(self) -> None:
        """无人机已断电重启且链路断开后，清除上位机的急停锁状态。"""
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
