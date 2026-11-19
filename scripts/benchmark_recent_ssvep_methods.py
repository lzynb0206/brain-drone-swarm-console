#!/usr/bin/env python3
"""Leakage-aware six-method benchmark on aligned 5-second SSVEP trials.

The split can leave out a complete recording run or a complete repetition/block
within one run.  No held-out active trial contributes scaler statistics,
templates, spatial filters, or neural weights.  Idle trials are inventoried but
never relabeled as one of the six active commands.  The output is an offline
research report and never changes the real-time decoder or flight-control state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import matplotlib
import numpy as np
from scipy import linalg, signal, stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.preprocessing import StandardScaler


matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bci.config import (  # noqa: E402
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    TARGET_FREQUENCIES_HZ,
)
from src.ml.dual_fbcca_features import extract_dual_fbcca_features  # noqa: E402
from src.ml.fb_eegnet import FBEEGNetFusion  # noqa: E402
from src.ml.recent_ssvep_models import (  # noqa: E402
    BrainWaveDecoderAdaptation,
    SSVEPTFFNet,
)


SAMPLE_RATE_HZ = 250.0
SAMPLE_COUNT = 1250
FILTER_BANK_LOW_EDGES_HZ = (6.0, 14.0, 22.0)
DEFAULT_RUN_IDS = (
    "auto-20261024-145545-3730b3",
    "auto-20261025-200224-e208d5",
    "auto-20261025-205949-2b713f",
    "auto-20261025-215910-076676",
)
METHOD_ORDER = (
    "FBCCA",
    "Dual-FBCCA + LR",
    "FB-eTRCA",
    "FB-EEGNet",
    "SSVEP-TFFNet (2025)",
    "CNN-BiGRU (2026 adapted)",
)


@dataclass(frozen=True)
class Trial:
    path: Path
    run_id: str
    block: int
    label_index: int
    eeg: np.ndarray


@dataclass(frozen=True)
class PreparedData:
    trials: tuple[Trial, ...]
    truth: np.ndarray
    runs: np.ndarray
    blocks: np.ndarray
    dual_features: np.ndarray
    fbcca_scores: np.ndarray
    trca_banks: np.ndarray
    eegnet_clean: np.ndarray
    eegnet_banks: np.ndarray
    tff_temporal: np.ndarray
    tff_spectrum: np.ndarray
    brainwave_raw: np.ndarray


def _json_scalar(payload, key: str) -> dict[str, object]:
    if key not in payload.files:
        return {}
    try:
        decoded = json.loads(str(np.asarray(payload[key]).item()))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def load_trials(raw_dir: Path, run_ids: Sequence[str]) -> tuple[Trial, ...]:
    trials: list[Trial] = []
    for run_id in run_ids:
        paths = sorted(raw_dir.glob(f"{run_id}-trial-*.npz"))
        if not paths:
            raise FileNotFoundError(f"No trials found for run {run_id}")
        for path in paths:
            with np.load(path, allow_pickle=False) as payload:
                label = str(np.asarray(payload["true_label"]).item())
                if label not in COMMANDS:
                    continue
                eeg = np.asarray(payload["eeg"], dtype=np.float64)
                sample_rate = float(np.asarray(payload["fs"]).item())
                names = tuple(str(value) for value in payload["channel_names"].tolist())
                context = _json_scalar(payload, "trial_context")
            if names != tuple(SSVEP_CHANNEL_NAMES):
                raise ValueError(f"{path.name}: unexpected channel order {names}")
            if abs(sample_rate - SAMPLE_RATE_HZ) > 1e-6:
                raise ValueError(f"{path.name}: expected 250 Hz, got {sample_rate}")
            if eeg.shape[0] != len(SSVEP_CHANNEL_NAMES) or eeg.shape[1] < SAMPLE_COUNT:
                raise ValueError(f"{path.name}: expected at least (8, 1250), got {eeg.shape}")
            frequencies = context.get("target_frequencies_hz", ())
            if not frequencies or not np.allclose(
                np.asarray(frequencies, dtype=float),
                np.asarray(TARGET_FREQUENCIES_HZ, dtype=float),
                rtol=0.0,
                atol=1e-6,
            ):
                raise ValueError(f"{path.name}: incompatible stimulus frequencies")
            block = int(context.get("repetition", context.get("block_index", 0)) or 0)
            if block <= 0:
                raise ValueError(f"{path.name}: missing repetition/block index")
            trials.append(Trial(
                path=path,
                run_id=run_id,
                block=block,
                label_index=COMMANDS.index(label),
                eeg=eeg[:len(SSVEP_CHANNEL_NAMES), :SAMPLE_COUNT],
            ))
    for run_id in run_ids:
        labels = {trial.label_index for trial in trials if trial.run_id == run_id}
        if labels != set(range(len(COMMANDS))):
            raise ValueError(f"{run_id}: active trials do not cover all six classes")
    return tuple(trials)


def trial_inventory(raw_dir: Path, run_ids: Sequence[str]) -> dict[str, object]:
    """Count every saved trial while keeping non-command labels explicit."""

    label_counts: dict[str, int] = {}
    for run_id in run_ids:
        for path in sorted(raw_dir.glob(f"{run_id}-trial-*.npz")):
            with np.load(path, allow_pickle=False) as payload:
                label = str(np.asarray(payload["true_label"]).item())
            label_counts[label] = label_counts.get(label, 0) + 1
    active_count = sum(label_counts.get(label, 0) for label in COMMANDS)
    excluded_counts = {
        label: count for label, count in label_counts.items() if label not in COMMANDS
    }
    return {
        "saved_trial_count": int(sum(label_counts.values())),
        "active_trial_count": int(active_count),
        "excluded_non_command_trial_count": int(sum(excluded_counts.values())),
        "label_counts": label_counts,
        "excluded_label_counts": excluded_counts,
    }


def _notched_standardized(eeg: np.ndarray) -> np.ndarray:
    values = signal.detrend(np.asarray(eeg, dtype=np.float64), axis=1, type="linear")
    numerator, denominator = signal.iirnotch(50.0, 30.0, fs=SAMPLE_RATE_HZ)
    values = signal.filtfilt(numerator, denominator, values, axis=1)
    values -= values.mean(axis=1, keepdims=True)
    values /= np.maximum(values.std(axis=1, keepdims=True), 1e-9)
    return values


def _bandpass(values: np.ndarray, low: float, high: float) -> np.ndarray:
    sos = signal.butter(
        4,
        (float(low), float(high)),
        btype="bandpass",
        fs=SAMPLE_RATE_HZ,
        output="sos",
    )
    filtered = signal.sosfiltfilt(sos, values, axis=1)
    filtered -= filtered.mean(axis=1, keepdims=True)
    filtered /= np.maximum(filtered.std(axis=1, keepdims=True), 1e-9)
    return filtered.astype(np.float32)


def _complex_spectrum(values: np.ndarray) -> np.ndarray:
    frequencies = np.fft.rfftfreq(values.shape[1], 1.0 / SAMPLE_RATE_HZ)
    selected = (frequencies >= 7.0) & (frequencies < 64.0)
    spectrum = np.fft.rfft(values, axis=1)[:, selected]
    if spectrum.shape[1] != 285:
        raise RuntimeError(f"TFFNet expected 285 bins, got {spectrum.shape[1]}")
    concatenated = np.concatenate((spectrum.real, spectrum.imag), axis=1)
    scale = np.maximum(np.std(concatenated, axis=1, keepdims=True), 1e-9)
    return ((concatenated - concatenated.mean(axis=1, keepdims=True)) / scale).astype(np.float32)


def prepare_data(trials: Sequence[Trial]) -> PreparedData:
    dual_features = []
    fbcca_scores = []
    trca_banks = []
    eegnet_clean = []
    eegnet_banks = []
    tff_temporal = []
    tff_spectrum = []
    brainwave_raw = []
    for index, trial in enumerate(trials, start=1):
        dual = extract_dual_fbcca_features(
            trial.eeg,
            SAMPLE_RATE_HZ,
            sample_count=SAMPLE_COUNT,
            channel_count=len(SSVEP_CHANNEL_NAMES),
        )
        dual_features.append(dual.values)
        fbcca_scores.append(dual.standard50_scores)

        normalized = _notched_standardized(trial.eeg)
        trca_banks.append(np.stack([
            _bandpass(normalized, low, 50.0)
            for low in FILTER_BANK_LOW_EDGES_HZ
        ]))
        # Match the existing project FB-EEGNet profile: one 5--45 Hz stream
        # plus four normalized sub-bands.
        eegnet_clean.append(_bandpass(normalized, 5.0, 45.0))
        eegnet_banks.append(np.stack([
            _bandpass(normalized, low, 45.0)
            for low in (6.0, 10.0, 14.0, 18.0)
        ]))

        tff = _bandpass(normalized, 6.0, 80.0)
        tff_temporal.append(tff)
        tff_spectrum.append(_complex_spectrum(tff))
        brainwave_raw.append(normalized.astype(np.float32))
        if index % 50 == 0 or index == len(trials):
            print(f"prepared {index}/{len(trials)} trials", flush=True)

    return PreparedData(
        trials=tuple(trials),
        truth=np.asarray([trial.label_index for trial in trials], dtype=np.int64),
        runs=np.asarray([trial.run_id for trial in trials]),
        blocks=np.asarray([trial.block for trial in trials], dtype=np.int64),
        dual_features=np.asarray(dual_features, dtype=np.float64),
        fbcca_scores=np.asarray(fbcca_scores, dtype=np.float64),
        trca_banks=np.asarray(trca_banks, dtype=np.float32),
        eegnet_clean=np.asarray(eegnet_clean, dtype=np.float32),
        eegnet_banks=np.asarray(eegnet_banks, dtype=np.float32),
        tff_temporal=np.asarray(tff_temporal, dtype=np.float32),
        tff_spectrum=np.asarray(tff_spectrum, dtype=np.float32),
        brainwave_raw=np.asarray(brainwave_raw, dtype=np.float32),
    )


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    x = np.ravel(left).astype(np.float64)
    y = np.ravel(right).astype(np.float64)
    x -= float(np.mean(x))
    y -= float(np.mean(y))
    denominator = float(np.linalg.norm(x) * np.linalg.norm(y))
    return 0.0 if denominator <= 1e-12 else float(np.dot(x, y) / denominator)


def _trca_filter(trials: np.ndarray, ridge: float = 1e-3) -> np.ndarray:
    stack = np.asarray(trials, dtype=np.float64)
    stack -= stack.mean(axis=2, keepdims=True)
    summed = np.sum(stack, axis=0)
    q = np.einsum("nct,ndt->cd", stack, stack, optimize=True)
    s = summed @ summed.T - q
    scale = max(float(np.trace(q)) / q.shape[0], 1e-12)
    values, vectors = linalg.eigh(s, q + ridge * scale * np.eye(q.shape[0]))
    return np.asarray(vectors[:, int(np.argmax(values))], dtype=np.float64)


def fit_predict_fb_etrca(
    banks: np.ndarray,
    truth: np.ndarray,
    train: np.ndarray,
    test: np.ndarray,
) -> np.ndarray:
    predictions = []
    weights = np.asarray([
        (index + 1) ** -1.25 + 0.25 for index in range(banks.shape[1])
    ])
    templates: list[list[np.ndarray]] = []
    filters: list[np.ndarray] = []
    for band_index in range(banks.shape[1]):
        band_templates = []
        band_filters = []
        for class_index in range(len(COMMANDS)):
            class_trials = banks[train][truth[train] == class_index, band_index]
            if class_trials.shape[0] < 2:
                raise ValueError("FB-eTRCA needs at least two training trials per class")
            band_templates.append(np.mean(class_trials, axis=0))
            band_filters.append(_trca_filter(class_trials))
        templates.append(band_templates)
        filters.append(np.column_stack(band_filters))
    for trial_index in test:
        scores = np.zeros(len(COMMANDS), dtype=np.float64)
        for band_index, weight in enumerate(weights):
            spatial = filters[band_index]
            value = banks[trial_index, band_index]
            for class_index in range(len(COMMANDS)):
                rho = _corr(
                    spatial.T @ value,
                    spatial.T @ templates[band_index][class_index],
                )
                scores[class_index] += weight * np.sign(rho) * rho * rho
        predictions.append(int(np.argmax(scores)))
    return np.asarray(predictions, dtype=np.int64)


def _torch_dependencies():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    return torch, nn, DataLoader, TensorDataset


def _set_seed(seed: int, torch) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def train_predict_neural(
    method: str,
    data: PreparedData,
    train: np.ndarray,
    test: np.ndarray,
    *,
    epochs: int,
    batch_size: int,
    seed: int,
    device_name: str,
) -> tuple[np.ndarray, dict[str, object]]:
    torch, nn, DataLoader, TensorDataset = _torch_dependencies()
    _set_seed(seed, torch)
    torch.set_num_threads(max(1, min(4, int(torch.get_num_threads()))))
    device = torch.device(device_name)

    if method == "FB-EEGNet":
        model = FBEEGNetFusion(
            channels=len(SSVEP_CHANNEL_NAMES), bands=4, classes=len(COMMANDS)
        )
        arrays = (data.eegnet_clean, data.eegnet_banks)

        def forward(network, batch):
            return network(batch[0], batch[1])

    elif method == "SSVEP-TFFNet (2025)":
        model = SSVEPTFFNet(
            channels=len(SSVEP_CHANNEL_NAMES), classes=len(COMMANDS)
        )
        arrays = (data.tff_temporal, data.tff_spectrum)

        def forward(network, batch):
            return network(batch[0], batch[1])

    elif method == "CNN-BiGRU (2026 adapted)":
        model = BrainWaveDecoderAdaptation(
            channels=len(SSVEP_CHANNEL_NAMES), classes=len(COMMANDS)
        )
        arrays = (data.brainwave_raw,)

        def forward(network, batch):
            return network(batch[0])

    else:
        raise ValueError(method)

    model = model.to(device)
    tensor_arrays = tuple(torch.from_numpy(array) for array in arrays)
    targets = torch.from_numpy(data.truth)
    train_dataset = TensorDataset(
        *(array[train] for array in tensor_arrays), targets[train]
    )
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(
        train_dataset,
        batch_size=min(batch_size, len(train_dataset)),
        shuffle=True,
        generator=generator,
        drop_last=False,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    losses = []
    started = time.perf_counter()
    for _ in range(int(epochs)):
        model.train()
        epoch_losses = []
        for packed in loader:
            *batch_values, batch_target = packed
            batch_values = [value.to(device) for value in batch_values]
            batch_target = batch_target.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = forward(model, batch_values)
            loss = loss_fn(logits, batch_target)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(float(np.mean(epoch_losses)))
    training_seconds = time.perf_counter() - started
    model.eval()
    with torch.no_grad():
        batch = [array[test].to(device) for array in tensor_arrays]
        prediction = torch.argmax(forward(model, batch), dim=1).cpu().numpy()
    metadata = {
        "seed": int(seed),
        "epochs": int(epochs),
        "batch_size": int(batch_size),
        "device": str(device),
        "parameter_count": int(sum(value.numel() for value in model.parameters())),
        "training_seconds": float(training_seconds),
        "first_epoch_loss": losses[0],
        "final_epoch_loss": losses[-1],
    }
    return prediction.astype(np.int64), metadata


def _metrics(
    truth: np.ndarray,
    prediction: np.ndarray,
    runs: np.ndarray,
    run_ids: Sequence[str],
) -> dict[str, object]:
    matrix = np.zeros((len(COMMANDS), len(COMMANDS)), dtype=int)
    for actual, candidate in zip(truth, prediction):
        matrix[int(actual), int(candidate)] += 1
    correct = int(np.sum(truth == prediction))
    total = int(truth.size)
    proportion = correct / total
    z_value = 1.959963984540054
    denominator = 1.0 + z_value * z_value / total
    center = (proportion + z_value * z_value / (2.0 * total)) / denominator
    radius = z_value * math.sqrt(
        proportion * (1.0 - proportion) / total
        + z_value * z_value / (4.0 * total * total)
    ) / denominator
    per_run = {}
    run_values = []
    for run_id in run_ids:
        mask = runs == run_id
        accuracy = float(np.mean(prediction[mask] == truth[mask]))
        run_values.append(accuracy)
        per_run[run_id] = {
            "correct": int(np.sum(prediction[mask] == truth[mask])),
            "total": int(np.sum(mask)),
            "accuracy": accuracy,
        }
    return {
        "correct": correct,
        "total": total,
        "accuracy": proportion,
        "accuracy_wilson_95ci": [max(0.0, center - radius), min(1.0, center + radius)],
        "balanced_accuracy": float(balanced_accuracy_score(truth, prediction)),
        "macro_f1": float(f1_score(truth, prediction, average="macro", zero_division=0)),
        "run_accuracy_mean": float(np.mean(run_values)),
        "run_accuracy_sample_sd": float(
            np.std(run_values, ddof=1) if len(run_values) > 1 else 0.0
        ),
        "per_run": per_run,
        "per_class_recall": {
            label: float(matrix[index, index] / max(np.sum(matrix[index]), 1))
            for index, label in enumerate(COMMANDS)
        },
        "confusion_matrix_rows_true_columns_predicted": matrix.tolist(),
    }


def _mcnemar(reference_correct: np.ndarray, candidate_correct: np.ndarray) -> dict[str, object]:
    reference_only = int(np.sum(reference_correct & ~candidate_correct))
    candidate_only = int(np.sum(~reference_correct & candidate_correct))
    discordant = reference_only + candidate_only
    p_value = float(
        stats.binomtest(
            min(reference_only, candidate_only),
            discordant,
            0.5,
            alternative="two-sided",
        ).pvalue
        if discordant else 1.0
    )
    return {
        "reference_only_correct": reference_only,
        "candidate_only_correct": candidate_only,
        "discordant": discordant,
        "two_sided_exact_p": p_value,
    }


def evaluate(data: PreparedData, args) -> tuple[dict[str, np.ndarray], list[dict[str, object]]]:
    predictions = {
        method: np.full(data.truth.shape, -1, dtype=np.int64)
        for method in METHOD_ORDER
    }
    fold_rows: list[dict[str, object]] = []
    predictions["FBCCA"][:] = np.argmax(data.fbcca_scores, axis=1)

    if args.split_mode == "recording-run":
        folds = [
            {
                "held_group": held_run,
                "held_run": held_run,
                "held_block": None,
                "train": np.flatnonzero(data.runs != held_run),
                "test": np.flatnonzero(data.runs == held_run),
            }
            for held_run in args.run_ids
        ]
    else:
        if len(args.run_ids) != 1:
            raise ValueError("block split currently requires exactly one run id")
        held_run = args.run_ids[0]
        folds = [
            {
                "held_group": f"{held_run}:block-{held_block}",
                "held_run": held_run,
                "held_block": int(held_block),
                "train": np.flatnonzero(
                    (data.runs == held_run) & (data.blocks != held_block)
                ),
                "test": np.flatnonzero(
                    (data.runs == held_run) & (data.blocks == held_block)
                ),
            }
            for held_block in sorted(set(data.blocks[data.runs == held_run].tolist()))
        ]

    for fold_index, fold in enumerate(folds):
        held_run = str(fold["held_run"])
        held_block = fold["held_block"]
        train = np.asarray(fold["train"], dtype=int)
        test = np.asarray(fold["test"], dtype=int)
        train_classes = set(data.truth[train].tolist())
        if train_classes != set(range(len(COMMANDS))):
            raise ValueError(
                f"Training split for {fold['held_group']} lacks one or more classes"
            )
        print(
            f"held_group={fold['held_group']} train={len(train)} test={len(test)}",
            flush=True,
        )

        scaler = StandardScaler().fit(data.dual_features[train])
        classifier = LogisticRegression(C=0.1, max_iter=5000, random_state=args.seed)
        classifier.fit(scaler.transform(data.dual_features[train]), data.truth[train])
        predictions["Dual-FBCCA + LR"][test] = classifier.predict(
            scaler.transform(data.dual_features[test])
        )

        predictions["FB-eTRCA"][test] = fit_predict_fb_etrca(
            data.trca_banks, data.truth, train, test
        )

        if not args.classical_only:
            for method_index, method in enumerate(METHOD_ORDER[3:], start=3):
                print(f"  training {method}", flush=True)
                prediction, training = train_predict_neural(
                    method,
                    data,
                    train,
                    test,
                    epochs=args.epochs,
                    batch_size=args.batch_size,
                    seed=args.seed + 1000 * fold_index + 100 * method_index,
                    device_name=args.device,
                )
                predictions[method][test] = prediction
                fold_rows.append({
                    "held_group": str(fold["held_group"]),
                    "held_run": held_run,
                    "held_block": "" if held_block is None else int(held_block),
                    "method": method,
                    "train_count": int(len(train)),
                    "test_count": int(len(test)),
                    "test_correct": int(np.sum(prediction == data.truth[test])),
                    "test_accuracy": float(np.mean(prediction == data.truth[test])),
                    **training,
                })
                print(
                    f"    accuracy={np.mean(prediction == data.truth[test]):.3f} "
                    f"seconds={training['training_seconds']:.1f}",
                    flush=True,
                )

    if args.classical_only:
        for method in METHOD_ORDER[3:]:
            predictions.pop(method)
    for method, values in predictions.items():
        if np.any(values < 0):
            raise RuntimeError(f"{method} predictions are incomplete")
    return predictions, fold_rows


def personalized_lr_diagnostic(data: PreparedData, run_ids: Sequence[str]) -> dict[str, object]:
    """Same-session block-held-out result for deployment-context interpretation."""

    prediction = np.full(data.truth.shape, -1, dtype=np.int64)
    for run_id in run_ids:
        run_mask = data.runs == run_id
        for held_block in sorted(set(data.blocks[run_mask].tolist())):
            train = np.flatnonzero(run_mask & (data.blocks != held_block))
            test = np.flatnonzero(run_mask & (data.blocks == held_block))
            scaler = StandardScaler().fit(data.dual_features[train])
            classifier = LogisticRegression(C=0.1, max_iter=5000, random_state=0)
            classifier.fit(scaler.transform(data.dual_features[train]), data.truth[train])
            prediction[test] = classifier.predict(scaler.transform(data.dual_features[test]))
    if np.any(prediction < 0):
        raise RuntimeError("Personalized LR diagnostic did not cover every trial")
    return {
        "purpose": "context only; not part of the six-method cross-run ranking",
        "validation": "within each run, leave one complete repetition/block out",
        **_metrics(data.truth, prediction, data.runs, run_ids),
    }


def _plot_summary(
    results: dict[str, dict[str, object]],
    output_path: Path,
    *,
    split_mode: str,
) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
    ordered = sorted(results.items(), key=lambda item: float(item[1]["accuracy"]))
    names = [name for name, _ in ordered]
    values = np.asarray([100.0 * float(value["accuracy"]) for _, value in ordered])
    lower = np.asarray([
        100.0 * (float(value["accuracy"]) - float(value["accuracy_wilson_95ci"][0]))
        for _, value in ordered
    ])
    upper = np.asarray([
        100.0 * (float(value["accuracy_wilson_95ci"][1]) - float(value["accuracy"]))
        for _, value in ordered
    ])
    figure, axis = plt.subplots(figsize=(10.5, 5.8))
    positions = np.arange(len(names))
    bars = axis.barh(
        positions,
        values,
        xerr=np.vstack((lower, upper)),
        color="#2563eb",
        alpha=0.88,
        error_kw={"ecolor": "#334155", "capsize": 3, "elinewidth": 1.2},
    )
    axis.set_yticks(positions, names)
    axis.axvline(
        100.0 / len(COMMANDS),
        color="#64748b",
        linestyle="--",
        linewidth=1.3,
        label="chance 16.7%",
    )
    axis.set_xlim(0.0, max(45.0, float(np.max(values + upper)) + 8.0))
    if split_mode == "block":
        axis.set_xlabel("Leave-one-complete-block-out accuracy (%)")
        axis.set_title(
            "Oct-24 six-method SSVEP benchmark (5 s, 8 channels, 6 classes)\n"
            "60 recorded: 51 active evaluated; 9 idle excluded"
        )
    elif split_mode == "chronological-30-30":
        axis.set_xlabel("Chronological holdout accuracy on trials 31--60 (%)")
        axis.set_title(
            "Oct-24 first-30 train / last-30 test SSVEP benchmark\n"
            "Test half: 25 active evaluated; 5 idle excluded"
        )
    elif split_mode == "random-30-30":
        axis.set_xlabel("Fixed-seed random 30/30 holdout accuracy (%)")
        axis.set_title(
            "Oct-24 random-30 train / remaining-30 test SSVEP benchmark\n"
            "Random seed 20261027; test half: 25 active evaluated; 5 idle excluded"
        )
    else:
        axis.set_xlabel("Leave-one-recording-run-out accuracy (%)")
        axis.set_title("Six-method SSVEP pilot benchmark (5 s, 8 channels, 6 classes)")
    axis.grid(axis="x", alpha=0.22)
    axis.legend(loc="lower right", frameon=False)
    for bar, value in zip(bars, values):
        axis.text(
            value + 0.7,
            bar.get_y() + bar.get_height() / 2.0,
            f"{value:.1f}%",
            va="center",
        )
    figure.tight_layout()
    figure.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(figure)


def write_outputs(
    output_dir: Path,
    data: PreparedData,
    args,
    predictions: dict[str, np.ndarray],
    fold_rows: list[dict[str, object]],
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory = trial_inventory(args.raw_dir, args.run_ids)
    if int(inventory["active_trial_count"]) != int(data.truth.size):
        raise RuntimeError("Saved-trial inventory disagrees with loaded active trials")
    results = {
        method: _metrics(data.truth, prediction, data.runs, args.run_ids)
        for method, prediction in predictions.items()
    }
    reference_correct = predictions["Dual-FBCCA + LR"] == data.truth
    for method, prediction in predictions.items():
        results[method]["paired_vs_dual_fbcca_lr"] = _mcnemar(
            reference_correct, prediction == data.truth
        )
    ranking = sorted(
        results,
        key=lambda name: (
            float(results[name]["accuracy"]),
            float(results[name]["balanced_accuracy"]),
        ),
        reverse=True,
    )
    validation_description = (
        "Within the selected recording run, leave one complete repetition/block "
        "out. The held block contributes no training trials, scaler statistics, "
        "templates, spatial filters, or neural weights."
        if args.split_mode == "block"
        else
        "Leave one complete recording run out. The held run contributes no "
        "training trials, scaler statistics, templates, spatial filters, "
        "neural weights, or hyperparameter choices."
    )
    report = {
        "status": "offline_research_pilot_not_runtime_or_flight_approved",
        "created_date": "2026-10-27",
        "split_mode": args.split_mode,
        "primary_validation": validation_description,
        "window": {
            "seconds": 5.0,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "samples": SAMPLE_COUNT,
            "channels": list(SSVEP_CHANNEL_NAMES),
            "classes": list(COMMANDS),
            "frequencies_hz": list(TARGET_FREQUENCIES_HZ),
        },
        "data": {
            "run_ids": list(args.run_ids),
            "recording_run_count": len(args.run_ids),
            **inventory,
            "six_class_metric_denominator": int(data.truth.size),
            "idle_handling": (
                "Non-command/idle trials are excluded from the six-class accuracy; "
                "they are not converted into active-command labels."
            ),
            "important_limitation": (
                "This block-held-out result uses one recording session and must not "
                "be described as cross-session or cross-subject generalization."
                if args.split_mode == "block" else
                "The saved metadata identifies local-research sessions, not verified "
                "participant identities. These folds are recording runs and must not "
                "be described as leave-one-subject-out."
            ),
        },
        "methods": {
            "FBCCA": "fixed training-free 6/14/22--50 Hz filter-bank CCA",
            "Dual-FBCCA + LR": "current 12-score calibration with C=0.1 multinomial LR",
            "FB-eTRCA": "three-subband ensemble TRCA trained only on non-held runs",
            "FB-EEGNet": "project filter-bank EEGNet fusion implementation",
            "SSVEP-TFFNet (2025)": (
                "Dai et al. time/complex-spectrum branches and dynamic fusion; "
                "output adapted from paper datasets to six classes"
            ),
            "CNN-BiGRU (2026 adapted)": (
                "CNN + bidirectional-GRU family reported by BrainWaveDecoder; "
                "research adaptation, not an exact paper reproduction"
            ),
        },
        "neural_training": {
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "optimizer": "AdamW(lr=0.001, weight_decay=0.001)",
            "loss": "cross entropy with label smoothing 0.05",
            "seed": int(args.seed),
            "note": (
                "One deterministic seed was used per fold. Deep-model uncertainty "
                "requires repeated seeds and a larger dataset before publication."
            ),
        },
        "ranking": [
            {"rank": index, "method": method, **results[method]}
            for index, method in enumerate(ranking, start=1)
        ],
        "personalized_lr_diagnostic": personalized_lr_diagnostic(data, args.run_ids),
        "neural_fold_training": fold_rows,
        "publication_limits": [
            (
                "This is a same-session pilot on one recording run and 51 active trials."
                if args.split_mode == "block" else
                "This is a pilot on four recording runs and 206 active trials."
            ),
            (
                "The result does not measure future-session or new-subject generalization."
                if args.split_mode == "block" else
                "Recording-run identities are not verified participant identities."
            ),
            "BrainWaveDecoder is an architecture-family adaptation, not an exact reproduction.",
            "No independent future-day prospective online test is included.",
            "Do not copy paper-reported public-dataset accuracies beside these numbers as if directly comparable.",
        ],
        "references": [
            "https://doi.org/10.3389/fnins.2025.1679451",
            "https://doi.org/10.1016/j.bspc.2025.108792",
            "https://doi.org/10.1088/1741-2560/12/4/046008",
            "https://doi.org/10.1109/TBME.2017.2694818",
        ],
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow((
            "rank", "method", "correct", "total", "accuracy",
            "balanced_accuracy", "macro_f1", "ci95_low", "ci95_high",
            "run_mean", "run_sample_sd",
        ))
        for index, method in enumerate(ranking, start=1):
            value = results[method]
            writer.writerow((
                index,
                method,
                value["correct"],
                value["total"],
                value["accuracy"],
                value["balanced_accuracy"],
                value["macro_f1"],
                value["accuracy_wilson_95ci"][0],
                value["accuracy_wilson_95ci"][1],
                value["run_accuracy_mean"],
                value["run_accuracy_sample_sd"],
            ))
    with (output_dir / "predictions.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow((
            "source_file", "run_id", "block", "true_label",
            *[f"prediction::{method}" for method in predictions],
        ))
        for index, trial in enumerate(data.trials):
            writer.writerow((
                trial.path.name,
                trial.run_id,
                trial.block,
                COMMANDS[int(data.truth[index])],
                *[COMMANDS[int(values[index])] for values in predictions.values()],
            ))
    with (output_dir / "neural_folds.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fieldnames = sorted({key for row in fold_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(fold_rows)
    _plot_summary(
        results,
        output_dir / "method_comparison.png",
        split_mode=args.split_mode,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "research_experiments" / "raw_trials",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "recent_ssvep_method_benchmark_20261027",
    )
    parser.add_argument("--run-ids", nargs="+", default=list(DEFAULT_RUN_IDS))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--split-mode",
        choices=("recording-run", "block"),
        default="recording-run",
    )
    parser.add_argument("--classical-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.raw_dir = args.raw_dir.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.epochs < 1 or args.batch_size < 1:
        raise ValueError("epochs and batch size must be positive")
    trials = load_trials(args.raw_dir, args.run_ids)
    print(
        f"loaded {len(trials)} active trials from {len(args.run_ids)} recording runs",
        flush=True,
    )
    data = prepare_data(trials)
    predictions, fold_rows = evaluate(data, args)
    report = write_outputs(args.output_dir, data, args, predictions, fold_rows)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "ranking": [
            {
                "rank": row["rank"],
                "method": row["method"],
                "accuracy": row["accuracy"],
                "correct": row["correct"],
                "total": row["total"],
            }
            for row in report["ranking"]
        ],
        "personalized_lr_accuracy": report["personalized_lr_diagnostic"]["accuracy"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
