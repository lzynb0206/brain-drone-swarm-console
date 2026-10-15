"""Safety-aware swarm orchestration independent from workbench presentation state."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from src.domain import SwarmCommand


class SwarmControlService:
    """Own gateway interaction, command audit and emergency state."""

    def __init__(
        self,
        gateway,
        experiment_recorder,
        record: Callable[[str], str],
    ) -> None:
        self.gateway = gateway
        self.experiment_recorder = experiment_recorder
        self._record = record
        self.disable_bci: Callable[[], None] = lambda: None
        self.emergency_stopped = False
        self.command_history: list[str] = ["前进", "悬停"]
        if gateway is not None:
            add_listener = getattr(gateway, "add_event_listener", None)
            if callable(add_listener):
                add_listener(self._on_gateway_event)
            add_command_listener = getattr(gateway, "add_command_listener", None)
            if callable(add_command_listener):
                add_command_listener(self._on_command_result)

    def _on_gateway_event(self, level: str, message: str) -> None:
        self._record(f"{level.upper()}  {message}")
        if "急停" in message and level.upper() in {"ERROR", "SAFETY"}:
            self.emergency_stopped = True
            self.disable_bci()
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(level.upper(), message)

    def _on_command_result(self, status: str, message: str, command: SwarmCommand) -> None:
        self._record(f"{status.upper()}  {command.action}  {message}")
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(
                status.upper(),
                message,
                command.action,
                command.command_id,
                command.uav_id or "",
            )

    def send_command(
        self,
        action: str,
        uav_id: str | None = None,
        parameters: dict[str, float | str] | None = None,
    ) -> str:
        labels = {
            "takeoff": "起飞",
            "land": "降落",
            "forward": "前进",
            "backward": "后退",
            "left": "左移",
            "right": "右移",
            "hover": "悬停",
            "led_test": "LED连通性测试",
            "lighthouse_hold": "切换Lighthouse保持",
            "form_line": "组成直线编队",
            "form_triangle": "组成三角编队",
        }
        if self.emergency_stopped:
            return self._record("系统处于急停状态，指令被安全策略拒绝")
        command_id = uuid.uuid4().hex
        command_parameters = dict(parameters or {})
        if action in {"forward", "backward", "left", "right"}:
            # Snapshot the configured step when the command is submitted so a
            # later UI adjustment cannot change an already queued movement.
            command_parameters.setdefault("distance_m", self.move_distance_m())
        command = SwarmCommand(
            action,
            uav_id=uav_id,
            parameters=command_parameters,
            command_id=command_id,
        )
        if self.gateway is not None:
            try:
                self.gateway.send_command(command)
            except RuntimeError as exc:
                if self.experiment_recorder is not None:
                    self.experiment_recorder.record_command_event(
                        "REJECTED", str(exc), action, command_id, uav_id or "",
                    )
                return self._record(f"指令失败：{exc}")
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(
                "SUBMITTED", "指令已进入后台队列", action, command_id, uav_id or "",
            )
        label = labels.get(action, action)
        if uav_id:
            label = f"{uav_id} · {label}"
        self.command_history.insert(0, label)
        del self.command_history[8:]
        return self._record(f"蜂群指令已提交：{label}")

    def configure_uris(self, uris: list[str]) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.gateway.configure_uris(uris)
            return self._record(f"已配置 {len(uris)} 个 Crazyflie URI")
        except RuntimeError as exc:
            return self._record(f"URI 配置失败：{exc}")

    def takeoff_height_m(self) -> float:
        if self.gateway is None:
            return 0.4
        getter = getattr(self.gateway, "takeoff_height_m", None)
        return float(getter()) if callable(getter) else 0.4

    def set_takeoff_height(self, height_m: float) -> str:
        if self.gateway is None:
            return self._record("无人机接口未配置")
        try:
            self.gateway.set_takeoff_height(height_m)
            return self._record(f"Flow Hover 启动目标高度：{height_m:.2f}m")
        except RuntimeError as exc:
            return self._record(f"目标高度设置失败：{exc}")

    def adjust_takeoff_height(self, delta_m: float) -> str:
        return self.set_takeoff_height(round(self.takeoff_height_m() + float(delta_m), 2))

    def move_distance_m(self) -> float:
        if self.gateway is None:
            return 0.05
        getter = getattr(self.gateway, "move_distance_m", None)
        if callable(getter):
            return float(getter())
        config = getattr(self.gateway, "config", None)
        return float(getattr(config, "move_distance_m", 0.05))

    def set_move_distance(self, distance_m: float) -> str:
        if self.gateway is None:
            return self._record("无人机接口未配置")
        setter = getattr(self.gateway, "set_move_distance", None)
        if not callable(setter):
            return self._record("当前无人机接口不支持在线调节移动距离")
        try:
            setter(distance_m)
        except (TypeError, ValueError, RuntimeError) as exc:
            return self._record(f"移动距离设置失败：{exc}")
        distance = self.move_distance_m()
        return self._record(f"SSVEP 单次移动距离：{distance * 100:g}cm")

    def connect(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.gateway.connect()
            return self._record("Crazyflie 连接任务已在后台启动")
        except RuntimeError as exc:
            return self._record(f"连接失败：{exc}")

    def disconnect(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        self.disable_bci()
        self.gateway.disconnect()
        return self._record("蜂群连接已安全关闭")

    def arm(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.gateway.arm()
            return self._record("实机控制已解锁；飞行指令将发送到硬件")
        except RuntimeError as exc:
            return self._record(f"解锁失败：{exc}")

    def disarm(self) -> str:
        self.disable_bci()
        if self.gateway is not None:
            try:
                self.gateway.disarm()
            except RuntimeError as exc:
                return self._record(f"锁定失败：{exc}")
        return self._record("实机控制和脑控执行均已锁定")

    def stop_motion(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.gateway.stop_motion()
            self.command_history.insert(0, "停止移动/保持高度")
            del self.command_history[8:]
            return self._record("已请求停止水平移动并保持当前高度")
        except RuntimeError as exc:
            return self._record(f"停止移动失败：{exc}")

    def stop_motors(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        self.disable_bci()
        try:
            self.gateway.stop_motors()
            self.command_history.insert(0, "电机立即停转")
            del self.command_history[8:]
            return self._record("正在停止电机；完成后重新起飞需要再次解锁")
        except RuntimeError as exc:
            return self._record(f"电机停止失败：{exc}")

    def set_manual_input(self, vertical: float, forward: float, lateral: float) -> str | None:
        if self.gateway is None:
            return "无人机接口未配置"
        try:
            self.gateway.set_manual_input(vertical, forward, lateral)
        except RuntimeError as exc:
            return f"摇杆控制失败：{exc}"
        return None

    def emergency_stop(self) -> str:
        hardware_message = ""
        if not self.emergency_stopped and self.gateway is not None:
            try:
                self.gateway.emergency_stop()
                hardware_message = "，硬件急停调用已发送"
            except RuntimeError as exc:
                hardware_message = f"；硬件急停未送达：{exc}"
        self.emergency_stopped = True
        self.disable_bci()
        self.command_history.insert(0, "紧急停止")
        return self._record(f"本地控制已锁定{hardware_message}；请断电重启无人机后再复位")

    def reset_emergency_after_reboot(self) -> str:
        if self.gateway is None:
            return self._record("蜂群接口未配置")
        try:
            self.gateway.disconnect()
            self.gateway.reset_after_emergency_reboot()
        except RuntimeError as exc:
            return self._record(f"急停复位失败：{exc}")
        self.emergency_stopped = False
        self.disable_bci()
        return self._record(
            "上位机急停锁已复位；请重新连接并解锁（本操作不能代替无人机断电重启）"
        )
