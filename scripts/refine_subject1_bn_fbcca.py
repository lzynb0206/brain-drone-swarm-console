#!/usr/bin/env python3
"""Tune only FBCCA internals on the first two subject-1 sessions.

The final 105 records (90 commands) form the user-designated new batch.
No confidence rejection; bad recordings remain in the command denominator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_subject1_bn_fbcca import RUNS, metrics
from src.bci.config import COMMANDS
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.fbcca_components import fbcca_components, causal_idle_background, combine_components
from src.bci.preprocessing import prepare_ssvep_channels, FILTER_BANK_WEIGHTS

OUT = ROOT / "outputs/subject1_bn_refinement_20261101"
AUDIT = ROOT / "outputs/subject1_bn_audit_20261101"
BANKS = {
    "original": FILTER_BANK_WEIGHTS,
    "first_only": (1., 0., 0.),
    "upper_only": (0., 1., 1.),
    "fundamental_emphasis": (1., .5, .25),
    "middle_emphasis": (1., 1., .5),
    "equal": (1., 1., 1.),
    "upper_emphasis": (.5, 1., 1.),
}


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def components(records, out):
    cache = out / "components.npz"
    hashes = []
    for row in records:
        path = ROOT / "data/research_experiments/raw_trials" / row["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ValueError(f"Raw trial changed: {path}")
        hashes.append(digest)
    if cache.exists():
        with np.load(cache, allow_pickle=False) as p:
            if p["hashes"].tolist() != hashes:
                raise ValueError("Cached components do not match raw inputs")
            return p["components"]
    values = np.zeros((len(records), 4, 3, 6))
    for i, row in enumerate(records):
        path = ROOT / "data/research_experiments/raw_trials" / row["file"]
        with np.load(path, allow_pickle=False) as p:
            fs = float(p["fs"])
            data = p["eeg"][:, -round(5 * fs):]
        try:
            prepared, _, _ = prepare_ssvep_channels(data, range(1, 9), fs)
        except ValueError:
            continue
        values[i] = fbcca_components(prepared, fs)
        if (i + 1) % 30 == 0:
            print(f"Decomposed {i + 1}/{len(records)} recordings", flush=True)
    np.savez_compressed(cache, components=values, hashes=np.asarray(hashes))
    return values


def predictions(raw, backgrounds, config):
    background = backgrounds[config.get("background_reducer", "mean")]
    if config.get("background_scale", "fixed") == "median_capped":
        # Estimate common background strength separately within each bank.
        # The class median limits influence from the one attended target.
        denominator = np.median(background, axis=-1)
        factor = np.divide(np.median(raw, axis=-1), denominator,
                           out=np.zeros_like(denominator), where=denominator > 1e-12)
        background = background * np.clip(factor, 0., 1.)[..., None]
    return combine_components(raw - config["background_strength"] * background, config)


def select_on_old(raw, backgrounds, records, valid, out):
    # Candidate grid and tie-breaking are fixed before evaluating the new batch.
    old = np.array([i for i, r in enumerate(records) if r["run"] in RUNS[:2]])
    candidates = []
    for h in (1, 2, 3, 4):
        for name, weights in BANKS.items():
            for strength in (0., .25, .5, .75, 1.):
                config = {"harmonics": h, "bank_weights": list(weights),
                          "bank_name": name, "background_strength": strength}
                pred = predictions(raw[old], {k:v[old] for k,v in backgrounds.items()}, config).argmax(axis=1)
                old_records = [records[i] for i in old]
                result = metrics(old_records, pred, valid[old], np.arange(len(old)))
                sessions = [metrics(old_records, pred, valid[old],
                            [i for i, r in enumerate(old_records) if r["run"] == run]) for run in RUNS[:2]]
                candidates.append({"config": config, "old": result,
                                   "old_sessions": sessions})
    def rank(row):
        c = row["config"]
        return (-row["old"]["correct"], -min(s["accuracy"] for s in row["old_sessions"]),
                c["bank_name"] != "original", abs(c["harmonics"] - 4),
                abs(c["background_strength"] - 1), c["bank_name"])
    candidates.sort(key=rank)
    save(out / "old_batch_search.json", {"candidate_count": len(candidates),
        "selection_rule": "old correct count; worst old-session accuracy; closest to original configuration",
        "candidates": candidates})
    selected = {"schema_version": 1, **candidates[0]["config"],
                "sample_rate_hz": 250., "window_seconds": 5,
                "selection_runs": list(RUNS[:2]), "evaluation_run": RUNS[2],
                "background_policy": "preceding_valid_idle_in_same_session",
                "confidence_rejection": False}
    save(out / "selected_config.json", selected)
    print("Frozen from OLD batch only:", json.dumps(selected, ensure_ascii=False), flush=True)
    return selected, candidates


def select_robustness_on_old(raw, backgrounds, records, valid, stage1, out):
    """Second, fixed 36-setting grid: background estimator and scale only.

    Include stage 1 unchanged. The new batch cannot participate in the rank.
    """
    old = np.array([i for i, r in enumerate(records) if r["run"] in RUNS[:2]])
    old_records = [records[i] for i in old]
    old_backgrounds = {k:v[old] for k,v in backgrounds.items()}
    candidates = []
    for weights in (list(FILTER_BANK_WEIGHTS), stage1["bank_weights"]):
        for reducer in ("mean", "median", "ewma"):
            for scale in ("fixed", "median_capped"):
                for strength in (.5, .75, 1.):
                    config = {**stage1, "bank_weights": weights,
                              "bank_name": "original" if weights == list(FILTER_BANK_WEIGHTS) else stage1["bank_name"],
                              "background_reducer": reducer, "background_scale": scale,
                              "background_strength": strength}
                    pred = predictions(raw[old], old_backgrounds, config).argmax(1)
                    result = metrics(old_records, pred, valid[old], np.arange(len(old)))
                    sessions = [metrics(old_records, pred, valid[old],
                        [i for i,r in enumerate(old_records) if r["run"] == run]) for run in RUNS[:2]]
                    candidates.append({"config": config, "old": result, "old_sessions": sessions})
    candidates.sort(key=lambda r:(-r["old"]["correct"], -min(s["accuracy"] for s in r["old_sessions"]),
                    r["config"]["background_scale"] != "fixed", r["config"]["background_reducer"] != "mean",
                    r["config"]["bank_name"] != "original", abs(r["config"]["background_strength"] - 1)))
    save(out / "old_batch_background_search.json", {"candidate_count": len(candidates), "candidates": candidates})
    selected = candidates[0]["config"]
    save(out / "selected_config.json", selected)
    print("Frozen background refinement from OLD batch only:", json.dumps(selected, ensure_ascii=False), flush=True)
    return selected


def select_bank_harmonics_on_old(raw, backgrounds, records, valid, previous, out):
    old = np.array([i for i,r in enumerate(records) if r["run"] in RUNS[:2]])
    old_records = [records[i] for i in old]
    old_backgrounds = {k:v[old] for k,v in backgrounds.items()}
    candidates = []
    profiles = ((4,4,4), (2,3,4), (2,4,4), (3,3,4), (3,4,4), (4,3,3), (4,4,3))
    for hs in profiles:
        for weights in (list(FILTER_BANK_WEIGHTS), previous["bank_weights"]):
            for strength in (.5, .75, 1.):
                config = {**previous, "harmonics_by_bank": list(hs), "bank_weights": weights,
                          "bank_name": "original" if weights == list(FILTER_BANK_WEIGHTS) else previous["bank_name"],
                          "background_strength": strength}
                pred = predictions(raw[old], old_backgrounds, config).argmax(1)
                result = metrics(old_records, pred, valid[old], np.arange(len(old)))
                sessions = [metrics(old_records, pred, valid[old],
                    [i for i,r in enumerate(old_records) if r["run"] == run]) for run in RUNS[:2]]
                candidates.append({"config": config, "old": result, "old_sessions": sessions})
    candidates.sort(key=lambda r:(-r["old"]["correct"], -min(s["accuracy"] for s in r["old_sessions"]),
                    r["config"]["harmonics_by_bank"] != [4,4,4],
                    sum(4-h for h in r["config"]["harmonics_by_bank"]),
                    abs(r["config"]["background_strength"] - 1)))
    selected = candidates[0]["config"]
    save(out / "old_batch_bank_harmonics_search.json", {"candidate_count": len(candidates), "candidates": candidates})
    save(out / "selected_config.json", selected)
    print("Frozen bank/harmonic refinement from OLD batch only:", json.dumps(selected, ensure_ascii=False), flush=True)
    return selected


def evaluate_all(raw, backgrounds, counts, records, valid, selected, candidates, out):
    original = {"harmonics": 4, "bank_weights": list(FILTER_BANK_WEIGHTS),
                "background_strength": 0.}
    previous = {**original, "background_strength": 1.}
    configs = {"original_FBCCA": original, "previous_BN": previous,
               "strength_only": {**original, "background_strength": selected["background_strength"]},
               "harmonics_only": {**previous, "harmonics": selected["harmonics"],
                                   "harmonics_by_bank": selected.get("harmonics_by_bank", [selected["harmonics"]]*3)},
               "banks_only": {**previous, "bank_weights": selected["bank_weights"]},
               "selected_without_background": {**selected, "background_strength": 0.},
               "selected_full_background": {**selected, "background_strength": 1.},
               "selected_mean_fixed_background": {**selected, "background_reducer": "mean", "background_scale": "fixed"},
               "selected_robustness_only": {**selected, "bank_weights": list(FILTER_BANK_WEIGHTS)},
               "selected": selected}
    # Also evaluate a predeclared one-factor ladder, never use it for selection.
    for h in (1, 2, 3, 4):
        configs[f"H{h}_original_banks_BN"] = {**previous, "harmonics": h}
    for strength in (0., .25, .5, .75, 1.):
        configs[f"strength_{strength}_H4_original_banks"] = {**original, "background_strength": strength}
    for name, weights in BANKS.items():
        configs[f"banks_{name}_H4_BN"] = {**previous, "bank_weights": list(weights)}
    # One old-selected configuration per background family, reported without
    # using new-batch outcomes to change the frozen selected configuration.
    background_search = json.loads((out / "old_batch_background_search.json").read_text())["candidates"]
    for candidate in background_search:
        c = candidate["config"]
        key = f'background_{c["background_reducer"]}_{c["background_scale"]}'
        configs.setdefault(key, c)
    groups = {"all": np.arange(len(records)),
              "old": np.array([i for i,r in enumerate(records) if r["run"] in RUNS[:2]]),
              "new": np.array([i for i,r in enumerate(records) if r["run"] == RUNS[2]])}
    groups.update({run: np.array([i for i,r in enumerate(records) if r["run"] == run]) for run in RUNS})
    truth = np.array([COMMANDS.index(r["label"]) if r["label"] in COMMANDS else -2 for r in records])
    scores = {name: predictions(raw, backgrounds, c) for name, c in configs.items()}
    preds = {name: np.where(valid, score.argmax(1), -1) for name, score in scores.items()}
    methods = {name: {"config": configs[name], **{group: metrics(records, p, valid, ix)
                      for group, ix in groups.items()}} for name, p in preds.items()}
    transitions = {}
    for before, after in (("original_FBCCA", "previous_BN"), ("previous_BN", "selected"), ("original_FBCCA", "selected")):
        pair = f"{before}_to_{after}"
        transitions[pair] = {}
        for group, ix in groups.items():
            active = ix[truth[ix] >= 0]
            p, q, y = preds[before][active], preds[after][active], truth[active]
            transitions[pair][group] = {
                "fixed": int(np.sum((p != y) & (q == y))),
                "harmed": int(np.sum((p == y) & (q != y))),
                "both_correct": int(np.sum((p == y) & (q == y))),
                "both_wrong": int(np.sum((p != y) & (q != y))),
                "wrong_to_different_wrong": int(np.sum((p != y) & (q != y) & (p != q))),
            }
    ledger = []
    for i, row in enumerate(records):
        if truth[i] < 0:
            continue
        item = {**row, "batch": "new" if row["run"] == RUNS[2] else "old",
                "prior_valid_idle_count": int(counts[i]), "valid": bool(valid[i]),
                "predictions": {}, "correct": {}, "scores": {}}
        for name in ("original_FBCCA", "previous_BN", "selected"):
            p = preds[name][i]
            item["predictions"][name] = COMMANDS[p] if p >= 0 else "数据无效"
            item["correct"][name] = bool(p == truth[i])
            item["scores"][name] = scores[name][i].tolist()
        item["raw_components_H_by_bank_by_class"] = raw[i].tolist()
        item["prior_idle_components_mean"] = backgrounds["mean"][i].tolist()
        item["prior_idle_components_selected_reducer"] = backgrounds[selected.get("background_reducer", "mean")][i].tolist()
        ledger.append(item)
    report = {"protocol": {"window_seconds": 5, "old_commands": 141, "new_commands": 90,
        "new_records": 105, "all_commands": 231, "all_idle": 39,
        "confidence_rejection": False, "invalid_counted_as_errors": True,
        "parameter_selection": "first two batches only", "background": "past valid idle in current batch only",
        "old_accuracy_is_parameter_search_result": True},
        "labels": list(COMMANDS), "frequencies_hz": [8,9,10,11,12,13],
        "selected_config": selected, "methods": methods, "transitions": transitions}
    save(out / "report.json", report)
    save(out / "all_231_command_trials.json", ledger)
    save(out / "previous_72_errors.json", [r for r in ledger if not r["correct"]["previous_BN"]])
    save(out / "previous_26_fixed_9_harmed.json", [r for r in ledger if r["correct"]["original_FBCCA"] != r["correct"]["previous_BN"]])
    save(out / "refinement_changed_correctness.json", [r for r in ledger if r["correct"]["previous_BN"] != r["correct"]["selected"]])
    np.savez_compressed(out / "scores.npz", valid=valid, **scores)
    for name, result in methods.items():
        print(name, {g: f'{result[g]["correct"]}/{result[g]["total"]}' for g in ("old", "new", "all")}, flush=True)
    print("Transitions:", json.dumps({k:{g:v[g] for g in ("old","new","all")} for k,v in transitions.items()}, ensure_ascii=False), flush=True)


def verify_runtime(records, valid, selected, out):
    """Replay the raw windows through the actual configurable FBCCA decoder."""
    hs = selected.get("harmonics_by_bank", [selected["harmonics"]] * 3)
    if len(set(hs)) != 1 or selected.get("background_reducer", "mean") != "mean" or selected.get("background_scale", "fixed") != "fixed":
        raise ValueError("Runtime verification currently supports uniform harmonics and fixed mean background")
    decoder = FBCCADecoder(harmonics=hs[0], bank_weights=selected["bank_weights"])
    values = np.zeros((len(records), len(COMMANDS)))
    for i, row in enumerate(records):
        path = ROOT / "data/research_experiments/raw_trials" / row["file"]
        with np.load(path, allow_pickle=False) as p:
            fs = float(p["fs"])
            try:
                result = decoder.decode(p["eeg"][:, -round(5*fs):], fs)
            except ValueError:
                continue
        values[i] = [result.scores[label] for label in COMMANDS]
    background, _ = causal_idle_background(values, [r["run"] for r in records],
                                [r["label"] == "空闲" for r in records], valid)
    corrected = values - selected["background_strength"] * background
    with np.load(out / "scores.npz") as p:
        expected = p["selected"]
    np.testing.assert_allclose(corrected[valid], expected[valid], atol=1e-12, rtol=1e-12)
    result = {"valid_records_compared": int(valid.sum()),
              "max_absolute_score_difference": float(np.max(np.abs(corrected[valid] - expected[valid]))),
              "prediction_differences": int(np.sum(corrected[valid].argmax(1) != expected[valid].argmax(1))),
              "config": selected}
    save(out / "runtime_replay_check.json", result)
    print("Runtime replay:", result, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    records = json.loads((AUDIT / "report.json").read_text())["records"]
    with np.load(AUDIT / "scores.npz") as audit:
        valid = audit["valid_5s"]
        expected = audit["scores_FBCCA_5s"]
        expected_bn = audit["scores_BN_B_mean_past_idle_same_run_5s"]
    raw = components(records, args.out)
    backgrounds = {}
    for reducer in ("mean", "median", "ewma"):
        backgrounds[reducer], counts = causal_idle_background(raw, [r["run"] for r in records],
                    [r["label"] == "空闲" for r in records], valid, reducer=reducer)
    original = {"harmonics": 4, "bank_weights": list(FILTER_BANK_WEIGHTS)}
    np.testing.assert_allclose(combine_components(raw, original)[valid], expected[valid], atol=1e-12, rtol=1e-12)
    np.testing.assert_allclose(combine_components(raw-backgrounds["mean"], original)[valid], expected_bn[valid], atol=1e-12, rtol=1e-12)
    selected, candidates = select_on_old(raw, backgrounds, records, valid, args.out)
    save(args.out / "stage1_selected_config.json", selected)
    selected = select_robustness_on_old(raw, backgrounds, records, valid, selected, args.out)
    save(args.out / "stage2_selected_config.json", selected)
    selected = select_bank_harmonics_on_old(raw, backgrounds, records, valid, selected, args.out)
    evaluate_all(raw, backgrounds, counts, records, valid, selected, candidates, args.out)
    verify_runtime(records, valid, selected, args.out)


if __name__ == "__main__":
    main()
