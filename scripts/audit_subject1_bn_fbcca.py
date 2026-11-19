#!/usr/bin/env python3
"""Offline audit of the proposed BN-FBCCA; never changes runtime decoding.

Fixed comparisons: original FBCCA, leave-other-classes-out centering (A),
idle mean subtraction (B), idle median subtraction, and equal-three-harmonic
ablations. No command labels fit scores. Calibration folds exclude entire
acquisition blocks. Oct-29 is an already-inspected historical evaluation,
not a new untouched test set. Short windows are cropped before preprocessing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.preprocessing import prepare_ssvep_channels

RUNS = (
    "auto-20261024-145545-3730b3",
    "auto-20261027-204129-9c221a",
    "auto-20261029-190545-96e896",
)


def center_against_other_classes(scores: np.ndarray) -> np.ndarray:
    """Document A: a common affine transform, hence no ranking improvement."""
    scores = np.asarray(scores, dtype=float)
    return scores - (scores.sum(axis=-1, keepdims=True) - scores) / (scores.shape[-1] - 1)


def fit_idle_background(scores, records, valid, training_indices, reducer="mean"):
    idle = [int(i) for i in training_indices if records[i]["label"] == "空闲" and valid[i]]
    if not idle:
        raise ValueError("No valid calibration idle trials in this training partition")
    reduce = {"mean": np.mean, "median": np.median}[reducer]
    return reduce(scores[idle], axis=0), idle


def metrics(records, prediction, valid, indices):
    active = [int(i) for i in indices if records[i]["label"] in COMMANDS]
    truth = np.asarray([COMMANDS.index(records[i]["label"]) for i in active])
    pred = prediction[active].copy()
    pred[~valid[active]] = -1
    confusion = np.zeros((6, 7), dtype=int)
    for y, p in zip(truth, pred):
        confusion[y, p if p >= 0 else 6] += 1
    return {
        "correct": int(np.sum(truth == pred)), "total": len(active),
        "accuracy": float(np.mean(truth == pred)),
        "invalid_as_failure": int(np.sum(~valid[active])),
        "non_8hz_predicted_8hz": int(np.sum((truth != 0) & (pred == 0))),
        "true_8hz_correct": int(np.sum((truth == 0) & (pred == 0))),
        "true_8hz_total": int(np.sum(truth == 0)),
        "confusion_rows_truth_columns_predicted_then_invalid": confusion.tolist(),
    }


def evaluate(scores, records, valid):
    dev = np.asarray([i for i, r in enumerate(records) if r["run"] in RUNS[:2]])
    historical = np.asarray([i for i, r in enumerate(records) if r["run"] == RUNS[2]])
    groups = np.asarray([records[i]["block"] for i in dev])
    folds = [(dev[a], dev[b]) for a, b in GroupKFold(5).split(dev, groups=groups)]
    methods = {"FBCCA": scores[4], "BN_A": center_against_other_classes(scores[4]),
               "FBCCA_H3": scores[3]}
    calibration = {}
    for h in (4, 3):
        for reducer in ("mean", "median"):
            name = f"BN_B_{reducer}" + ("_H3" if h == 3 else "")
            values = np.zeros_like(scores[h])
            sources = []
            for train, test in folds + [(dev, historical)]:
                background, idle = fit_idle_background(scores[h], records, valid, train, reducer)
                assert not set(idle).intersection(test.tolist())
                assert not {records[i]["block"] for i in idle}.intersection(
                    records[i]["block"] for i in test)
                values[test] = scores[h][test] - background
                sources.append({"test_indices": test.tolist(), "idle_indices": idle,
                                "background": background.tolist()})
            methods[name] = values
            calibration[name] = sources
    result = {}
    for name, values in methods.items():
        prediction = values.argmax(axis=1)
        result[name] = {
            "development_group5_oof": metrics(records, prediction, valid, dev),
            "historical_oct29": metrics(records, prediction, valid, historical),
            "combined_descriptive": metrics(records, prediction, valid, np.arange(len(records))),
        }
        base_pred = scores[4].argmax(axis=1)
        result[name]["prediction_changes_from_FBCCA"] = int(np.sum((base_pred != prediction) & valid))
    assert np.array_equal(methods["FBCCA"].argmax(axis=1), methods["BN_A"].argmax(axis=1))
    return result, methods, calibration


def chronological_idle_evaluation(scores, records, valid):
    """Session-start zero background; update after each known idle trial only.

    Unlike the production calibration this has no eight-window minimum and
    no rejection thresholds. It isolates the chronological class subtraction.
    """
    values = scores.copy()
    sources = []
    for run in RUNS:
        seen = []
        for i, row in enumerate(records):
            if row["run"] != run:
                continue
            if seen:
                values[i] -= np.mean(scores[seen], axis=0)
            sources.append({"test_index": i, "idle_indices": seen.copy()})
            if row["label"] == "空闲" and valid[i]:
                seen.append(i)
    return values, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/subject1_bn_audit_20261101")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "data/sessions/subject_catalog.json").read_text())
    for run in RUNS:
        assert catalog["runs"][run]["subject_id"] == "subject-0001"
    windows = (5, 4, 2)
    decoders = {h: FBCCADecoder(harmonics=h) for h in (4, 3)}
    records = []
    raw_scores = {seconds: {h: [] for h in decoders} for seconds in windows}
    valid_by_window = {seconds: [] for seconds in windows}
    for run in RUNS:
        paths = sorted((ROOT / "data/research_experiments/raw_trials").glob(f"{run}-trial-*.npz"))
        if not paths:
            raise ValueError(f"No raw trials for {run}")
        for path in paths:
            with np.load(path, allow_pickle=False) as payload:
                eeg = payload["eeg"].astype(float)
                fs = float(payload["fs"])
                timestamps = payload["sample_timestamps"].astype(float)
                context = json.loads(str(payload["trial_context"].item()))
                label = str(payload["true_label"].item())
            assert tuple(context["target_frequencies_hz"]) == TARGET_FREQUENCIES_HZ
            assert timestamps.shape == (eeg.shape[1],)
            records.append({"file": path.name, "run": run, "label": label,
                            "block": context["block_id"], "fs": fs,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "idle_targets_flicker": context.get("idle_targets_flicker")})
            for seconds in windows:
                n = round(seconds * fs)
                assert eeg.shape[1] >= n
                segment = eeg[:, -n:]
                differences = np.diff(timestamps[-n:])
                timing_ok = bool(np.isfinite(differences).all() and np.all(differences > 0)
                                 and np.max(differences) <= 0.02)
                try:
                    _, channels, _ = prepare_ssvep_channels(segment, range(1, 9), fs)
                except ValueError:
                    channels = ()
                valid = timing_ok and len(channels) >= 2
                records[-1].setdefault("quality", {})[str(seconds)] = {
                    "valid": valid, "timing_ok": timing_ok,
                    "used_channels": list(channels),
                    "non_increasing_timestamp_steps": int(np.sum(differences <= 0)),
                    "maximum_timestamp_step_seconds": float(np.max(differences)),
                }
                for h, decoder in decoders.items():
                    if channels:
                        output = decoder.decode(segment, fs)
                        raw_scores[seconds][h].append([output.scores[label] for label in COMMANDS])
                    else:
                        raw_scores[seconds][h].append(np.zeros(len(COMMANDS)))
                valid_by_window[seconds].append(valid)
            if len(records) % 30 == 0:
                print(f"Recomputed {len(records)} raw trials", flush=True)
    report = {
        "scope": "subject-0001; offline fixed-method retrospective audit",
        "limitations": ["Oct-29 has already been inspected in earlier research; not a new untouched test.",
                        "Grouped OOF uses only training-fold idle trials; command labels are only for metrics.",
                        "Historical Oct-29 backgrounds use only Oct-24 and Oct-27 idle trials.",
                        "Idle recordings are eyes-open centre gaze with flickering targets, not eyes-closed rest.",
                        "Classification is forced-choice; no online acceptance or idle false-trigger claim.",
                        "Short windows are cropped before all preprocessing and quality checks.",
                        "Quality requires strictly increasing timestamps; older reports checked maximum gap only.",
                        "Three-harmonic references are an ablation, not an assumed correction of a proven bias."],
        "inventory": dict(Counter(r["label"] for r in records)),
        "runs": {run: dict(Counter(r["label"] for r in records if r["run"] == run)) for run in RUNS},
        "records": records, "windows": {}, "calibration": {},
    }
    arrays = {}
    for seconds in windows:
        scores = {h: np.asarray(raw_scores[seconds][h]) for h in decoders}
        assert all(s.shape == (len(records), len(COMMANDS)) and np.isfinite(s).all()
                   for s in scores.values())
        valid = np.asarray(valid_by_window[seconds], dtype=bool)
        results, methods, calibration = evaluate(scores, records, valid)
        causal, sources = chronological_idle_evaluation(scores[4], records, valid)
        methods["BN_B_mean_past_idle_same_run"] = causal
        causal_pred = causal.argmax(axis=1)
        results["BN_B_mean_past_idle_same_run"] = {
            run: metrics(records, causal_pred, valid,
                         [i for i, r in enumerate(records) if r["run"] == run]) for run in RUNS}
        results["BN_B_mean_past_idle_same_run"]["combined_descriptive"] = metrics(
            records, causal_pred, valid, np.arange(len(records)))
        results["BN_B_mean_past_idle_same_run"]["development_group5_oof"] = None
        results["BN_B_mean_past_idle_same_run"]["development_chronological"] = metrics(
            records, causal_pred, valid, [i for i, r in enumerate(records) if r["run"] in RUNS[:2]])
        report["windows"][str(seconds)] = {"methods": results,
            "valid_idle_count": sum(r["label"] == "空闲" and bool(valid[i]) for i, r in enumerate(records))}
        report["calibration"][str(seconds)] = {**calibration, "chronological": sources}
        arrays[f"valid_{seconds}s"] = valid
        for name, values in methods.items():
            arrays[f"scores_{name}_{seconds}s"] = values
        # Verify the provenance of prior full-window results, without reusing
        # their short-window preprocessing or treating them as fresh evidence.
        if seconds == 5:
            previous = ROOT / "outputs/subject1_scn_fbcca_20261031/scores.npz"
            if previous.exists():
                with np.load(previous, allow_pickle=False) as cached:
                    old = cached["scores_FBCCA_full5"]
                    report["previous_full5_score_check"] = {
                        "valid_max_absolute_difference": float(np.max(np.abs(old[valid] - scores[4][valid]))),
                        "valid_prediction_differences": int(np.sum(old[valid].argmax(1) != scores[4][valid].argmax(1))),
                    }
        for name, values in results.items():
            if "historical_oct29" in values:
                print(f"{seconds}s {name}: dev={values['development_group5_oof']['correct']}/141 "
                      f"Oct29={values['historical_oct29']['correct']}/90", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    np.savez_compressed(args.out / "scores.npz", **arrays)
    print(args.out / "report.json", flush=True)


if __name__ == "__main__":
    main()
