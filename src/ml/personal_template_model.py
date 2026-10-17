"""Subject-specific, phase-insensitive SSVEP template model.

The model deliberately keeps every calibration trial.  At inference time a
window is compared with all three examples of every command and the three
similarities are averaged.  Features use spectra and autocorrelation rather
than the absolute stimulus phase, because online sliding windows do not start
at the same phase as isolated calibration trials.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.bci.preprocessing import fft_bandpass, remove_linear_drift


MODEL_VERSION = "personal_template_ssvep_v3"
GUIDED_CALIBRATION_CONDITION = "six_targets_all_on_cued_v1"
MIN_RELIABLE_CHANNEL_FRACTION = 0.80
MIN_LOO_CONSISTENCY = 0.50


def _unit(values: np.ndarray) -> np.ndarray:
    flat = np.asarray(values, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(flat))
    if not np.isfinite(norm) or norm <= 1e-12:
        return np.zeros_like(flat)
    return flat / norm


def _resample(data: np.ndarray, source_rate: float, target_rate: float, count: int) -> np.ndarray:
    if data.shape[1] == count and abs(source_rate - target_rate) < 1e-6:
        return data
    source_time = np.arange(data.shape[1], dtype=np.float64) / source_rate
    target_time = np.arange(count, dtype=np.float64) / target_rate
    return np.vstack([
        np.interp(target_time, source_time, channel, left=channel[0], right=channel[-1])
        for channel in data
    ])


def extract_template_feature(
    samples,
    sample_rate_hz: float,
    *,
    channel_count: int = len(SSVEP_CHANNEL_NAMES),
    target_rate_hz: float = 250.0,
    sample_count: int = 500,
) -> np.ndarray:
    """Return one unit-length feature that is insensitive to window phase."""

    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < channel_count or data.shape[1] < 100:
        raise ValueError(f"个人模板输入必须为至少{channel_count}通道、100采样点的 EEG 窗口")
    fs = float(sample_rate_hz)
    if fs <= 0 or not np.isfinite(data[:channel_count]).all():
        raise ValueError("个人模板输入包含无效采样率或 NaN/Inf")
    required_source_samples = max(100, round(sample_count * fs / target_rate_hz))
    if data.shape[1] < required_source_samples:
        raise ValueError(
            f"个人模板需要约 {sample_count / target_rate_hz:g} 秒 EEG，"
            f"当前只有 {data.shape[1] / fs:.2f} 秒"
        )
    segment = data[:channel_count, -required_source_samples:]
    segment = _resample(segment, fs, target_rate_hz, sample_count)
    segment = remove_linear_drift(segment)
    segment = fft_bandpass(segment, target_rate_hz, 6.0, 45.0)
    scale = np.std(segment, axis=1, keepdims=True)
    segment = segment / np.where(scale > 1e-9, scale, 1.0)

    # Log spectrum: target response and the operator/electrode-specific spatial
    # distribution, without requiring equal stimulus phase at inference time.
    tapered = segment * np.hanning(sample_count)[None, :]
    spectrum = np.abs(np.fft.rfft(tapered, axis=1)) ** 2
    bins = np.fft.rfftfreq(sample_count, 1.0 / target_rate_hz)
    band = (bins >= 6.0) & (bins <= 45.0)
    log_spectrum = np.log1p(spectrum[:, band])
    log_spectrum -= np.mean(log_spectrum, axis=1, keepdims=True)
    spectral_feature = _unit(log_spectrum)

    # Autocorrelation retains each trial's repeating waveform shape while also
    # removing its absolute phase.  Lags cover periods from roughly 4–45 Hz.
    autocorrelation = np.fft.irfft(
        np.abs(np.fft.rfft(segment, axis=1)) ** 2,
        n=sample_count,
        axis=1,
    )
    autocorrelation /= np.maximum(np.abs(autocorrelation[:, :1]), 1e-12)
    waveform_feature = _unit(autocorrelation[:, 1:64])

    # Concatenating square-root weights makes cosine similarity equal to the
    # intended weighted combination after the final normalization.
    return _unit(np.concatenate((np.sqrt(0.75) * spectral_feature,
                                 np.sqrt(0.25) * waveform_feature)))


def train_personal_template_model(
    run_directory: Path | str,
    model_path: Path | str,
    metadata_path: Path | str,
    *,
    analysis_seconds: float = 2.0,
) -> dict[str, object]:
    """Build a six-class model from isolated, labelled calibration trials."""

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
        calibration_conditions != {GUIDED_CALIBRATION_CONDITION}
        or visible_target_counts != {len(COMMANDS)}
    ):
        raise ValueError(
            "单目标独闪数据不能泛化到六目标同时闪烁；"
            "请运行“六目标引导校准”后再生成个人模板"
        )

    # Never let a corrupt electrode dominate every template.  A channel must
    # pass the same quality checks in at least 80% of calibration trials before
    # it can be used by the runtime model.
    mask_matrix = np.asarray(quality_masks, dtype=bool)
    reliability = np.mean(mask_matrix, axis=0)
    channel_indices = np.flatnonzero(reliability >= MIN_RELIABLE_CHANNEL_FRACTION)
    if channel_indices.size == 0:
        details = ", ".join(
            f"{name} {ratio * 100:.0f}%"
            for name, ratio in zip(SSVEP_CHANNEL_NAMES, reliability)
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
    for held_out, feature in enumerate(matrix):
        similarities = []
        for class_label in COMMANDS:
            indices = [
                index for index, trial_label in enumerate(labels)
                if trial_label == class_label and index != held_out
            ]
            similarities.append(float(np.mean(matrix[indices] @ feature)))
        scores = np.asarray(similarities, dtype=np.float64)
        logits = (scores - np.max(scores)) / 0.08
        probabilities = np.exp(np.clip(logits, -60.0, 0.0))
        probabilities /= np.sum(probabilities)
        order = np.argsort(probabilities)[::-1]
        loo_predictions.append(COMMANDS[int(order[0])])
        loo_confidences.append(float(probabilities[order[0]]))
        loo_margins.append(float(probabilities[order[0]] - probabilities[order[1]]))
    loo_consistency = float(np.mean([
        predicted == truth for predicted, truth in zip(loo_predictions, labels)
    ]))
    if loo_consistency < MIN_LOO_CONSISTENCY:
        raise ValueError(
            f"三轮 EEG 不可重复：留一试次内部一致性 {loo_consistency * 100:.1f}% "
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
        calibration_condition=np.asarray(GUIDED_CALIBRATION_CONDITION),
        confidence_threshold=np.asarray(confidence_threshold, dtype=np.float32),
        margin_threshold=np.asarray(margin_threshold, dtype=np.float32),
    )
    metadata: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "algorithm": "quality_gated_guided_multi_trial_template_matching",
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
        "calibration_condition": GUIDED_CALIBRATION_CONDITION,
        "trial_count": len(labels),
        "trials_per_class": {label: counts[label] for label in COMMANDS},
        "source_files": source_files,
        "evaluation": None,
        "accuracy": None,
        "leave_one_trial_consistency": loo_consistency,
        "confidence_threshold": confidence_threshold,
        "margin_threshold": margin_threshold,
        "note": (
            "留一试次一致性仅用于阻止坏模板启用，不是在线准确率；"
            "全部校准试次用于最终建模，仍需独立六目标测试。"
        ),
    }
    metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


class PersonalTemplateModel:
    """Load and rank all saved subject-specific templates."""

    def __init__(self, model_path: Path | str, metadata_path: Path | str) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.metadata: dict[str, object] = {}
        self.features: np.ndarray | None = None
        self.trial_labels: tuple[str, ...] = ()
        self.labels: tuple[str, ...] = ()
        self.frequencies_hz: tuple[float, ...] = ()
        self.sample_rate_hz = 0.0
        self.sample_count = 0
        self.channel_indices: tuple[int, ...] = ()
        self.confidence_threshold = 1.0
        self.margin_threshold = 1.0
        self.error = ""
        self.load()

    @property
    def enabled(self) -> bool:
        return self.features is not None and bool(self.labels)

    def load(self) -> bool:
        self.features = None
        if not self.model_path.is_file() or not self.metadata_path.is_file():
            self.error = "缺少个人模板模型；请先完成单目标校准并生成模型"
            return False
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            with np.load(self.model_path, allow_pickle=False) as payload:
                if "stimulus_layout" not in payload.files:
                    raise ValueError("个人模板使用旧的满屏校准布局，不能用于当前角落六目标界面")
                version = str(np.asarray(payload["version"]).item())
                features = np.asarray(payload["features"], dtype=np.float64)
                trial_labels = tuple(str(value) for value in payload["trial_labels"].tolist())
                labels = tuple(str(value) for value in payload["class_labels"].tolist())
                frequencies = tuple(float(value) for value in payload["frequencies_hz"].tolist())
                channel_names = tuple(str(value) for value in payload["channel_names"].tolist())
                channel_indices = tuple(int(value) for value in payload["channel_indices"].tolist())
                sample_rate = float(np.asarray(payload["sample_rate_hz"]).item())
                sample_count = int(np.asarray(payload["sample_count"]).item())
                stimulus_layout = str(np.asarray(payload["stimulus_layout"]).item())
                calibration_condition = str(np.asarray(payload["calibration_condition"]).item())
                confidence_threshold = float(np.asarray(payload["confidence_threshold"]).item())
                margin_threshold = float(np.asarray(payload["margin_threshold"]).item())
            if version != MODEL_VERSION or metadata.get("model_version") != MODEL_VERSION:
                raise ValueError("个人模板版本不匹配")
            if labels != tuple(COMMANDS) or frequencies != tuple(TARGET_FREQUENCIES_HZ):
                raise ValueError("个人模板的六类标签或频率与当前刺激界面不一致")
            if not channel_indices or any(
                index < 0 or index >= len(SSVEP_CHANNEL_NAMES) for index in channel_indices
            ):
                raise ValueError("个人模板没有有效的稳定通道索引")
            expected_names = tuple(SSVEP_CHANNEL_NAMES[index] for index in channel_indices)
            if channel_names != expected_names:
                raise ValueError("个人模板的稳定通道名称与索引不一致")
            if (
                stimulus_layout != SSVEP_STIMULUS_LAYOUT
                or metadata.get("stimulus_layout") != SSVEP_STIMULUS_LAYOUT
            ):
                raise ValueError("个人模板使用旧刺激布局，不能用于当前角落六目标界面")
            if (
                calibration_condition != GUIDED_CALIBRATION_CONDITION
                or metadata.get("calibration_condition") != GUIDED_CALIBRATION_CONDITION
            ):
                raise ValueError("个人模板不是在六目标同时闪烁条件下采集")
            if features.ndim != 2 or features.shape[0] != len(trial_labels):
                raise ValueError("个人模板特征文件损坏")
            if any(label not in labels for label in trial_labels):
                raise ValueError("个人模板中存在未知标签")
            if any(trial_labels.count(label) < 1 for label in labels):
                raise ValueError("个人模板没有覆盖全部六类指令")
            self.metadata = metadata
            self.features = features
            self.trial_labels = trial_labels
            self.labels = labels
            self.frequencies_hz = frequencies
            self.sample_rate_hz = sample_rate
            self.sample_count = sample_count
            self.channel_indices = channel_indices
            self.confidence_threshold = confidence_threshold
            self.margin_threshold = margin_threshold
            self.error = ""
            return True
        except Exception as exc:
            self.error = f"个人模板加载失败：{exc}"
            self.features = None
            return False

    def predict(self, samples, sample_rate_hz: float) -> dict[str, object]:
        if not self.enabled or self.features is None:
            raise RuntimeError(self.error or "个人模板不可用")
        data = np.asarray(samples, dtype=np.float64)
        if data.ndim != 2 or data.shape[0] <= max(self.channel_indices):
            raise ValueError("在线 EEG 通道数不足，无法选择个人模板稳定通道")
        feature = extract_template_feature(
            data[list(self.channel_indices)],
            sample_rate_hz,
            channel_count=len(self.channel_indices),
            target_rate_hz=self.sample_rate_hz,
            sample_count=self.sample_count,
        )
        trial_similarities = self.features @ feature
        class_similarities = np.asarray([
            float(np.mean(trial_similarities[
                np.asarray([trial_label == label for trial_label in self.trial_labels])
            ]))
            for label in self.labels
        ])
        # Softmax is used only to display relative ranking.  It is explicitly
        # not a calibrated probability or an accuracy estimate.
        temperature = 0.08
        logits = (class_similarities - np.max(class_similarities)) / temperature
        probabilities = np.exp(np.clip(logits, -60.0, 0.0))
        probabilities /= np.sum(probabilities)
        order = np.argsort(probabilities)[::-1]
        best, second = int(order[0]), int(order[1])
        return {
            "label": self.labels[best],
            "frequency_hz": self.frequencies_hz[best],
            "confidence": float(probabilities[best]),
            "margin": float(probabilities[best] - probabilities[second]),
            "scores": {label: float(probabilities[index]) for index, label in enumerate(self.labels)},
            "raw_similarities": {
                label: float(class_similarities[index]) for index, label in enumerate(self.labels)
            },
            "sample_count": self.sample_count,
            "accepted": bool(
                probabilities[best] >= self.confidence_threshold
                and probabilities[best] - probabilities[second] >= self.margin_threshold
            ),
        }


__all__ = [
    "MODEL_VERSION",
    "GUIDED_CALIBRATION_CONDITION",
    "PersonalTemplateModel",
    "extract_template_feature",
    "train_personal_template_model",
]
