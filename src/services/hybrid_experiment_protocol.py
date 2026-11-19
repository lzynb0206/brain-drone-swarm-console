"""Paired SSVEP→MI acquisition, independent of clocks, GUI and hardware."""
from dataclasses import dataclass
from enum import Enum
import math
import random

from src.bci.config import COMMANDS

MI_TASK_SETS = {"left_right": ("想象左手", "想象右手"), "wave_grasp": ("想象挥手", "想象握拳")}


class HybridPhase(str, Enum):
    IDLE = "idle"
    REST_OPEN_CUE = "rest_open_cue"
    REST_OPEN = "rest_open"
    SAVE_REST_OPEN = "save_rest_open"
    REST_CLOSED_CUE = "rest_closed_cue"
    REST_CLOSED = "rest_closed"
    SAVE_REST_CLOSED = "save_rest_closed"
    REST_READY = "rest_ready"
    BASELINE = "baseline"
    SAVE_BASELINE = "save_baseline"
    CUE = "cue"
    SSVEP = "ssvep"
    SAVE_SSVEP = "save_ssvep"
    GAP = "gap"
    MI_CUE = "mi_cue"
    MI = "mi"
    SAVE_MI = "save_mi"
    REST = "rest"
    FINISHED = "finished"
    ABORTED = "aborted"


@dataclass(frozen=True)
class HybridExperimentConfig:
    mode: str = "serial"
    repetitions: int = 7
    task_set: str = "left_right"
    cue_seconds: float = 1.
    mi_cue_seconds: float = 3.
    ssvep_seconds: float = 5.
    gap_seconds: float = 1.
    mi_seconds: float = 5.
    rest_seconds: float = 3.
    idle_trials: int = 8
    random_seed: int = 20261101
    baseline_seconds: float = 5.
    include_dedicated_rest: bool = False
    resting_open_seconds: float = 140.
    resting_closed_seconds: float = 140.
    rest_eye_state: str = "both"
    purpose: str = "formal"
    serial_pair_count: int | None = None
    serial_study_seed: int | None = None

    def validate(self):
        if self.mode not in ("serial", "mi", "rest") or self.task_set not in MI_TASK_SETS:
            raise ValueError("Unknown experiment mode or MI task set")
        if self.purpose not in ("formal", "pilot"):
            raise ValueError("Unknown acquisition purpose")
        if not isinstance(self.repetitions, int) or not 1 <= self.repetitions <= 50:
            raise ValueError("重复次数必须为 1–50 的整数")
        if self.serial_pair_count is not None and (type(self.serial_pair_count) is not int or not 1 <= self.serial_pair_count <= 600):
            raise ValueError("正式串行总组数必须为 1–600 的整数")
        if self.serial_study_seed is not None and (type(self.serial_study_seed) is not int or not 0 <= self.serial_study_seed <= 2147483647):
            raise ValueError("论文串行采集种子应为 0–2147483647 的整数")
        if not isinstance(self.idle_trials, int) or not 0 <= self.idle_trials <= 30:
            raise ValueError("空闲校准次数必须为 0–30 的整数")
        if self.rest_eye_state not in ("open", "closed", "both"):
            raise ValueError("未知静息眼状态")
        for name in ("cue_seconds", "mi_cue_seconds", "ssvep_seconds", "gap_seconds", "mi_seconds", "rest_seconds", "baseline_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or not .5 <= value <= 30:
                raise ValueError(f"{name} 必须为 0.5–30 秒")
        if self.ssvep_seconds < 2 or self.mi_seconds < 2:
            raise ValueError("SSVEP 与 MI 采集窗口至少 2 秒")
        durations = (self.resting_open_seconds, self.resting_closed_seconds)
        if any(not math.isfinite(v) or not 5 <= v <= 240 for v in durations) or sum(durations) < 15:
            raise ValueError("静息每段为 5–240 秒，睁眼与闭眼合计至少 15 秒")
        return self

    @property
    def mi_labels(self):
        return MI_TASK_SETS[self.task_set]

    @property
    def pair_count(self):
        if self.mode == "rest":
            return 0
        if self.purpose == "pilot":
            return 2
        if self.mode == "serial" and self.serial_pair_count is not None:
            return self.serial_pair_count
        return self.repetitions * len(self.mi_labels) * (len(COMMANDS) if self.mode == "serial" else 1)

    @property
    def block_count(self):
        size = len(self.mi_labels) * (len(COMMANDS) if self.mode == "serial" else 1)
        return math.ceil(self.pair_count / size)


@dataclass(frozen=True)
class HybridTrial:
    index: int
    block: int
    ssvep_label: str | None
    mi_label: str | None


def build_hybrid_plan(config):
    config.validate()
    if config.mode == "rest":
        return ()
    rng = random.Random(config.random_seed)
    rows = []
    if config.mode == "serial":
        rows.extend((0, "空闲", None) for _ in range(config.idle_trials))
    if config.purpose == "pilot":
        # Presentation/recording smoke check only, never a research evaluation.
        rows.extend((1, COMMANDS[0] if config.mode == "serial" else None, m) for m in config.mi_labels)
        return tuple(HybridTrial(i+1, *row) for i, row in enumerate(rows))
    remaining = config.pair_count
    for block in range(1, config.block_count + 1):
        pairs = [(s, m) for s in (COMMANDS if config.mode == "serial" else (None,)) for m in config.mi_labels]
        if remaining < len(pairs):
            # Spread a partial block over all targets and alternate MI labels.
            # For 80 pairs this gives 40 per hand, 13/14 per target and 6/7
            # per target×hand combination, rather than truncating at random.
            targets, labels = list(COMMANDS), list(config.mi_labels)
            rng.shuffle(targets)
            rng.shuffle(labels)
            pairs = [(target, labels[(i + offset) % len(labels)])
                     for offset in range(len(labels)) for i, target in enumerate(targets)][:remaining]
        rng.shuffle(pairs)
        rows.extend((block, s, m) for s, m in pairs)
        remaining -= len(pairs)
        if config.mode == "serial" and config.idle_trials > 0 and block < config.block_count:
            rows.append((block, "空闲", None))
    return tuple(HybridTrial(i+1, *row) for i, row in enumerate(rows))


class HybridExperimentProtocol:
    def __init__(self, config):
        self.config = config.validate()
        self.plan = build_hybrid_plan(config)
        self.index = -1
        self.phase = HybridPhase.IDLE
        self.deadline = None
        self.paused = False
        self.pause_pending = False
        self._remaining = 0.

    @property
    def trial(self):
        return self.plan[self.index] if 0 <= self.index < len(self.plan) else None

    @property
    def running(self):
        return self.phase not in (HybridPhase.IDLE, HybridPhase.FINISHED, HybridPhase.ABORTED)

    def enter(self, phase, now):
        self.phase = phase
        durations = {HybridPhase.REST_OPEN_CUE: 5.,
                     HybridPhase.REST_CLOSED_CUE: 5.,
                     HybridPhase.REST_OPEN: self.config.resting_open_seconds,
                     HybridPhase.REST_CLOSED: self.config.resting_closed_seconds,
                     HybridPhase.CUE: (self.config.mi_cue_seconds if self.trial and self.trial.ssvep_label is None
                                       else self.config.cue_seconds),
                     HybridPhase.BASELINE: self.config.baseline_seconds,
                     HybridPhase.SSVEP: self.config.ssvep_seconds,
                     HybridPhase.GAP: self.config.gap_seconds,
                     HybridPhase.MI_CUE: self.config.mi_cue_seconds,
                     HybridPhase.MI: self.config.mi_seconds,
                     HybridPhase.REST: self.config.rest_seconds}
        self.deadline = float(now) + durations[phase] if phase in durations else None
        return phase

    def start(self, now):
        if self.phase != HybridPhase.IDLE:
            raise RuntimeError("实验已开始")
        self.index = 0
        first = (HybridPhase.REST_OPEN_CUE if self.config.include_dedicated_rest or self.config.mode == "rest"
                 else HybridPhase.BASELINE)
        if self.config.mode == "rest" and self.config.rest_eye_state == "closed":
            first = HybridPhase.REST_CLOSED_CUE
        return self.enter(first, now)

    def tick(self, now):
        if self.paused or self.deadline is None or now < self.deadline:
            return None
        transitions = {HybridPhase.REST_OPEN_CUE: HybridPhase.REST_OPEN,
                       HybridPhase.REST_OPEN: HybridPhase.SAVE_REST_OPEN,
                       HybridPhase.REST_CLOSED_CUE: HybridPhase.REST_CLOSED,
                       HybridPhase.REST_CLOSED: HybridPhase.SAVE_REST_CLOSED}
        if self.phase in transitions:
            return self.enter(transitions[self.phase], now)
        if self.phase == HybridPhase.CUE:
            return self.enter(HybridPhase.SSVEP if self.trial.ssvep_label is not None else HybridPhase.MI, now)
        if self.phase == HybridPhase.BASELINE:
            return self.enter(HybridPhase.SAVE_BASELINE, now)
        if self.phase == HybridPhase.SSVEP:
            return self.enter(HybridPhase.SAVE_SSVEP, now)
        if self.phase == HybridPhase.GAP:
            return self.enter(HybridPhase.MI_CUE, now)
        if self.phase == HybridPhase.MI_CUE:
            return self.enter(HybridPhase.MI, now)
        if self.phase == HybridPhase.MI:
            return self.enter(HybridPhase.SAVE_MI, now)
        if self.phase == HybridPhase.REST:
            self.index += 1
            return self.enter(HybridPhase.CUE if self.index < len(self.plan) else HybridPhase.FINISHED, now)
        return None

    def saved(self, stage, now):
        if stage == "rest_open" and self.phase == HybridPhase.SAVE_REST_OPEN:
            if self.config.mode == "rest" and self.config.rest_eye_state == "open":
                return self.enter(HybridPhase.FINISHED, now)
            return self.enter(HybridPhase.REST_CLOSED_CUE, now)
        if stage == "rest_closed" and self.phase == HybridPhase.SAVE_REST_CLOSED:
            if self.config.mode == "rest":
                return self.enter(HybridPhase.FINISHED, now)
            return self.enter(HybridPhase.REST_READY, now)
        if stage == "baseline" and self.phase == HybridPhase.SAVE_BASELINE:
            return self.enter(HybridPhase.CUE, now)
        expected = HybridPhase.SAVE_SSVEP if stage == "ssvep" else HybridPhase.SAVE_MI if stage == "mi" else None
        if self.phase != expected or expected is None:
            raise RuntimeError("保存确认与采集阶段不匹配")
        phase = HybridPhase.GAP if stage == "ssvep" and self.trial.mi_label else HybridPhase.REST
        self.enter(phase, now)
        if phase == HybridPhase.REST and self.pause_pending:
            self.pause_pending = False
            self.toggle_pause(now)
        return phase

    def resume_after_rest(self, now):
        if self.phase != HybridPhase.REST_READY:
            raise RuntimeError("尚未完成专门静息")
        self.pause_pending = False
        return self.enter(HybridPhase.FINISHED if self.config.mode == "rest" else HybridPhase.BASELINE, now)

    def toggle_pause(self, now):
        if self.paused:
            self.paused = False
            self.deadline = now + self._remaining
        elif self.phase in (HybridPhase.CUE, HybridPhase.REST):
            self._remaining = max(0., (self.deadline or now) - now)
            self.paused = True
            self.deadline = None
        elif self.running:
            self.pause_pending = True

    def abort(self, now):
        self.paused = False
        return self.enter(HybridPhase.ABORTED, now)
