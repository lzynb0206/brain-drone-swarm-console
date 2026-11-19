#!/usr/bin/env python3
"""Evaluate the independent BrainWaveDecoder reproduction on subject 24.

Only the two user-confirmed runs are read.  The six-command accuracy uses the
same frozen, block-grouped five-fold splits as the audited method comparison,
so every active trial is tested exactly once and no crop from a test trial can
enter training.  Idle trials are audited but are outside the paper's closed-set
target classifier and therefore are not silently treated as a seventh class.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

# The installed NumPy/scikit-learn/Torch stack contains two OpenMP runtimes.
# Keeping numerical kernels single-threaded prevents libomp crashes and avoids
# making the user's machine unresponsive during the five-fold run.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bci.config import COMMANDS, SSVEP_CHANNEL_NAMES
from src.ml.brainwave_decoder import BrainWaveDecoder
from src.ml.ssvep_repair import quality_preprocess


RUNS = ("auto-20261024-145545-3730b3", "auto-20261027-204129-9c221a")
EXPECTED_RUN_COUNTS = (60, 105)
EXPECTED_LABEL_COUNTS = dict(zip((*COMMANDS, "空闲"), (23, 23, 24, 24, 23, 24, 24)))
RAW_ROOT = ROOT / "data/research_experiments/raw_trials"
AUDIT_ROOT = ROOT / "outputs/subject24_audited_six_methods_20261028"
MANIFEST_PATH = AUDIT_ROOT / "data_manifest.json"
DEFAULT_OUTPUT = ROOT / "outputs/brainwave_decoder_subject24_20261028"
SAMPLE_RATE_HZ = 250
TRIAL_SAMPLES = 1250
WINDOW_SAMPLES = 250
VISUAL_LATENCY_SAMPLES = 34  # 0.136 seconds at 250 Hz.


@dataclass(frozen=True)
class EvaluationConfig:
    epochs: int = 30
    batch_size: int = 32
    learning_rate: float = 1.0e-3
    weight_decay: float = 0.0
    seed: int = 20261028
    input_scale: float = 1.0e-4
    normalization: str = "batch"
    temporal_kernel_size: int = 7
    gru_layout: str = "time_features"
    input_transform: str = "per-window per-channel mean removal"
    optimizer: str = "Adam"
    loss: str = "cross entropy"


@dataclass(frozen=True)
class SubjectData:
    eeg: np.ndarray
    labels: np.ndarray
    files: tuple[str, ...]
    records: tuple[dict[str, object], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_subject_data() -> SubjectData:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if len(manifest) != sum(EXPECTED_RUN_COUNTS):
        raise AssertionError(f"expected 165 manifest rows, got {len(manifest)}")
    records_by_file = {str(row["source_file"]): row for row in manifest}
    if len(records_by_file) != len(manifest):
        raise AssertionError("duplicate source filenames in manifest")

    eeg: list[np.ndarray] = []
    labels: list[int] = []
    files: list[str] = []
    active_records: list[dict[str, object]] = []
    observed_labels: Counter[str] = Counter()
    for run, expected in zip(RUNS, EXPECTED_RUN_COUNTS):
        paths = sorted(RAW_ROOT.glob(f"{run}-trial-*.npz"))
        if len(paths) != expected:
            raise AssertionError(f"{run}: expected {expected} files, got {len(paths)}")
        for path in paths:
            record = records_by_file.get(path.name)
            if record is None:
                raise AssertionError(f"{path.name} is absent from frozen manifest")
            if _sha256(path) != str(record["file_sha256"]):
                raise AssertionError(f"source file changed after audit: {path.name}")
            with np.load(path, allow_pickle=False) as payload:
                values = np.asarray(payload["eeg"], dtype=np.float32)
                label = str(payload["true_label"].item())
                fs = float(payload["fs"])
                channels = tuple(str(value) for value in payload["channel_names"].tolist())
            if values.shape != (8, TRIAL_SAMPLES) or fs != SAMPLE_RATE_HZ:
                raise AssertionError(f"unexpected acquisition geometry in {path.name}")
            if channels != SSVEP_CHANNEL_NAMES or not np.isfinite(values).all():
                raise AssertionError(f"invalid channels or non-finite EEG in {path.name}")
            if label != str(record["true_label"]):
                raise AssertionError(f"label changed after audit: {path.name}")
            observed_labels[label] += 1
            label_index = int(record["label_index"])
            if label_index >= 0:
                eeg.append(values)
                labels.append(label_index)
                files.append(path.name)
                active_records.append(record)

    if observed_labels != Counter(EXPECTED_LABEL_COUNTS):
        raise AssertionError(f"unexpected label counts: {observed_labels}")
    result = SubjectData(
        eeg=np.asarray(eeg, dtype=np.float32),
        labels=np.asarray(labels, dtype=np.int64),
        files=tuple(files),
        records=tuple(active_records),
    )
    if result.eeg.shape != (141, 8, TRIAL_SAMPLES):
        raise AssertionError(f"unexpected active EEG shape {result.eeg.shape}")
    if np.bincount(result.labels, minlength=6).tolist() != [23, 23, 24, 24, 23, 24]:
        raise AssertionError("active class counts changed")
    return result


def crop_starts(mode: str) -> tuple[int, ...]:
    if mode == "single_1s":
        return (VISUAL_LATENCY_SAMPLES,)
    if mode in {"five_window_fusion", "project_preprocessed_fusion"}:
        # Five nearly non-overlapping one-second views after visual latency.
        # All views from a trial remain in the same outer fold.
        return tuple(
            int(value)
            for value in np.linspace(
                VISUAL_LATENCY_SAMPLES,
                TRIAL_SAMPLES - WINDOW_SAMPLES,
                num=5,
            ).round()
        )
    raise ValueError(f"unknown evaluation mode {mode!r}")


def make_segments(
    eeg: np.ndarray, starts: tuple[int, ...], *, project_preprocess: bool = False
) -> np.ndarray:
    """Return trial-major one-second views using a label-free transform."""

    if eeg.ndim != 3 or eeg.shape[1:] != (8, TRIAL_SAMPLES):
        raise ValueError(f"expected (trial,8,{TRIAL_SAMPLES}), got {eeg.shape}")
    source = eeg
    if project_preprocess:
        # Apply the project's existing QC/detrend/notch/6-80 Hz/standardize
        # chain to each full five-second trial before cropping. Bad electrodes
        # retain their physical slots as zeros, as in the repaired benchmarks.
        source = np.stack([quality_preprocess(row)[0] for row in eeg])
    views = np.stack(
        [source[:, :, start : start + WINDOW_SAMPLES] for start in starts], axis=1
    ).astype(np.float32, copy=True)
    if views.shape != (len(eeg), len(starts), 8, WINDOW_SAMPLES):
        raise AssertionError(f"unexpected crop shape {views.shape}")
    if not project_preprocess:
        views -= views.mean(axis=-1, keepdims=True)
    if not np.isfinite(views).all():
        raise ValueError("prepared EEG contains NaN or Inf")
    return views


def load_folds(data: SubjectData) -> list[tuple[str, np.ndarray, np.ndarray]]:
    index_by_file = {name: index for index, name in enumerate(data.files)}
    folds: list[tuple[str, np.ndarray, np.ndarray]] = []
    all_test: list[int] = []
    for number in range(1, 6):
        split_path = AUDIT_ROOT / f"grouped_fold_{number}" / "split.json"
        split = json.loads(split_path.read_text(encoding="utf-8"))
        train = np.asarray(
            [index_by_file[name] for name in split["train"] if name in index_by_file],
            dtype=np.int64,
        )
        test = np.asarray(
            [index_by_file[name] for name in split["test"] if name in index_by_file],
            dtype=np.int64,
        )
        if set(train.tolist()) & set(test.tolist()):
            raise AssertionError(f"train/test leakage in fold {number}")
        if set(train.tolist()) | set(test.tolist()) != set(range(len(data.files))):
            raise AssertionError(f"fold {number} does not cover all active trials")
        train_groups = {str(data.records[index]["group"]) for index in train}
        test_groups = {str(data.records[index]["group"]) for index in test}
        if train_groups & test_groups:
            raise AssertionError(f"block leakage in fold {number}")
        folds.append((str(split["name"]), train, test))
        all_test.extend(test.tolist())
    if Counter(all_test) != Counter(range(len(data.files))):
        raise AssertionError("each active trial must be tested exactly once")
    return folds


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except TypeError:
        torch.use_deterministic_algorithms(True)


def _train_and_predict(
    views: np.ndarray,
    labels: np.ndarray,
    train: np.ndarray,
    test: np.ndarray,
    *,
    config: EvaluationConfig,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, list[float], int, float]:
    """Train on train-trial crops and pool test-crop probabilities per trial."""

    _seed_everything(seed)
    train_x = views[train].reshape(-1, 8, WINDOW_SAMPLES)
    train_y = np.repeat(labels[train], views.shape[1])
    test_x = views[test].reshape(-1, 8, WINDOW_SAMPLES)
    model = BrainWaveDecoder(
        channels=8,
        samples=WINDOW_SAMPLES,
        classes=len(COMMANDS),
        input_scale=config.input_scale,
        normalization=config.normalization,
        temporal_kernel_size=config.temporal_kernel_size,
        gru_layout=config.gru_layout,
    )
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    criterion = nn.CrossEntropyLoss()
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(train_x), torch.from_numpy(train_y)),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
        drop_last=False,
    )
    losses: list[float] = []
    started = time.monotonic()
    for _ in range(config.epochs):
        model.train()
        total_loss = 0.0
        total_count = 0
        for values, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(values)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            count = len(targets)
            total_loss += float(loss.detach()) * count
            total_count += count
        losses.append(total_loss / total_count)

    model.eval()
    probabilities: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(test_x), config.batch_size):
            logits = model(torch.from_numpy(test_x[start : start + config.batch_size]))
            probabilities.append(torch.softmax(logits, dim=1).cpu().numpy())
    crop_probabilities = np.concatenate(probabilities, axis=0)
    trial_probabilities = crop_probabilities.reshape(len(test), views.shape[1], -1).mean(axis=1)
    predictions = trial_probabilities.argmax(axis=1).astype(np.int64)
    return predictions, trial_probabilities, losses, parameter_count, time.monotonic() - started


def _confusion_matrix(targets: np.ndarray, predictions: np.ndarray) -> np.ndarray:
    matrix = np.zeros((len(COMMANDS), len(COMMANDS)), dtype=np.int64)
    np.add.at(matrix, (targets, predictions), 1)
    return matrix


def run_mode(
    mode: str,
    data: SubjectData,
    folds: list[tuple[str, np.ndarray, np.ndarray]],
    *,
    output_root: Path,
    config: EvaluationConfig,
    max_folds: int | None,
    resume: bool,
) -> dict[str, object]:
    starts = crop_starts(mode)
    project_preprocess = mode == "project_preprocessed_fusion"
    views = make_segments(data.eeg, starts, project_preprocess=project_preprocess)
    if project_preprocess:
        config = replace(
            config,
            input_scale=1.0,
            input_transform=(
                "project QC mask + detrend + 50-Hz notch + 6-80-Hz bandpass + "
                "per-channel standardization"
            ),
        )
    output_dir = output_root / mode
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "fold_results.json"
    results: list[dict[str, object]] = []
    if resume and results_path.is_file():
        results = json.loads(results_path.read_text(encoding="utf-8"))
    completed = {str(row["fold_name"]) for row in results}
    planned_folds = folds[:max_folds] if max_folds is not None else folds
    started = time.monotonic()

    for position, (name, train, test) in enumerate(planned_folds, start=1):
        if name in completed:
            print(f"{mode} [{position}/{len(planned_folds)}] {name}: resumed", flush=True)
            continue
        fold_seed = config.seed + position
        predictions, probabilities, losses, parameter_count, elapsed = _train_and_predict(
            views,
            data.labels,
            train,
            test,
            config=config,
            seed=fold_seed,
        )
        targets = data.labels[test]
        rows = []
        for local_index, data_index in enumerate(test):
            rows.append(
                {
                    "source_file": data.files[int(data_index)],
                    "run_id": str(data.records[int(data_index)]["run_id"]),
                    "block": int(data.records[int(data_index)]["block"]),
                    "true_label_index": int(targets[local_index]),
                    "true_label": COMMANDS[int(targets[local_index])],
                    "predicted_label_index": int(predictions[local_index]),
                    "predicted_label": COMMANDS[int(predictions[local_index])],
                    "correct": bool(predictions[local_index] == targets[local_index]),
                    "probabilities": [float(value) for value in probabilities[local_index]],
                }
            )
        result = {
            "fold_name": name,
            "seed": fold_seed,
            "train_trial_count": int(len(train)),
            "train_segment_count": int(len(train) * len(starts)),
            "test_trial_count": int(len(test)),
            "test_segment_count": int(len(test) * len(starts)),
            "correct": int(np.sum(predictions == targets)),
            "accuracy": float(np.mean(predictions == targets)),
            "final_train_loss": float(losses[-1]),
            "train_loss_by_epoch": [float(value) for value in losses],
            "parameter_count": int(parameter_count),
            "elapsed_seconds": elapsed,
            "predictions": rows,
        }
        results.append(result)
        results.sort(key=lambda value: str(value["fold_name"]))
        _atomic_json(results_path, results)
        print(
            f"{mode} [{position}/{len(planned_folds)}] {name}: "
            f"{result['correct']}/{len(test)}={100.0 * result['accuracy']:.2f}% "
            f"loss={losses[-1]:.4f} elapsed={elapsed:.1f}s",
            flush=True,
        )

    planned_names = {name for name, _, _ in planned_folds}
    rows = [
        prediction
        for result in results
        if str(result["fold_name"]) in planned_names
        for prediction in result["predictions"]
    ]
    filenames = [str(row["source_file"]) for row in rows]
    if len(filenames) != len(set(filenames)):
        raise AssertionError("a test trial was predicted more than once")
    targets = np.asarray([int(row["true_label_index"]) for row in rows], dtype=np.int64)
    predictions = np.asarray(
        [int(row["predicted_label_index"]) for row in rows], dtype=np.int64
    )
    complete = len(planned_folds) == 5 and len(rows) == len(data.files)
    if complete and set(filenames) != set(data.files):
        raise AssertionError("complete run did not test every active trial exactly once")
    matrix = _confusion_matrix(targets, predictions)
    per_class = {
        label: {
            "correct": int(matrix[index, index]),
            "count": int(matrix[index].sum()),
            "accuracy": float(matrix[index, index] / matrix[index].sum()),
        }
        for index, label in enumerate(COMMANDS)
    }

    with (output_dir / "out_of_fold_predictions.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        fields = (
            "source_file",
            "run_id",
            "block",
            "true_label",
            "predicted_label",
            "correct",
            "max_probability",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda value: str(value["source_file"])):
            writer.writerow(
                {
                    **{field: row[field] for field in fields if field != "max_probability"},
                    "max_probability": max(float(value) for value in row["probabilities"]),
                }
            )

    summary = {
        "status": "complete" if complete else "pilot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "independent BrainWaveDecoder paper-derived CNN + Bi-GRU reproduction",
        "paper_doi": "10.1016/j.bspc.2025.108792",
        "evaluation_mode": mode,
        "source_runs": list(RUNS),
        "all_recorded_trial_count": 165,
        "active_six_command_trial_count": 141,
        "idle_trial_count_excluded_from_closed_set_classifier": 24,
        "class_order": list(COMMANDS),
        "class_counts": np.bincount(data.labels, minlength=6).tolist(),
        "window_samples": WINDOW_SAMPLES,
        "window_seconds": WINDOW_SAMPLES / SAMPLE_RATE_HZ,
        "crop_starts_samples": list(starts),
        "crop_starts_seconds": [value / SAMPLE_RATE_HZ for value in starts],
        "probability_pooling": "arithmetic mean across windows per trial",
        "config": asdict(config),
        "architecture_profile": BrainWaveDecoder(
            channels=8,
            samples=WINDOW_SAMPLES,
            classes=6,
            input_scale=config.input_scale,
            normalization=config.normalization,
            temporal_kernel_size=config.temporal_kernel_size,
            gru_layout=config.gru_layout,
        ).profile,
        "parameter_count": int(results[0]["parameter_count"]) if results else 0,
        "evaluated_fold_count": len(planned_folds),
        "prediction_count": len(rows),
        "correct": int(np.sum(predictions == targets)),
        "accuracy": float(np.mean(predictions == targets)),
        "confusion_matrix_rows_true_columns_predicted": matrix.tolist(),
        "per_class": per_class,
        "fold_accuracy": {
            str(result["fold_name"]): float(result["accuracy"])
            for result in results
            if str(result["fold_name"]) in planned_names
        },
        "elapsed_seconds_this_invocation": time.monotonic() - started,
        "leakage_audit": {
            "split_source": str(AUDIT_ROOT / "grouped_fold_N/split.json"),
            "split_unit": "whole acquisition block",
            "train_test_block_overlap": 0,
            "duplicate_test_trials": len(filenames) - len(set(filenames)),
            "every_active_trial_tested_once": complete,
            "all_windows_from_each_trial_kept_in_one_fold": True,
        },
        "fidelity_note": (
            "Uses the five public convolution widths and Bi-GRU structure, but the paper's "
            "official code/full hyperparameter table was unavailable. The low-memory "
            "time_features GRU layout, kernel size, centering, optimizer, and training "
            "settings are explicit reproduction assumptions, not claimed paper facts."
        ),
        "artifacts": {
            "fold_results": str(results_path.resolve()),
            "out_of_fold_predictions": str(
                (output_dir / "out_of_fold_predictions.csv").resolve()
            ),
        },
    }
    _atomic_json(output_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("single_1s", "five_window_fusion", "project_preprocessed_fusion"),
        default=("single_1s",),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--max-folds", type=int, choices=range(1, 6))
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = EvaluationConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    data = load_subject_data()
    folds = load_folds(data)
    summaries = {}
    for mode in args.modes:
        summaries[mode] = run_mode(
            mode,
            data,
            folds,
            output_root=args.output,
            config=config,
            max_folds=args.max_folds,
            resume=args.resume,
        )
    print(
        json.dumps(
            {
                mode: {
                    "status": value["status"],
                    "correct": value["correct"],
                    "prediction_count": value["prediction_count"],
                    "accuracy": value["accuracy"],
                }
                for mode, value in summaries.items()
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
