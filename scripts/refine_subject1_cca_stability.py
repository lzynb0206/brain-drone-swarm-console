#!/usr/bin/env python3
"""Onset crop and EEG-side CCA regularization, selected on old batches only.

Keep all 231 commands; full-window QC failures cannot be rescued by cropping.
No confidence rejection, no fitted classifier, no new-batch label selection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_subject1_bn_fbcca import RUNS, metrics
from src.bci.config import COMMANDS
from src.bci.fbcca_components import causal_idle_background
from src.bci.preprocessing import prepare_ssvep_channels, FILTER_BANK_WEIGHTS
from src.bci.regularized_fbcca import regularized_components

OUT = ROOT / "outputs/subject1_cca_stability_20261101"
AUDIT = ROOT / "outputs/subject1_bn_audit_20261101"
PRIOR = ROOT / "outputs/subject1_bn_refinement_20261101"
OFFSETS = (0., .2, .4, .6)
RIDGES = (0., .0001, .0003, .001, .003, .01, .05, .1, .25, .5, 1.)
WEIGHTS = (tuple(FILTER_BANK_WEIGHTS), (1., 1., .5))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract(records, original_valid):
    hashes = []
    for row in records:
        path = ROOT / "data/research_experiments/raw_trials" / row["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ValueError("Raw trial has changed")
        hashes.append(digest)
    cache = OUT / "components_ridge_v2.npz"
    if cache.exists():
        with np.load(cache, allow_pickle=False) as p:
            assert p["hashes"].tolist() == hashes
            assert p["offsets"].tolist() == list(OFFSETS) and p["ridges"].tolist() == list(RIDGES)
            return p["values"], p["valid"]
    values = np.zeros((len(records), len(OFFSETS), len(RIDGES), 3, 6))
    valid = np.zeros((len(records), len(OFFSETS)), bool)
    diagnostics = []
    for i, row in enumerate(records):
        path = ROOT / "data/research_experiments/raw_trials" / row["file"]
        with np.load(path, allow_pickle=False) as p:
            eeg, fs = p["eeg"].astype(float), float(p["fs"])
            ts = p["sample_timestamps"].astype(float)
            context = json.loads(str(p["trial_context"].item()))
            start, rest = float(p["stimulus_lsl_time"]), float(p["rest_lsl_time"])
        diag = {"file": row["file"], "onset_delay_seconds": float(ts[0]-start),
                "rest_minus_last_sample_seconds": float(rest-ts[-1]),
                "declared_frequency_hz": context["frequency_hz"], "offsets": {}}
        for oi, offset in enumerate(OFFSETS):
            segment = eeg[:, round(offset*fs):]
            try:
                data, channels, _ = prepare_ssvep_channels(segment, range(1, 9), fs)
            except ValueError:
                continue
            values[i, oi], info = regularized_components(data, fs, RIDGES)
            valid[i, oi] = original_valid[i] and len(channels) >= 2
            diag["offsets"][str(offset)] = {**info, "channels": list(channels), "valid": bool(valid[i, oi])}
        diagnostics.append(diag)
        if (i+1) % 30 == 0:
            print(f"CCA decomposition {i+1}/{len(records)}", flush=True)
    save(OUT / "timing_and_condition_diagnostics.json", diagnostics)
    np.savez_compressed(cache, values=values, valid=valid, offsets=OFFSETS, ridges=RIDGES, hashes=hashes)
    return values, valid


def backgrounds_for(values, records, valid):
    backgrounds = np.zeros_like(values)
    for oi in range(len(OFFSETS)):
        backgrounds[:, oi], _ = causal_idle_background(values[:, oi], [r["run"] for r in records],
                        [r["label"] == "空闲" for r in records], valid[:, oi])
    return backgrounds


def scores_for(values, backgrounds, config):
    oi, ri = OFFSETS.index(config["discard_seconds"]), RIDGES.index(config["ridge"])
    return np.einsum("nbk,b->nk", values[:, oi, ri] - backgrounds[:, oi, ri], config["bank_weights"])


def select(values, backgrounds, records, valid):
    old = np.array([i for i, r in enumerate(records) if r["run"] in RUNS[:2]])
    old_records = [records[i] for i in old]
    candidates = []
    for offset in OFFSETS:
        for ridge in RIDGES:
            for weights in WEIGHTS:
                config = {"discard_seconds": offset, "ridge": ridge, "bank_weights": list(weights),
                          "background_strength": 1., "harmonics": 4}
                score = scores_for(values[old], backgrounds[old], config)
                ok = valid[old, OFFSETS.index(offset)]
                prediction = score.argmax(1)
                result = metrics(old_records, prediction, ok, np.arange(len(old)))
                days = [metrics(old_records, prediction, ok, [i for i,r in enumerate(old_records) if r["run"] == run]) for run in RUNS[:2]]
                candidates.append({"config": config, "old": result, "old_sessions": days})
    candidates.sort(key=lambda c:(-c["old"]["correct"], -min(m["accuracy"] for m in c["old_sessions"]),
                                  c["config"]["discard_seconds"], c["config"]["ridge"],
                                  c["config"]["bank_weights"] != list(WEIGHTS[1])))
    save(OUT / "old_batch_search.json", {"candidate_count": len(candidates), "candidates": candidates})
    selected = candidates[0]["config"]
    save(OUT / "selected_config.json", {"selection_runs": RUNS[:2], "evaluation_run": RUNS[2], **selected})
    print("Frozen using old batch only:", selected, "old correct", candidates[0]["old"]["correct"], flush=True)
    return selected, candidates


def evaluate(values, backgrounds, records, valid, selected, candidates):
    groups = {"all": np.arange(len(records)), "old": [i for i,r in enumerate(records) if r["run"] in RUNS[:2]],
              "new": [i for i,r in enumerate(records) if r["run"] == RUNS[2]]}
    baseline = {"discard_seconds": 0., "ridge": 0., "bank_weights": list(WEIGHTS[0]), "background_strength": 1., "harmonics": 4}
    configs = {"previous_BN": baseline, "previous_reweighted": {**baseline, "bank_weights": list(WEIGHTS[1])},
               "selected": selected, "selected_onset_only": {**selected, "ridge": 0.},
               "selected_ridge_only": {**selected, "discard_seconds": 0.}}
    for candidate in candidates:
        c = candidate["config"]
        configs.setdefault(f'best_old_at_ridge_{c["ridge"]}', c)
        configs.setdefault(f'best_old_at_offset_{c["discard_seconds"]}', c)
    results, predictions, all_scores = {}, {}, {}
    for name, c in configs.items():
        score = scores_for(values, backgrounds, c)
        ok = valid[:, OFFSETS.index(c["discard_seconds"])]
        pred = np.where(ok, score.argmax(1), -1)
        results[name] = {"config": c, **{g:metrics(records, pred, ok, ix) for g,ix in groups.items()}}
        predictions[name], all_scores[name] = pred, score
        print(name, {g:f'{results[name][g]["correct"]}/{results[name][g]["total"]}' for g in groups}, flush=True)
    truth = np.array([COMMANDS.index(r["label"]) if r["label"] in COMMANDS else -2 for r in records])
    changes = {}
    for before in ("previous_BN", "previous_reweighted"):
        changes[before] = {}
        for group, ix in groups.items():
            ix = np.array([i for i in ix if truth[i] >= 0])
            a,b,y = predictions[before][ix],predictions["selected"][ix],truth[ix]
            changes[before][group] = {"fixed":int(np.sum((a!=y)&(b==y))), "harmed":int(np.sum((a==y)&(b!=y)))}
    ledger = []
    for i,row in enumerate(records):
        if truth[i] < 0:
            continue
        item = {**row, "batch": "new" if row["run"] == RUNS[2] else "old", "predictions": {}, "correct": {}, "scores": {}}
        for name in ("previous_BN", "previous_reweighted", "selected"):
            p = predictions[name][i]
            item["predictions"][name] = COMMANDS[p] if p>=0 else "数据无效"
            item["correct"][name] = bool(p==truth[i])
            item["scores"][name] = all_scores[name][i].tolist()
        ledger.append(item)
    report = {"protocol": "old batches select parameters; latest 105 records evaluate; 231 commands forced choice; full-window QC failures retained",
              "methods": results, "transitions": changes, "selected": selected}
    save(OUT / "report.json", report)
    save(OUT / "all_231_commands.json", ledger)
    np.savez_compressed(OUT / "scores.npz", **all_scores)
    print("Changes", changes, flush=True)
    with np.load(PRIOR / "scores.npz") as p:
        ok = valid[:, 0]
        np.testing.assert_allclose(all_scores["previous_BN"][ok], p["previous_BN"][ok], atol=1e-12, rtol=1e-12)
        np.testing.assert_allclose(all_scores["previous_reweighted"][ok], p["selected"][ok], atol=1e-12, rtol=1e-12)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = json.loads((AUDIT / "report.json").read_text())["records"]
    with np.load(AUDIT / "scores.npz") as p:
        original_valid = p["valid_5s"]
    values, valid = extract(records, original_valid)
    backgrounds = backgrounds_for(values, records, valid)
    selected, candidates = select(values, backgrounds, records, valid)
    evaluate(values, backgrounds, records, valid, selected, candidates)


if __name__ == "__main__":
    main()
