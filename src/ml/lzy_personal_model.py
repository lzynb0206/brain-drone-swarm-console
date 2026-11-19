"""Runtime model for the subject-specific ``个人算法-Lzy`` decoder.

The feature extractor uses the complete five-second recording through three
overlapping four-second FBCCA windows.  The persisted personal parameters are
small per-frequency non-target baselines learned from labelled active trials;
the runtime deliberately has no scikit-learn dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    SAMPLE_RATE_HZ,
    SSVEP_CHANNEL_NAMES,
    SSVEP_STIMULUS_LAYOUT,
    TARGET_FREQUENCIES_HZ,
)
from src.bci.fbcca_decoder import _canonical_correlation
from src.bci.preprocessing import fft_bandpass, notch_mains, remove_linear_drift


MODEL_VERSION = "lzy_segment_fbcca_calibration_v1"
ALGORITHM_NAME = "个人算法-Lzy"
REQUIRED_WINDOW_SECONDS = 5.0
SEGMENT_SECONDS = 4.0
SEGMENT_HOP_SECONDS = 0.5
FILTER_BANK_LOW_EDGES_HZ = (6.0, 14.0, 22.0)
FILTER_BANK_HIGH_EDGE_HZ = 46.0
CALIBRATION_ALPHA = 2.0


@dataclass(frozen=True)
class LzyFeatureResult:
    values: np.ndarray
    segment_scores: np.ndarray
    segment_starts_seconds: tuple[float, ...]


def _reference_signals(sample_rate_hz: float, sample_count: int) -> tuple[np.ndarray, ...]:
    timeline = np.arange(sample_count, dtype=np.float64) / float(sample_rate_hz)
    references = []
    for frequency in TARGET_FREQUENCIES_HZ:
        rows = []
        for harmonic in range(1, 5):
            if harmonic * float(frequency) > FILTER_BANK_HIGH_EDGE_HZ:
                break
            phase = 2.0 * np.pi * harmonic * float(frequency) * timeline
            rows.extend((np.sin(phase), np.cos(phase)))
        references.append(np.asarray(rows, dtype=np.float64))
    return tuple(references)


def _score_segment(data: np.ndarray, sample_rate_hz: float) -> np.ndarray:
    references = _reference_signals(sample_rate_hz, data.shape[1])
    scores = np.zeros(len(COMMANDS), dtype=np.float64)
    for bank_index, low_hz in enumerate(FILTER_BANK_LOW_EDGES_HZ, start=1):
        filtered = fft_bandpass(
            data, sample_rate_hz, low_hz, FILTER_BANK_HIGH_EDGE_HZ,
        )
        weight = bank_index ** -1.25 + 0.25
        for class_index, reference in enumerate(references):
            correlation = _canonical_correlation(filtered, reference)
            scores[class_index] += weight * correlation * correlation
    return scores


def extract_lzy_fbcca_features(
    samples,
    sample_rate_hz: float,
    *,
    sample_count: int,
    channel_count: int = 8,
) -> LzyFeatureResult:
    """Return median FBCCA evidence from three overlapping four-second views."""

    fs = float(sample_rate_hz)
    data = np.asarray(samples, dtype=np.float64)
    if fs <= 0.0:
        raise ValueError("EEG sample rate must be positive")
    if data.ndim != 2 or data.shape[0] < int(channel_count):
        raise ValueError(f"个人算法-Lzy需要前 {channel_count} 个枕区 EEG 通道")
    if data.shape[1] < int(sample_count):
        seconds = int(sample_count) / fs
        raise ValueError(f"个人算法-Lzy需要完整 {seconds:g} 秒 EEG 窗口")
    data = data[:int(channel_count), -int(sample_count):].copy()
    if not np.isfinite(data).all():
        raise ValueError("EEG 含 NaN/Inf，不能提取 Lzy 个人特征")
    data = remove_linear_drift(data)
    data = notch_mains(data, fs, 50.0, 30.0)
    scale = data.std(axis=1, keepdims=True)
    if np.any(scale[:, 0] <= 1e-12):
        raise ValueError("EEG 存在平坦通道，不能提取 Lzy 个人特征")
    data /= scale

    segment_count = int(round(SEGMENT_SECONDS * fs))
    hop_count = max(1, int(round(SEGMENT_HOP_SECONDS * fs)))
    if segment_count > data.shape[1]:
        raise ValueError("Lzy FBCCA 子窗长度超过完整分析窗")
    last_start = data.shape[1] - segment_count
    starts = list(range(0, last_start + 1, hop_count))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    segment_scores = np.stack([
        _score_segment(data[:, start:start + segment_count], fs)
        for start in starts
    ])
    values = np.median(segment_scores, axis=0)
    return LzyFeatureResult(
        values=values,
        segment_scores=segment_scores,
        segment_starts_seconds=tuple(float(start) / fs for start in starts),
    )


class LzyPersonalModel:
    """Load and run the compact personal non-target score calibrator."""

    def __init__(self, model_path: Path | str, metadata_path: Path | str) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.metadata: dict[str, object] = {}
        self.nontarget_center = np.empty(0, dtype=np.float64)
        self.nontarget_scale = np.empty(0, dtype=np.float64)
        self.alpha = CALIBRATION_ALPHA
        self.sample_rate_hz = 0.0
        self.sample_count = 0
        self.error = ""
        self.load()

    @property
    def enabled(self) -> bool:
        return (
            self.nontarget_center.shape == (len(COMMANDS),)
            and self.nontarget_scale.shape == (len(COMMANDS),)
        )

    @property
    def production_approved(self) -> bool:
        return bool(self.metadata.get("production_approved", False))

    def load(self) -> bool:
        self.nontarget_center = np.empty(0, dtype=np.float64)
        self.nontarget_scale = np.empty(0, dtype=np.float64)
        if not self.model_path.is_file() or not self.metadata_path.is_file():
            self.error = "缺少个人算法-Lzy模型；请先完成本人带标签数据训练"
            return False
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            with np.load(self.model_path, allow_pickle=False) as payload:
                version = str(np.asarray(payload["version"]).item())
                labels = tuple(str(value) for value in payload["class_labels"].tolist())
                frequencies = tuple(float(value) for value in payload["frequencies_hz"].tolist())
                channel_names = tuple(str(value) for value in payload["channel_names"].tolist())
                layout = str(np.asarray(payload["stimulus_layout"]).item())
                sample_rate = float(np.asarray(payload["sample_rate_hz"]).item())
                sample_count = int(np.asarray(payload["sample_count"]).item())
                center = np.asarray(payload["nontarget_center"], dtype=np.float64)
                scale = np.asarray(payload["nontarget_scale"], dtype=np.float64)
                alpha = float(np.asarray(payload["calibration_alpha"]).item())
            if version != MODEL_VERSION or metadata.get("model_version") != MODEL_VERSION:
                raise ValueError("Lzy个人模型版本不匹配")
            if labels != tuple(COMMANDS) or frequencies != tuple(TARGET_FREQUENCIES_HZ):
                raise ValueError("Lzy模型六类标签/频率与当前刺激界面不一致")
            if channel_names != tuple(SSVEP_CHANNEL_NAMES):
                raise ValueError("Lzy模型电极顺序不是 O1/Oz/O2/PO3/POz/PO4/P3/P4")
            if layout != SSVEP_STIMULUS_LAYOUT:
                raise ValueError("Lzy模型刺激布局与当前六目标界面不一致")
            if abs(sample_rate - float(SAMPLE_RATE_HZ)) > 1e-6:
                raise ValueError("Lzy模型采样率与当前系统配置不一致")
            if center.shape != (len(COMMANDS),) or scale.shape != center.shape:
                raise ValueError("Lzy模型的非目标校准参数损坏")
            if not np.isfinite(np.concatenate((center, scale, [alpha]))).all():
                raise ValueError("Lzy模型参数含 NaN/Inf")
            if np.any(scale <= 0.0) or sample_count <= 0 or alpha < 0.0:
                raise ValueError("Lzy模型缩放尺度、窗口长度或校准系数无效")
            self.metadata = metadata
            self.nontarget_center = center
            self.nontarget_scale = scale
            self.alpha = alpha
            self.sample_rate_hz = sample_rate
            self.sample_count = sample_count
            self.error = ""
            return True
        except Exception as exc:
            self.error = f"个人算法-Lzy模型加载失败：{exc}"
            self.nontarget_center = np.empty(0, dtype=np.float64)
            self.nontarget_scale = np.empty(0, dtype=np.float64)
            return False

    def predict(self, samples, sample_rate_hz: float) -> dict[str, object]:
        if not self.enabled:
            raise RuntimeError(self.error or "个人算法-Lzy模型不可用")
        if abs(float(sample_rate_hz) - self.sample_rate_hz) > 1.0:
            raise ValueError(
                f"Lzy个人模型需要 {self.sample_rate_hz:g} Hz，当前为 {float(sample_rate_hz):g} Hz"
            )
        features = extract_lzy_fbcca_features(
            samples,
            sample_rate_hz,
            sample_count=self.sample_count,
            channel_count=len(SSVEP_CHANNEL_NAMES),
        )
        evidence = (
            features.values - self.alpha * self.nontarget_center
        ) / self.nontarget_scale
        stabilized = evidence - float(np.max(evidence))
        probabilities = np.exp(np.clip(stabilized, -60.0, 0.0))
        probabilities /= float(np.sum(probabilities))
        order = np.argsort(evidence)[::-1]
        best, second = int(order[0]), int(order[1])
        return {
            "label": COMMANDS[best],
            "frequency_hz": float(TARGET_FREQUENCIES_HZ[best]),
            "confidence": float(probabilities[best]),
            "margin": float(probabilities[best] - probabilities[second]),
            "scores": {
                label: float(probabilities[index]) for index, label in enumerate(COMMANDS)
            },
            "raw_fbcca_scores": {
                label: float(features.values[index]) for index, label in enumerate(COMMANDS)
            },
            "calibrated_evidence": {
                label: float(evidence[index]) for index, label in enumerate(COMMANDS)
            },
            "segment_scores": features.segment_scores,
            "segment_starts_seconds": features.segment_starts_seconds,
            "sample_count": self.sample_count,
        }


__all__ = [
    "ALGORITHM_NAME",
    "CALIBRATION_ALPHA",
    "FILTER_BANK_HIGH_EDGE_HZ",
    "FILTER_BANK_LOW_EDGES_HZ",
    "LzyFeatureResult",
    "LzyPersonalModel",
    "MODEL_VERSION",
    "REQUIRED_WINDOW_SECONDS",
    "SEGMENT_HOP_SECONDS",
    "SEGMENT_SECONDS",
    "extract_lzy_fbcca_features",
]
