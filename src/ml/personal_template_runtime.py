"""Validated loading and online prediction for personal SSVEP templates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.ml.personal_template_contract import (
    ISOLATED_CALIBRATION_CONDITION,
    MODEL_VERSION,
)
from src.ml.personal_template_features import extract_template_feature


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
        self.experimental_override = False
        self.prediction_mode = "mean_multi_trial_template"
        self.kernel_coefficients = np.empty((0, 0), dtype=np.float64)
        self.kernel_gamma = 0.0
        self.model_temperature = 0.08
        self.fusion_template_weight = 1.0
        self.fbcca_temperature = 1.0
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
                experimental_override = bool(
                    np.asarray(payload["experimental_override"]).item()
                ) if "experimental_override" in payload.files else False
                prediction_mode = str(
                    np.asarray(payload["prediction_mode"]).item()
                ) if "prediction_mode" in payload.files else "mean_multi_trial_template"
                kernel_coefficients = np.asarray(
                    payload["kernel_coefficients"], dtype=np.float64,
                ) if "kernel_coefficients" in payload.files else np.empty((0, 0))
                kernel_gamma = float(
                    np.asarray(payload["kernel_gamma"]).item()
                ) if "kernel_gamma" in payload.files else 0.0
                model_temperature = float(
                    np.asarray(payload["model_temperature"]).item()
                ) if "model_temperature" in payload.files else 0.08
                fusion_template_weight = float(
                    np.asarray(payload["fusion_template_weight"]).item()
                ) if "fusion_template_weight" in payload.files else 1.0
                fbcca_temperature = float(
                    np.asarray(payload["fbcca_temperature"]).item()
                ) if "fbcca_temperature" in payload.files else 1.0
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
                calibration_condition != ISOLATED_CALIBRATION_CONDITION
                or metadata.get("calibration_condition") != ISOLATED_CALIBRATION_CONDITION
            ):
                raise ValueError("个人模板不是在同位置单目标独闪条件下采集")
            if features.ndim != 2 or features.shape[0] != len(trial_labels):
                raise ValueError("个人模板特征文件损坏")
            if any(label not in labels for label in trial_labels):
                raise ValueError("个人模板中存在未知标签")
            if any(trial_labels.count(label) < 1 for label in labels):
                raise ValueError("个人模板没有覆盖全部六类指令")
            if prediction_mode == "kernel_ridge_trial_retrieval" and (
                kernel_coefficients.shape != (len(trial_labels), len(labels))
                or kernel_gamma <= 0.0
            ):
                raise ValueError("实验核检索模型参数损坏")
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
            self.experimental_override = experimental_override
            self.prediction_mode = prediction_mode
            self.kernel_coefficients = kernel_coefficients
            self.kernel_gamma = kernel_gamma
            self.model_temperature = model_temperature
            self.fusion_template_weight = fusion_template_weight
            self.fbcca_temperature = fbcca_temperature
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
        if self.prediction_mode == "kernel_ridge_trial_retrieval":
            squared_distances = np.maximum(0.0, 2.0 - 2.0 * trial_similarities)
            kernel_votes = np.exp(-self.kernel_gamma * squared_distances)
            class_similarities = kernel_votes @ self.kernel_coefficients
        else:
            class_similarities = np.asarray([
                float(np.mean(trial_similarities[
                    np.asarray([trial_label == label for trial_label in self.trial_labels])
                ]))
                for label in self.labels
            ])
        # Softmax is used only to display relative ranking.  It is explicitly
        # not a calibrated probability or an accuracy estimate.
        temperature = self.model_temperature
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


__all__ = ["PersonalTemplateModel"]

