#!/usr/bin/env python3
"""Reproduce the public 12-class BrainWaveDecoder experiment.

This is an independent, auditable reproduction.  It uses the architecture
facts visible in the paper's public graphical abstract and ablation figure and
records every setting that had to be inferred because no official source code
or open full text was available.  The evaluation is subject-dependent and
stores strictly out-of-fold predictions for every trial.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import h5py
from scipy import signal
from scipy.io import loadmat
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.brainwave_decoder import BrainWaveDecoder, BrainWaveDecoderProfile


DATASET_URL = "https://github.com/mnakanishi/12JFPM_SSVEP"
PAPER_URL = "https://doi.org/10.1016/j.bspc.2025.108792"
FREQUENCIES_HZ = (
    9.25, 11.25, 13.25, 9.75, 11.75, 13.75,
    10.25, 12.25, 14.25, 10.75, 12.75, 14.75,
)
CHANNELS = ("PO7", "PO3", "POz", "PO4", "PO8", "O1", "Oz", "O2")
SOURCE_SAMPLE_RATE_HZ = 256
TARGET_SAMPLE_RATE_HZ = 250
VISUAL_LATENCY_SECONDS = 0.135
SOURCE_ONSET_SAMPLE = 39


@dataclass(frozen=True)
class ReproductionConfig:
    window_seconds: float = 1.0
    folds: int = 5
    epochs: int = 100
    batch_size: int = 32
    learning_rate: float = 1.0e-3
    weight_decay: float = 0.0
    seed: int = 20251028
    input_scale: float = 1.0e-4
    normalization: str = "batch"
    input_transform: str = "raw"
    temporal_kernel_size: int = 3
    start_after_onset_seconds: float = VISUAL_LATENCY_SECONDS
    gru_layout: str = "per_feature_map"
    split_mode: str = "grouped_blocks"
    optimizer: str = "Adam"
    loss: str = "cross entropy"
    split: str = "subject-dependent grouped 5-fold by recording block"
    preprocessing: str = "latency crop and 256-to-250 Hz resampling only"
    augmentation: str = "none"
    transfer_learning: str = "none"


@dataclass(frozen=True)
class PreparedData:
    eeg: np.ndarray
    labels: np.ndarray
    subjects: np.ndarray
    blocks: np.ndarray
    source_rows: np.ndarray


def apply_input_transform(data: PreparedData, name: str) -> PreparedData:
    """Apply a label-free diagnostic transform without mutating source data."""

    if name == "raw":
        return data
    eeg = np.asarray(data.eeg, dtype=np.float32).copy()
    eeg -= eeg.mean(axis=-1, keepdims=True)
    if name == "channel_zscore":
        scale = np.maximum(eeg.std(axis=-1, keepdims=True), 1.0e-6)
        eeg /= scale
    elif name != "channel_center":
        raise ValueError(f"unknown input transform {name!r}")
    return PreparedData(eeg, data.labels, data.subjects, data.blocks, data.source_rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha256(path: Path, subject_count: int | None = None) -> str:
    if path.is_file():
        return _sha256(path)
    if not path.is_dir():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    if subject_count is None:
        children = sorted(path.glob("s*.mat"))
    else:
        children = [path / f"s{subject}.mat" for subject in range(1, subject_count + 1)]
        missing = [child for child in children if not child.is_file()]
        if missing:
            raise FileNotFoundError(f"missing source files: {missing}")
    for child in children:
        digest.update(child.name.encode("utf-8"))
        digest.update(_sha256(child).encode("ascii"))
    return digest.hexdigest()


def _locate_subject_members(archive: zipfile.ZipFile) -> list[str]:
    by_number: dict[int, str] = {}
    for name in archive.namelist():
        path = Path(name)
        if path.parent.name.lower() != "data" or path.suffix.lower() != ".mat":
            continue
        stem = path.stem.lower()
        if stem.startswith("s") and stem[1:].isdigit():
            number = int(stem[1:])
            if 1 <= number <= 10:
                by_number[number] = name
    if sorted(by_number) != list(range(1, 11)):
        raise ValueError(f"archive must contain data/s1.mat ... data/s10.mat; got {sorted(by_number)}")
    return [by_number[number] for number in range(1, 11)]


def prepare_nakanishi_archive(
    archive_path: Path,
    *,
    window_seconds: float,
    start_after_onset_seconds: float = VISUAL_LATENCY_SECONDS,
    max_subjects: int | None = None,
) -> PreparedData:
    """Load raw eight-channel trials directly from the official GitHub archive."""

    if not archive_path.is_file():
        raise FileNotFoundError(
            f"missing {archive_path}; download the official 12JFPM_SSVEP master.zip"
        )
    if not 0.2 <= window_seconds <= 1.0:
        raise ValueError("window_seconds must be between 0.2 and 1.0")

    source_count = round(window_seconds * SOURCE_SAMPLE_RATE_HZ)
    target_count = round(window_seconds * TARGET_SAMPLE_RATE_HZ)
    start = SOURCE_ONSET_SAMPLE + round(start_after_onset_seconds * SOURCE_SAMPLE_RATE_HZ)
    all_eeg: list[np.ndarray] = []
    all_labels: list[int] = []
    all_subjects: list[int] = []
    all_blocks: list[int] = []
    all_rows: list[int] = []
    source_row = 0

    with zipfile.ZipFile(archive_path) as archive:
        members = _locate_subject_members(archive)
        subject_limit = min(max_subjects or 10, 10)
        for subject, member in enumerate(members[:subject_limit], start=1):
            with archive.open(member) as handle:
                matrix = np.asarray(loadmat(handle, squeeze_me=True)["eeg"])
            if matrix.ndim != 4 or matrix.shape[:2] != (12, 8):
                raise ValueError(f"unexpected {member} EEG shape {matrix.shape}")
            if matrix.shape[3] != 15:
                raise ValueError(f"expected 15 blocks in {member}; got {matrix.shape[3]}")
            stop = start + source_count
            if stop > matrix.shape[2]:
                raise ValueError(f"window exceeds {member} time axis")
            for label in range(12):
                for block in range(15):
                    trial = np.asarray(matrix[label, :, start:stop, block], dtype=np.float32)
                    # Changing the sampling grid is acquisition alignment, not a
                    # learned transform, band-pass filter, or feature extractor.
                    trial = signal.resample_poly(trial, 125, 128, axis=-1)
                    trial = trial[:, :target_count]
                    if trial.shape != (8, target_count):
                        raise ValueError(f"unexpected resampled shape {trial.shape}")
                    all_eeg.append(trial)
                    all_labels.append(label)
                    all_subjects.append(subject)
                    all_blocks.append(block + 1)
                    all_rows.append(source_row)
                    source_row += 1

    prepared = PreparedData(
        eeg=np.asarray(all_eeg, dtype=np.float32),
        labels=np.asarray(all_labels, dtype=np.int64),
        subjects=np.asarray(all_subjects, dtype=np.int64),
        blocks=np.asarray(all_blocks, dtype=np.int64),
        source_rows=np.asarray(all_rows, dtype=np.int64),
    )
    expected = (max_subjects or 10) * 12 * 15
    if prepared.eeg.shape != (expected, 8, target_count):
        raise AssertionError(f"prepared shape mismatch: {prepared.eeg.shape}")
    if not np.isfinite(prepared.eeg).all():
        raise ValueError("raw EEG contains NaN or Inf")
    return prepared


def prepare_nakanishi_directory(
    directory: Path,
    *,
    window_seconds: float,
    start_after_onset_seconds: float = VISUAL_LATENCY_SECONDS,
    max_subjects: int | None = None,
) -> PreparedData:
    """Load verified original s1.mat ... s10.mat files from a directory."""

    subject_limit = min(max_subjects or 10, 10)
    paths = [directory / f"s{subject}.mat" for subject in range(1, subject_limit + 1)]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing source MAT files: {missing}")
    if not 0.2 <= window_seconds <= 1.0:
        raise ValueError("window_seconds must be between 0.2 and 1.0")
    source_count = round(window_seconds * SOURCE_SAMPLE_RATE_HZ)
    target_count = round(window_seconds * TARGET_SAMPLE_RATE_HZ)
    start = SOURCE_ONSET_SAMPLE + round(start_after_onset_seconds * SOURCE_SAMPLE_RATE_HZ)
    values: list[np.ndarray] = []
    labels: list[int] = []
    subjects: list[int] = []
    blocks: list[int] = []
    for subject, path in enumerate(paths, start=1):
        matrix = np.asarray(loadmat(path, squeeze_me=True)["eeg"])
        if matrix.shape[:2] != (12, 8) or matrix.ndim != 4 or matrix.shape[3] != 15:
            raise ValueError(f"unexpected {path} EEG shape {matrix.shape}")
        for label in range(12):
            for block in range(15):
                trial = np.asarray(
                    matrix[label, :, start:start + source_count, block], dtype=np.float32
                )
                trial = signal.resample_poly(trial, 125, 128, axis=-1)[:, :target_count]
                values.append(trial)
                labels.append(label)
                subjects.append(subject)
                blocks.append(block + 1)
    label_array = np.asarray(labels, dtype=np.int64)
    return PreparedData(
        eeg=np.asarray(values, dtype=np.float32),
        labels=label_array,
        subjects=np.asarray(subjects, dtype=np.int64),
        blocks=np.asarray(blocks, dtype=np.int64),
        source_rows=np.arange(len(label_array), dtype=np.int64),
    )


def prepare_nakanishi_h5(
    h5_path: Path,
    *,
    window_seconds: float,
    start_after_onset_seconds: float = VISUAL_LATENCY_SECONDS,
    max_subjects: int | None = None,
) -> PreparedData:
    """Load the existing six-channel raw compact cache for a fast pilot.

    The cache was created from the same official MAT files with the same
    latency correction and 256-to-250 Hz resampling.  It keeps six of the
    original eight posterior channels, so results from this path are labelled
    as a six-channel pilot and never presented as the exact paper protocol.
    """

    if not h5_path.is_file():
        raise FileNotFoundError(h5_path)
    if not np.isclose(start_after_onset_seconds, VISUAL_LATENCY_SECONDS):
        raise ValueError("compact H5 is fixed at the 0.135-second latency crop")
    if not 0.2 <= window_seconds <= 1.0:
        raise ValueError("window_seconds must be between 0.2 and 1.0")
    sample_count = round(window_seconds * TARGET_SAMPLE_RATE_HZ)
    with h5py.File(h5_path, "r") as handle:
        eeg = np.asarray(handle["eeg"][:, :, :sample_count], dtype=np.float32)
        labels = np.asarray(handle["label"][:], dtype=np.int64)
        subjects = np.asarray(handle["subject"][:], dtype=np.int64)
        blocks = np.asarray(handle["block"][:], dtype=np.int64)
    if max_subjects is not None:
        keep = subjects <= max_subjects
        eeg, labels, subjects, blocks = eeg[keep], labels[keep], subjects[keep], blocks[keep]
    if eeg.ndim != 3 or eeg.shape[1] != 6 or eeg.shape[2] != sample_count:
        raise ValueError(f"unexpected compact EEG shape {eeg.shape}")
    return PreparedData(
        eeg=eeg,
        labels=labels,
        subjects=subjects,
        blocks=blocks,
        source_rows=np.arange(len(labels), dtype=np.int64),
    )


def grouped_block_folds(blocks: np.ndarray, folds: int) -> list[np.ndarray]:
    unique = np.unique(blocks)
    if folds < 2 or len(unique) % folds:
        raise ValueError(f"{len(unique)} blocks cannot be evenly divided into {folds} folds")
    # Interleaving prevents any chronological cluster from becoming one fold.
    return [unique[fold::folds] for fold in range(folds)]


def stratified_trial_folds(labels: np.ndarray, folds: int, seed: int) -> list[np.ndarray]:
    """Return sample-level stratified folds with every row tested exactly once."""

    labels = np.asarray(labels)
    if folds < 2:
        raise ValueError("folds must be at least two")
    rng = np.random.default_rng(seed)
    parts: list[list[np.ndarray]] = [[] for _ in range(folds)]
    for label in np.unique(labels):
        indices = np.flatnonzero(labels == label)
        if len(indices) % folds:
            raise ValueError(
                f"class {label} with {len(indices)} trials cannot be evenly divided into {folds} folds"
            )
        shuffled = rng.permutation(indices)
        for fold, values in enumerate(np.split(shuffled, folds)):
            parts[fold].append(values)
    return [np.sort(np.concatenate(values)) for values in parts]


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except TypeError:
        torch.use_deterministic_algorithms(True)


def _device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    if device.type == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS was requested but is unavailable")
    return device


def _train_fold(
    train_x: np.ndarray,
    train_y: np.ndarray,
    test_x: np.ndarray,
    *,
    config: ReproductionConfig,
    seed: int,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, list[float], int]:
    _seed_everything(seed)
    model = BrainWaveDecoder(
        channels=train_x.shape[1],
        samples=train_x.shape[2],
        classes=12,
        input_scale=config.input_scale,
        normalization=config.normalization,
        temporal_kernel_size=config.temporal_kernel_size,
        gru_layout=config.gru_layout,
    ).to(device)
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
    for _ in range(config.epochs):
        model.train()
        total_loss = 0.0
        total_count = 0
        for values, targets in loader:
            values = values.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(values)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            count = int(len(targets))
            total_loss += float(loss.detach().cpu()) * count
            total_count += count
        losses.append(total_loss / total_count)

    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(test_x).to(device))
        probabilities = torch.softmax(logits, dim=1).cpu().numpy()
    predictions = np.argmax(probabilities, axis=1).astype(np.int64)
    return predictions, probabilities, losses, parameter_count


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _architecture_summary(config: ReproductionConfig) -> dict[str, object]:
    profile = BrainWaveDecoderProfile().to_dict()
    profile["normalization"] = config.normalization
    profile["temporal_kernel_size"] = config.temporal_kernel_size
    profile["gru_layout"] = config.gru_layout
    return profile


def _load_completed(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"invalid resume file {path}")
    return payload


def run_experiment(
    data: PreparedData,
    *,
    source_path: Path,
    output_dir: Path,
    config: ReproductionConfig,
    device: torch.device,
    max_folds: int | None,
    resume: bool,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fold_path = output_dir / "fold_results.json"
    results = _load_completed(fold_path) if resume else []
    completed = {(int(row["subject"]), int(row["fold"])) for row in results}
    if config.split_mode not in {"grouped_blocks", "stratified_trials"}:
        raise ValueError(f"unknown split mode {config.split_mode!r}")
    fold_groups = (
        grouped_block_folds(data.blocks[data.subjects == data.subjects[0]], config.folds)
        if config.split_mode == "grouped_blocks"
        else None
    )
    selected_subjects = np.unique(data.subjects)
    planned = [(int(subject), fold + 1) for subject in selected_subjects for fold in range(config.folds)]
    if max_folds is not None:
        planned = planned[:max_folds]

    started = time.monotonic()
    for run_number, (subject, fold_number) in enumerate(planned, start=1):
        if (subject, fold_number) in completed:
            print(f"[{run_number}/{len(planned)}] subject={subject} fold={fold_number}: resumed", flush=True)
            continue
        subject_mask = data.subjects == subject
        if config.split_mode == "grouped_blocks":
            assert fold_groups is not None
            test_blocks = fold_groups[fold_number - 1]
            test_mask = subject_mask & np.isin(data.blocks, test_blocks)
        else:
            subject_indices = np.flatnonzero(subject_mask)
            trial_folds = stratified_trial_folds(
                data.labels[subject_mask], config.folds, config.seed + subject
            )
            test_indices = subject_indices[trial_folds[fold_number - 1]]
            test_mask = np.zeros_like(subject_mask, dtype=bool)
            test_mask[test_indices] = True
            test_blocks = np.unique(data.blocks[test_mask])
        train_mask = subject_mask & ~test_mask
        if np.any(train_mask & test_mask):
            raise AssertionError("train/test overlap")
        if set(data.source_rows[train_mask]) & set(data.source_rows[test_mask]):
            raise AssertionError("source-row leakage")
        fold_seed = config.seed + subject * 100 + fold_number
        predictions, probabilities, losses, parameter_count = _train_fold(
            data.eeg[train_mask],
            data.labels[train_mask],
            data.eeg[test_mask],
            config=config,
            seed=fold_seed,
            device=device,
        )
        targets = data.labels[test_mask]
        accuracy = float(np.mean(predictions == targets))
        rows = []
        test_indices = np.flatnonzero(test_mask)
        for local_index, data_index in enumerate(test_indices):
            rows.append(
                {
                    "source_row": int(data.source_rows[data_index]),
                    "subject": subject,
                    "block": int(data.blocks[data_index]),
                    "true_label": int(targets[local_index]),
                    "true_frequency_hz": FREQUENCIES_HZ[int(targets[local_index])],
                    "predicted_label": int(predictions[local_index]),
                    "predicted_frequency_hz": FREQUENCIES_HZ[int(predictions[local_index])],
                    "probabilities": [float(value) for value in probabilities[local_index]],
                }
            )
        results.append(
            {
                "subject": subject,
                "fold": fold_number,
                "split_mode": config.split_mode,
                "train_blocks": [int(value) for value in np.unique(data.blocks[train_mask])],
                "test_blocks": [int(value) for value in test_blocks],
                "train_count": int(np.sum(train_mask)),
                "test_count": int(np.sum(test_mask)),
                "accuracy": accuracy,
                "final_train_loss": float(losses[-1]),
                "train_loss_by_epoch": losses,
                "parameter_count": parameter_count,
                "predictions": rows,
            }
        )
        results.sort(key=lambda row: (int(row["subject"]), int(row["fold"])))
        _atomic_json(fold_path, results)
        print(
            f"[{run_number}/{len(planned)}] subject={subject} fold={fold_number} "
            f"test={len(targets)} accuracy={accuracy:.4f} loss={losses[-1]:.5f}",
            flush=True,
        )

    prediction_rows = [
        {**prediction, "fold": int(result["fold"])}
        for result in results
        if (int(result["subject"]), int(result["fold"])) in set(planned)
        for prediction in result["predictions"]
    ]
    source_rows = [int(row["source_row"]) for row in prediction_rows]
    if len(source_rows) != len(set(source_rows)):
        raise AssertionError("a test sample was predicted more than once")
    accuracy = float(np.mean([
        int(row["true_label"]) == int(row["predicted_label"]) for row in prediction_rows
    ])) if prediction_rows else float("nan")
    subject_accuracy = {
        str(subject): float(np.mean([
            int(row["true_label"]) == int(row["predicted_label"])
            for row in prediction_rows if int(row["subject"]) == subject
        ]))
        for subject in sorted({int(row["subject"]) for row in prediction_rows})
    }
    mean_subject_accuracy = float(np.mean(list(subject_accuracy.values()))) if subject_accuracy else float("nan")
    complete = len(planned) == len(selected_subjects) * config.folds and len(results) == len(planned)
    if complete:
        expected_rows = len(selected_subjects) * 12 * 15
        if len(prediction_rows) != expected_rows or set(source_rows) != set(data.source_rows.tolist()):
            raise AssertionError("complete run did not predict every trial exactly once")
        label_counts = np.bincount(
            np.asarray([int(row["true_label"]) for row in prediction_rows]), minlength=12
        )
        expected_per_label = len(selected_subjects) * 15
        if not np.all(label_counts == expected_per_label):
            raise AssertionError(f"unexpected per-class test counts {label_counts}")

    csv_path = output_dir / "out_of_fold_predictions.csv"
    fields = (
        "source_row", "subject", "fold", "block", "true_label", "true_frequency_hz",
        "predicted_label", "predicted_frequency_hz", "correct", "max_probability",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(prediction_rows, key=lambda item: int(item["source_row"])):
            writer.writerow(
                {
                    **{key: row[key] for key in fields if key not in {"correct", "max_probability"}},
                    "correct": int(row["true_label"]) == int(row["predicted_label"]),
                    "max_probability": max(float(value) for value in row["probabilities"]),
                }
            )

    assumptions = [
        *BrainWaveDecoderProfile().reproduction_assumptions,
        "Bi-GRU weight sharing, temporal kernel size, Adam, learning rate, batch size, epoch count, input unit scale, normalization, and diagnostic input transform",
        (
            "five subject-dependent block-grouped folds (12 blocks train, 3 test)"
            if config.split_mode == "grouped_blocks"
            else "five subject-dependent sample-level stratified folds (12 trials per class train, 3 test)"
        ),
        "crop start after stimulus onset",
    ]
    summary = {
        "status": "complete" if complete else "pilot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper": PAPER_URL,
        "dataset": DATASET_URL,
        "dataset_source_file": str(source_path.resolve()),
        "dataset_source_sha256": _source_sha256(
            source_path, subject_count=len(selected_subjects) if source_path.is_dir() else None
        ),
        "input_channels": int(data.eeg.shape[1]),
        "protocol_fidelity": (
            "original 8-channel source; preprocessing fidelity is described separately"
            if data.eeg.shape[1] == 8
            else "six-channel pilot; PO7 and PO8 absent from compact cache"
        ),
        "device": str(device),
        "torch_version": torch.__version__,
        "config": asdict(config),
        "architecture": _architecture_summary(config),
        "unverified_reproduction_assumptions": assumptions,
        "evaluated_fold_count": len(planned),
        "prediction_count": len(prediction_rows),
        "overall_accuracy": accuracy,
        "mean_subject_accuracy": mean_subject_accuracy,
        "subject_accuracy": subject_accuracy,
        "reported_paper_accuracy_1s": 0.992,
        "difference_from_reported_percentage_points": (accuracy - 0.992) * 100.0,
        "elapsed_seconds_this_invocation": time.monotonic() - started,
        "leakage_audit": {
            "unique_test_source_rows": len(set(source_rows)),
            "duplicate_test_source_rows": len(source_rows) - len(set(source_rows)),
            "every_available_trial_tested_once": complete,
        },
        "artifacts": {
            "fold_results": str(fold_path),
            "out_of_fold_predictions": str(csv_path),
        },
    }
    _atomic_json(output_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=PROJECT_ROOT / "data/public_ssvep/cache/nakanishi/master.zip",
    )
    parser.add_argument(
        "--h5",
        type=Path,
        help="use the existing six-channel compact H5 for a clearly labelled pilot",
    )
    parser.add_argument(
        "--raw-directory",
        type=Path,
        help="directory containing verified original s1.mat ... s10.mat files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs/brainwave_decoder_reproduction/nakanishi_12class_1s",
    )
    parser.add_argument("--window-seconds", type=float, default=1.0)
    parser.add_argument(
        "--start-after-onset",
        type=float,
        default=VISUAL_LATENCY_SECONDS,
        help="seconds after stimulus onset at which the analysis window starts",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--split-mode",
        choices=("grouped_blocks", "stratified_trials"),
        default="grouped_blocks",
        help="block-grouped validation or sample-level stratified validation",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=20251028)
    parser.add_argument("--input-scale", type=float, default=1.0e-4)
    parser.add_argument("--normalization", choices=("batch", "group", "none"), default="batch")
    parser.add_argument(
        "--input-transform",
        choices=("raw", "channel_center", "channel_zscore"),
        default="raw",
        help="raw is the paper-claim path; the other choices are diagnostic ablations",
    )
    parser.add_argument("--temporal-kernel-size", type=int, default=3)
    parser.add_argument(
        "--gru-layout",
        choices=("time_features", "feature_maps", "per_feature_map"),
        default="per_feature_map",
    )
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--max-subjects", type=int)
    parser.add_argument("--max-folds", type=int)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ReproductionConfig(
        window_seconds=args.window_seconds,
        folds=args.folds,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        input_scale=args.input_scale,
        normalization=args.normalization,
        input_transform=args.input_transform,
        temporal_kernel_size=args.temporal_kernel_size,
        start_after_onset_seconds=args.start_after_onset,
        gru_layout=args.gru_layout,
        split_mode=args.split_mode,
        preprocessing=(
            f"crop {args.start_after_onset:g}s after onset and 256-to-250 Hz resampling only"
            if args.input_transform == "raw"
            else f"crop {args.start_after_onset:g}s after onset, resampling, and label-free per-trial " + args.input_transform
        ),
        split=(
            f"subject-dependent grouped {args.folds}-fold by recording block"
            if args.split_mode == "grouped_blocks"
            else f"subject-dependent sample-level stratified {args.folds}-fold"
        ),
    )
    device = _device(args.device)
    if args.h5 and args.raw_directory:
        parser_error = "--h5 and --raw-directory are mutually exclusive"
        raise ValueError(parser_error)
    source_path = args.h5 or args.raw_directory or args.archive
    if args.h5:
        data = prepare_nakanishi_h5(
            args.h5,
            window_seconds=args.window_seconds,
            start_after_onset_seconds=args.start_after_onset,
            max_subjects=args.max_subjects,
        )
    elif args.raw_directory:
        data = prepare_nakanishi_directory(
            args.raw_directory,
            window_seconds=args.window_seconds,
            start_after_onset_seconds=args.start_after_onset,
            max_subjects=args.max_subjects,
        )
    else:
        data = prepare_nakanishi_archive(
            args.archive,
            window_seconds=args.window_seconds,
            start_after_onset_seconds=args.start_after_onset,
            max_subjects=args.max_subjects,
        )
    data = apply_input_transform(data, args.input_transform)
    print(
        f"prepared eeg={data.eeg.shape} labels={np.bincount(data.labels).tolist()} "
        f"device={device}",
        flush=True,
    )
    summary = run_experiment(
        data,
        source_path=source_path,
        output_dir=args.output,
        config=config,
        device=device,
        max_folds=args.max_folds,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
