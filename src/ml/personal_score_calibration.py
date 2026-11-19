"""Validated NumPy runtime for the personal dual-FBCCA score calibrator."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SAMPLE_RATE_HZ,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.ml.dual_fbcca_features import extract_dual_fbcca_features
from src.ml.personal_score_contract import FEATURE_NAMES, MODEL_VERSION


class PersonalScoreCalibrationModel:
    """Load StandardScaler + multinomial logistic-regression parameters."""

    def __init__(
        self,
        model_path: Path | str,
        metadata_path: Path | str,
        *,
        algorithm_name: str = "个人校准融合",
    ) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.algorithm_name = str(algorithm_name)
        self.metadata: dict[str, object] = {}
        self.scaler_mean = np.empty(0, dtype=np.float64)
        self.scaler_scale = np.empty(0, dtype=np.float64)
        self.coefficients = np.empty((0, 0), dtype=np.float64)
        self.intercept = np.empty(0, dtype=np.float64)
        self.sample_rate_hz = 0.0
        self.sample_count = 0
        self.confidence_threshold = 1.0
        self.margin_threshold = 1.0
        self.error = ""
        self.load()

    @property
    def enabled(self) -> bool:
        return self.coefficients.shape == (len(COMMANDS), len(FEATURE_NAMES))

    @property
    def production_approved(self) -> bool:
        return bool(self.metadata.get("production_approved", False))

    def load(self) -> bool:
        self.coefficients = np.empty((0, 0), dtype=np.float64)
        if not self.model_path.is_file() or not self.metadata_path.is_file():
            self.error = f"缺少{self.algorithm_name}模型；请先用六目标随机带标签数据训练"
            return False
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            with np.load(self.model_path, allow_pickle=False) as payload:
                version = str(np.asarray(payload["version"]).item())
                labels = tuple(str(value) for value in payload["class_labels"].tolist())
                frequencies = tuple(float(value) for value in payload["frequencies_hz"].tolist())
                channel_names = tuple(str(value) for value in payload["channel_names"].tolist())
                feature_names = tuple(str(value) for value in payload["feature_names"].tolist())
                sample_rate = float(np.asarray(payload["sample_rate_hz"]).item())
                sample_count = int(np.asarray(payload["sample_count"]).item())
                layout = str(np.asarray(payload["stimulus_layout"]).item())
                scaler_mean = np.asarray(payload["scaler_mean"], dtype=np.float64)
                scaler_scale = np.asarray(payload["scaler_scale"], dtype=np.float64)
                coefficients = np.asarray(payload["coefficients"], dtype=np.float64)
                intercept = np.asarray(payload["intercept"], dtype=np.float64)
                confidence = float(np.asarray(payload["confidence_threshold"]).item())
                margin = float(np.asarray(payload["margin_threshold"]).item())
            if version != MODEL_VERSION or metadata.get("model_version") != MODEL_VERSION:
                raise ValueError("个人校准融合模型版本不匹配")
            if labels != tuple(COMMANDS) or frequencies != tuple(TARGET_FREQUENCIES_HZ):
                raise ValueError("模型六类标签/频率与当前刺激界面不一致")
            if channel_names != tuple(SSVEP_CHANNEL_NAMES):
                raise ValueError("模型电极顺序不是 O1/Oz/O2/PO3/POz/PO4/P3/P4")
            if feature_names != FEATURE_NAMES:
                raise ValueError("模型的双证据特征顺序不一致")
            if layout != SSVEP_STIMULUS_LAYOUT:
                raise ValueError("模型刺激布局与当前六目标界面不一致")
            if abs(sample_rate - float(SAMPLE_RATE_HZ)) > 1e-6:
                raise ValueError("模型采样率与当前系统配置不一致")
            if scaler_mean.shape != (len(FEATURE_NAMES),) or scaler_scale.shape != scaler_mean.shape:
                raise ValueError("模型标准化参数损坏")
            if coefficients.shape != (len(COMMANDS), len(FEATURE_NAMES)):
                raise ValueError("模型逻辑回归系数损坏")
            if intercept.shape != (len(COMMANDS),):
                raise ValueError("模型逻辑回归截距损坏")
            if not np.isfinite(np.concatenate((scaler_mean, scaler_scale, coefficients.ravel(), intercept))).all():
                raise ValueError("模型参数含 NaN/Inf")
            if np.any(scaler_scale <= 0.0) or sample_count <= 0:
                raise ValueError("模型缩放尺度或窗口长度无效")
            self.metadata = metadata
            self.scaler_mean = scaler_mean
            self.scaler_scale = scaler_scale
            self.coefficients = coefficients
            self.intercept = intercept
            self.sample_rate_hz = sample_rate
            self.sample_count = sample_count
            self.confidence_threshold = confidence
            self.margin_threshold = margin
            self.error = ""
            return True
        except Exception as exc:
            self.error = f"{self.algorithm_name}模型加载失败：{exc}"
            self.coefficients = np.empty((0, 0), dtype=np.float64)
            return False

    def predict(self, samples, sample_rate_hz: float) -> dict[str, object]:
        if not self.enabled:
            raise RuntimeError(self.error or f"{self.algorithm_name}模型不可用")
        if abs(float(sample_rate_hz) - self.sample_rate_hz) > 1.0:
            raise ValueError(
                f"个人校准模型需要 {self.sample_rate_hz:g} Hz，当前为 {float(sample_rate_hz):g} Hz"
            )
        feature_result = extract_dual_fbcca_features(
            samples,
            sample_rate_hz,
            sample_count=self.sample_count,
            channel_count=len(SSVEP_CHANNEL_NAMES),
        )
        standardized = (feature_result.values - self.scaler_mean) / self.scaler_scale
        logits = self.coefficients @ standardized + self.intercept
        logits -= np.max(logits)
        probabilities = np.exp(np.clip(logits, -60.0, 0.0))
        probabilities /= np.sum(probabilities)
        order = np.argsort(probabilities)[::-1]
        best, second = int(order[0]), int(order[1])
        return {
            "label": COMMANDS[best],
            "frequency_hz": float(TARGET_FREQUENCIES_HZ[best]),
            "confidence": float(probabilities[best]),
            "margin": float(probabilities[best] - probabilities[second]),
            "scores": {label: float(probabilities[index]) for index, label in enumerate(COMMANDS)},
            "standard50_scores": {
                label: float(feature_result.standard50_scores[index])
                for index, label in enumerate(COMMANDS)
            },
            "equal46_scores": {
                label: float(feature_result.equal46_scores[index])
                for index, label in enumerate(COMMANDS)
            },
            "features": feature_result.values,
            "sample_count": self.sample_count,
        }


__all__ = ["PersonalScoreCalibrationModel"]
