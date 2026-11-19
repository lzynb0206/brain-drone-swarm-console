#!/usr/bin/env python3
"""Frozen, validation-only small-data experiment. No runtime model changes.

Keep the repair study's exact raw data, splits, QC, and per-method crop recipe.
Only capacity and training regularization change. No test evaluation occurs
until the two validation candidates have been chosen and final seeds fitted.
The repeatedly inspected dataset makes this exploratory, not a blind trial.
"""
from __future__ import annotations
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.repair_subject24_methods import (
    OLD, OUT as BASE, METHODS, SEEDS, setup, load_local, get_splits, eligible,
    streams, inputs, predict, final_rows,
)
from src.ml.ssvep_repair import crop_batch
from src.ml.ssvep_regularized import build_model, ValidationStopper, validation_choice

OUT = ROOT / "outputs/subject24_regularized_20261028"
VARIANTS = ("regularized_original", "compact_regularized")
MAX_EPOCHS = 60


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    sources = [Path(__file__), ROOT / "src/ml/ssvep_regularized.py",
        ROOT / "scripts/repair_subject24_methods.py", ROOT / "src/ml/ssvep_repair.py",
        ROOT / "src/ml/recent_ssvep_models.py", ROOT / "src/ml/fb_eegnet.py",
        ROOT / "src/bci/preprocessing.py"]
    dependencies = [OLD / "data_manifest.json", BASE / "quality.json"]
    for fold in [f"grouped_fold_{k}" for k in range(1, 6)] + ["cross_day_oct24_to_oct27"]:
        dependencies.append(OLD / fold / "split.json")
        dependencies.extend(BASE / fold / m / "result.json" for m in METHODS)
    config = dict(
        scope="same subject A, Oct24 60 plus Oct27 20:41 105; no subject B or public pretraining",
        status="exploratory repeated-use dataset, not prospective blinded evaluation",
        variants=list(VARIANTS), seeds=list(SEEDS), validation_seed=101,
        maximum_epochs=MAX_EPOCHS, optimizer="AdamW", learning_rate=.001,
        weight_decay=.01, label_smoothing=.1, batch_size=32, gradient_clip_norm=1,
        stopping="minimum20; every5epochs; patience15; NLL improvement >0.0001; restore minimum NLL",
        selection="lowest inner validation NLL; ties lower parameters, earlier epoch, lexical variant",
        refit="selected epochs on all eligible outer training; seeds17,29,43; probability mean",
        unchanged="raw data, whole-trial group splits, QC, pre-processing, prior inner-selected crop recipe",
        quality="repair study rule, unchanged; invalid test trials retained as failures",
        metric="one six-class candidate per original trial; no idle acceptance gate",
        isolation="train and validation views exclude test inputs/labels; no outer-test choice",
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in sources},
        dependency_hashes={str(p.relative_to(ROOT)): sha(p) for p in dependencies},
        references=["https://docs.pytorch.org/docs/stable/generated/torch.optim.AdamW.html",
            "https://docs.pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html"])
    target = OUT / "configuration.json"
    if target.exists():
        assert read(target) == config, "Frozen code/config changed; use a new experiment directory"
    else:
        save(target, config)
    return config


def subset(data, indices):
    """A physical data view; training routines cannot inspect outer test data."""
    indices = np.asarray(indices, int)
    return dict(clean=data["clean"][indices],
        eegnet=tuple(x[indices] for x in data["eegnet"]),
        y=data["y"][indices], valid=data["valid"][indices],
        records=[data["records"][i] for i in indices])


def metrics(prob, data):
    y = data["y"]
    assert np.all(y >= 0) and len(prob) == len(y)
    assert prob.shape == (len(y), 6) and np.isfinite(prob).all()
    pred = prob.argmax(1)
    pred[~data["valid"]] = -1
    return dict(correct=int((pred == y).sum()), count=len(y),
        nll=float(-np.log(np.maximum(prob[np.arange(len(y)), y], 1e-12)).mean()))


def fit(method, variant, recipe, train, seed, epochs, validation=None):
    setup(seed)
    model = build_model(method, variant)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    stopper = ValidationStopper()
    rng = np.random.default_rng(seed)
    source = streams(train, method)
    n = source[0].shape[-1]
    length = 500 if recipe == "qc_crop_phase_aug" else n
    repeats = 2 if recipe == "qc_crop_phase_aug" else 1
    history, best, started = [], None, time.monotonic()
    for epoch in range(1, epochs + 1):
        model.train()
        losses = []
        order = rng.permutation(np.tile(np.arange(len(train["y"])), repeats))
        for ix in np.array_split(order, max(1, int(np.ceil(len(order) / 32)))):
            if recipe == "qc_crop_phase_aug":
                starts = rng.integers(50, n - length + 1, len(ix))
                shifts = rng.integers(0, 250, len(ix))
            else:
                starts, shifts = np.zeros(len(ix), int), None
            values = crop_batch(source, ix, starts, length=length, shifts=shifts)
            optimizer.zero_grad(set_to_none=True)
            logits = model(*inputs(method, values))
            loss = torch.nn.functional.cross_entropy(logits,
                torch.from_numpy(train["y"][ix]), label_smoothing=.1)
            assert torch.isfinite(loss)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            losses.append(float(loss.detach()))
        row = dict(epoch=epoch, smoothed_training_loss=float(np.mean(losses)))
        if epoch % 5 == 0 or epoch == epochs:
            tr = metrics(predict(model, method, train, np.arange(len(train["y"])), recipe), train)
            row.update(training_correct=tr["correct"], training_count=tr["count"], training_nll=tr["nll"])
            if validation is not None:
                va = metrics(predict(model, method, validation, np.arange(len(validation["y"])), recipe), validation)
                row.update(validation_correct=va["correct"], validation_count=va["count"], validation_nll=va["nll"])
                improved, stop = stopper.update(epoch, va["nll"])
                if improved:
                    best = {k: v.detach().clone() for k, v in model.state_dict().items()}
                if stop:
                    history.append(row)
                    break
        history.append(row)
    if validation is not None:
        assert best is not None
        model.load_state_dict(best)
    info = dict(method=method, variant=variant, recipe=recipe, seed=seed,
        epochs_run=epoch, selected_epoch=stopper.best_epoch if validation is not None else epoch,
        parameters=sum(p.numel() for p in model.parameters()), history=history,
        seconds=time.monotonic() - started)
    tr = metrics(predict(model, method, train, np.arange(len(train["y"])), recipe), train)
    info.update(training_correct=tr["correct"], training_count=tr["count"], training_nll=tr["nll"])
    if validation is not None:
        va = metrics(predict(model, method, validation, np.arange(len(validation["y"])), recipe), validation)
        info.update(validation_correct=va["correct"], validation_count=va["count"], validation_nll=va["nll"])
    return model, info


def run(data, name, split, method):
    folder = OUT / name / method
    if (folder / "result.json").exists():
        return
    base = read(BASE / name / method / "result.json")
    recipe = base["selected"]["recipe"]
    fit_idx, train_idx = eligible(data, split["inner_fit"]), eligible(data, split["train"])
    val_idx = split["inner_validation"][data["y"][split["inner_validation"]] >= 0]
    assert not set(train_idx) & set(split["test"])
    assert not set(fit_idx) & set(val_idx)
    fit_data, val_data = subset(data, fit_idx), subset(data, val_idx)
    candidates = []
    for variant in VARIANTS:
        target = folder / f"validation_{variant}.json"
        if target.exists():
            info = read(target)
        else:
            print(name, method, "validate", variant, flush=True)
            model, info = fit(method, variant, recipe, fit_data, 101, MAX_EPOCHS, val_data)
            folder.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), folder / f"validation_{variant}.pt")
            save(target, info)
        candidates.append(info)
    chosen = validation_choice(candidates)
    save(folder / "selection_before_test.json", {k: v for k, v in chosen.items() if k != "history"})
    print(name, method, "selected", chosen["variant"], chosen["selected_epoch"], flush=True)
    train = subset(data, train_idx)
    refits = []
    # Fit ALL seeds before opening the outer-test view.
    for seed in SEEDS:
        ckpt, infopath = folder / f"seed_{seed}.pt", folder / f"refit_{seed}.json"
        if ckpt.exists() and infopath.exists():
            info = read(infopath)
        else:
            print(name, method, "refit", seed, flush=True)
            model, info = fit(method, chosen["variant"], recipe, train, seed, chosen["selected_epoch"])
            torch.save(model.state_dict(), ckpt)
            save(infopath, info)
        refits.append(info)
    all_test, all_train = [], []
    test = subset(data, split["test"])
    for seed in SEEDS:
        model = build_model(method, chosen["variant"])
        model.load_state_dict(torch.load(folder / f"seed_{seed}.pt", map_location="cpu", weights_only=True))
        p = predict(model, method, test, np.arange(len(test["y"])), recipe)
        assert np.isfinite(p).all()
        np.testing.assert_allclose(p.sum(1), 1, atol=1e-6)
        check = build_model(method, chosen["variant"])
        check.load_state_dict(torch.load(folder / f"seed_{seed}.pt", map_location="cpu", weights_only=True))
        np.testing.assert_allclose(p, predict(check, method, test, np.arange(len(test["y"])), recipe), atol=1e-7)
        all_test.append(p)
        all_train.append(predict(model, method, train, np.arange(len(train["y"])), recipe))
    train_scores = np.mean(all_train, axis=0)
    np.savez_compressed(folder / "ensemble_scores.npz", test=np.array(all_test), train=np.array(all_train))
    result = dict(fold=name, method=method + " regularized/adapted", recipe=recipe,
        selected={k: v for k, v in chosen.items() if k != "history"},
        candidates=[{k: v for k, v in c.items() if k != "history"} for c in candidates],
        refits=[{k: v for k, v in r.items() if k != "history"} for r in refits],
        train_files=[data["records"][i]["source_file"] for i in train_idx],
        fit_files=[data["records"][i]["source_file"] for i in fit_idx],
        validation_files=[data["records"][i]["source_file"] for i in val_idx],
        train_metrics=metrics(train_scores, train),
        combination="mean softmax of seeds17,29,43; one candidate per original trial",
        checkpoint_reload_verified=True,
        rows=final_rows(data, split["test"], np.mean(all_test, axis=0), method, name))
    save(folder / "result.json", result)
    print(name, method, "complete", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--fold")
    args = parser.parse_args()
    freeze()
    if args.init:
        data = load_local(OUT)
        assert read(OUT / "quality.json") == read(BASE / "quality.json")
        print("Configuration frozen; unchanged quality verified", flush=True)
        return
    assert args.method, "Run one method per worker; artifacts are disjoint"
    # Workers never write the shared quality artifact.
    records = read(OLD / "data_manifest.json")
    from src.ml.ssvep_repair import quality_preprocess, eegnet_streams
    clean = []
    for r in records:
        path = ROOT / "data/research_experiments/raw_trials" / r["source_file"]
        assert sha(path) == r["file_sha256"]
        with np.load(path, allow_pickle=False) as h:
            x, _ = quality_preprocess(h["eeg"])
            clean.append(x)
    clean = np.array(clean)
    quality = read(OUT / "quality.json")
    assert [r["source_file"] for r in quality] == [r["source_file"] for r in records]
    data = dict(clean=clean, eegnet=eegnet_streams(clean),
        y=np.array([r["label_index"] for r in records]),
        valid=np.array([r["eligible"] for r in quality]), records=records)
    for name, split in get_splits(data):
        if args.fold and args.fold != name:
            continue
        run(data, name, split, args.method)


if __name__ == "__main__":
    main()
