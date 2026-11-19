#!/usr/bin/env python3
"""Build an eight-channel SSVEP graph dataset and run GCN/GCD-GCN."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import h5py
import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.preprocessing import StandardScaler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.gcd_ssvep import (  # noqa: E402
    DEFAULT_CHANNEL_NAMES,
    DEFAULT_FREQUENCIES_HZ,
    NODE_FEATURE_NAMES,
    predict_graph_classifier,
    train_graph_classifier,
    trial_to_graph,
)


DEFAULT_RUN_ID = "auto-20261017-204406-0ea0fc"
LABELS = ("空闲", "起飞", "前进", "后退", "左移", "右移", "降落")
GRAPH_TYPES = ("G_PCC", "G_MSC", "G_PLV")
SEEDS = (42, 43, 44, 45, 46)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build processed_data.h5 and evaluate GCD-GCN on historical SSVEP",
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--train-through-repetition", type=int, default=10)
    parser.add_argument("--pretraining-epochs", type=int, default=30)
    parser.add_argument("--training-epochs", type=int, default=50)
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=list(SEEDS),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "research_experiments" / "analysis" / "gcd_ssvep_8ch_105",
    )
    return parser.parse_args(argv)


def read_records(run_id: str) -> list[dict[str, str]]:
    index_path = (
        PROJECT_ROOT / "data" / "research_experiments" / "sessions" / "raw_trials_index.csv"
    )
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        records = [
            row for row in csv.DictReader(handle)
            if row["trial_id"].startswith(run_id)
        ]
    records.sort(key=lambda row: int(row["trial_index"]))
    if not records:
        raise ValueError(f"run not found: {run_id}")
    return records


def load_analysis_window(
    record: dict[str, str],
    window_seconds: float,
) -> tuple[np.ndarray, float]:
    path = PROJECT_ROOT / "data" / record["path"]
    with np.load(path, allow_pickle=False) as archive:
        eeg = np.asarray(archive["eeg"], dtype=np.float64)
        sample_rate = float(np.asarray(archive["fs"]).item())
        timestamps = np.asarray(archive["sample_timestamps"], dtype=np.float64)
        analysis_time = float(np.asarray(archive["analysis_lsl_time"]).item())
        channel_names = tuple(str(value) for value in archive["channel_names"].tolist())
    if channel_names != DEFAULT_CHANNEL_NAMES:
        raise ValueError(f"{path.name}: expected {DEFAULT_CHANNEL_NAMES}, got {channel_names}")
    start = int(np.searchsorted(timestamps, analysis_time, side="left"))
    stop = start + round(window_seconds * sample_rate)
    if stop > eeg.shape[1]:
        raise ValueError(f"{path.name}: insufficient samples after analysis marker")
    return eeg[:, start:stop], sample_rate


def build_dataset(records: list[dict[str, str]], window_seconds: float) -> dict[str, object]:
    feature_rows = []
    graph_rows = {name: [] for name in GRAPH_TYPES}
    labels = []
    repetitions = []
    sample_rate = None
    for index, record in enumerate(records, 1):
        eeg, current_rate = load_analysis_window(record, window_seconds)
        if sample_rate is None:
            sample_rate = current_rate
        elif current_rate != sample_rate:
            raise ValueError("inconsistent sample rates in one run")
        features, graphs, _ = trial_to_graph(
            eeg,
            current_rate,
            DEFAULT_FREQUENCIES_HZ,
        )
        feature_rows.append(features)
        for name in GRAPH_TYPES:
            graph_rows[name].append(graphs[name])
        labels.append(LABELS.index(record["true_label"]))
        repetitions.append(int(record["repetition"]))
        if index % 15 == 0 or index == len(records):
            print(f"graph construction: {index}/{len(records)}", flush=True)
    return {
        "features": np.asarray(feature_rows, dtype=np.float32),
        "graphs": {
            name: np.asarray(values, dtype=np.float32)
            for name, values in graph_rows.items()
        },
        "labels": np.asarray(labels, dtype=np.int64),
        "repetitions": np.asarray(repetitions, dtype=np.int64),
        "sample_rate_hz": float(sample_rate),
        "trial_ids": np.asarray([record["trial_id"] for record in records]),
    }


def write_h5(
    path: Path,
    dataset: dict[str, object],
    train_mask: np.ndarray,
    test_mask: np.ndarray,
    run_id: str,
    window_seconds: float,
) -> None:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(path, "w") as file:
        # Match upstream layout: samples x feature_dim x nodes.
        file.create_dataset(
            "node_features",
            data=np.transpose(dataset["features"], (0, 2, 1)),
            compression="gzip",
        )
        for name, values in dataset["graphs"].items():
            file.create_dataset(name, data=values, compression="gzip")
        file.create_dataset("labels", data=dataset["labels"])
        file.create_dataset("repetitions", data=dataset["repetitions"])
        file.create_dataset("train_mask", data=train_mask.astype(np.uint8))
        file.create_dataset("test_mask", data=test_mask.astype(np.uint8))
        file.create_dataset(
            "trial_ids",
            data=np.asarray(dataset["trial_ids"], dtype=object),
            dtype=string_dtype,
        )
        file.create_dataset(
            "label_names",
            data=np.asarray(LABELS, dtype=object),
            dtype=string_dtype,
        )
        file.create_dataset(
            "channel_names",
            data=np.asarray(DEFAULT_CHANNEL_NAMES, dtype=object),
            dtype=string_dtype,
        )
        file.create_dataset(
            "node_feature_names",
            data=np.asarray(NODE_FEATURE_NAMES, dtype=object),
            dtype=string_dtype,
        )
        file.attrs["run_id"] = run_id
        file.attrs["sample_rate_hz"] = dataset["sample_rate_hz"]
        file.attrs["window_seconds"] = window_seconds
        file.attrs["raw_filter"] = "5th-order zero-phase Butterworth 0.5-40 Hz"
        file.attrs["source_paper"] = "Liu et al. (2025), GCD"


def standardize_features(
    features: np.ndarray,
    train_mask: np.ndarray,
) -> tuple[np.ndarray, StandardScaler]:
    scaler = StandardScaler()
    train = features[train_mask]
    scaler.fit(train.reshape(-1, train.shape[-1]))
    shape = features.shape
    standardized = scaler.transform(features.reshape(-1, shape[-1])).reshape(shape)
    return standardized.astype(np.float32), scaler


def metrics(labels: np.ndarray, predictions: np.ndarray) -> dict[str, object]:
    matrix = confusion_matrix(labels, predictions, labels=np.arange(len(LABELS)))
    active = labels != 0
    idle = labels == 0
    return {
        "n": int(len(labels)),
        "correct": int(np.sum(labels == predictions)),
        "accuracy": float(accuracy_score(labels, predictions)),
        "macro_f1": float(
            f1_score(labels, predictions, labels=np.arange(len(LABELS)), average="macro")
        ),
        "active_six_class_accuracy": float(
            accuracy_score(labels[active], predictions[active])
        ),
        "idle_false_activation_rate": float(np.mean(predictions[idle] != 0)),
        "prediction_counts": {
            LABELS[index]: int(count)
            for index, count in Counter(predictions.tolist()).items()
        },
        "confusion_matrix_labels": list(LABELS),
        "confusion_matrix": matrix.tolist(),
    }


def aggregate(seed_reports: list[dict[str, object]]) -> dict[str, object]:
    keys = ("accuracy", "macro_f1", "active_six_class_accuracy", "idle_false_activation_rate")
    result = {"seeds": seed_reports}
    for key in keys:
        values = np.asarray([report[key] for report in seed_reports], dtype=float)
        result[f"{key}_mean"] = float(values.mean())
        result[f"{key}_std"] = float(values.std())
    return result


def write_predictions(
    path: Path,
    records: list[dict[str, str]],
    test_indices: np.ndarray,
    rows: list[dict[str, object]],
) -> None:
    fields = [
        "trial_id",
        "repetition",
        "true_label",
        "graph_type",
        "model",
        "seed",
        "prediction",
        "correct",
    ] + [f"probability_{label}" for label in LABELS]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in rows:
            for position, sample_index in enumerate(test_indices):
                record = records[int(sample_index)]
                prediction = int(result["predictions"][position])
                output = {
                    "trial_id": record["trial_id"],
                    "repetition": record["repetition"],
                    "true_label": record["true_label"],
                    "graph_type": result["graph_type"],
                    "model": result["model"],
                    "seed": result["seed"],
                    "prediction": LABELS[prediction],
                    "correct": int(prediction == LABELS.index(record["true_label"])),
                }
                for label_index, label in enumerate(LABELS):
                    output[f"probability_{label}"] = float(
                        result["probabilities"][position, label_index]
                    )
                writer.writerow(output)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records = read_records(args.run_id)
    dataset = build_dataset(records, args.window_seconds)
    repetitions = dataset["repetitions"]
    train_mask = repetitions <= args.train_through_repetition
    test_mask = repetitions > args.train_through_repetition
    if not np.any(train_mask) or not np.any(test_mask):
        raise ValueError("both train and test repetitions are required")
    train_counts = Counter(dataset["labels"][train_mask].tolist())
    test_counts = Counter(dataset["labels"][test_mask].tolist())
    if set(train_counts) != set(range(len(LABELS))) or set(test_counts) != set(range(len(LABELS))):
        raise ValueError("every class must appear in both train and test")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    h5_path = args.output_dir / "processed_data_ssvep.h5"
    write_h5(
        h5_path,
        dataset,
        train_mask,
        test_mask,
        args.run_id,
        args.window_seconds,
    )
    features, scaler = standardize_features(dataset["features"], train_mask)
    labels = dataset["labels"]
    test_indices = np.flatnonzero(test_mask)
    report: dict[str, object] = {
        "implementation": {
            "mode": "SSVEP adaptation of the public GCD repository",
            "repository": "https://github.com/Quentin2022/Graph-Contrastive-Denoising-Module",
            "pretraining": "repository Gaussian kernel and labelled InfoNCE masking",
            "gcn": "two graph-convolution layers, each with an independently fine-tuned copy of the pretrained graph embedding",
            "task_specific_step": "raw EEG to SSVEP node features and PCC/MSC/PLV graphs; this step is absent from the public repository",
        },
        "scope": {
            "run_id": args.run_id,
            "trial_count": len(records),
            "train_repetitions": f"1-{args.train_through_repetition}",
            "test_repetitions": (
                f"{args.train_through_repetition + 1}-{int(repetitions.max())}"
            ),
            "train_count": int(train_mask.sum()),
            "test_count": int(test_mask.sum()),
            "labels": list(LABELS),
            "channels": list(DEFAULT_CHANNEL_NAMES),
            "frequencies_hz": list(DEFAULT_FREQUENCIES_HZ),
            "sample_rate_hz": dataset["sample_rate_hz"],
            "window_seconds": args.window_seconds,
        },
        "preprocessing": {
            "raw_filter": "5th-order zero-phase Butterworth 0.5-40 Hz",
            "node_feature_names": list(NODE_FEATURE_NAMES),
            "node_feature_shape": list(dataset["features"].shape),
            "graph_shapes": {
                name: list(values.shape)
                for name, values in dataset["graphs"].items()
            },
            "standard_scaler_fit_scope": "training repetitions only",
            "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(),
        },
        "training": {
            "seeds": list(args.seeds),
            "pretraining_epochs": args.pretraining_epochs,
            "fine_tuning_epochs": args.training_epochs,
            "split_policy": "fixed repetition-disjoint holdout",
            "test_used_for_model_selection": False,
        },
        "results": {},
    }
    prediction_rows = []
    best = None
    for graph_type in GRAPH_TYPES:
        report["results"][graph_type] = {}
        graph_values = dataset["graphs"][graph_type]
        for use_gcd, model_name in ((False, "GCN"), (True, "GCD+GCN")):
            seed_reports = []
            for seed in args.seeds:
                trained = train_graph_classifier(
                    features[train_mask],
                    graph_values[train_mask],
                    labels[train_mask],
                    num_classes=len(LABELS),
                    use_gcd=use_gcd,
                    seed=int(seed),
                    pretraining_epochs=args.pretraining_epochs,
                    training_epochs=args.training_epochs,
                )
                predictions, probabilities = predict_graph_classifier(
                    trained.model,
                    features[test_mask],
                    graph_values[test_mask],
                )
                train_predictions, _ = predict_graph_classifier(
                    trained.model,
                    features[train_mask],
                    graph_values[train_mask],
                )
                seed_metrics = metrics(labels[test_mask], predictions)
                train_metrics = metrics(labels[train_mask], train_predictions)
                seed_metrics.update(
                    {
                        "seed": int(seed),
                        "train_accuracy": train_metrics["accuracy"],
                        "train_macro_f1": train_metrics["macro_f1"],
                        "final_training_loss": trained.losses[-1],
                        "final_pretraining_loss": (
                            trained.pretraining_losses[-1]
                            if trained.pretraining_losses
                            else None
                        ),
                        "initial_pretraining_loss": (
                            trained.pretraining_losses[0]
                            if trained.pretraining_losses
                            else None
                        ),
                        "pretraining_loss_change": (
                            trained.pretraining_losses[-1]
                            - trained.pretraining_losses[0]
                            if trained.pretraining_losses
                            else None
                        ),
                    }
                )
                seed_reports.append(seed_metrics)
                prediction_rows.append(
                    {
                        "graph_type": graph_type,
                        "model": model_name,
                        "seed": int(seed),
                        "predictions": predictions,
                        "probabilities": probabilities,
                    }
                )
                print(
                    f"{graph_type} {model_name} seed={seed}: "
                    f"accuracy={seed_metrics['accuracy']:.4f} "
                    f"macro_f1={seed_metrics['macro_f1']:.4f}",
                    flush=True,
                )
                candidate = {
                    "graph_type": graph_type,
                    "model": model_name,
                    **seed_metrics,
                }
                if best is None or candidate["accuracy"] > best["accuracy"]:
                    best = candidate
            report["results"][graph_type][model_name] = aggregate(seed_reports)

    report["best_single_seed_test_result"] = best
    for graph_type in GRAPH_TYPES:
        baseline = report["results"][graph_type]["GCN"]["accuracy_mean"]
        gcd = report["results"][graph_type]["GCD+GCN"]["accuracy_mean"]
        report["results"][graph_type]["gcd_accuracy_change_percentage_points"] = (
            100.0 * (gcd - baseline)
        )

    report_path = args.output_dir / "report.json"
    predictions_path = args.output_dir / "predictions.csv"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_predictions(predictions_path, records, test_indices, prediction_rows)
    print(f"processed_data={h5_path}")
    print(f"report={report_path}")
    print(f"predictions={predictions_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
