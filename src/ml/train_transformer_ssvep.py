"""Train the paper-adapted Transformer-SSVEP model from labeled raw trials.

Production/cross-session evaluation (default)::

    python -m src.ml.train_transformer_ssvep \
      --index data/sessions/raw_trials_index.csv --split-mode session

For a small pipeline smoke test only, ``--split-mode stratified`` permits a
within-session trial split.  It must not be reported as cross-session evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np

from src.bci.config import SSVEP_CHANNEL_NAMES, TRUE_LABELS
from src.ml.preprocessing import preprocess_transformer_trial


def _dependencies():
    try:
        import torch
        from sklearn.metrics import confusion_matrix, f1_score
        from sklearn.model_selection import GroupShuffleSplit, StratifiedShuffleSplit
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise RuntimeError("请先执行 pip install -r requirements-ml.txt") from exc
    return (
        torch, nn, DataLoader, TensorDataset, GroupShuffleSplit,
        StratifiedShuffleSplit, f1_score, confusion_matrix,
    )


def read_records(index_path: Path, labels: tuple[str, ...]) -> list[dict[str, str]]:
    if not index_path.is_file():
        raise FileNotFoundError(f"试次索引不存在：{index_path}")
    data_root = index_path.parent.parent
    records: list[dict[str, str]] = []
    with index_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            label = str(row.get("true_label", "")).strip()
            relative = str(row.get("path", "")).strip()
            if label not in labels or not relative:
                continue
            path = Path(relative)
            path = path if path.is_absolute() else data_root / path
            if path.is_file():
                row["absolute_path"] = str(path)
                records.append(row)
    if not records:
        raise ValueError("索引中没有可用的 Transformer-SSVEP 带标签试次")
    counts = Counter(record["true_label"] for record in records)
    missing = [label for label in labels if counts[label] < 2]
    if missing:
        raise ValueError(f"每类至少需要2个试次，数量不足：{', '.join(missing)}")
    return records


def load_arrays(
    records: list[dict[str, str]],
    labels: tuple[str, ...],
    sample_rate: float,
    sample_count: int,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, str]]]:
    arrays, targets, valid = [], [], []
    label_index = {label: index for index, label in enumerate(labels)}
    for record in records:
        try:
            with np.load(record["absolute_path"], allow_pickle=False) as archive:
                eeg, fs = archive["eeg"], float(archive["fs"])
                if "channel_names" in archive:
                    names = tuple(str(value) for value in archive["channel_names"].tolist())
                    if names != tuple(SSVEP_CHANNEL_NAMES):
                        raise ValueError(
                            f"通道顺序必须为 {SSVEP_CHANNEL_NAMES}，实际为 {names}"
                        )
            processed = preprocess_transformer_trial(
                eeg,
                fs,
                target_fs=sample_rate,
                target_samples=sample_count,
                expected_channels=len(SSVEP_CHANNEL_NAMES),
            )
        except Exception as exc:
            print(f"跳过 {record['absolute_path']}：{exc}")
            continue
        arrays.append(processed["clean"])
        targets.append(label_index[record["true_label"]])
        valid.append(record)
    if not valid:
        raise ValueError("所有试次都未通过通道和信号质量检查")
    return (
        np.stack(arrays).astype(np.float32),
        np.asarray(targets, dtype=np.int64),
        valid,
    )


def split_indices(records, targets, args, GroupSplitter, StratifiedSplitter, labels):
    all_classes = set(range(len(labels)))
    if args.split_mode == "session":
        groups = np.asarray([
            str(record.get("session_id", "")).strip() or "missing-session"
            for record in records
        ])
        if len(set(groups)) < 2:
            raise ValueError(
                "跨会话划分至少需要两个 session_id；管线测试可显式使用 "
                "--split-mode stratified"
            )
        splitter = GroupSplitter(
            n_splits=64,
            test_size=args.validation_fraction,
            random_state=args.seed,
        )
        for train_indices, validation_indices in splitter.split(targets, targets, groups):
            if set(targets[train_indices]) == all_classes and set(targets[validation_indices]) == all_classes:
                train_sessions = sorted(set(groups[train_indices]))
                validation_sessions = sorted(set(groups[validation_indices]))
                return train_indices, validation_indices, {
                    "strategy": "session-group-holdout",
                    "train_sessions": train_sessions,
                    "validation_sessions": validation_sessions,
                }
        raise ValueError("无法找到训练/验证均覆盖全部类别的跨会话划分，请补采各会话数据")

    validation_count = math.ceil(len(targets) * args.validation_fraction)
    if validation_count < len(labels) or len(targets) - validation_count < len(labels):
        raise ValueError("分层划分后的训练集和验证集必须都覆盖全部类别")
    splitter = StratifiedSplitter(
        n_splits=1,
        test_size=args.validation_fraction,
        random_state=args.seed,
    )
    train_indices, validation_indices = next(splitter.split(np.zeros(len(targets)), targets))
    return train_indices, validation_indices, {
        "strategy": "within-session-stratified-pilot-only",
        "warning": "不得作为跨会话泛化结果报告",
    }


def evaluate(model, loader, loss_fn, device, torch):
    model.eval()
    losses, truth, prediction = [], [], []
    with torch.no_grad():
        for values, target in loader:
            logits = model(values.to(device))
            losses.append(float(loss_fn(logits, target.to(device)).item()))
            prediction.extend(torch.argmax(logits, dim=1).cpu().tolist())
            truth.extend(target.tolist())
    return float(np.mean(losses)), truth, prediction


def train(args) -> dict[str, object]:
    (
        torch, nn, DataLoader, TensorDataset, GroupSplitter,
        StratifiedSplitter, f1_score, confusion_matrix,
    ) = _dependencies()
    from src.ml.transformer_ssvep import TransformerSSVEP

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    labels = tuple(TRUE_LABELS)
    sample_count = round(args.sample_rate * args.window_seconds)
    records = read_records(args.index, labels)
    values, targets, records = load_arrays(records, labels, args.sample_rate, sample_count)
    train_indices, validation_indices, split_metadata = split_indices(
        records, targets, args, GroupSplitter, StratifiedSplitter, labels
    )

    device = torch.device(args.device or ("mps" if torch.backends.mps.is_available() else "cpu"))
    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(values[train_indices]), torch.from_numpy(targets[train_indices])),
        batch_size=args.batch_size,
        shuffle=True,
    )
    validation_loader = DataLoader(
        TensorDataset(
            torch.from_numpy(values[validation_indices]),
            torch.from_numpy(targets[validation_indices]),
        ),
        batch_size=args.batch_size,
    )
    model_args = {
        "channels": len(SSVEP_CHANNEL_NAMES),
        "sample_count": int(sample_count),
        "patch_count": 4,
        "classes": len(labels),
        "embedding_dim": 128,
        "encoder_layers": 2,
        "attention_heads": 8,
        "feedforward_dim": 256,
        "classifier_hidden_dim": 64,
        "dropout": 0.1,
    }
    model = TransformerSSVEP(**model_args).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay
    )
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    best_loss, best_state, patience = float("inf"), None, 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_losses = []
        for batch_values, batch_target in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_values.to(device))
            loss = loss_fn(logits, batch_target.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.item()))
        validation_loss, truth, prediction = evaluate(
            model, validation_loader, loss_fn, device, torch
        )
        accuracy = float(np.mean(np.asarray(truth) == np.asarray(prediction)))
        history.append({
            "epoch": epoch,
            "train_loss": float(np.mean(train_losses)),
            "validation_loss": validation_loss,
            "validation_accuracy": accuracy,
        })
        print(
            f"epoch={epoch:03d} train={np.mean(train_losses):.4f} "
            f"val={validation_loss:.4f} acc={accuracy:.3f}"
        )
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_state = {
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
            patience = 0
        else:
            patience += 1
            if patience >= args.early_stopping:
                break
    if best_state is None:
        raise RuntimeError("训练没有产生有效权重")
    model.load_state_dict(best_state)
    validation_loss, truth, prediction = evaluate(
        model, validation_loader, loss_fn, device, torch
    )
    truth_array, prediction_array = np.asarray(truth), np.asarray(prediction)
    idle_index = labels.index("空闲")
    idle_truth = truth_array == idle_index
    report = {
        "training_trials": int(len(train_indices)),
        "validation_trials": int(len(validation_indices)),
        "validation_loss": validation_loss,
        "validation_accuracy": float(np.mean(truth_array == prediction_array)),
        "validation_macro_f1": float(
            f1_score(truth, prediction, average="macro", zero_division=0)
        ),
        "idle_false_activation_rate": (
            float(np.mean(prediction_array[idle_truth] != idle_index))
            if np.any(idle_truth) else None
        ),
        "confusion_matrix": confusion_matrix(
            truth, prediction, labels=list(range(len(labels)))
        ).tolist(),
        "split": split_metadata,
        "history": history,
    }
    metadata = {
        "model_name": "Transformer-SSVEP",
        "model_version": 1,
        "architecture": "4 temporal patches -> log FFT magnitude -> Transformer encoder",
        "labels": list(labels),
        "channel_names": list(SSVEP_CHANNEL_NAMES),
        "reference": "SRB2/A1",
        "bias": "BIAS/A2",
        "sample_rate": float(args.sample_rate),
        "window_seconds": float(args.window_seconds),
        "model_args": model_args,
        "gate_thresholds": {"min_confidence": float(args.min_confidence)},
        "training_report": report,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": best_state}, args.model_out)
    args.metadata_out.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"权重已保存：{args.model_out}")
    print(f"评估与 metadata 已保存：{args.metadata_out}")
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练论文适配版 Transformer-SSVEP")
    parser.add_argument("--index", type=Path, default=Path("data/sessions/raw_trials_index.csv"))
    parser.add_argument("--model-out", type=Path, default=Path("models/transformer_ssvep.pt"))
    parser.add_argument(
        "--metadata-out", type=Path, default=Path("models/transformer_ssvep_metadata.json")
    )
    parser.add_argument("--sample-rate", type=float, default=250.0)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--split-mode", choices=("session", "stratified"), default="session")
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-3)
    parser.add_argument("--early-stopping", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="")
    parser.add_argument("--min-confidence", type=float, default=0.60)
    return parser


def main() -> int:
    train(build_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

