"""Train and evaluate FB-EEGNet from ExperimentRecorder raw trials.

Example:
    python -m src.ml.train_fb_eegnet --index data/sessions/raw_trials_index.csv
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

from src.bci.config import TRUE_LABELS
from src.ml.preprocessing import DEFAULT_FILTER_BANKS, preprocess_trial


def _dependencies():
    try:
        import torch
        from sklearn.metrics import confusion_matrix, f1_score
        from sklearn.model_selection import StratifiedShuffleSplit
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise RuntimeError("请先执行 pip install -r requirements-ml.txt") from exc
    return torch, nn, DataLoader, TensorDataset, StratifiedShuffleSplit, f1_score, confusion_matrix


def read_records(index_path: Path, labels: tuple[str, ...]) -> list[dict[str, str]]:
    if not index_path.is_file():
        raise FileNotFoundError(f"试次索引不存在：{index_path}")
    data_root = index_path.parent.parent
    records = []
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
        raise ValueError("索引中没有可用的带标签真实 EEG 试次")
    counts = Counter(row["true_label"] for row in records)
    missing = [label for label in labels if counts[label] < 2]
    if missing:
        raise ValueError(f"每类至少需要 2 个试次，数量不足：{', '.join(missing)}")
    return records


def load_arrays(records, labels, sample_rate: float, sample_count: int):
    clean, banks, targets, valid = [], [], [], []
    label_index = {label: index for index, label in enumerate(labels)}
    for record in records:
        try:
            with np.load(record["absolute_path"], allow_pickle=False) as archive:
                eeg, fs = archive["eeg"], float(archive["fs"])
            processed = preprocess_trial(eeg, fs, sample_rate, sample_count, 8, DEFAULT_FILTER_BANKS)
        except Exception as exc:
            print(f"跳过 {record['absolute_path']}：{exc}")
            continue
        clean.append(processed["clean"])
        banks.append(processed["filter_bank"])
        targets.append(label_index[record["true_label"]])
        valid.append(record)
    if not valid:
        raise ValueError("所有试次都未通过预处理")
    return (
        np.stack(clean).astype(np.float32), np.stack(banks).astype(np.float32),
        np.asarray(targets, dtype=np.int64), valid,
    )


def evaluate(model, loader, loss_fn, device, torch):
    model.eval()
    losses, truth, prediction = [], [], []
    with torch.no_grad():
        for clean, banks, target in loader:
            logits = model(clean.to(device), banks.to(device))
            losses.append(float(loss_fn(logits, target.to(device)).item()))
            prediction.extend(torch.argmax(logits, dim=1).cpu().tolist())
            truth.extend(target.tolist())
    return float(np.mean(losses)), truth, prediction


def train(args) -> dict[str, object]:
    torch, nn, DataLoader, TensorDataset, Splitter, f1_score, confusion_matrix = _dependencies()
    from src.ml.fb_eegnet import FBEEGNetFusion

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    labels = tuple(TRUE_LABELS)
    sample_count = round(args.sample_rate * args.window_seconds)
    records = read_records(args.index, labels)
    clean, banks, targets, records = load_arrays(records, labels, args.sample_rate, sample_count)
    validation_count = math.ceil(len(targets) * args.validation_fraction)
    if validation_count < len(labels) or len(targets) - validation_count < len(labels):
        raise ValueError(
            f"分层划分要求训练集和验证集都覆盖 {len(labels)} 类；"
            "请增加每类试次，或在合理范围内调整 --validation-fraction"
        )
    splitter = Splitter(n_splits=1, test_size=args.validation_fraction, random_state=args.seed)
    train_indices, validation_indices = next(splitter.split(np.zeros(len(targets)), targets))

    device = torch.device(args.device or ("mps" if torch.backends.mps.is_available() else "cpu"))
    train_set = TensorDataset(
        torch.from_numpy(clean[train_indices]), torch.from_numpy(banks[train_indices]),
        torch.from_numpy(targets[train_indices]),
    )
    validation_set = TensorDataset(
        torch.from_numpy(clean[validation_indices]), torch.from_numpy(banks[validation_indices]),
        torch.from_numpy(targets[validation_indices]),
    )
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    validation_loader = DataLoader(validation_set, batch_size=args.batch_size)
    model_args = {
        "channels": 8, "bands": len(DEFAULT_FILTER_BANKS), "classes": len(labels),
        "temporal_filters": 8, "depth_multiplier": 2, "output_filters": 16,
        "dropout": 0.4,
    }
    model = FBEEGNetFusion(**model_args).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-3)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    best_loss, best_state, patience = float("inf"), None, 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_losses = []
        for batch_clean, batch_banks, batch_target in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_clean.to(device), batch_banks.to(device))
            loss = loss_fn(logits, batch_target.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.item()))
        validation_loss, truth, prediction = evaluate(model, validation_loader, loss_fn, device, torch)
        accuracy = float(np.mean(np.asarray(truth) == np.asarray(prediction)))
        history.append({
            "epoch": epoch, "train_loss": float(np.mean(train_losses)),
            "validation_loss": validation_loss, "validation_accuracy": accuracy,
        })
        print(f"epoch={epoch:03d} train={np.mean(train_losses):.4f} val={validation_loss:.4f} acc={accuracy:.3f}")
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= args.early_stopping:
                break
    if best_state is None:
        raise RuntimeError("训练没有产生有效权重")
    model.load_state_dict(best_state)
    validation_loss, truth, prediction = evaluate(model, validation_loader, loss_fn, device, torch)
    matrix = confusion_matrix(truth, prediction, labels=list(range(len(labels))))
    idle_index = labels.index("空闲")
    idle_truth = np.asarray(truth) == idle_index
    false_activation_rate = float(np.mean(np.asarray(prediction)[idle_truth] != idle_index)) if np.any(idle_truth) else None
    accuracy = float(np.mean(np.asarray(truth) == np.asarray(prediction)))
    report = {
        "labels": list(labels),
        "training_trials": int(len(train_indices)),
        "validation_trials": int(len(validation_indices)),
        "validation_loss": validation_loss,
        "validation_accuracy": accuracy,
        "validation_macro_f1": float(f1_score(truth, prediction, average="macro", zero_division=0)),
        "idle_false_activation_rate": false_activation_rate,
        "confusion_matrix": matrix.tolist(),
        "history": history,
    }
    metadata = {
        "model_name": "FB-EEGNet Fusion",
        "labels": list(labels),
        "sample_rate": float(args.sample_rate),
        "sample_count": int(sample_count),
        "filter_banks": [list(values) for values in DEFAULT_FILTER_BANKS],
        "model_args": model_args,
        "gate_thresholds": {
            "min_confidence": args.min_confidence,
            "min_margin": args.min_margin,
            "max_entropy": args.max_entropy,
        },
        "training_report": report,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": best_state}, args.model_out)
    args.metadata_out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"权重已保存：{args.model_out}")
    print(f"评估与 metadata 已保存：{args.metadata_out}")
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用真实带标签 EEG 训练 FB-EEGNet")
    parser.add_argument("--index", type=Path, default=Path("data/sessions/raw_trials_index.csv"))
    parser.add_argument("--model-out", type=Path, default=Path("models/fb_eegnet_fusion.pt"))
    parser.add_argument("--metadata-out", type=Path, default=Path("models/fb_eegnet_metadata.json"))
    parser.add_argument("--sample-rate", type=float, default=250.0)
    parser.add_argument("--window-seconds", type=float, default=4.0)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--early-stopping", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="")
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument("--min-margin", type=float, default=0.10)
    parser.add_argument("--max-entropy", type=float, default=1.4)
    return parser


def main() -> int:
    train(build_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
