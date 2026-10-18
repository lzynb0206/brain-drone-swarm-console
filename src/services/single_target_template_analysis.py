"""Offline subject-specific template evaluation for isolated target trials."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.signal import butter, detrend, sosfiltfilt

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ, WINDOW_SECONDS


def _unit(values: np.ndarray) -> np.ndarray:
    flat = np.asarray(values, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(flat))
    return flat / norm if np.isfinite(norm) and norm > 1e-12 else np.zeros_like(flat)


def _representation(
    eeg: np.ndarray,
    sample_rate_hz: float,
    discard_seconds: float,
    analysis_seconds: float = WINDOW_SECONDS,
) -> np.ndarray:
    data = np.asarray(eeg, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] == 0:
        raise ValueError("EEG 必须为 channels × samples")
    fs = float(sample_rate_hz)
    start = max(0, round(float(discard_seconds) * fs))
    count = max(64, round(float(analysis_seconds) * fs))
    segment = data[:, start:start + count]
    if segment.shape[1] < count:
        raise ValueError("单目标试次长度不足")
    segment = detrend(segment, axis=1, type="linear")
    high = min(48.0, fs * 0.45)
    if high > 7.0:
        sos = butter(4, (6.0, high), btype="bandpass", fs=fs, output="sos")
        segment = sosfiltfilt(sos, segment, axis=1)
    scale = np.std(segment, axis=1, keepdims=True)
    segment = segment / np.where(scale > 1e-9, scale, 1.0)

    # Preserve the phase-locked waveform and add a phase-insensitive spectral
    # signature.  Each part is normalized independently so neither dominates.
    time_feature = _unit(segment)
    window = np.hanning(segment.shape[1])[None, :]
    spectrum = np.abs(np.fft.rfft(segment * window, axis=1))
    bins = np.fft.rfftfreq(segment.shape[1], 1.0 / fs)
    spectral_values: list[float] = []
    for channel in spectrum:
        for frequency in TARGET_FREQUENCIES_HZ:
            for harmonic in (1.0, 2.0):
                target = float(frequency) * harmonic
                if target <= bins[-1]:
                    spectral_values.append(float(np.interp(target, bins, channel)))
                else:
                    spectral_values.append(0.0)
    spectral_feature = _unit(np.log1p(np.asarray(spectral_values)))
    return np.concatenate((time_feature, spectral_feature))


def evaluate_single_target_run(run_directory: Path | str) -> dict[str, object]:
    """Leave-one-trial-out template matching without quality-gate rejection."""

    root = Path(run_directory).expanduser().resolve()
    files = sorted(root.glob("trial-*.npz"))
    if not files:
        raise ValueError("该次单目标采集没有找到试次文件")
    records: list[dict[str, object]] = []
    for path in files:
        with np.load(path, allow_pickle=False) as payload:
            label = str(np.asarray(payload["true_label"]).item())
            if label not in COMMANDS:
                continue
            fs = float(np.asarray(payload["fs"]).item())
            discard = float(np.asarray(payload["discard_seconds"]).item())
            feature = _representation(payload["eeg"], fs, discard)
        records.append({"path": path.name, "label": label, "feature": feature})
    counts = Counter(str(item["label"]) for item in records)
    missing = [label for label in COMMANDS if counts[label] < 2]
    if missing:
        raise ValueError(f"以下指令至少需要2轮才能做留一模板评估：{', '.join(missing)}")

    confusion = {label: {candidate: 0 for candidate in COMMANDS} for label in COMMANDS}
    details: list[dict[str, object]] = []
    for index, record in enumerate(records):
        truth = str(record["label"])
        feature = np.asarray(record["feature"])
        scores: dict[str, float] = {}
        for candidate in COMMANDS:
            members = [
                np.asarray(other["feature"])
                for other_index, other in enumerate(records)
                if str(other["label"]) == candidate and other_index != index
            ]
            template = _unit(np.mean(members, axis=0))
            scores[candidate] = float(np.dot(_unit(feature), template))
        ordered = sorted(scores, key=scores.get, reverse=True)
        prediction = ordered[0]
        confusion[truth][prediction] += 1
        details.append({
            "file": str(record["path"]),
            "true_label": truth,
            "prediction": prediction,
            "correct": prediction == truth,
            "score": scores[prediction],
            "margin": scores[prediction] - scores[ordered[1]],
            "scores": scores,
        })
    correct = sum(bool(item["correct"]) for item in details)
    per_class = {
        label: {
            "trials": counts[label],
            "correct": confusion[label][label],
            "accuracy": confusion[label][label] / counts[label],
        }
        for label in COMMANDS
    }
    report: dict[str, object] = {
        "algorithm": "subject_spectral_time_template_v1",
        "evaluation": "leave-one-trial-out",
        "trial_count": len(details),
        "accuracy": correct / len(details),
        "correct": correct,
        "class_counts": dict(counts),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "details": details,
        "warning": "这是单目标校准内部验证，不等于六目标同时闪烁时的在线准确率。",
    }
    (root / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report


__all__ = ["evaluate_single_target_run"]
