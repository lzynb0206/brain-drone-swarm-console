"""Independent protocol for subject-specific, one-target-at-a-time calibration."""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.bci.config import COMMANDS


@dataclass(frozen=True)
class SingleTargetCalibrationConfig:
    repetitions_per_command: int = 10
    cue_seconds: float = 1.0
    stimulus_seconds: float = 5.0
    discard_seconds: float = 0.0
    rest_seconds: float = 1.5
    random_seed: int = 20261018

    def validate(self) -> "SingleTargetCalibrationConfig":
        if not 1 <= int(self.repetitions_per_command) <= 50:
            raise ValueError("每个指令的重复次数必须在 1–50 之间")
        if not 0.2 <= float(self.cue_seconds) <= 10.0:
            raise ValueError("提示时长必须在 0.2–10 秒之间")
        if not 1.0 <= float(self.stimulus_seconds) <= 20.0:
            raise ValueError("刺激时长必须在 1–20 秒之间")
        if not 0.0 <= float(self.discard_seconds) < float(self.stimulus_seconds):
            raise ValueError("丢弃时长必须小于刺激时长")
        if not 0.2 <= float(self.rest_seconds) <= 20.0:
            raise ValueError("休息时长必须在 0.2–20 秒之间")
        return self

    @property
    def trial_count(self) -> int:
        return len(COMMANDS) * int(self.repetitions_per_command)


@dataclass(frozen=True)
class SingleTargetTrial:
    trial_index: int
    repetition: int
    label: str


def build_single_target_plan(
    config: SingleTargetCalibrationConfig,
) -> tuple[SingleTargetTrial, ...]:
    """Return deterministic balanced blocks without adjacent duplicate labels."""

    config.validate()
    rng = random.Random(int(config.random_seed))
    trials: list[SingleTargetTrial] = []
    previous = ""
    for repetition in range(1, int(config.repetitions_per_command) + 1):
        block = list(COMMANDS)
        rng.shuffle(block)
        if previous and block[0] == previous:
            swap = next(index for index, value in enumerate(block[1:], 1) if value != previous)
            block[0], block[swap] = block[swap], block[0]
        for label in block:
            trials.append(SingleTargetTrial(len(trials) + 1, repetition, label))
        previous = block[-1]
    return tuple(trials)


__all__ = [
    "SingleTargetCalibrationConfig",
    "SingleTargetTrial",
    "build_single_target_plan",
]
