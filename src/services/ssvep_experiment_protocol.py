"""Hardware-independent protocol for balanced, randomized SSVEP acquisition.

The protocol deliberately owns no Qt timer and no EEG worker.  A presentation
layer advances it with a monotonic clock, reacts to phase transitions and only
acknowledges a trial after the recorder reports that the raw EEG file is safely
on disk.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from src.bci.config import TRUE_LABELS, WINDOW_SECONDS


class ExperimentPhase(str, Enum):
    IDLE = "idle"
    CUE = "cue"
    STIMULUS = "stimulus"
    SAVING = "saving"
    REST = "rest"
    FINISHED = "finished"
    ABORTED = "aborted"


@dataclass(frozen=True)
class AutomaticExperimentConfig:
    """User-configurable timings for one balanced acquisition run."""

    repetitions_per_label: int = 15
    cue_seconds: float = 1.0
    stimulus_seconds: float = 5.0
    discard_seconds: float = 1.0
    rest_seconds: float = 1.5
    random_seed: int = 20261014
    labels: tuple[str, ...] = tuple(TRUE_LABELS)

    def validate(self) -> "AutomaticExperimentConfig":
        labels = tuple(str(label).strip() for label in self.labels)
        if not labels or any(not label for label in labels):
            raise ValueError("实验标签不能为空")
        if len(set(labels)) != len(labels):
            raise ValueError("实验标签不能重复")
        if not 1 <= int(self.repetitions_per_label) <= 200:
            raise ValueError("每类次数必须在 1–200 之间")
        if not 0.1 <= float(self.cue_seconds) <= 30.0:
            raise ValueError("提示时长必须在 0.1–30 秒之间")
        if not 0.5 <= float(self.stimulus_seconds) <= 60.0:
            raise ValueError("刺激时长必须在 0.5–60 秒之间")
        if not 0.0 <= float(self.discard_seconds) < float(self.stimulus_seconds):
            raise ValueError("丢弃起始段必须小于刺激时长")
        if float(self.stimulus_seconds) - float(self.discard_seconds) < WINDOW_SECONDS:
            raise ValueError(
                f"丢弃起始段后至少要保留 {WINDOW_SECONDS:g} 秒，"
                "才能生成当前 Transformer/FBCCA 训练窗口"
            )
        if not 0.1 <= float(self.rest_seconds) <= 60.0:
            raise ValueError("休息时长必须在 0.1–60 秒之间")
        return self

    @property
    def trial_count(self) -> int:
        return len(self.labels) * int(self.repetitions_per_label)

    @property
    def analysis_seconds(self) -> float:
        return float(self.stimulus_seconds) - float(self.discard_seconds)


@dataclass(frozen=True)
class ExperimentTrial:
    """One item in the immutable run plan."""

    trial_index: int
    block_index: int
    repetition: int
    label: str
    retry_of_trial_index: int = 0


@dataclass(frozen=True)
class ProtocolTransition:
    phase: ExperimentPhase
    trial: ExperimentTrial | None
    reason: str = ""


def build_balanced_trial_plan(
    config: AutomaticExperimentConfig,
) -> tuple[ExperimentTrial, ...]:
    """Create seeded blocks containing every label exactly once.

    Besides class balance, a boundary rotation avoids presenting the same label
    twice in a row when the last item of one shuffled block equals the first of
    the next.  This keeps the plan deterministic for a given seed.
    """

    config.validate()
    rng = random.Random(int(config.random_seed))
    plan: list[ExperimentTrial] = []
    previous = ""
    for block_index in range(1, int(config.repetitions_per_label) + 1):
        block = list(config.labels)
        rng.shuffle(block)
        if previous and len(block) > 1 and block[0] == previous:
            replacement = next(index for index, label in enumerate(block[1:], 1) if label != previous)
            block[0], block[replacement] = block[replacement], block[0]
        for label in block:
            plan.append(
                ExperimentTrial(
                    trial_index=len(plan) + 1,
                    block_index=block_index,
                    repetition=block_index,
                    label=label,
                )
            )
        previous = block[-1]
    return tuple(plan)


class AutomaticExperimentProtocol:
    """Deterministic cue/stimulus/save/rest state machine."""

    def __init__(self, config: AutomaticExperimentConfig) -> None:
        self.config = config.validate()
        self.plan = list(build_balanced_trial_plan(config))
        self.phase = ExperimentPhase.IDLE
        self.current_index = -1
        self.deadline: float | None = None
        self.paused = False
        self._paused_remaining = 0.0
        self._pause_after_save = False
        self._abort_after_save = False
        self._rejected_trial_indexes: set[int] = set()

    @property
    def current_trial(self) -> ExperimentTrial | None:
        if 0 <= self.current_index < len(self.plan):
            return self.plan[self.current_index]
        return None

    @property
    def total_trials(self) -> int:
        return len(self.plan)

    @property
    def completed_trials(self) -> int:
        if self.phase == ExperimentPhase.FINISHED:
            return len(self.plan)
        if self.current_index < 0:
            return 0
        return self.current_index if self.phase in {ExperimentPhase.CUE, ExperimentPhase.STIMULUS, ExperimentPhase.SAVING} else self.current_index + 1

    def start(self, now: float) -> ProtocolTransition:
        if self.phase != ExperimentPhase.IDLE:
            raise RuntimeError("实验已经开始")
        self.current_index = 0
        return self._enter(ExperimentPhase.CUE, now)

    def tick(self, now: float) -> ProtocolTransition | None:
        if self.paused or self.deadline is None or float(now) < self.deadline:
            return None
        if self.phase == ExperimentPhase.CUE:
            return self._enter(ExperimentPhase.STIMULUS, now)
        if self.phase == ExperimentPhase.STIMULUS:
            return self._enter(ExperimentPhase.SAVING, now)
        if self.phase == ExperimentPhase.REST:
            if self.current_index + 1 >= len(self.plan):
                return self._enter(ExperimentPhase.FINISHED, now)
            self.current_index += 1
            return self._enter(ExperimentPhase.CUE, now)
        return None

    def trial_saved(self, now: float) -> ProtocolTransition:
        if self.phase != ExperimentPhase.SAVING:
            raise RuntimeError("当前没有等待保存的试次")
        if self._abort_after_save:
            self._abort_after_save = False
            return self._enter(ExperimentPhase.ABORTED, now, "本试次保存后中止")
        transition = self._enter(ExperimentPhase.REST, now)
        if self._pause_after_save:
            self._pause_after_save = False
            self.request_pause(now)
        return transition

    def reject_current_trial(self) -> ExperimentTrial:
        """Exclude the just-saved trial and append a same-label retry."""

        trial = self.current_trial
        if self.phase != ExperimentPhase.REST or trial is None:
            raise RuntimeError("只能在休息阶段剔除刚保存的试次")
        if trial.trial_index in self._rejected_trial_indexes:
            raise RuntimeError("当前试次已经标记为剔除")
        self._rejected_trial_indexes.add(trial.trial_index)
        retry = ExperimentTrial(
            trial_index=len(self.plan) + 1,
            block_index=trial.block_index,
            repetition=trial.repetition,
            label=trial.label,
            retry_of_trial_index=trial.trial_index,
        )
        self.plan.append(retry)
        return retry

    @property
    def rejected_trials(self) -> int:
        return len(self._rejected_trial_indexes)

    def request_pause(self, now: float) -> bool:
        """Pause safely, or defer until an in-flight trial has been saved."""

        if self.paused or self.phase in {
            ExperimentPhase.IDLE, ExperimentPhase.FINISHED, ExperimentPhase.ABORTED,
        }:
            return False
        if self.phase in {ExperimentPhase.STIMULUS, ExperimentPhase.SAVING}:
            self._pause_after_save = True
            return False
        self.paused = True
        self._paused_remaining = max(0.0, (self.deadline or float(now)) - float(now))
        self.deadline = None
        return True

    def resume(self, now: float) -> bool:
        if not self.paused:
            return False
        self.paused = False
        self.deadline = float(now) + self._paused_remaining
        self._paused_remaining = 0.0
        return True

    def request_abort(self, now: float) -> ProtocolTransition | None:
        """Abort now when safe, or after an in-flight trial reaches disk."""

        if self.phase in {ExperimentPhase.FINISHED, ExperimentPhase.ABORTED}:
            return None
        if self.phase in {ExperimentPhase.STIMULUS, ExperimentPhase.SAVING}:
            self._abort_after_save = True
            self._pause_after_save = False
            return None
        return self._enter(ExperimentPhase.ABORTED, now, "用户中止")

    def abort_immediately(self, now: float, reason: str) -> ProtocolTransition:
        """Terminate after the integration layer has cancelled its EEG capture."""

        self._abort_after_save = False
        self._pause_after_save = False
        return self._enter(ExperimentPhase.ABORTED, now, str(reason))

    def seconds_remaining(self, now: float) -> float:
        if self.paused:
            return self._paused_remaining
        if self.deadline is None:
            return 0.0
        return max(0.0, self.deadline - float(now))

    def labels(self) -> Iterable[str]:
        return (trial.label for trial in self.plan)

    def _enter(
        self,
        phase: ExperimentPhase,
        now: float,
        reason: str = "",
    ) -> ProtocolTransition:
        self.phase = phase
        duration = {
            ExperimentPhase.CUE: self.config.cue_seconds,
            ExperimentPhase.STIMULUS: self.config.stimulus_seconds,
            ExperimentPhase.REST: self.config.rest_seconds,
        }.get(phase)
        self.deadline = None if duration is None else float(now) + float(duration)
        self.paused = False
        self._paused_remaining = 0.0
        return ProtocolTransition(phase, self.current_trial, reason)
