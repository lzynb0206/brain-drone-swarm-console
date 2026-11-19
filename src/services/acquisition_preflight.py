"""Passive EEG preflight and saved-trial timing audit; no equipment commands."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
import numpy as np
from src.bci.preprocessing import assess_signal_quality
from src.bci.montage import HYBRID


class AcquisitionProbe:
    def __init__(self, names=HYBRID.names, minimum_seconds=5.):
        self.names = tuple(names)
        self.minimum_seconds = float(minimum_seconds)
        self.values, self.timestamps, self.ages = [], [], []
        self.errors = []
        self.source_id = None
        self.original_names = None

    def feed(self, frame, timestamps, now_lsl):
        x = np.asarray(frame.samples, dtype=float)
        t = np.asarray(timestamps, dtype=float)
        if tuple(frame.channel_names) != self.names:
            self.errors.append("通道名称或顺序与候选布局不一致")
            return
        if x.ndim != 2 or x.shape[0] != len(self.names) or t.shape != (x.shape[1],) or not len(t):
            self.errors.append("EEG 数组或逐样本时间戳长度错误")
            return
        if len(frame.sample_timestamps) != x.shape[1]:
            self.errors.append("数据源未提供逐样本时间戳，不能验证采样连续性")
        if not np.isfinite(frame.sample_rate_hz) or abs(frame.sample_rate_hz - 250.) > 5:
            self.errors.append("采样率元数据不是约 250 Hz")
        units = tuple(str(u).lower().replace("μ", "u").replace("µ", "u") for u in frame.channel_units)
        if len(units) != len(self.names) or any(u not in ("uv", "microvolt", "microvolts") for u in units):
            self.errors.append("需要明确的微伏单位，不能对未知单位判断幅值质量")
        original = tuple(frame.original_channel_names or frame.channel_names)
        source = str(frame.source_id)
        if self.source_id is not None and (source != self.source_id or original != self.original_names):
            self.errors.append("检查期间数据源或原始通道标签发生变化")
        self.source_id, self.original_names = source, original
        self.values.append(x.copy())
        self.timestamps.append(t.copy())
        self.ages.append(float(now_lsl) - float(t[-1]))

    def arrays(self):
        if not self.values:
            return np.empty((len(self.names), 0)), np.empty(0)
        return np.concatenate(self.values, axis=1), np.concatenate(self.timestamps)

    def report(self):
        x, t = self.arrays()
        errors = list(self.errors)
        result = {"sample_count": len(t), "source_id": self.source_id,
                  "channel_names": list(self.names), "original_channel_names": self.original_names,
                  "physical_electrode_positions_verified_by_software": False,
                  "hardware_clock_accuracy_verified": False, "physical_display_timing_verified": False}
        if len(t) < 2:
            errors.append("没有足够实时 EEG 数据")
        else:
            dt = np.diff(t)
            finite = np.isfinite(t).all() and np.isfinite(x).all()
            if not finite:
                errors.append("信号或时间戳含 NaN/Inf")
            if not np.all(dt > 0):
                errors.append("时间戳倒序或重复")
            span = float(t[-1] - t[0])
            rate = (len(t)-1) / span if span > 0 and np.isfinite(span) else None
            if len(t) < round(self.minimum_seconds*250) or span < self.minimum_seconds - .02:
                errors.append("有效试采不足 5 秒")
            if rate is None or not 245 <= rate <= 255:
                errors.append("实测采样率超出 245–255 Hz")
            if not np.isfinite(dt).all() or float(np.max(dt)) > .02:
                errors.append("样本间隔超过 20 ms，可能丢包或时钟异常")
            ages = np.asarray(self.ages)
            if not np.isfinite(ages).all() or ages.min() < -.1 or ages.max() > 1.:
                errors.append("EEG 时间戳与本机标记时钟不一致或数据延迟超过 1 秒")
            if finite:
                quality = assess_signal_quality(x, 250.)
                bad = [n for n, ok in zip(self.names, quality["usable_mask"]) if not ok]
                if bad:
                    errors.append("需检查这些电极的信号：" + ", ".join(bad))
                result["signal_quality"] = quality
            result.update(duration_seconds=span, measured_rate_hz=rate,
                          largest_sample_gap_seconds=float(np.max(dt)),
                          latest_sample_age_seconds=float(ages[-1]),
                          max_sample_age_seconds=float(np.max(ages)))
        result["errors"] = sorted(set(errors))
        result["software_checks_passed"] = not errors
        return result


def save_probe(root, probe, markers, mapping_confirmed, clock_source):
    root = Path(root) / "research_preflight"
    root.mkdir(parents=True, exist_ok=True)
    identifier = "preflight-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    result = probe.report()
    x, t = probe.arrays()
    result.update(id=identifier, montage_id=HYBRID.id,
                  created_utc=datetime.now(timezone.utc).isoformat(), clock_source=clock_source,
                  wiring_confirmed_by_operator=bool(mapping_confirmed), markers=markers,
                  data_kind="preflight_only_not_formal_trial",
                  raw_file=identifier + ".npz")
    times = [m.get("lsl_timestamp") for m in markers]
    valid_markers = (len(times) >= 2 and all(isinstance(v, (float, int)) and np.isfinite(v) for v in times)
                     and all(a < b for a, b in zip(times, times[1:])))
    # At least one marker emitted during the EEG interval must be aligned.
    marker_inside = bool(valid_markers and len(t) and any(t[0] <= v <= t[-1] for v in times))
    persisted = set()
    wanted = {m.get("name") for m in markers}
    try:
        for journal in {m["local_marker_path"] for m in markers}:
            with Path(journal).open(encoding="utf-8") as handle:
                for line in handle:
                    event = json.loads(line)
                    if event.get("name") in wanted:
                        persisted.add((event["name"], event["lsl_timestamp"]))
    except (OSError, ValueError, KeyError):
        persisted.clear()
    disk_ok = bool(markers) and all((m.get("name"), m.get("lsl_timestamp")) in persisted for m in markers)
    result["local_marker_sequence_verified"] = valid_markers and marker_inside and disk_ok
    result["lsl_publication_requested_successfully"] = bool(markers) and all(m.get("published_to_lsl") for m in markers)
    result["lsl_receiver_delivery_verified"] = False
    if not valid_markers or not marker_inside or not disk_ok:
        result["errors"].append("本机标记顺序、磁盘记录或标记与 EEG 时间范围不一致")
    if clock_source == "time.monotonic_fallback":
        result["errors"].append("使用后备时钟，未验证与 LSL 标记的时钟关系")
    if not mapping_confirmed:
        result["errors"].append("实验员尚未核对实际接线")
    result["software_checks_passed"] = not result["errors"]
    result["formal_ready"] = result["software_checks_passed"] and bool(mapping_confirmed)
    np.savez_compressed(root/result["raw_file"], eeg=x, sample_timestamps=t, fs=250.,
                        channel_names=np.asarray(probe.names), data_kind="preflight_only_not_formal_trial")
    (root/(identifier+".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return result


def audit_hybrid_run(root, summary):
    """Verify saved raw files against software presentation evidence, retain failures."""
    root = Path(root)
    events = {e["trial_id"]: e for e in summary.get("presentation_events", [])}
    rows = []
    contexts = {}
    for name in summary.get("saved_paths", []):
        path = Path(name)
        if not path.is_absolute():
            path = root/path
        row = {"file": path.name, "errors": [], "warnings": []}
        try:
            with np.load(path, allow_pickle=False) as p:
                ctx = json.loads(str(p["trial_context"].item()))
                t = p["sample_timestamps"].copy()
                x = p["eeg"]
                fs = float(p["fs"])
                names = tuple(p["channel_names"])
                q = json.loads(str(p["signal_quality"].item()))
            trial_id = ctx["trial_id"]
            event = events.get(trial_id)
            contexts[trial_id] = (ctx, t)
            row.update(trial_id=trial_id, stage=ctx["stage"], signal_quality_status=q.get("status"),
                       sample_count=len(t), channel_names=list(names))
            if names != HYBRID.names or ctx.get("montage_id") != HYBRID.id:
                row["errors"].append("通道版本与采集计划不一致")
            if ctx.get("run_id") != summary["run_id"]:
                row["errors"].append("记录属于另一个 run")
            duration = float(ctx["duration_seconds"])
            if x.shape != (8, round(duration*fs)) or len(t) != x.shape[1]:
                row["errors"].append("样本数与计划时长不符")
            dt = np.diff(t)
            if not len(dt) or not np.isfinite(t).all() or not np.all(dt > 0) or dt.max() > .02:
                row["errors"].append("时间戳重复、倒序、缺失或间隔超过 20 ms")
            if q.get("status") != "good":
                row["warnings"].append("存在信号质量问题，原始记录保留供复核")
            onset = float(ctx["stimulus_lsl_time"])
            delay = float(t[0]-onset)
            row["first_sample_after_marker_seconds"] = delay
            if not 0 <= delay <= .05 or ctx.get("sample_clock_alignment") == "unverified_fallback":
                row["errors"].append("首样本未在呈现标记后 50 ms 内，或时钟未对齐")
            if not event:
                row["errors"].append("缺少该阶段的画面呈现结束证据")
            else:
                shown = float(event["presentation_duration_seconds"])
                row["software_presentation_duration_seconds"] = shown
                if abs(shown-duration) > .1 or t[-1]-onset > shown + .02:
                    row["errors"].append("EEG 窗口与画面阶段时长不一致")
                frames = np.asarray(event.get("render_frame_monotonic_seconds", []), dtype=float)
                if len(frames) > 1:
                    gaps = np.diff(frames)
                    row["largest_presentation_interval_seconds"] = float(gaps.max())
                    if (gaps <= 0).any() or gaps.max() > .1:
                        target = row["errors"] if ctx["stage"] == "ssvep" else row["warnings"]
                        target.append("软件画面呈现间隔异常，需检查掉帧")
                else:
                    row["warnings"].append("画面呈现记录不足")
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            row["errors"].append("读取或校验失败："+str(exc))
        row["passed"] = not row["errors"]
        rows.append(row)
    for row in rows:
        item = contexts.get(row.get("trial_id"))
        if not item or item[0].get("record_kind") not in ("serial_pair", "pilot_pair") or item[0].get("stage") != "mi":
            continue
        ctx, t = item
        if ctx.get("ssvep_true_label") is None:
            continue
        preceding = contexts.get(ctx.get("preceding_ssvep_trial_id"))
        if not preceding or preceding[0].get("pair_id") != ctx.get("pair_id") or preceding[1][-1] >= t[0]:
            row["errors"].append("SSVEP→MI 配对编号或先后时间错误")
            row["passed"] = False
    actual_rest = {r.get("stage") for r in rows if r["passed"]}
    config = summary.get("config", {})
    planned_rest = config.get("include_dedicated_rest") or config.get("mode") == "rest"
    expected_rest = {"rest_open", "rest_closed"}
    if config.get("mode") == "rest" and config.get("rest_eye_state") in ("open", "closed"):
        expected_rest = {"rest_" + config["rest_eye_state"]}
    rest_complete = expected_rest.issubset(actual_rest)
    all_passed = bool(rows) and all(r["passed"] for r in rows)
    if planned_rest and not rest_complete:
        all_passed = False
    return {"run_id": summary["run_id"], "software_timing_checks_passed": all_passed,
            "signal_quality_checks_passed": bool(rows) and all(r.get("signal_quality_status") == "good" for r in rows),
            "run_completed": summary.get("status") == "completed", "trials": rows,
            "dedicated_rest_complete": rest_complete,
            "expected_rest_stages": sorted(expected_rest) if planned_rest else [],
            "physical_display_timing_verified": False,
            "physical_timing_requirement": "需要光电二极管或硬件回环独立实测；Qt 标记不能替代物理起点"}
