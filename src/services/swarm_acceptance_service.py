"""Ordered lab acceptance plans for Crazyflie/Lighthouse integration."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain import SwarmCommand
from src.ports import SwarmGateway


@dataclass(frozen=True)
class AcceptanceStep:
    name: str
    command: SwarmCommand
    requires_airborne: bool = False


class SwarmAcceptanceService:
    """Submit one operator-confirmed step at a time through normal safety gates."""

    CONFIRMATION = "运行受限验收"

    def __init__(self, gateway: SwarmGateway) -> None:
        self.gateway = gateway

    def plan(self, mode: str) -> tuple[AcceptanceStep, ...]:
        plans = {
            "led": (AcceptanceStep("LED 无桨连通性测试", SwarmCommand("led_test")),),
            "hover_one_by_one": (
                AcceptanceStep("逐架起飞、悬停并降落", SwarmCommand("hover_one_by_one")),
            ),
            "hover_sync": (
                AcceptanceStep("同步起飞、悬停并降落", SwarmCommand("hover_sync_test")),
            ),
            "relative_forward_back": (
                AcceptanceStep("相对前进并返回", SwarmCommand("relative_forward_back"), True),
            ),
            "relative_square": (
                AcceptanceStep("相对方形轨迹", SwarmCommand("relative_square"), True),
            ),
            "absolute_line": (
                AcceptanceStep(
                    "绝对坐标一字队形",
                    SwarmCommand("formation", parameters={"name": "line"}),
                    True,
                ),
            ),
        }
        if mode not in plans:
            raise ValueError(f"未知验收模式：{mode}")
        return plans[mode]

    def submit_step(self, mode: str, step_index: int, confirmation: str) -> str:
        if confirmation != self.CONFIRMATION:
            raise RuntimeError(f"确认文字不正确，请输入：{self.CONFIRMATION}")
        steps = self.plan(mode)
        if not 0 <= step_index < len(steps):
            raise IndexError("验收步骤序号越界")
        status = self.gateway.status()
        if not status.connected:
            raise RuntimeError("蜂群未连接")
        if steps[step_index].command.action != "led_test" and not status.armed:
            raise RuntimeError("飞行验收前必须完成实机解锁")
        self.gateway.send_command(steps[step_index].command)
        return steps[step_index].name
