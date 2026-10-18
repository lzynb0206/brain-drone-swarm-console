"""Training and calibration-quality gates for personal SSVEP templates."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNELS_1BASED,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.ml.personal_template_contract import (
    ISOLATED_CALIBRATION_CONDITION,
    MIN_LOO_CONSISTENCY,
    MIN_RELIABLE_CHANNEL_FRACTION,
    MODEL_VERSION,
)
from src.ml.personal_template_features import extract_template_feature


def train_personal_template_model(
    run_directory: Path | str,
    model_path: Path | str,
    metadata_path: Path | str,
    *,
    analysis_seconds: float = WINDOW_SECONDS,
    experimental_override: bool = False,
    experimental_channels_1based: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Build a six-class model from isolated, labelled calibration trials.

    ``experimental_override`` is an explicit opt-in for making a disposable
    model from a weak calibration run.  It never changes the normal quality
    gate used by the UI's automatic model builder.
    """

    run_root = Path(run_directory).expanduser().resolve()
    paths = sorted(run_root.glob("trial-*.npz"))
    if not paths:
        raise ValueError("单目标校准目录中没有 trial-*.npz")

    segments: list[np.ndarray] = []
    labels: list[str] = []
    source_files: list[str] = []
    sample_rate = 0.0
    sample_count = 0
    stimulus_layouts: set[str] = set()
    calibration_conditions: set[str] = set()
    visible_target_counts: set[int] = set()
    quality_masks: list[np.ndarray] = []
    for path in paths:
        with np.load(path, allow_pickle=False) as payload:
            label = str(np.asarray(payload["true_label"]).item())
            if label not in COMMANDS:
                continue
            fs = float(np.asarray(payload["fs"]).item())
            discard = float(np.asarray(payload["discard_seconds"]).item())
            eeg = np.asarray(payload["eeg"], dtype=np.float64)
            start = max(0, round(discard * fs))
            count = round(float(analysis_seconds) * fs)
            segment = eeg[:, start:start + count]
            if segment.shape[1] != count:
                raise ValueError(f"{path.name} 不足 {analysis_seconds:g} 秒可分析数据")
            layout = ""
            if "stimulus_layout" in payload.files:
                layout = str(np.asarray(payload["stimulus_layout"]).item())
            elif "metadata" in payload.files:
                trial_metadata = json.loads(str(np.asarray(payload["metadata"]).item()))
                layout = str(trial_metadata.get("stimulus_layout", ""))
            stimulus_layouts.add(layout)
            condition = (
                str(np.asarray(payload["calibration_condition"]).item())
                if "calibration_condition" in payload.files else ""
            )
            calibration_conditions.add(condition)
            visible_count = (
                int(np.asarray(payload["visible_target_count"]).item())
                if "visible_target_count" in payload.files else 0
            )
            visible_target_counts.add(visible_count)
            quality_mask = np.ones(len(SSVEP_CHANNEL_NAMES), dtype=bool)
            if "signal_quality" in payload.files:
                quality = json.loads(str(np.asarray(payload["signal_quality"]).item()))
                candidate = np.asarray(quality.get("usable_mask", []), dtype=bool)
                if candidate.shape == quality_mask.shape:
                    quality_mask = candidate
        if sample_rate and abs(sample_rate - fs) > 1e-6:
            raise ValueError("校准试次的采样率不一致")
        sample_rate, sample_count = fs, count
        segments.append(segment)
        quality_masks.append(quality_mask)
        labels.append(label)
        source_files.append(path.name)

    counts = Counter(labels)
    missing = [label for label in COMMANDS if counts[label] < 3]
    if missing:
        raise ValueError(f"以下个人模板不足3段：{', '.join(missing)}")
    if stimulus_layouts != {SSVEP_STIMULUS_LAYOUT}:
        raise ValueError(
            "校准画面与当前六目标布局不一致；旧的满屏单目标数据已保留，"
            "但必须用新的同位置/同尺寸校准重新采集"
        )
    if (
        calibration_conditions != {ISOLATED_CALIBRATION_CONDITION}
        or visible_target_counts != {1}
    ):
        raise ValueError(
            "个人检索模板必须来自当前六目标相同位置和尺寸的单目标独闪采集"
        )

    # Never let a corrupt electrode dominate every template.  A channel must
    # pass the same quality checks in at least 80% of calibration trials before
    # it can be used by the runtime model.
    mask_matrix = np.asarray(quality_masks, dtype=bool)
    reliability = np.mean(mask_matrix, axis=0)
    requested_channels = (
        tuple(int(value) for value in experimental_channels_1based)
        if experimental_channels_1based is not None else SSVEP_CHANNELS_1BASED
    )
    if not requested_channels or any(
        value < 1 or value > len(SSVEP_CHANNEL_NAMES) for value in requested_channels
    ) or len(set(requested_channels)) != len(requested_channels):
        raise ValueError("实验模型通道必须是互不重复的 1–8 通道编号")
    requested_indices = np.asarray([channel - 1 for channel in requested_channels], dtype=int)
    if experimental_override:
        # The user explicitly asked for a trial model even though one of the
        # selected channels does not pass the conservative 80% run-wide gate.
        # Reliability is still persisted so the limitation remains visible.
        channel_indices = requested_indices
    else:
        channel_indices = requested_indices[
            reliability[requested_indices] >= MIN_RELIABLE_CHANNEL_FRACTION
        ]
    if channel_indices.size == 0:
        details = ", ".join(
            f"{name} {ratio * 100:.0f}%"
            for name, ratio in (
                (SSVEP_CHANNEL_NAMES[index], reliability[index])
                for index in requested_indices
            )
        )
        raise ValueError(f"没有跨试次稳定的 EEG 通道（需≥80%）：{details}")
    features = [
        extract_template_feature(
            segment[channel_indices],
            sample_rate,
            channel_count=len(channel_indices),
            target_rate_hz=sample_rate,
            sample_count=sample_count,
        ).astype(np.float32)
        for segment in segments
    ]
    matrix = np.asarray(features, dtype=np.float32)

    # This is not a reported online accuracy.  It is a leakage-free consistency
    # check: the held-out trial is compared only with the other repetitions.
    loo_predictions: list[str] = []
    loo_confidences: list[float] = []
    loo_margins: list[float] = []
    prediction_mode = "mean_multi_trial_template"
    kernel_gamma = 0.0
    kernel_regularization = 0.0
    kernel_coefficients = np.empty((0, len(COMMANDS)), dtype=np.float32)
    model_temperature = 0.08
    fusion_template_weight = 1.0
    fbcca_temperature = 1.0
    if experimental_override:
        # A small kernel ridge head keeps this a trial-retrieval model while
        # allowing all 60 labelled examples to vote non-linearly.  These
        # hyperparameters were selected only from leave-one-trial checks on the
        # current run and must not be described as independent test accuracy.
        prediction_mode = "kernel_ridge_trial_retrieval"
        kernel_gamma = 0.3
        kernel_regularization = 0.003
        model_temperature = 1.0
        fusion_template_weight = 0.975
        fbcca_temperature = 0.02
        truth_indices = np.asarray([COMMANDS.index(label) for label in labels], dtype=int)
        targets = np.eye(len(COMMANDS), dtype=np.float64)[truth_indices]
        squared_distances = np.maximum(0.0, 2.0 - 2.0 * (matrix @ matrix.T))
        kernel = np.exp(-kernel_gamma * squared_distances)
        inverse = np.linalg.inv(
            kernel + kernel_regularization * np.eye(len(matrix), dtype=np.float64)
        )
        coefficients = inverse @ targets
        kernel_coefficients = coefficients.astype(np.float32)
        loo_scores = targets - coefficients / np.diag(inverse)[:, None]
        logits = (loo_scores - np.max(loo_scores, axis=1, keepdims=True)) / model_temperature
        template_probabilities = np.exp(np.clip(logits, -60.0, 0.0))
        template_probabilities /= np.sum(template_probabilities, axis=1, keepdims=True)

        # FBCCA stays a small auxiliary vote instead of a hard consensus gate;
        # hard consensus rejected most windows in this weak run.
        from src.bci.fbcca_decoder import FBCCADecoder

        fbcca = FBCCADecoder(
            channels_1based=tuple(int(index) + 1 for index in channel_indices),
            min_score=0.0,
            min_margin=0.0,
        )
        fbcca_scores = np.zeros((len(segments), len(COMMANDS)), dtype=np.float64)
        for index, segment in enumerate(segments):
            try:
                result = fbcca.decode(segment, sample_rate)
                fbcca_scores[index] = [float(result.scores[label]) for label in COMMANDS]
            except ValueError:
                # Retain a neutral vote for a calibration window whose selected
                # leads all fail the runtime signal-quality preparation.
                pass
        fbcca_logits = (
            fbcca_scores - np.max(fbcca_scores, axis=1, keepdims=True)
        ) / fbcca_temperature
        fbcca_probabilities = np.exp(np.clip(fbcca_logits, -60.0, 0.0))
        fbcca_probabilities /= np.sum(fbcca_probabilities, axis=1, keepdims=True)
        probabilities_matrix = (
            fusion_template_weight * template_probabilities
            + (1.0 - fusion_template_weight) * fbcca_probabilities
        )
        for probabilities in probabilities_matrix:
            order = np.argsort(probabilities)[::-1]
            loo_predictions.append(COMMANDS[int(order[0])])
            loo_confidences.append(float(probabilities[order[0]]))
            loo_margins.append(float(probabilities[order[0]] - probabilities[order[1]]))
    else:
        for held_out, feature in enumerate(matrix):
            similarities = []
            for class_label in COMMANDS:
                indices = [
                    index for index, trial_label in enumerate(labels)
                    if trial_label == class_label and index != held_out
                ]
                similarities.append(float(np.mean(matrix[indices] @ feature)))
            scores = np.asarray(similarities, dtype=np.float64)
            logits = (scores - np.max(scores)) / model_temperature
            probabilities = np.exp(np.clip(logits, -60.0, 0.0))
            probabilities /= np.sum(probabilities)
            order = np.argsort(probabilities)[::-1]
            loo_predictions.append(COMMANDS[int(order[0])])
            loo_confidences.append(float(probabilities[order[0]]))
            loo_margins.append(float(probabilities[order[0]] - probabilities[order[1]]))
    loo_consistency = float(np.mean([
        predicted == truth for predicted, truth in zip(loo_predictions, labels)
    ]))
    if not experimental_override and loo_consistency < MIN_LOO_CONSISTENCY:
        raise ValueError(
            f"多轮 EEG 不可重复：留一试次内部一致性 {loo_consistency * 100:.1f}% "
            f"低于 {MIN_LOO_CONSISTENCY * 100:.0f}%；请先改善电极接触再重录"
        )

    correct_confidences = [
        value for value, predicted, truth in zip(loo_confidences, loo_predictions, labels)
        if predicted == truth
    ]
    correct_margins = [
        value for value, predicted, truth in zip(loo_margins, loo_predictions, labels)
        if predicted == truth
    ]
    if experimental_override:
        # Keep the disposable model usable without accepting every mere
        # first-place ranking.  On the current 60-trial run these middle gates
        # retain roughly half of held-out windows; test/control still requires
        # two consecutive matching decisions.
        confidence_threshold = 0.21
        margin_threshold = 0.015
    else:
        confidence_threshold = max(0.25, float(np.percentile(correct_confidences, 20)))
        margin_threshold = max(0.03, float(np.percentile(correct_margins, 20)))
    model_file = Path(model_path).expanduser().resolve()
    metadata_file = Path(metadata_path).expanduser().resolve()
    model_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        model_file,
        version=np.asarray(MODEL_VERSION),
        features=matrix,
        trial_labels=np.asarray(labels),
        class_labels=np.asarray(COMMANDS),
        frequencies_hz=np.asarray(TARGET_FREQUENCIES_HZ, dtype=np.float32),
        sample_rate_hz=np.asarray(sample_rate, dtype=np.float32),
        sample_count=np.asarray(sample_count, dtype=np.int32),
        channel_names=np.asarray([SSVEP_CHANNEL_NAMES[index] for index in channel_indices]),
        channel_indices=np.asarray(channel_indices, dtype=np.int32),
        stimulus_layout=np.asarray(SSVEP_STIMULUS_LAYOUT),
        calibration_condition=np.asarray(ISOLATED_CALIBRATION_CONDITION),
        confidence_threshold=np.asarray(confidence_threshold, dtype=np.float32),
        margin_threshold=np.asarray(margin_threshold, dtype=np.float32),
        experimental_override=np.asarray(experimental_override, dtype=np.bool_),
        prediction_mode=np.asarray(prediction_mode),
        kernel_coefficients=kernel_coefficients,
        kernel_gamma=np.asarray(kernel_gamma, dtype=np.float32),
        kernel_regularization=np.asarray(kernel_regularization, dtype=np.float32),
        model_temperature=np.asarray(model_temperature, dtype=np.float32),
        fusion_template_weight=np.asarray(fusion_template_weight, dtype=np.float32),
        fbcca_temperature=np.asarray(fbcca_temperature, dtype=np.float32),
    )
    metadata: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "algorithm": (
            "experimental_kernel_trial_retrieval_with_soft_fbcca_fusion"
            if experimental_override else
            "quality_gated_isolated_multi_trial_template_matching_with_fbcca_consensus"
        ),
        "source_run": str(run_root),
        "model_path": str(model_file),
        "labels": list(COMMANDS),
        "target_frequencies_hz": list(TARGET_FREQUENCIES_HZ),
        "channel_names": [SSVEP_CHANNEL_NAMES[index] for index in channel_indices],
        "channel_indices_0based": channel_indices.tolist(),
        "channel_reliability": {
            name: float(ratio) for name, ratio in zip(SSVEP_CHANNEL_NAMES, reliability)
        },
        "sample_rate_hz": sample_rate,
        "sample_count": sample_count,
        "analysis_seconds": sample_count / sample_rate,
        "stimulus_layout": SSVEP_STIMULUS_LAYOUT,
        "calibration_condition": ISOLATED_CALIBRATION_CONDITION,
        "trial_count": len(labels),
        "trials_per_class": {label: counts[label] for label in COMMANDS},
        "source_files": source_files,
        "evaluation": None,
        "accuracy": None,
        "leave_one_trial_consistency": loo_consistency,
        "confidence_threshold": confidence_threshold,
        "margin_threshold": margin_threshold,
        "experimental_override": experimental_override,
        "production_approved": False,
        "prediction_mode": prediction_mode,
        "kernel_gamma": kernel_gamma,
        "kernel_regularization": kernel_regularization,
        "model_temperature": model_temperature,
        "fusion_template_weight": fusion_template_weight,
        "fbcca_temperature": fbcca_temperature,
        "note": (
            "这是按用户要求强制生成的可删除实验模型；留一试次一致性不是在线准确率，"
            "门槛已故意放宽，必须在六目标测试页实测。"
            if experimental_override else
            "留一试次一致性仅用于阻止坏模板启用，不是在线准确率；"
            "全部校准试次用于最终建模，仍需独立六目标测试。"
        ),
    }
    metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


__all__ = ["train_personal_template_model"]

