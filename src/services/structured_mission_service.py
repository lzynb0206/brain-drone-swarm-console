"""Structured mission configuration, planning and execution for one UAV."""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping

from src.domain import (
    IntentResult,
    MissionPlan,
    MissionPolicy,
    MissionStep,
    Position,
    SwarmCommand,
    TargetPoint,
)
from src.ports import SwarmGateway


class MissionPlanningError(RuntimeError):
    """The decoded intent or current aircraft state cannot produce a safe mission."""


class StructuredMissionService:


    SIMULATED_FBCCA_OUTPUT: Mapping[str, object] = {
        "label": "去A点", #界面显示和日志记录
        "confidence": 0.96,
        "margin": 0.32, #第一候选结果与第二候选结果的分数差距 越大表示识别结果越明确
        "accepted": True,
        "reason": "模拟 FBCCA 结构化输出", #说明为什么接受或者拒绝识别结果，主要用于日志。
        "frequency_hz": 0.0, #ssvep频率


        "task_type": "GOTO", #GOTO:前往目标 INSPECT：巡检目标点
        "target_id": "A", #目标点编号
        "arrival_action": "HOLD", #HOLD:到达后保持当前位置，不进行定时观察  "HOVER"：到达后悬停观察指定时间
        "observe_seconds": 0.0, #HOVER悬停时间
        "return_policy": "NONE", #是否返航 RETURN_TO_START 返航
        "landing_policy": "NONE", #是否降落 任务结束时 LAND：自动受控降落
    }

    # 2. 任务目标点；Position 的单位为米。
    DEFAULT_TARGETS: Mapping[str, TargetPoint] = {

        "A": TargetPoint("A", "A点观察区", Position(1.0 / 3.0, 0.5 / 3.0, 0.2)),
        
    }
    # 3. 当解码结果只有 label、没有结构化字段时使用的语义映射。
    TASK_POLICIES: Mapping[str, MissionPolicy] = {
        "去A点": MissionPolicy("GOTO", "A"),
        "去A点并降落": MissionPolicy("GOTO", "A", landing_policy="LAND"),
        "巡检A点": MissionPolicy(
            "INSPECT",
            "A",
            arrival_action="HOVER",
            observe_seconds=5.0,
            return_policy="RETURN_TO_START",
            landing_policy="LAND",
        ),
        "巡检A点不返航": MissionPolicy(
            "INSPECT",
            "A",
            arrival_action="HOVER",
            observe_seconds=5.0,
        ),
    }

    @classmethod
    def simulated_fbcca_result(cls) -> IntentResult:
        """Build the current hand-written structured result as if FBCCA produced it."""
        data = cls.SIMULATED_FBCCA_OUTPUT
        label = str(data["label"])
        confidence = float(data["confidence"])
        return IntentResult(
            timestamp=time.time(),
            label=label,
            confidence=confidence,
            margin=float(data["margin"]),
            accepted=bool(data["accepted"]),
            reason=str(data["reason"]),
            frequency_hz=float(data["frequency_hz"]),
            scores={label: confidence},
            sample_rate_hz=0.0,
            sample_count=0,
            used_channels_1based=(),
            signal_quality={"source": "simulated_fbcca_structured_output"},
            task_type=str(data["task_type"]),
            target_id=str(data["target_id"]),
            arrival_action=str(data["arrival_action"]),
            observe_seconds=float(data["observe_seconds"]),
            return_policy=str(data["return_policy"]),
            landing_policy=str(data["landing_policy"]),
        )

    def __init__(
        self,
        gateway: SwarmGateway | None,
        event_listener: Callable[[str], None] | None = None,
        experiment_recorder=None,
        targets: Mapping[str, TargetPoint] | None = None,
        waypoint_spacing_m: float = 0.25,
        cruise_speed_mps: float = 0.15,
    ) -> None:
        self.gateway = gateway
        self.event_listener = event_listener
        self.experiment_recorder = experiment_recorder
        self.targets = dict(targets or self.DEFAULT_TARGETS)
        self.waypoint_spacing_m = max(0.05, float(waypoint_spacing_m))
        self.cruise_speed_mps = max(0.05, float(cruise_speed_mps))
        self._lock = threading.RLock()
        self._active_plan: MissionPlan | None = None
        self._pending_steps: deque[MissionStep] = deque()
        self._active_command_id = ""
        self._completed_steps = 0
        self.last_status = "IDLE"
        if gateway is not None:
            add_listener = getattr(gateway, "add_command_listener", None)
            if callable(add_listener):
                add_listener(self._on_command_result)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active_plan is not None

    @classmethod
    def normalize_label(cls, label: str) -> str:
        return (
            str(label)
            .strip()
            .replace(" ", "")
            .replace("，", "")
            .replace(",", "")
            .replace("a", "A")
        )

    @classmethod
    def supports(cls, label: str) -> bool:
        return cls.normalize_label(label) in cls.TASK_POLICIES

    @classmethod
    def supports_result(cls, result: IntentResult) -> bool:
        return bool(result.task_type) or cls.supports(result.label)

    def _policy_from_result(self, result: IntentResult) -> MissionPolicy:
        if not result.task_type:
            policy = self.TASK_POLICIES.get(self.normalize_label(result.label))
            if policy is None:
                raise MissionPlanningError(f"暂不支持高层意图：{result.label}")
            return policy

        if not result.target_id:
            raise MissionPlanningError("结构化任务缺少 target_id")
        policy = MissionPolicy(
            task_type=result.task_type.strip().upper(),
            target_id=result.target_id.strip().upper(),
            arrival_action=(result.arrival_action or "HOLD").strip().upper(),
            observe_seconds=max(0.0, float(result.observe_seconds)),
            return_policy=(result.return_policy or "NONE").strip().upper(),
            landing_policy=(result.landing_policy or "NONE").strip().upper(),
        )
        if policy.task_type not in {"GOTO", "INSPECT"}:
            raise MissionPlanningError(f"不支持的 task_type：{policy.task_type}")
        if policy.arrival_action not in {"HOLD", "HOVER"}:
            raise MissionPlanningError(f"不支持的 arrival_action：{policy.arrival_action}")
        if policy.return_policy not in {"NONE", "RETURN_TO_START"}:
            raise MissionPlanningError(f"不支持的 return_policy：{policy.return_policy}")
        if policy.landing_policy not in {"NONE", "LAND"}:
            raise MissionPlanningError(f"不支持的 landing_policy：{policy.landing_policy}")
        return policy

    def prepare(self, result: IntentResult) -> MissionPlan:
        if not result.accepted:
            raise MissionPlanningError(f"脑电意图未通过安全门控：{result.reason}")
        policy = self._policy_from_result(result)
        target = self.targets.get(policy.target_id)
        if target is None:
            raise MissionPlanningError(f"找不到目标点：{policy.target_id}")
        if self.gateway is None:
            raise MissionPlanningError("无人机接口未配置")

        gateway_status = self.gateway.status()
        if not gateway_status.connected:
            raise MissionPlanningError("无人机尚未连接")
        if gateway_status.emergency:
            raise MissionPlanningError("无人机处于急停锁定状态")
        if gateway_status.backend != "mock" and not gateway_status.control_enabled:
            raise MissionPlanningError("当前为只读遥测模式，尚未允许实机执行")
        aircraft = tuple(self.gateway.list_uavs())
        if not aircraft:
            raise MissionPlanningError("尚未收到单机遥测")

        # The real Flow Deck adapter exposes exactly one aircraft. The demo adapter
        # contains several display aircraft, so single-UAV development fixes UAV-01.
        uav = aircraft[0]
        if gateway_status.backend != "mock" and not uav.positioning_ok:
            raise MissionPlanningError(f"{uav.uav_id} 定位尚未就绪")

        observe_seconds = max(0.0, float(policy.observe_seconds))
        start = uav.position
        cruise_start = start if uav.airborne else Position(start.x, start.y, target.position.z)
        dx = target.position.x - cruise_start.x
        dy = target.position.y - cruise_start.y
        dz = target.position.z - cruise_start.z
        outbound_distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if outbound_distance <= 1e-6:
            raise MissionPlanningError(f"无人机已经位于{target.name}，无需重复生成前往任务")
        waypoints = self._straight_line_waypoints(cruise_start, target.position)

        steps: list[MissionStep] = []
        if not uav.airborne:
            steps.append(
                MissionStep(
                    "takeoff",
                    f"起飞到 {target.position.z:.2f} 米",
                    {"height_m": target.position.z},
                )
            )
        steps.append(
            MissionStep(
                "move_relative",
                f"沿最短直线飞往{target.name}",
                {
                    "dx_m": dx,
                    "dy_m": dy,
                    "dz_m": dz,
                    "distance_m": outbound_distance,
                },
            )
        )
        if policy.arrival_action == "HOVER" and observe_seconds > 0.0:
            steps.append(
                MissionStep(
                    "hover",
                    f"在{target.name}悬停观察 {observe_seconds:.0f} 秒",
                    {"duration_s": observe_seconds},
                )
            )
        if policy.return_policy == "RETURN_TO_START":
            steps.append(
                MissionStep(
                    "move_relative",
                    "沿原直线路径返回起点",
                    {
                        "dx_m": -dx,
                        "dy_m": -dy,
                        "dz_m": -dz,
                        "distance_m": outbound_distance,
                    },
                )
            )
        if policy.landing_policy == "LAND":
            location = "起点" if policy.return_policy == "RETURN_TO_START" else target.name
            steps.append(MissionStep("land", f"在{location}受控降落"))

        total_distance = outbound_distance * (
            2.0 if policy.return_policy == "RETURN_TO_START" else 1.0
        )
        estimated_duration = (
            (0.0 if uav.airborne else max(2.0, target.position.z / self.cruise_speed_mps))
            + (total_distance / self.cruise_speed_mps)
            + (observe_seconds if policy.arrival_action == "HOVER" else 0.0)
            + (3.0 if policy.landing_policy == "LAND" else 0.0)
        )
        return MissionPlan(
            mission_id=f"mission-{uuid.uuid4().hex[:10]}",
            task_label=result.label,
            policy=policy,
            target=target,
            uav_id=uav.uav_id,
            start_position=start,
            cruise_start=cruise_start,
            waypoints=waypoints,
            outbound_distance_m=outbound_distance,
            total_distance_m=total_distance,
            estimated_duration_s=estimated_duration,
            steps=tuple(steps),
        )

    def execute(self, plan: MissionPlan) -> None:
        if self.gateway is None:
            raise RuntimeError("无人机接口未配置")
        with self._lock:
            if self._active_plan is not None:
                raise RuntimeError(f"任务 {self._active_plan.mission_id} 正在执行")
            self._active_plan = plan
            self._pending_steps = deque(plan.steps)
            self._active_command_id = ""
            self._completed_steps = 0
            self.last_status = "EXECUTING"
        self._emit(f"任务 {plan.mission_id} 已确认，开始由 {plan.uav_id} 执行")
        self._submit_next_step()

    def _straight_line_waypoints(self, start: Position, target: Position) -> tuple[Position, ...]:
        dx = target.x - start.x
        dy = target.y - start.y
        dz = target.z - start.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        count = max(1, math.ceil(distance / self.waypoint_spacing_m))
        return tuple(
            (
                target
                if index == count
                else Position(
                    start.x + dx * index / count,
                    start.y + dy * index / count,
                    start.z + dz * index / count,
                )
            )
            for index in range(1, count + 1)
        )

    def _submit_next_step(self) -> None:
        with self._lock:
            plan = self._active_plan
            if plan is None:
                return
            if not self._pending_steps:
                self.last_status = "COMPLETED"
                self._active_plan = None
                self._active_command_id = ""
                self._emit(f"任务 {plan.mission_id} 已完成")
                return
            step = self._pending_steps.popleft()
            command_id = uuid.uuid4().hex
            self._active_command_id = command_id
            step_number = self._completed_steps + 1
            total_steps = len(plan.steps)
            command = SwarmCommand(
                step.action,
                uav_id=plan.uav_id,
                parameters=step.parameters,
                command_id=command_id,
            )
        self._emit(f"任务步骤 {step_number}/{total_steps}：{step.description}")
        self._record_command("SUBMITTED", step.description, command)
        try:
            self.gateway.send_command(command)
        except RuntimeError as exc:
            with self._lock:
                self.last_status = "FAILED"
                self._active_plan = None
                self._active_command_id = ""
                self._pending_steps.clear()
            self._record_command("REJECTED", str(exc), command)
            self._emit(f"任务执行失败：{exc}")
            raise

    def _on_command_result(self, status: str, message: str, command: SwarmCommand) -> None:
        with self._lock:
            if self._active_plan is None or command.command_id != self._active_command_id:
                return
            if status.upper() == "COMPLETED":
                self._completed_steps += 1
                self._active_command_id = ""
                advance = True
            else:
                plan_id = self._active_plan.mission_id
                self.last_status = status.upper()
                self._active_plan = None
                self._active_command_id = ""
                self._pending_steps.clear()
                advance = False
        if advance:
            self._submit_next_step()
        else:
            self._emit(f"任务 {plan_id} 中止：{message}")

    def _record_command(self, status: str, message: str, command: SwarmCommand) -> None:
        if self.experiment_recorder is not None:
            self.experiment_recorder.record_command_event(
                status,
                message,
                command.action,
                command.command_id,
                command.uav_id or "",
                source="single_uav_mission",
            )

    def _emit(self, message: str) -> None:
        if self.event_listener is not None:
            self.event_listener(message)

