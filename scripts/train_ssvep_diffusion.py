#!/usr/bin/env python3
"""Train a frequency-conditioned DDPM on the local Benchmark cache, offline.

Subjects 1--25 train, 26--30 validate, 31--35 test by default. Generated EEG
is added only to a downstream diagnostic classifier's training data. It never
enters the BN-FBCCA decoder or the real validation/test sets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import h5py
import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ml.ssvep_diffusion import FrequencyDDPM, standardize_trials


def subject_split(subjects):
    return subjects <= 25, (subjects >= 26) & (subjects <= 30), subjects >= 31


def spectral_features(values, fs):
    frequencies = np.fft.rfftfreq(values.shape[-1], 1 / fs)
    power = np.abs(np.fft.rfft(values * np.hanning(values.shape[-1]), axis=-1)) ** 2
    return np.log1p(power[:, :, (frequencies >= 6) & (frequencies <= 45)]).reshape(len(values), -1)


def downstream_comparison(real, labels, masks, generated, generated_labels, fs):
    train, validation, test = masks
    features = spectral_features(real, fs)
    extra = spectral_features(generated, fs) if len(generated) else np.empty((0, features.shape[1]))
    results = {}
    for name, x, y in (
        ("real_only", features[train], labels[train]),
        ("real_plus_generated", np.concatenate((features[train], extra)),
         np.concatenate((labels[train], generated_labels))),
    ):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000, random_state=20261101))
        model.fit(x, y)
        results[name] = {}
        for partition, mask in (("validation", validation), ("test", test)):
            prediction = model.predict(features[mask])
            results[name][partition] = {"correct": int(np.sum(prediction == labels[mask])),
                                       "total": int(mask.sum()), "accuracy": float(np.mean(prediction == labels[mask])),
                                       "predictions": prediction.tolist(), "labels": labels[mask].tolist()}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/public_ssvep/processed/benchmark_common6_2s.h5")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/ssvep_diffusion_20261101")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--width", type=int, default=24)
    parser.add_argument("--per-class", type=int, default=40)
    parser.add_argument("--physics-weight", type=float, default=.1)
    parser.add_argument("--margin-db", type=float, default=3.)
    parser.add_argument("--seed", type=int, default=20261101)
    args = parser.parse_args()
    if min(args.epochs, args.batch_size, args.per_class) < 1 or args.physics_weight < 0:
        parser.error("Positive epochs/batch/generation counts and nonnegative physics weight required")
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    with h5py.File(args.data, "r") as handle:
        if "benchmark" not in str(handle.attrs["dataset_name"]).lower():
            raise ValueError("This fixed subject split is for Tsinghua Benchmark only")
        values = np.asarray(handle["eeg"], dtype=np.float32)
        labels = np.asarray(handle["label"], dtype=np.int64)
        subjects = np.asarray(handle["subject"], dtype=int)
        frequency_by_trial = np.asarray(handle["frequency_hz"], dtype=float)
        fs = float(handle.attrs["sample_rate_hz"])
        frequencies = json.loads(handle.attrs["frequencies_hz_json"])
        channels = json.loads(handle.attrs["common_channels_json"])
    if not np.allclose(frequency_by_trial, np.asarray(frequencies)[labels]):
        raise ValueError("Frequency-label mapping mismatch")
    if set(np.unique(subjects)) != set(range(1, 36)):
        raise ValueError("Expected all 35 Benchmark subjects for this split")
    masks = subject_split(subjects)
    if not all(mask.any() for mask in masks):
        raise ValueError("Train/validation/test partitions must be nonempty")
    values = standardize_trials(values, fs)
    tensors = torch.from_numpy(values)
    targets = torch.from_numpy(labels)
    train_indices = torch.from_numpy(np.flatnonzero(masks[0]))
    validation_indices = torch.from_numpy(np.flatnonzero(masks[1]))
    model = FrequencyDDPM(values.shape[1], values.shape[2], fs, frequencies,
                          args.steps, args.width, args.physics_weight, args.margin_db)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4, weight_decay=1e-4)
    args.out.mkdir(parents=True, exist_ok=True)
    checkpoint = args.out / "diffusion.pt"
    history, best, best_epoch = [], float("inf"), 0
    started = time.monotonic()
    for epoch in range(1, args.epochs + 1):
        model.train()
        order = train_indices[torch.randperm(len(train_indices))]
        total = np.zeros(3)
        for indices in order.split(args.batch_size):
            optimizer.zero_grad(set_to_none=True)
            loss, mse, physics = model.loss(tensors[indices], targets[indices])
            if not torch.isfinite(loss):
                raise RuntimeError("Nonfinite diffusion loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            total += np.asarray([loss.item(), mse.item(), physics.item()]) * len(indices)
        model.eval()
        validation = 0.
        generator = torch.Generator().manual_seed(args.seed + 1)
        with torch.no_grad():
            for indices in validation_indices.split(args.batch_size):
                loss, _, _ = model.loss(tensors[indices], targets[indices], generator)
                validation += loss.item() * len(indices)
        validation /= len(validation_indices)
        record = dict(epoch=epoch, loss=float(total[0] / len(order)),
                      noise_mse=float(total[1] / len(order)), physics_loss=float(total[2] / len(order)),
                      validation_loss=validation)
        history.append(record)
        if validation < best:
            best, best_epoch = validation, epoch
            torch.save({"schema_version": 1, "config": model.config, "state_dict": model.state_dict(),
                        "epoch": epoch, "train_subjects": list(range(1, 26)),
                        "validation_subjects": list(range(26, 31)), "seed": args.seed}, checkpoint)
        print(json.dumps(record), flush=True)
        (args.out / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    payload = torch.load(checkpoint, weights_only=True, map_location="cpu")
    model.load_state_dict(payload["state_dict"])
    generated_labels = torch.arange(len(frequencies)).repeat_interleave(args.per_class)
    generator = torch.Generator().manual_seed(args.seed + 2)
    generated = torch.cat([model.sample(y, generator) for y in generated_labels.split(args.batch_size)])
    # Apply exactly the same per-trial preprocessing as real classifier data;
    # this neither reads labels nor inserts a target sinusoid into the output.
    generated = torch.from_numpy(standardize_trials(generated.numpy(), fs))
    with torch.no_grad():
        db = model.spectral.excess_db(generated)
        target_db = db.gather(1, generated_labels[:, None]).flatten()
        # Export only finite candidates meeting the declared peak criterion
        # and having their strongest candidate peak at the requested label.
        keep = (torch.isfinite(generated).all(dim=(1, 2)) & (target_db >= args.margin_db)
                & (db.argmax(1) == generated_labels))
        real_db = model.spectral.excess_db(tensors[masks[0]]).gather(1, targets[masks[0], None]).flatten()
    fake = generated[keep].numpy()
    fake_y = generated_labels[keep].numpy()
    np.savez_compressed(args.out / "generated_train_only.npz", eeg=fake, label=fake_y,
                        frequency_hz=np.asarray(frequencies)[fake_y], sample_rate_hz=fs,
                        channel_names=np.asarray(channels), source=np.asarray("conditional_ddpm_synthetic"))
    np.savez_compressed(args.out / "generation_audit.npz", label=generated_labels.numpy(),
                        target_excess_db=target_db.numpy(), accepted=keep.numpy(),
                        all_candidate_excess_db=db.numpy())
    comparison = downstream_comparison(values, labels, masks, fake, fake_y, fs)
    report = {
        "status": "offline_research_prototype_not_online_decoder",
        "source": str(args.data.resolve()), "source_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "ddpm_reference": "https://arxiv.org/abs/2006.11239",
        "protocol": {"train_subjects": list(range(1, 26)), "validation_subjects": list(range(26, 31)),
                     "test_subjects": list(range(31, 36)), "partition_trials": [int(m.sum()) for m in masks],
                     "channels": channels, "frequencies_hz": frequencies, "sample_rate_hz": fs,
                     "samples": values.shape[-1], "normalization": "per-trial global RMS, no cross-trial fitting"},
        "config": model.config, "seed": args.seed, "epochs": args.epochs, "best_epoch": best_epoch,
        "history": history, "seconds": time.monotonic() - started,
        "generated_count": len(generated), "accepted_count": len(fake),
        "accepted_per_class": np.bincount(fake_y, minlength=len(frequencies)).tolist(),
        "target_excess_db_median_generated": float(target_db.median()),
        "target_excess_db_median_real_train": float(real_db.median()),
        "downstream_log_psd_logistic_regression": comparison,
        "limitations": ["3 dB is a fixed engineering margin, not statistical significance.",
                        "Spectral checks do not establish biological realism or generative diversity.",
                        "The diagnostic downstream classifier is separate from BN-FBCCA.",
                        "Six-channel public data must not be passed as eight-channel local EEG.",
                        "The fixed test subjects were also inspected during an earlier implementation check; results are retrospective.",
                        "A short prototype run cannot establish an accuracy benefit; report both outcomes."]}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"accepted": len(fake), "generated": len(generated), "downstream": comparison}), flush=True)
    print(args.out / "report.json", flush=True)


if __name__ == "__main__":
    main()
