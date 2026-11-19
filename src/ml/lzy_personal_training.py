"""Training and leakage-aware evaluation for ``个人算法-Lzy``."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.ml.dual_fbcca_features import extract_dual_fbcca_features
from src.ml.lzy_personal_model import (
    CALIBRATION_ALPHA,
    FILTER_BANK_HIGH_EDGE_HZ,
    FILTER_BANK_LOW_EDGES_HZ,
    MODEL_VERSION,
    REQUIRED_WINDOW_SECONDS,
    SEGMENT_HOP_SECONDS,
    SEGMENT_SECONDS,
    extract_lzy_fbcca_features,
)


def _json_scalar(payload, key: str) -> dict[str, object]:
    if key not in payload.files:
        return {}
    try:
        value = json.loads(str(np.asarray(payload[key]).item()))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _fit_nontarget_calibration(
    scores: np.ndarray, truth: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    centers = np.asarray([
        np.median(scores[truth != class_index, class_index])
        for class_index in range(len(COMMANDS))
    ])
    scales = np.asarray([
        1.4826 * np.median(np.abs(
            scores[truth != class_index, class_index] - centers[class_index]
        ))
        for class_index in range(len(COMMANDS))
    ])
    return centers, np.maximum(scales, 1e-3)


def _metrics(truth: np.ndarray, prediction: np.ndarray) -> dict[str, object]:
    confusion = np.zeros((len(COMMANDS), len(COMMANDS)), dtype=int)
    for actual, candidate in zip(truth, prediction):
        confusion[int(actual), int(candidate)] += 1
    recalls = {
        label: float(confusion[index, index] / max(np.sum(confusion[index]), 1))
        for index, label in enumerate(COMMANDS)
    }
    return {
        "correct": int(np.sum(truth == prediction)),
        "total": int(truth.size),
        "block_held_out_accuracy": float(np.mean(truth == prediction)),
        "balanced_accuracy": float(np.mean(list(recalls.values()))),
        "per_class_recall": recalls,
        "confusion_matrix_rows_true_columns_predicted": confusion.tolist(),
    }


def _evaluate_dual_feature_options(
    matrix: np.ndarray,
    truth: np.ndarray,
    blocks: np.ndarray,
    idle_matrix: np.ndarray,
    idle_blocks: np.ndarray,
) -> dict[str, object]:
    """Evaluate the requested raw/corrected/concatenated score alternatives."""

    results: dict[str, object] = {}
    for feature_mode in ("raw_12", "idle_corrected_12", "raw_plus_idle_corrected_24"):
        candidates = []
        for c_value in (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0):
            prediction = np.full(truth.shape, -1, dtype=int)
            for held_block in sorted(set(int(value) for value in blocks)):
                train = blocks != held_block
                test = blocks == held_block
                idle_train = idle_blocks != held_block
                background = np.median(idle_matrix[idle_train], axis=0)

                def transform(values: np.ndarray) -> np.ndarray:
                    corrected = values - background
                    if feature_mode == "raw_12":
                        return values
                    if feature_mode == "idle_corrected_12":
                        return corrected
                    return np.concatenate((values, corrected), axis=1)

                classifier = make_pipeline(
                    StandardScaler(), LogisticRegression(C=c_value, max_iter=5000),
                )
                classifier.fit(transform(matrix[train]), truth[train])
                prediction[test] = classifier.predict(transform(matrix[test]))
            candidates.append({
                "C": c_value,
                "correct": int(np.sum(prediction == truth)),
                "total": int(truth.size),
                "accuracy": float(np.mean(prediction == truth)),
            })
        results[feature_mode] = max(
            candidates, key=lambda item: (float(item["accuracy"]), -float(item["C"])),
        )
    results["explanation"] = (
        "固定空闲背景只会给每一列减去常数；经StandardScaler后与原列线性重复，"
        "所以24维拼接不保证带来新判别信息。"
    )
    return results


def train_lzy_personal_algorithm(
    raw_directory: Path | str,
    model_path: Path | str,
    metadata_path: Path | str,
    output_directory: Path | str,
    *,
    run_id: str,
) -> dict[str, object]:
    root = Path(raw_directory).expanduser().resolve()
    paths = sorted(root.glob(f"{run_id}-trial-*.npz"))
    if not paths:
        raise ValueError(f"没有找到运行 {run_id} 的原始试次")

    active_scores: list[np.ndarray] = []
    dual_active: list[np.ndarray] = []
    labels: list[int] = []
    blocks: list[int] = []
    source_files: list[str] = []
    idle_scores: list[np.ndarray] = []
    dual_idle: list[np.ndarray] = []
    idle_blocks: list[int] = []
    counts: Counter[str] = Counter()
    sample_rate = 0.0
    sample_count = 0
    for path in paths:
        with np.load(path, allow_pickle=False) as payload:
            label = str(np.asarray(payload["true_label"]).item())
            fs = float(np.asarray(payload["fs"]).item())
            eeg = np.asarray(payload["eeg"], dtype=np.float64)
            channel_names = tuple(str(value) for value in payload["channel_names"].tolist())
            context = _json_scalar(payload, "trial_context")
        if channel_names != tuple(SSVEP_CHANNEL_NAMES):
            raise ValueError(f"{path.name} 的通道顺序与固定8通道枕区布局不一致")
        if sample_rate and abs(sample_rate - fs) > 1e-6:
            raise ValueError("同一训练运行内采样率不一致")
        sample_rate = fs
        sample_count = round(REQUIRED_WINDOW_SECONDS * fs)
        if eeg.shape[1] < sample_count:
            raise ValueError(f"{path.name} 不足完整 {REQUIRED_WINDOW_SECONDS:g} 秒")
        segment = eeg[:len(SSVEP_CHANNEL_NAMES), :sample_count]
        score = extract_lzy_fbcca_features(
            segment, fs, sample_count=sample_count, channel_count=len(SSVEP_CHANNEL_NAMES),
        ).values
        dual = extract_dual_fbcca_features(
            segment, fs, sample_count=sample_count, channel_count=len(SSVEP_CHANNEL_NAMES),
        ).values
        block = int(context.get("repetition", context.get("block_index", 0)) or 0)
        if block <= 0:
            raise ValueError(f"{path.name} 缺少 repetition/block_index，不能整块留一验证")
        counts[label] += 1
        if label in COMMANDS:
            active_scores.append(score)
            dual_active.append(dual)
            labels.append(COMMANDS.index(label))
            blocks.append(block)
            source_files.append(path.name)
        elif label == "空闲":
            idle_scores.append(score)
            dual_idle.append(dual)
            idle_blocks.append(block)

    missing = [label for label in COMMANDS if counts[label] < 2]
    if missing:
        raise ValueError(f"以下指令不足2段，不能训练：{', '.join(missing)}")
    scores = np.asarray(active_scores, dtype=np.float64)
    truth = np.asarray(labels, dtype=int)
    block_values = np.asarray(blocks, dtype=int)
    idle = np.asarray(idle_scores, dtype=np.float64)
    idle_block_values = np.asarray(idle_blocks, dtype=int)
    if len(set(blocks)) < 3 or idle.shape[0] < 3:
        raise ValueError("至少需要3个重复块及3段空闲数据")

    prediction = np.full(truth.shape, -1, dtype=int)
    oof_evidence = np.full((truth.size, len(COMMANDS)), np.nan, dtype=np.float64)
    fold_results = []
    for held_block in sorted(set(blocks)):
        train = block_values != held_block
        test = block_values == held_block
        center, scale = _fit_nontarget_calibration(scores[train], truth[train])
        evidence = (scores[test] - CALIBRATION_ALPHA * center) / scale
        values = np.argmax(evidence, axis=1)
        prediction[test] = values
        oof_evidence[test] = evidence
        fold_results.append({
            "held_block": int(held_block),
            "correct": int(np.sum(values == truth[test])),
            "total": int(np.sum(test)),
        })
    if np.any(prediction < 0) or not np.isfinite(oof_evidence).all():
        raise RuntimeError("整块留一预测未覆盖全部主动试次")
    evaluation = _metrics(truth, prediction)
    evaluation["folds"] = fold_results

    dual_evaluation = _evaluate_dual_feature_options(
        np.asarray(dual_active, dtype=np.float64),
        truth,
        block_values,
        np.asarray(dual_idle, dtype=np.float64),
        idle_block_values,
    )
    center, scale = _fit_nontarget_calibration(scores, truth)
    idle_center = np.median(idle, axis=0)
    idle_scale = np.maximum(
        1.4826 * np.median(np.abs(idle - idle_center), axis=0), 1e-3,
    )

    model_file = Path(model_path).expanduser().resolve()
    metadata_file = Path(metadata_path).expanduser().resolve()
    output_root = Path(output_directory).expanduser().resolve()
    model_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        model_file,
        version=np.asarray(MODEL_VERSION),
        class_labels=np.asarray(COMMANDS),
        frequencies_hz=np.asarray(TARGET_FREQUENCIES_HZ, dtype=np.float32),
        channel_names=np.asarray(SSVEP_CHANNEL_NAMES),
        stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
        sample_rate_hz=np.asarray(sample_rate, dtype=np.float32),
        sample_count=np.asarray(sample_count, dtype=np.int32),
        nontarget_center=center,
        nontarget_scale=scale,
        calibration_alpha=np.asarray(CALIBRATION_ALPHA, dtype=np.float32),
        idle_background_center=idle_center,
        idle_background_scale=idle_scale,
    )
    metadata: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "algorithm_name": "个人算法-Lzy",
        "algorithm": "overlapping_segment_fbcca_then_personal_nontarget_calibration",
        "source_run_id": run_id,
        "source_directory": str(root),
        "model_path": str(model_file),
        "labels": list(COMMANDS),
        "target_frequencies_hz": list(TARGET_FREQUENCIES_HZ),
        "channel_names": list(SSVEP_CHANNEL_NAMES),
        "sample_rate_hz": sample_rate,
        "sample_count": sample_count,
        "analysis_seconds": sample_count / sample_rate,
        "stimulus_layout": SSVEP_STIMULUS_LAYOUT,
        "trial_count": int(truth.size),
        "idle_trial_count": int(idle.shape[0]),
        "all_saved_trial_count": int(len(paths)),
        "trials_per_class": {label: counts[label] for label in COMMANDS},
        "source_files": source_files,
        "feature_pipeline": {
            "preprocessing": "完整5秒，线性去漂移，50Hz Q=30零相位陷波，逐通道标准化",
            "segments": "0-4、0.5-4.5、1-5秒三个重叠4秒窗，未丢弃首尾数据",
            "fbcca": (
                f"{list(FILTER_BANK_LOW_EDGES_HZ)}-"
                f"{FILTER_BANK_HIGH_EDGE_HZ:g}Hz标准权重，1-4次有效谐波"
            ),
            "aggregation": "三个子窗的六类FBCCA分数逐类取中位数",
            "personal_calibration": (
                f"(分数-{CALIBRATION_ALPHA:g}×该频率非目标中位数)/非目标MAD尺度"
            ),
            "decision": "校准证据最大者",
        },
        "evaluation": evaluation,
        "requested_feature_comparison": dual_evaluation,
        "validation": (
            "完整repetition/block留一；被留出的整块不参与该折的个人非目标基线训练"
        ),
        "selection_warning": (
            "该方案是在同一批数据上比较多种候选后选出，47.1%属于探索性内部验证，"
            "不是下一次独立会话的准确率保证。"
        ),
        "production_approved": False,
        "note": "仅限科研测试；尚未通过跨会话独立验证，禁止直接用于实机动作放行。",
    }
    metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output_root / "report.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    with (output_root / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("source_file", "block", "true_label", "predicted_label", "correct"))
        for path_name, block, actual, candidate in zip(
            source_files, block_values, truth, prediction,
        ):
            writer.writerow((
                path_name,
                int(block),
                COMMANDS[int(actual)],
                COMMANDS[int(candidate)],
                int(actual == candidate),
            ))
    return metadata


__all__ = ["train_lzy_personal_algorithm"]
