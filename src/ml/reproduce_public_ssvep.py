"""Reproduce the eight-frequency public-data Transformer/FBCCA experiment.

This command never downloads a dataset.  It operates in four explicit stages
so the expensive leave-one-subject-out (LOSO) folds can be run independently::

    python -m src.ml.reproduce_public_ssvep inspect --benchmark-root /data/Benchmark
    python -m src.ml.reproduce_public_ssvep prepare --benchmark-root /data/Benchmark \
        --beta-root /data/BETA --output-dir artifacts/public_ssvep
    python -m src.ml.reproduce_public_ssvep run-fold \
        --prepared-dir artifacts/public_ssvep --fold-index 0
    python -m src.ml.reproduce_public_ssvep aggregate \
        --prepared-dir artifacts/public_ssvep
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from pathlib import Path
from typing import Sequence

import numpy as np

from src.ml.public_ssvep_data import (
    TARGET_CHANNEL_NAMES,
    TARGET_FREQUENCIES_HZ,
    PreparedPublicData,
    discover_subject_files,
    load_prepared_public_data,
    load_public_subject,
    prepare_public_data,
)


def _torch_dependencies():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise RuntimeError(
            "运行Transformer折需要PyTorch；请先执行 python -m pip install -r requirements-ml.txt"
        ) from exc
    return torch, nn, DataLoader, TensorDataset


def _roots(args) -> list[tuple[str, Path]]:
    roots = []
    if getattr(args, "benchmark_root", None):
        roots.append(("benchmark", args.benchmark_root))
    if getattr(args, "beta_root", None):
        roots.append(("beta", args.beta_root))
    if not roots:
        raise ValueError("至少提供 --benchmark-root 或 --beta-root；本脚本不会自动下载数据")
    return roots


def inspect(args) -> dict[str, object]:
    """Validate layouts and print a compact inventory without writing a cache."""

    inventory: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for dataset, root_value in _roots(args):
        root = Path(root_value).expanduser().resolve()
        for path in discover_subject_files(root):
            try:
                subject = load_public_subject(path, dataset, root)
                inventory.append({
                    "dataset": dataset,
                    "subject_id": subject.subject_id,
                    "source_file": str(path),
                    "shape": list(subject.eeg.shape),
                    "sample_rate_hz": subject.sample_rate_hz,
                    "blocks": int(subject.eeg.shape[3]),
                    "frequency_source": subject.frequency_source,
                    "channel_source": subject.channel_source,
                })
            except Exception as exc:
                errors.append({"dataset": dataset, "source_file": str(path), "error": str(exc)})
    report = {
        "subject_count": len(inventory),
        "datasets": sorted({row["dataset"] for row in inventory}),
        "subjects": inventory,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors and not args.allow_errors:
        raise ValueError(f"发现 {len(errors)} 个无法解析的被试；修复后再prepare，或inspect使用 --allow-errors")
    return report


def prepare(args) -> PreparedPublicData:
    prepared = prepare_public_data(
        _roots(args), args.output_dir, skip_invalid_subjects=args.skip_invalid_subjects
    )
    print(
        f"准备完成：{prepared.manifest['subject_count']}名被试，"
        f"{prepared.manifest['trial_count']}个8频试次\n"
        f"manifest：{Path(args.output_dir).resolve() / 'manifest.json'}"
    )
    return prepared


def loso_subjects(data: PreparedPublicData) -> list[str]:
    return sorted({str(value) for value in data.subjects.tolist()})


def resolve_held_out_subject(
    data: PreparedPublicData,
    held_out_subject: str | None,
    fold_index: int | None,
) -> tuple[str, int]:
    subjects = loso_subjects(data)
    if not subjects:
        raise ValueError("prepared cache中没有被试")
    if held_out_subject:
        matches = [subject for subject in subjects if subject == held_out_subject]
        if not matches and re.fullmatch(r"S0*\d+", held_out_subject, flags=re.IGNORECASE):
            suffix = f":S{int(held_out_subject[1:]):02d}"
            matches = [subject for subject in subjects if subject.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(
                f"--held-out-subject 必须唯一匹配，实际匹配 {matches}；"
                "合并数据时请写 benchmark:S01 或 beta:S01"
            )
        return matches[0], subjects.index(matches[0])
    index = 0 if fold_index is None else int(fold_index)
    if index < 0 or index >= len(subjects):
        raise ValueError(f"fold-index范围为 0..{len(subjects) - 1}，实际为 {index}")
    return subjects[index], index


def loso_split(
    data: PreparedPublicData,
    held_out_subject: str,
    validation_fraction: float,
    seed: int,
) -> dict[str, object]:
    """Build a test-subject fold plus a subject-disjoint validation subset."""

    subjects = loso_subjects(data)
    remaining = [subject for subject in subjects if subject != held_out_subject]
    if len(remaining) < 2:
        raise ValueError("Transformer LOSO至少需要3名被试：训练、验证、测试各自独立")
    if not 0.0 < validation_fraction < 0.5:
        raise ValueError("validation-fraction必须在(0, 0.5)范围内")
    validation_count = max(1, int(round(len(remaining) * validation_fraction)))
    validation_count = min(validation_count, len(remaining) - 1)
    rng = random.Random(int(seed) + subjects.index(held_out_subject) * 1009)
    shuffled = remaining[:]
    rng.shuffle(shuffled)
    validation_subjects = sorted(shuffled[:validation_count])
    training_subjects = sorted(shuffled[validation_count:])

    subject_values = data.subjects.astype(str)
    train_indices = np.flatnonzero(np.isin(subject_values, training_subjects))
    validation_indices = np.flatnonzero(np.isin(subject_values, validation_subjects))
    test_indices = np.flatnonzero(subject_values == held_out_subject)
    expected_classes = set(range(len(TARGET_FREQUENCIES_HZ)))
    for name, indices in (
        ("train", train_indices), ("validation", validation_indices), ("test", test_indices)
    ):
        if set(data.targets[indices].tolist()) != expected_classes:
            raise ValueError(f"{name}划分未覆盖全部8个频率")
    return {
        "strategy": "leave-one-subject-out-test-with-subject-held-out-validation",
        "held_out_subject": held_out_subject,
        "training_subjects": training_subjects,
        "validation_subjects": validation_subjects,
        "train_indices": train_indices,
        "validation_indices": validation_indices,
        "test_indices": test_indices,
    }


def _classification_metrics(truth, prediction) -> dict[str, object]:
    truth_array = np.asarray(truth, dtype=np.int64)
    prediction_array = np.asarray(prediction, dtype=np.int64)
    class_count = len(TARGET_FREQUENCIES_HZ)
    if truth_array.shape != prediction_array.shape or truth_array.ndim != 1:
        raise ValueError("分类真值和预测必须是一维等长数组")
    if not len(truth_array):
        raise ValueError("分类评估至少需要一个试次")
    confusion = np.zeros((class_count, class_count), dtype=np.int64)
    np.add.at(confusion, (truth_array, prediction_array), 1)
    true_positive = np.diag(confusion).astype(float)
    false_positive = confusion.sum(axis=0) - true_positive
    false_negative = confusion.sum(axis=1) - true_positive
    denominator = 2.0 * true_positive + false_positive + false_negative
    per_class_f1 = np.divide(
        2.0 * true_positive,
        denominator,
        out=np.zeros(class_count, dtype=float),
        where=denominator > 0,
    )
    return {
        "trial_count": int(len(truth_array)),
        "accuracy": float(np.mean(truth_array == prediction_array)),
        "macro_f1": float(per_class_f1.mean()),
        "confusion_matrix": confusion.tolist(),
    }


def _fbcca_predictions(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from src.bci.fbcca_decoder import FBCCADecoder

    labels = tuple(f"{frequency:g}Hz" for frequency in TARGET_FREQUENCIES_HZ)
    decoder = FBCCADecoder(
        frequencies_hz=TARGET_FREQUENCIES_HZ,
        labels=labels,
        min_score=0.0,
        min_margin=-1.0,
    )
    predictions, confidences = [], []
    label_to_index = {label: index for index, label in enumerate(labels)}
    for trial in values:
        result = decoder.decode(trial, 250.0)
        predictions.append(label_to_index[result.label])
        scores = np.asarray([result.scores[label] for label in labels], dtype=float)
        # FBCCA scores are not probabilities.  This normalized ratio is stored
        # only as a diagnostic and is never called calibrated confidence.
        denominator = float(scores.sum())
        confidences.append(float(scores.max() / denominator) if denominator > 0 else 0.0)
    return np.asarray(predictions, dtype=np.int64), np.asarray(confidences, dtype=float)


def _select_device(torch, requested: str):
    if requested:
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _evaluate_transformer(model, loader, loss_fn, device, torch):
    model.eval()
    losses, truth, prediction, confidence = [], [], [], []
    with torch.no_grad():
        for values, target in loader:
            logits = model(values.to(device))
            losses.append(float(loss_fn(logits, target.to(device)).item()))
            probabilities = torch.softmax(logits, dim=1)
            batch_confidence, batch_prediction = probabilities.max(dim=1)
            truth.extend(target.tolist())
            prediction.extend(batch_prediction.cpu().tolist())
            confidence.extend(batch_confidence.cpu().tolist())
    return float(np.mean(losses)), truth, prediction, confidence


def _train_transformer_fold(data, split, args, fold_dir: Path) -> tuple[dict, list[int], list[float]]:
    torch, nn, DataLoader, TensorDataset = _torch_dependencies()
    from src.ml.transformer_ssvep import TransformerSSVEP

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = _select_device(torch, args.device)
    train_indices = split["train_indices"]
    validation_indices = split["validation_indices"]
    test_indices = split["test_indices"]

    def loader(indices, shuffle=False):
        return DataLoader(
            TensorDataset(
                torch.from_numpy(data.values[indices]),
                torch.from_numpy(data.targets[indices]),
            ),
            batch_size=args.batch_size,
            shuffle=shuffle,
        )

    train_loader = loader(train_indices, True)
    validation_loader = loader(validation_indices)
    test_loader = loader(test_indices)
    model_args = {
        "channels": len(TARGET_CHANNEL_NAMES),
        "sample_count": int(round(250.0 * 2.0)),
        "patch_count": 4,
        "classes": len(TARGET_FREQUENCIES_HZ),
        "embedding_dim": 128,
        "encoder_layers": 2,
        "attention_heads": 8,
        "feedforward_dim": 256,
        "classifier_hidden_dim": 64,
        "dropout": 0.1,
    }
    model = TransformerSSVEP(**model_args).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay
    )
    loss_fn = nn.CrossEntropyLoss()
    best_loss, best_state, patience = float("inf"), None, 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_losses = []
        for values, target in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(values.to(device))
            loss = loss_fn(logits, target.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.item()))
        validation_loss, truth, prediction, _ = _evaluate_transformer(
            model, validation_loader, loss_fn, device, torch
        )
        validation_accuracy = float(np.mean(np.asarray(truth) == np.asarray(prediction)))
        history.append({
            "epoch": epoch,
            "train_loss": float(np.mean(train_losses)),
            "validation_loss": validation_loss,
            "validation_accuracy": validation_accuracy,
        })
        print(
            f"epoch={epoch:03d} train={np.mean(train_losses):.4f} "
            f"val={validation_loss:.4f} acc={validation_accuracy:.3f}"
        )
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_state = {
                name: parameter.detach().cpu().clone()
                for name, parameter in model.state_dict().items()
            }
            patience = 0
        else:
            patience += 1
            if patience >= args.early_stopping:
                break
    if best_state is None:
        raise RuntimeError("Transformer训练没有产生有效权重")
    model.load_state_dict(best_state)
    model.to(device)
    test_loss, truth, prediction, confidence = _evaluate_transformer(
        model, test_loader, loss_fn, device, torch
    )
    metrics = _classification_metrics(truth, prediction)
    metrics.update({
        "test_loss": test_loss,
        "best_validation_loss": best_loss,
        "epochs_completed": len(history),
        "history": history,
        "model_args": model_args,
        "optimizer": "Adam",
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "device": str(device),
        "torch_version": str(torch.__version__),
        "numpy_version": str(np.__version__),
    })
    checkpoint = {
        "state_dict": best_state,
        "model_args": model_args,
        "labels_hz": list(TARGET_FREQUENCIES_HZ),
        "channel_names": list(TARGET_CHANNEL_NAMES),
        "sample_rate_hz": 250.0,
        "window_seconds": 2.0,
        "runtime_compatible": False,
        "held_out_subject": split["held_out_subject"],
        "training_subjects": split["training_subjects"],
        "validation_subjects": split["validation_subjects"],
        "random_seed": int(args.seed),
    }
    torch.save(checkpoint, fold_dir / "transformer_weights.pt")
    return metrics, prediction, confidence


def _prediction_rows(data, test_indices, method, predictions, confidences):
    rows = []
    for array_index, prediction, confidence in zip(test_indices, predictions, confidences):
        base = data.trial_rows[int(array_index)]
        rows.append({
            "method": method,
            "row": int(array_index),
            "dataset": base["dataset"],
            "subject_id": base["subject_id"],
            "block_index": int(base["block_index"]),
            "true_index": int(data.targets[array_index]),
            "true_frequency_hz": float(TARGET_FREQUENCIES_HZ[data.targets[array_index]]),
            "predicted_index": int(prediction),
            "predicted_frequency_hz": float(TARGET_FREQUENCIES_HZ[int(prediction)]),
            "score": float(confidence),
        })
    return rows


def run_fold(args) -> dict[str, object]:
    data = load_prepared_public_data(args.prepared_dir)
    held_out, fold_index = resolve_held_out_subject(
        data, args.held_out_subject, args.fold_index
    )
    safe_name = held_out.replace(":", "_")
    fold_dir = Path(args.prepared_dir).expanduser().resolve() / "folds" / safe_name
    fold_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = fold_dir / "metrics.json"
    requested_methods = tuple(dict.fromkeys(args.methods))
    existing = None
    if metrics_path.is_file() and not args.force:
        existing = json.loads(metrics_path.read_text(encoding="utf-8"))
        if set(requested_methods).issubset(existing.get("methods", {})):
            print(f"折已完成，跳过：{held_out}（使用 --force 可重跑）")
            return existing

    split = loso_split(data, held_out, args.validation_fraction, args.seed)
    if existing:
        old_split = existing.get("split", {})
        if (
            old_split.get("training_subjects") != split["training_subjects"]
            or old_split.get("validation_subjects") != split["validation_subjects"]
        ):
            raise ValueError(
                "现有折使用了不同的训练/验证被试；请沿用原seed与validation-fraction，"
                "或显式使用 --force 重跑整折"
            )
    test_indices = split["test_indices"]
    methods: dict[str, object] = dict(existing.get("methods", {})) if existing else {}
    prediction_rows = []
    predictions_path = fold_dir / "predictions.csv"
    if existing:
        if not predictions_path.is_file():
            raise ValueError(f"现有metrics缺少配套预测文件：{predictions_path}")
        with predictions_path.open("r", newline="", encoding="utf-8") as handle:
            prediction_rows.extend(csv.DictReader(handle))
    methods_to_run = [method for method in requested_methods if method not in methods]
    if "fbcca" in methods_to_run:
        prediction, confidence = _fbcca_predictions(data.values[test_indices])
        methods["fbcca"] = _classification_metrics(data.targets[test_indices], prediction)
        prediction_rows.extend(
            _prediction_rows(data, test_indices, "fbcca", prediction, confidence)
        )
    if "transformer" in methods_to_run:
        transformer_metrics, prediction, confidence = _train_transformer_fold(
            data, split, args, fold_dir
        )
        methods["transformer"] = transformer_metrics
        prediction_rows.extend(
            _prediction_rows(data, test_indices, "transformer", prediction, confidence)
        )

    report = {
        "schema_version": 1,
        "status": "complete",
        "fold_index": fold_index,
        "held_out_subject": held_out,
        "labels_hz": list(TARGET_FREQUENCIES_HZ),
        "has_idle_class": False,
        "runtime_compatible": False,
        "settings": {
            "random_seed": int(args.seed),
            "validation_fraction": float(args.validation_fraction),
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "weight_decay": float(args.weight_decay),
            "early_stopping": int(args.early_stopping),
        },
        "split": {
            "strategy": split["strategy"],
            "training_subjects": split["training_subjects"],
            "validation_subjects": split["validation_subjects"],
            "test_subject": held_out,
            "training_trials": int(len(split["train_indices"])),
            "validation_trials": int(len(split["validation_indices"])),
            "test_trials": int(len(split["test_indices"])),
        },
        "methods": methods,
    }
    metrics_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with predictions_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(prediction_rows[0]))
        writer.writeheader()
        writer.writerows(prediction_rows)
    print(f"折完成：{held_out}\nmetrics：{metrics_path}")
    return report


def aggregate(args) -> dict[str, object]:
    data = load_prepared_public_data(args.prepared_dir)
    expected_subjects = loso_subjects(data)
    fold_root = Path(args.prepared_dir).expanduser().resolve() / "folds"
    reports = []
    for path in sorted(fold_root.glob("*/metrics.json")) if fold_root.is_dir() else []:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("status") == "complete":
            reports.append(report)
    completed_subjects = sorted({str(report["held_out_subject"]) for report in reports})
    expected_methods = tuple(dict.fromkeys(
        getattr(args, "methods", ("fbcca", "transformer"))
    ))
    methods = list(expected_methods)
    aggregated_methods: dict[str, object] = {}
    fold_rows = []
    for method in methods:
        method_reports = [
            (report, report["methods"][method])
            for report in reports if method in report.get("methods", {})
        ]
        if not method_reports:
            aggregated_methods[method] = {
                "completed_fold_count": 0,
                "completed_subjects": [],
                "missing_subjects": list(expected_subjects),
                "complete": False,
                "trial_count": 0,
                "subject_macro_accuracy_mean": None,
                "subject_macro_accuracy_std": None,
                "trial_weighted_accuracy": None,
                "subject_macro_f1_mean": None,
                "confusion_matrix": np.zeros(
                    (len(TARGET_FREQUENCIES_HZ), len(TARGET_FREQUENCIES_HZ)),
                    dtype=np.int64,
                ).tolist(),
            }
            continue
        counts = np.asarray([metrics["trial_count"] for _, metrics in method_reports], dtype=float)
        accuracies = np.asarray([metrics["accuracy"] for _, metrics in method_reports], dtype=float)
        macro_f1 = np.asarray([metrics["macro_f1"] for _, metrics in method_reports], dtype=float)
        confusion = np.sum(
            [np.asarray(metrics["confusion_matrix"], dtype=np.int64)
             for _, metrics in method_reports], axis=0,
        )
        aggregated_methods[method] = {
            "completed_fold_count": len(method_reports),
            "completed_subjects": sorted(report["held_out_subject"] for report, _ in method_reports),
            "missing_subjects": sorted(
                set(expected_subjects)
                - {str(report["held_out_subject"]) for report, _ in method_reports}
            ),
            "complete": (
                {str(report["held_out_subject"]) for report, _ in method_reports}
                == set(expected_subjects)
            ),
            "trial_count": int(counts.sum()),
            "subject_macro_accuracy_mean": float(accuracies.mean()),
            "subject_macro_accuracy_std": float(accuracies.std(ddof=1)) if len(accuracies) > 1 else 0.0,
            "trial_weighted_accuracy": float(np.average(accuracies, weights=counts)),
            "subject_macro_f1_mean": float(macro_f1.mean()),
            "confusion_matrix": confusion.tolist(),
        }
        for report, metrics in method_reports:
            fold_rows.append({
                "fold_index": report["fold_index"],
                "held_out_subject": report["held_out_subject"],
                "method": method,
                "trial_count": metrics["trial_count"],
                "accuracy": metrics["accuracy"],
                "macro_f1": metrics["macro_f1"],
            })
    result = {
        "schema_version": 1,
        "protocol": "LOSO test; subject-disjoint validation",
        "expected_fold_count": len(expected_subjects),
        "completed_subjects": completed_subjects,
        "missing_subjects": sorted(set(expected_subjects) - set(completed_subjects)),
        "complete": bool(aggregated_methods) and all(
            bool(metrics["complete"]) for metrics in aggregated_methods.values()
        ),
        "methods": aggregated_methods,
    }
    output = Path(args.prepared_dir).expanduser().resolve()
    (output / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if fold_rows:
        with (output / "fold_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fold_rows[0]))
            writer.writeheader()
            writer.writerows(sorted(fold_rows, key=lambda row: (row["fold_index"], row["method"])))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark-root", type=Path, help="已下载的Wang2016 Benchmark目录")
    parser.add_argument("--beta-root", type=Path, help="已下载的Liu2020 BETA目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tsinghua Benchmark/BETA八频Transformer-SSVEP公开数据复现（不下载数据）"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect", help="检查MAT结构和元数据，不写缓存")
    _add_roots(inspect_parser)
    inspect_parser.add_argument("--allow-errors", action="store_true")
    inspect_parser.set_defaults(handler=inspect)

    prepare_parser = commands.add_parser("prepare", help="提取8通道/8频/2秒窗口并写manifest")
    _add_roots(prepare_parser)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--skip-invalid-subjects", action="store_true")
    prepare_parser.set_defaults(handler=prepare)

    fold_parser = commands.add_parser("run-fold", help="独立运行一个LOSO测试被试折")
    fold_parser.add_argument("--prepared-dir", type=Path, required=True)
    selection = fold_parser.add_mutually_exclusive_group()
    selection.add_argument("--held-out-subject", help="如 benchmark:S01；单数据集可写S01")
    selection.add_argument("--fold-index", type=int, help="按manifest被试排序的0基折号")
    fold_parser.add_argument(
        "--methods", nargs="+", choices=("fbcca", "transformer"),
        default=("fbcca", "transformer"),
    )
    fold_parser.add_argument("--validation-fraction", type=float, default=0.10)
    fold_parser.add_argument("--epochs", type=int, default=100)
    fold_parser.add_argument("--batch-size", type=int, default=64)
    fold_parser.add_argument("--learning-rate", type=float, default=1e-3)
    fold_parser.add_argument("--weight-decay", type=float, default=1e-4)
    fold_parser.add_argument("--early-stopping", type=int, default=10)
    fold_parser.add_argument("--seed", type=int, default=42)
    fold_parser.add_argument("--device", default="", help="留空自动选择cuda/mps/cpu")
    fold_parser.add_argument("--force", action="store_true", help="覆盖已完成的同一折")
    fold_parser.set_defaults(handler=run_fold)

    aggregate_parser = commands.add_parser("aggregate", help="汇总所有已完成折并列出缺失折")
    aggregate_parser.add_argument("--prepared-dir", type=Path, required=True)
    aggregate_parser.add_argument(
        "--methods", nargs="+", choices=("fbcca", "transformer"),
        default=("fbcca", "transformer"),
        help="声明必须完成的方法；默认同时检查FBCCA和Transformer",
    )
    aggregate_parser.set_defaults(handler=aggregate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"错误：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
