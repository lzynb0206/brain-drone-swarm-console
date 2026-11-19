"""Train the fixed personal dual-FBCCA logistic score calibrator."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.ml.dual_fbcca_features import extract_dual_fbcca_features
from src.ml.personal_score_contract import FEATURE_NAMES, MODEL_VERSION, REQUIRED_WINDOW_SECONDS
from src.bci.preprocessing import assess_signal_quality


def _json_scalar(payload, key: str) -> dict[str, object]:
    if key not in payload.files:
        return {}
    try:
        value = json.loads(str(np.asarray(payload[key]).item()))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _metrics(truth: np.ndarray, prediction: np.ndarray) -> dict[str, object]:
    recalls = {}
    confusion = np.zeros((len(COMMANDS), len(COMMANDS)), dtype=int)
    for actual, candidate in zip(truth, prediction):
        confusion[int(actual), int(candidate)] += 1
    for index, label in enumerate(COMMANDS):
        mask = truth == index
        recalls[label] = float(np.mean(prediction[mask] == index)) if np.any(mask) else 0.0
    return {
        "correct": int(np.sum(truth == prediction)),
        "total": int(truth.size),
        "block_held_out_accuracy": float(np.mean(truth == prediction)),
        "balanced_accuracy": float(np.mean(list(recalls.values()))),
        "per_class_recall": recalls,
        "confusion_matrix_rows_true_columns_predicted": confusion.tolist(),
    }


def train_personal_score_calibration(
    raw_directory: Path | str,
    model_path: Path | str,
    metadata_path: Path | str,
    *,
    run_id: str,
    algorithm_name: str = "个人校准融合",
    minimum_trial_index: int | None = None,
    maximum_trial_index: int | None = None,
    trial_indices: Sequence[int] | None = None,
) -> dict[str, object]:
    """Fit one subject/session model and persist a strict runtime contract.

    Validation leaves one complete balanced repetition out at a time.  The
    final persisted estimator is then refit on every active labelled trial.
    Idle trials are retained only for diagnostic threshold reporting; they are
    not relabelled as one of the six commands.
    """

    root = Path(raw_directory).expanduser().resolve()
    paths = sorted(root.glob(f"{run_id}-trial-*.npz"))
    allowed_trial_indices = (
        {int(value) for value in trial_indices} if trial_indices is not None else None
    )
    if (
        minimum_trial_index is not None
        or maximum_trial_index is not None
        or allowed_trial_indices is not None
    ):
        selected_paths = []
        for path in paths:
            try:
                trial_index = int(path.stem.rsplit("-trial-", 1)[1])
            except (IndexError, ValueError):
                continue
            if allowed_trial_indices is not None and trial_index not in allowed_trial_indices:
                continue
            if minimum_trial_index is not None and trial_index < minimum_trial_index:
                continue
            if maximum_trial_index is not None and trial_index > maximum_trial_index:
                continue
            selected_paths.append(path)
        paths = selected_paths
    if not paths:
        raise ValueError(f"没有找到运行 {run_id} 的原始试次")
    features: list[np.ndarray] = []
    labels: list[int] = []
    blocks: list[int] = []
    source_files: list[str] = []
    sample_rate = 0.0
    sample_count = 0
    idle_features: list[np.ndarray] = []
    idle_blocks: list[int] = []
    active_usable_channel_counts: list[int] = []
    all_usable_channel_counts: list[int] = []
    counts: Counter[str] = Counter()
    expected_count = 0
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
        expected_count = round(REQUIRED_WINDOW_SECONDS * fs)
        if eeg.shape[1] < expected_count:
            raise ValueError(f"{path.name} 不足完整 {REQUIRED_WINDOW_SECONDS:g} 秒")
        # Normal collection starts at the verified stimulus marker.  Per the
        # experiment decision, use the complete first 5 s without head/tail trimming.
        segment = eeg[:len(SSVEP_CHANNEL_NAMES), :expected_count]
        result = extract_dual_fbcca_features(
            segment, fs, sample_count=expected_count, channel_count=len(SSVEP_CHANNEL_NAMES)
        )
        quality = assess_signal_quality(
            segment,
            fs,
            deduplicate_channels=range(1, len(SSVEP_CHANNEL_NAMES) + 1),
        )
        usable_channel_count = int(quality["usable_channel_count"])
        all_usable_channel_counts.append(usable_channel_count)
        block = int(context.get("repetition", context.get("block_index", 0)) or 0)
        if block <= 0:
            raise ValueError(f"{path.name} 缺少 repetition/block_index，不能整块留一验证")
        counts[label] += 1
        if label in COMMANDS:
            features.append(result.values)
            labels.append(COMMANDS.index(label))
            blocks.append(block)
            source_files.append(path.name)
            active_usable_channel_counts.append(usable_channel_count)
        elif label == "空闲":
            idle_features.append(result.values)
            idle_blocks.append(block)
        sample_count = expected_count
    missing = [label for label in COMMANDS if counts[label] < 2]
    if missing:
        raise ValueError(f"以下指令不足2段，不能训练：{', '.join(missing)}")
    matrix = np.asarray(features, dtype=np.float64)
    truth = np.asarray(labels, dtype=int)
    block_values = np.asarray(blocks, dtype=int)
    unique_blocks = sorted(set(blocks))
    if len(unique_blocks) < 3:
        raise ValueError("至少需要3个完整重复块才能训练个人校准融合")

    oof_prediction = np.full(truth.shape, -1, dtype=int)
    oof_probability = np.full((len(truth), len(COMMANDS)), np.nan, dtype=np.float64)
    idle_oof_probability: list[np.ndarray] = []
    idle_matrix = np.asarray(idle_features, dtype=np.float64)
    idle_block_values = np.asarray(idle_blocks, dtype=int)
    for held_block in unique_blocks:
        train = block_values != held_block
        test = block_values == held_block
        if len(set(truth[train].tolist())) != len(COMMANDS):
            raise ValueError(f"留出第{held_block}块后训练集未覆盖六类")
        scaler = StandardScaler().fit(matrix[train])
        classifier = LogisticRegression(C=0.1, max_iter=5000).fit(
            scaler.transform(matrix[train]), truth[train]
        )
        probabilities = classifier.predict_proba(scaler.transform(matrix[test]))
        oof_probability[test] = probabilities
        oof_prediction[test] = classifier.classes_[np.argmax(probabilities, axis=1)]
        if idle_matrix.size:
            idle_test = idle_block_values == held_block
            if np.any(idle_test):
                idle_oof_probability.extend(
                    classifier.predict_proba(scaler.transform(idle_matrix[idle_test]))
                )
    if np.any(oof_prediction < 0) or not np.isfinite(oof_probability).all():
        raise RuntimeError("整块留一预测未覆盖全部主动试次")
    evaluation = _metrics(truth, oof_prediction)
    order = np.sort(oof_probability, axis=1)
    confidence = order[:, -1]
    margin = order[:, -1] - order[:, -2]
    correct = oof_prediction == truth
    correct_confidence = confidence[correct]
    correct_margin = margin[correct]
    confidence_threshold = max(
        0.25,
        float(np.percentile(correct_confidence, 10.0)) if correct_confidence.size else 0.25,
    )
    margin_threshold = max(
        0.03,
        float(np.percentile(correct_margin, 10.0)) if correct_margin.size else 0.03,
    )
    oof_quality = np.asarray(active_usable_channel_counts, dtype=int) >= 2
    oof_accepted = (
        oof_quality
        & (confidence >= confidence_threshold)
        & (margin >= margin_threshold)
    )
    evaluation["thresholded_output"] = {
        "accepted_correct": int(np.sum(oof_accepted & correct)),
        "accepted_count": int(np.sum(oof_accepted)),
        "precision": float(np.sum(oof_accepted & correct) / max(np.sum(oof_accepted), 1)),
        "coverage": float(np.mean(oof_accepted)),
        "quality_passed_count": int(np.sum(oof_quality)),
        "quality_minimum_usable_channels": 2,
    }
    idle_diagnostic: dict[str, object] = {"count": len(idle_oof_probability)}
    if idle_oof_probability:
        idle_probabilities = np.asarray(idle_oof_probability)
        idle_diagnostic.update({
            "maximum_probability_median": float(np.median(np.max(idle_probabilities, axis=1))),
            "maximum_probability_95th_percentile": float(np.percentile(np.max(idle_probabilities, axis=1), 95.0)),
            "note": "空闲样本仅用于诊断；在线仍需当前会话空闲校准抬高门槛。",
        })

    scaler = StandardScaler().fit(matrix)
    classifier = LogisticRegression(C=0.1, max_iter=5000).fit(
        scaler.transform(matrix), truth
    )
    if tuple(int(value) for value in classifier.classes_) != tuple(range(len(COMMANDS))):
        raise RuntimeError("逻辑回归类别顺序与六指令契约不一致")
    model_file = Path(model_path).expanduser().resolve()
    metadata_file = Path(metadata_path).expanduser().resolve()
    model_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        model_file,
        version=np.asarray(MODEL_VERSION),
        class_labels=np.asarray(COMMANDS),
        frequencies_hz=np.asarray(TARGET_FREQUENCIES_HZ, dtype=np.float32),
        channel_names=np.asarray(SSVEP_CHANNEL_NAMES),
        feature_names=np.asarray(FEATURE_NAMES),
        sample_rate_hz=np.asarray(sample_rate, dtype=np.float32),
        sample_count=np.asarray(sample_count, dtype=np.int32),
        stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
        scaler_mean=np.asarray(scaler.mean_, dtype=np.float64),
        scaler_scale=np.asarray(scaler.scale_, dtype=np.float64),
        coefficients=np.asarray(classifier.coef_, dtype=np.float64),
        intercept=np.asarray(classifier.intercept_, dtype=np.float64),
        confidence_threshold=np.asarray(confidence_threshold, dtype=np.float32),
        margin_threshold=np.asarray(margin_threshold, dtype=np.float32),
    )
    metadata: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "algorithm_name": algorithm_name,
        "algorithm": "dual_fbcca_scores_standard50_equal46_then_personal_logistic_regression",
        "source_run_id": run_id,
        "source_directory": str(root),
        "model_path": str(model_file),
        "labels": list(COMMANDS),
        "target_frequencies_hz": list(TARGET_FREQUENCIES_HZ),
        "channel_names": list(SSVEP_CHANNEL_NAMES),
        "feature_names": list(FEATURE_NAMES),
        "feature_pipeline": {
            "preprocessing": "full 5 s, linear detrend, 50 Hz Q=30 zero-phase notch, per-channel standardization",
            "branch_1": "6/14/22-50 Hz FFT filter bank, standard weights, CCA harmonics 1-4",
            "branch_2": "6/14/22-46 Hz FFT filter bank, equal weights, CCA harmonics 1-4",
            "classifier": "StandardScaler + LogisticRegression(C=0.1)",
        },
        "sample_rate_hz": sample_rate,
        "sample_count": sample_count,
        "analysis_seconds": sample_count / sample_rate,
        "stimulus_layout": SSVEP_STIMULUS_LAYOUT,
        "trial_count": int(len(truth)),
        "idle_trial_count": int(len(idle_features)),
        "trials_per_class": {label: counts[label] for label in COMMANDS},
        "source_files": source_files,
        "source_trial_range": {
            "minimum_trial_index": minimum_trial_index,
            "maximum_trial_index": maximum_trial_index,
            "selected_trial_indices": (
                sorted(allowed_trial_indices)
                if allowed_trial_indices is not None else None
            ),
        },
        "evaluation": evaluation,
        "validation": "complete repetition/block held out; fixed method; no trial from held block used for scaler or classifier",
        "selection_warning": (
            "The block-held-out number is same-session internal validation, not an "
            "independent future-session accuracy claim."
        ),
        "idle_diagnostic": idle_diagnostic,
        "signal_quality_diagnostic": {
            "usable_channel_count_histogram": {
                str(count): int(all_usable_channel_counts.count(count))
                for count in range(len(SSVEP_CHANNEL_NAMES) + 1)
            },
            "saved_trials_with_fewer_than_two_usable_channels": int(
                np.sum(np.asarray(all_usable_channel_counts, dtype=int) < 2)
            ),
            "note": "质量检查先执行去漂移和50Hz陷波，再判断残余工频与有效频段。",
        },
        "confidence_threshold": confidence_threshold,
        "margin_threshold": margin_threshold,
        "production_approved": False,
        "note": "个人模型仅限科研测试；必须用新日期/新会话数据做独立验证后才能申请实机准入。",
    }
    metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


__all__ = ["train_personal_score_calibration"]
