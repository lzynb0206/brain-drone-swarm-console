#!/usr/bin/env python3
"""Separate exploratory transfer experiment, not an original-paper reproduction.

Source EEG only trains a TFF backbone. Local EEG NEVER updates the backbone.
The local classifier is train-only scaling + PCA + strongly regularized softmax
regression. Its capacity and regularization are selected inside saved folds.
All failed scratch-regularization results remain in their separate directory.
"""
from __future__ import annotations
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

import h5py
import numpy as np
import torch
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ml.ssvep_repair import quality_preprocess, complex_spectrum
from src.ml.recent_ssvep_models import SSVEPTFFNet
from src.ml.ssvep_regularized import ValidationStopper
from scripts.repair_subject24_methods import OLD, OUT as BASE, SEEDS, setup, get_splits, eligible, final_rows

OUT = ROOT / "outputs/subject24_tff_transfer_20261028"
PUBLIC = ROOT / "data/public_ssvep/processed/benchmark_common6_2s.h5"
COMPONENTS = (8, 16, 32)
CS = (.001, .01, .1)


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def freeze():
    sources = [Path(__file__), ROOT / "src/ml/recent_ssvep_models.py",
        ROOT / "src/ml/ssvep_repair.py", ROOT / "src/ml/ssvep_regularized.py",
        ROOT / "src/bci/preprocessing.py", ROOT / "scripts/repair_subject24_methods.py"]
    dependencies = [OLD / "data_manifest.json", BASE / "quality.json", PUBLIC]
    for fold in [f"grouped_fold_{k}" for k in range(1, 6)] + ["cross_day_oct24_to_oct27"]:
        dependencies += [OLD / fold / "split.json", BASE / fold / "SSVEP-TFFNet/result.json"]
    d = dict(scope="TFF transfer adaptation; NOT the paper architecture-plus-evaluation protocol",
        study="follow-up after scratch shrinkage underfit; all studies reported; repeated-use exploratory local data",
        source_split="Benchmark subjects1-28 train (1008); subjects29-35 validation (252); no local samples",
        channels=["O1", "Oz", "O2", "PO3", "POz", "PO4"],
        source_model="TFFNet six-channel, six-class, dropout0.5, complex spectrum570",
        source_training="Adam lr0.001 weight_decay0.001, CE smoothing0.05; batch64; max60; valNLL every5; minimum20 patience15",
        source_augmentation="common-channel circular shift0..249 samples; per-trial Gaussian noise SD uniform0..1; per-channel restandardization",
        seeds=list(SEEDS), local_backbone="strictly frozen eval; no local gradient updates",
        local_input="same whole5s QC and6-80Hz; common first6electrodes; mean embedding of 2s crops starting50,225,400,575,750",
        local_head="train-only StandardScaler, non-whitened full-SVD PCA, multinomial LogisticRegression",
        local_components=list(COMPONENTS), local_C=list(CS),
        local_selection="inner validation correct count, then NLL, then fewer components, then smaller C; ensemble of3source seeds",
        local_refit="same selected components and C; refit scaler,PCA,head on all eligible outer train for each frozen encoder",
        quality="same repair-study eligibility; invalid test retained as failure; no idle gate",
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in sources},
        dependency_hashes={str(p.relative_to(ROOT)): sha(p) for p in dependencies})
    p = OUT / "configuration.json"
    if p.exists(): assert read(p) == d, "Frozen configuration changed"
    else: save(p, d)


def source_data():
    with h5py.File(PUBLIC) as h:
        assert float(h.attrs["sample_rate_hz"]) == 250
        assert json.loads(h.attrs["frequencies_hz_json"]) == [8, 9, 10, 11, 12, 13]
        assert json.loads(h.attrs["common_channels_json"]) == ["O1", "OZ", "O2", "PO3", "POZ", "PO4"]
        raw, y, subject = h["eeg"][:], h["label"][:].astype(np.int64), h["subject"][:]
        np.testing.assert_array_equal(h["frequency_hz"][:], y + 8)
    assert raw.shape == (1260, 6, 500)
    x = np.stack([quality_preprocess(row, check_quality=False)[0] for row in raw])
    train, val = np.flatnonzero(subject <= 28), np.flatnonzero(subject >= 29)
    assert len(train) == 1008 and len(val) == 252
    assert set(subject[train]).isdisjoint(subject[val])
    return x, y, train, val


def source_predict(model, x):
    model.eval()
    result = []
    with torch.no_grad():
        for chunk in np.array_split(x, max(1, int(np.ceil(len(x) / 64)))):
            result.append(model(torch.from_numpy(chunk), torch.from_numpy(complex_spectrum(chunk))).softmax(1).numpy())
    return np.concatenate(result)


def pretrain(seed):
    folder = OUT / "source" / f"seed_{seed}"
    if (folder / "complete.json").exists(): return
    setup(seed)
    x, y, train, val = source_data()
    model = SSVEPTFFNet(channels=6, classes=6)
    optimizer = torch.optim.Adam(model.parameters(), lr=.001, weight_decay=.001)
    rng = np.random.default_rng(seed)
    stopper = ValidationStopper()
    history, started = [], time.monotonic()
    folder.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, 61):
        model.train()
        losses = []
        for ix in np.array_split(rng.permutation(train), int(np.ceil(len(train) / 64))):
            z = np.stack([np.roll(row, int(shift), axis=-1) for row, shift in zip(x[ix], rng.integers(0, 250, len(ix)))])
            z += (rng.normal(size=z.shape) * rng.uniform(0, 1, (len(ix), 1, 1))).astype(np.float32)
            z -= z.mean(-1, keepdims=True)
            z /= np.maximum(z.std(-1, keepdims=True), 1e-8)
            optimizer.zero_grad(set_to_none=True)
            logits = model(torch.from_numpy(z), torch.from_numpy(complex_spectrum(z)))
            loss = torch.nn.functional.cross_entropy(logits, torch.from_numpy(y[ix]), label_smoothing=.05)
            assert torch.isfinite(loss)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            losses.append(float(loss.detach()))
        row = dict(epoch=epoch, augmented_training_loss=float(np.mean(losses)))
        if epoch % 5 == 0:
            p = source_predict(model, x[val])
            nll = float(-np.log(np.maximum(p[np.arange(len(val)), y[val]], 1e-12)).mean())
            row.update(validation_correct=int((p.argmax(1) == y[val]).sum()), validation_count=len(val), validation_nll=nll)
            improved, stop = stopper.update(epoch, nll)
            if improved: torch.save(model.state_dict(), folder / "backbone.pt")
            history.append(row)
            save(folder / "progress.json", dict(seed=seed, history=history, seconds=time.monotonic() - started))
            print("public pretraining", seed, "epoch", epoch, "validation", row["validation_correct"], "/", len(val), flush=True)
            if stop: break
        else: history.append(row)
    model.load_state_dict(torch.load(folder / "backbone.pt", map_location="cpu", weights_only=True))
    p = source_predict(model, x[val])
    save(folder / "complete.json", dict(seed=seed, selected_epoch=stopper.best_epoch, epochs_run=epoch,
        train_public_rows=train.tolist(), validation_public_rows=val.tolist(), history=history,
        validation_correct=int((p.argmax(1) == y[val]).sum()), validation_count=len(val),
        validation_nll=float(-np.log(np.maximum(p[np.arange(len(val)), y[val]], 1e-12)).mean()),
        checkpoint_sha256=sha(folder / "backbone.pt"), seconds=time.monotonic() - started,
        note="Source validation, NOT held-out local accuracy or an independent public benchmark test"))
    print("public pretraining complete", seed, flush=True)


def local_data():
    records, quality = read(OLD / "data_manifest.json"), read(BASE / "quality.json")
    x = []
    for r in records:
        p = ROOT / "data/research_experiments/raw_trials" / r["source_file"]
        assert sha(p) == r["file_sha256"]
        with np.load(p, allow_pickle=False) as h:
            c, _ = quality_preprocess(h["eeg"])
            x.append(c[:6])
    assert [r["source_file"] for r in records] == [q["source_file"] for q in quality]
    return dict(x=np.stack(x), y=np.array([r["label_index"] for r in records]),
                valid=np.array([q["eligible"] for q in quality]), records=records)


def embedding(model, x):
    model.eval()
    for parameter in model.parameters(): parameter.requires_grad_(False)
    assert not any(p.requires_grad for p in model.parameters())
    results = []
    with torch.no_grad():
        for batch in np.array_split(x, max(1, int(np.ceil(len(x) / 32)))):
            views = []
            for start in (50, 225, 400, 575, 750):
                crop = batch[..., start:start + 500].copy()
                a = model.temporal(torch.from_numpy(crop))
                b = model.spectral(torch.from_numpy(complex_spectrum(crop)))
                pair = torch.stack((a, b), -1)
                fused = (pair * model.gate(pair).softmax(-1)).sum(-1)
                views.append(model.classifier[:-1](fused).numpy())
            results.append(np.mean(views, axis=0))
    return np.concatenate(results)


def fit_head(x, y, components, c):
    scaler = StandardScaler().fit(x)
    pca = PCA(n_components=components, whiten=False, svd_solver="full").fit(scaler.transform(x))
    clf = LogisticRegression(C=c, max_iter=5000).fit(pca.transform(scaler.transform(x)), y)
    assert np.array_equal(clf.classes_, np.arange(6))
    assert clf.n_iter_.max() < 5000
    return dict(mean=scaler.mean_, scale=scaler.scale_, pca_mean=pca.mean_,
        components=pca.components_, weights=clf.coef_, bias=clf.intercept_)


def head_predict(x, head):
    z = (x - head["mean"]) / head["scale"]
    z = (z - head["pca_mean"]) @ head["components"].T
    logits = z @ head["weights"].T + head["bias"]
    logits -= logits.max(1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(1, keepdims=True)


def score(prob, data, idx):
    assert np.all(data["y"][idx] >= 0)
    pred = prob.argmax(1); pred[~data["valid"][idx]] = -1
    return dict(correct=int((pred == data["y"][idx]).sum()), count=len(idx),
        nll=float(-np.log(np.maximum(prob[np.arange(len(idx)), data["y"][idx]], 1e-12)).mean()))


def choose(candidates):
    return max(candidates, key=lambda c: (c["validation_correct"], -c["validation_nll"], -c["components"], -c["C"]))


def local():
    torch.set_num_threads(1)
    data = local_data()
    feature_path = OUT / "frozen_embeddings.npz"
    hashes = {}
    features = []
    for seed in SEEDS:
        folder = OUT / "source" / f"seed_{seed}"
        d = read(folder / "complete.json")
        assert sha(folder / "backbone.pt") == d["checkpoint_sha256"]
        model = SSVEPTFFNet(channels=6, classes=6)
        model.load_state_dict(torch.load(folder / "backbone.pt", map_location="cpu", weights_only=True))
        features.append(embedding(model, data["x"]).astype(np.float64))
        hashes[str(seed)] = d["checkpoint_sha256"]
        assert sha(folder / "backbone.pt") == hashes[str(seed)]
    features = np.stack(features)
    assert features.shape == (3, 165, 198) and np.isfinite(features).all()
    if feature_path.exists():
        with np.load(feature_path) as h: np.testing.assert_allclose(features, h["features"], atol=1e-6)
    else: np.savez_compressed(feature_path, features=features)
    save(OUT / "embedding_provenance.json", dict(backbone_hashes=hashes,
        features_sha256=sha(feature_path), local_backbone_updates=0,
        source_files=[r["source_file"] for r in data["records"]]))
    for name, split in get_splits(data):
        folder = OUT / name
        if (folder / "result.json").exists(): continue
        fit, train = eligible(data, split["inner_fit"]), eligible(data, split["train"])
        val = split["inner_validation"][data["y"][split["inner_validation"]] >= 0]
        assert not set(train) & set(split["test"])
        candidates = []
        for k in COMPONENTS:
            for c in CS:
                probabilities = []
                for x in features:
                    h = fit_head(x[fit], data["y"][fit], k, c)
                    probabilities.append(head_predict(x[val], h))
                v = score(np.mean(probabilities, axis=0), data, val)
                candidates.append(dict(components=k, C=c, validation_correct=v["correct"], validation_count=v["count"], validation_nll=v["nll"]))
        chosen = choose(candidates)
        save(folder / "selection_before_test.json", chosen)
        train_prob, test_prob = [], []
        for seed, x in zip(SEEDS, features):
            h = fit_head(x[train], data["y"][train], chosen["components"], chosen["C"])
            np.savez_compressed(folder / f"head_{seed}.npz", **h)
            with np.load(folder / f"head_{seed}.npz") as check:
                p = head_predict(x[split["test"]], check)
                np.testing.assert_allclose(p, head_predict(x[split["test"]], h), atol=1e-12)
            train_prob.append(head_predict(x[train], h)); test_prob.append(p)
        np.savez_compressed(folder / "ensemble_scores.npz", train=np.array(train_prob), test=np.array(test_prob))
        save(folder / "result.json", dict(fold=name, method="TFF public-pretrained frozen features + regularized linear head (adapted)",
            candidates=candidates, selected=chosen, local_backbone_updates=0,
            head_classification_parameters_per_seed=6 * chosen["components"] + 6,
            train_files=[data["records"][i]["source_file"] for i in train],
            fit_files=[data["records"][i]["source_file"] for i in fit],
            validation_files=[data["records"][i]["source_file"] for i in val],
            train_metrics=score(np.mean(train_prob, axis=0), data, train),
            rows=final_rows(data, split["test"], np.mean(test_prob, axis=0), "TFF transfer adapted", name)))
        print("local frozen-head complete", name, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("init", "source", "local"), required=True)
    parser.add_argument("--seed", type=int, choices=SEEDS)
    args = parser.parse_args()
    freeze()
    if args.mode == "source":
        assert args.seed is not None
        pretrain(args.seed)
    elif args.mode == "local": local()


if __name__ == "__main__": main()
