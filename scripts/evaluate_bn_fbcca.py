#!/usr/bin/env python3
"""Calibrate BN gates on subject-1 development days, then replay Oct-29.

Uses the raw scores and strict quality flags from audit_subject1_bn_fbcca.py.
Each run starts with an empty background. Prediction precedes any update from
the current idle trial. Only Oct-24/27 labels select the gate threshold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.bci.bn_fbcca import normalize_scores, gate_scores
from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ, FBCCA_MIN_SCORE, FBCCA_MIN_MARGIN
from scripts.audit_subject1_bn_fbcca import RUNS


def replay(raw, records, valid, mode, min_idle_windows=8):
    output = []
    seen = []
    previous_run = None
    for i, row in enumerate(records):
        if row["run"] != previous_run:
            previous_run = row["run"]
            seen = []
        background = raw[seen].mean(0) if seen else np.zeros(raw.shape[1])
        result = normalize_scores(raw[i], mode, background)
        output.append({"result": result, "ready": mode == "a" or len(seen) >= min_idle_windows,
                       "idle_indices": seen.copy()})
        if valid[i] and row["label"] == "空闲":
            seen.append(i)
    return output


def summarize(raw, replayed, records, valid, indices, threshold):
    active = [i for i in indices if records[i]["label"] in COMMANDS]
    idle = [i for i in indices if records[i]["label"] == "空闲"]
    accepted = lambda i: bool(valid[i] and replayed[i]["ready"] and
                              gate_scores(replayed[i]["result"], raw[i], threshold))
    correct = lambda i: bool(valid[i] and COMMANDS[replayed[i]["result"].prediction] == records[i]["label"])
    passed = [i for i in active if accepted(i)]
    eligible_idle = [i for i in idle if valid[i] and replayed[i]["ready"]]
    idle_triggered = sum(accepted(i) for i in idle)
    class_recalls = [sum(accepted(i) and correct(i) for i in active if records[i]["label"] == label)
                     / max(1, sum(records[i]["label"] == label for i in active)) for label in COMMANDS]
    idle_recall = 1 - idle_triggered / len(idle) if idle else 0.
    return {"forced_choice_correct": sum(correct(i) for i in active), "active_total": len(active),
            "forced_choice_accuracy": sum(correct(i) for i in active) / len(active),
            "accepted_active": len(passed), "trigger_rate": len(passed) / len(active),
            "accepted_accuracy": sum(correct(i) for i in passed) / len(passed) if passed else None,
            "correct_trigger_rate": sum(correct(i) for i in passed) / len(active),
            "idle_total": len(idle), "eligible_idle": len(eligible_idle),
            "idle_false_triggers": idle_triggered,
            "idle_false_trigger_rate_all": idle_triggered / len(idle) if idle else None,
            "idle_false_trigger_rate_ready": idle_triggered / len(eligible_idle) if eligible_idle else None,
            "balanced_accuracy_6_commands_plus_idle": float(np.mean(class_recalls + [idle_recall])),
            "non_8hz_predicted_8hz": int(sum(valid[i] and records[i]["label"] != COMMANDS[0]
                                         and replayed[i]["result"].prediction == 0 for i in active)),
            "true_8hz_correct": sum(correct(i) for i in active if records[i]["label"] == COMMANDS[0]),
            "waiting_for_idle_active": sum(not replayed[i]["ready"] for i in active)}


def calibrate(raw, replayed, records, valid, development):
    candidates = sorted({0., .7, 1., *(replayed[i]["result"].confidence for i in development)})
    evaluated = []
    for threshold in candidates:
        summary = summarize(raw, replayed, records, valid, development, threshold)
        if summary["accepted_active"] and summary["eligible_idle"]:
            evaluated.append((summary["balanced_accuracy_6_commands_plus_idle"], threshold, summary))
    if not evaluated:
        return None, {"status": "no_active_triggers_or_no_eligible_idle_in_development"}
    selected = max(evaluated, key=lambda x: (x[0], x[2]["accepted_accuracy"], x[1]))
    return selected[1], {"status": "development_fit_only", **selected[2]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=ROOT / "outputs/subject1_bn_audit_20261101")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/bn_fbcca_implementation_20261101")
    parser.add_argument("--seconds", type=int, choices=(2, 4, 5), default=4)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    audit = json.loads((args.audit / "report.json").read_text())
    records = audit["records"]
    with np.load(args.audit / "scores.npz", allow_pickle=False) as z:
        raw = z[f"scores_FBCCA_{args.seconds}s"]
        valid = z[f"valid_{args.seconds}s"]
    if raw.shape != (len(records), len(COMMANDS)):
        raise ValueError("Score manifest mismatch")
    development = [i for i, row in enumerate(records) if row["run"] in RUNS[:2]]
    historical = [i for i, row in enumerate(records) if row["run"] == RUNS[2]]
    args.out.mkdir(parents=True, exist_ok=True)
    args.model_dir.mkdir(parents=True, exist_ok=True)
    report = {"protocol": "chronological same-session idle calibration; separate runs start empty",
              "window_seconds": args.seconds, "quality_invalid_as_failure": True,
              "threshold_selection": {"objective": "maximize macro recall over six commands plus idle",
                                      "tie_break": "accepted accuracy, then higher threshold",
                                      "note": "No guaranteed precision or idle false-trigger bound is claimed."},
              "limitations": ["Oct-29 is retrospective, not an untouched test set.",
                              "Eight independent saved idle trials are not equivalent to eight overlapping live windows.",
                              "Development gate metrics are fitted, not unbiased cross-validation estimates.",
                              "Few idle records cannot establish a reliable false-trigger probability."], "modes": {}}
    arrays = {}
    for mode in ("a", "b"):
        replayed = replay(raw, records, valid, mode)
        threshold, fitting = calibrate(raw, replayed, records, valid, development)
        payload = {"schema_version": 1, "mode": mode, "threshold": threshold,
                   "confidence_definition": "positive_top_clipped_relative_margin_v1",
                   "background_policy": "none" if mode == "a" else "session_prefix_idle_mean",
                   "frequencies_hz": list(TARGET_FREQUENCIES_HZ), "sample_rate_hz": 250.,
                   "sample_count": args.seconds * 250, "min_idle_windows": 8,
                   "min_raw_score": FBCCA_MIN_SCORE, "min_margin": FBCCA_MIN_MARGIN,
                   "development_runs": list(RUNS[:2]), "threshold_fitting": fitting,
                   "status": "research_only_no_hardware_validation"}
        path = args.model_dir / f"bn_fbcca_{mode}_gate.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        report["modes"][mode] = {"threshold": threshold, "fitting": fitting,
            "development": summarize(raw, replayed, records, valid, development, threshold),
            "historical_oct29": summarize(raw, replayed, records, valid, historical, threshold),
            "historical_oct29_at_0_70": summarize(raw, replayed, records, valid, historical, .7),
            "calibration_provenance": [row["idle_indices"] for row in replayed]}
        arrays[f"confidence_{mode}"] = np.asarray([r["result"].confidence for r in replayed])
        arrays[f"normalized_scores_{mode}"] = np.stack([r["result"].normalized for r in replayed])
        print(mode, threshold, report["modes"][mode]["historical_oct29"], flush=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    np.savez_compressed(args.out / "scores.npz", **arrays)


if __name__ == "__main__":
    main()
