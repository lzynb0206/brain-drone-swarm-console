"""Durable stimulus journal for a separate, unsynchronised EEG recorder."""
from dataclasses import asdict
from pathlib import Path
import json
import math
import os
import re
import time


class ExternalRecordingJournal:
    def __init__(self, root, run_id, context, config):
        self.directory = Path(root) / "external_experiments" / run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "events.jsonl"
        self.origin = time.monotonic()
        self.active = None
        self.completed = 0
        self.context = dict(context)
        self.write("session_start", {
            **context, "config": asdict(config), "recording_mode": "external_bdf",
            "eeg_recorded_locally": False, "hardware_synchronized": False,
            "p_ssvep_valid": False, "p_ssvep": None,
        })

    def write(self, event, metadata, *, monotonic=None, unix=None):
        stamp = time.monotonic() if monotonic is None else float(monotonic)
        row = {"schema_version": 1, "event": event, "elapsed_seconds": stamp-self.origin,
               "monotonic_seconds": stamp, "unix_seconds": time.time() if unix is None else unix,
               "metadata": {**metadata, **self.context}}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def capture(self, metadata):
        if self.active is not None:
            raise ValueError("上一个实验阶段尚未结束")
        row = self.write("segment_start", metadata,
                         monotonic=metadata.get("presentation_monotonic"))
        self.active = row

    def phase(self, name, timestamp, metadata):
        row = self.write(name, metadata, unix=timestamp)
        if name in ("hybrid_stage_end", "auto_ssvep_saving") and self.active is not None:
            start = self.active
            if metadata.get("trial_id") != start["metadata"].get("trial_id"):
                raise ValueError("实验阶段编号不一致")
            end = float(metadata.get("presentation_end_monotonic", row["monotonic_seconds"])) - self.origin
            context = start["metadata"]
            requested = float(context["duration_seconds"])
            complete = end - start["elapsed_seconds"] >= requested - .002
            self.write("segment_end", {**context, "start_seconds": start["elapsed_seconds"],
                       "end_seconds": end, "complete": complete})
            self.active = None
            self.completed += int(complete)

    def finish(self, status, summary):
        self.write("session_end", {**summary, "status": status,
                   "logged_segments": self.completed, "saved_trials": 0,
                   "eeg_recorded_locally": False, "hardware_synchronized": False})


def load_external_segments(path):
    """Only actual completed presentations carry labels; never infer a schedule."""
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for index, line in enumerate(handle, 1):
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"实验日志第 {index} 行损坏") from exc
    if not rows or rows[0].get("event") != "session_start":
        raise ValueError("请选择独立呈现模式保存的 events.jsonl")
    result = []
    seen = set()
    run_id = rows[0]['metadata'].get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[\w.-]+', run_id):
        raise ValueError('日志批次编号无效')
    for row in rows:
        if row.get("event") != "segment_end" or not row["metadata"].get("complete"):
            continue
        context = row["metadata"]
        trial = str(context.get("trial_id", ""))
        start, end = float(context["start_seconds"]), float(context["end_seconds"])
        if (not re.fullmatch(r'[\w.-]+', trial) or trial in seen or not context.get("true_label")
                or context.get('run_id') != run_id):
            raise ValueError("日志试次编号重复或缺少标签")
        if not math.isfinite(start+end) or not 0 <= start < end:
            raise ValueError("日志时间范围无效")
        seen.add(trial)
        result.append(context)
    if not result:
        raise ValueError("日志中没有完整呈现的阶段；中途终止的阶段不自动补齐")
    return result
