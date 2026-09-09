"""Timing, measurement and persistence for SSVEP visual stimulation."""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class StimulusTarget:
    label: str
    frequency_hz: float
    phase_radians: float = 0.0


@dataclass(frozen=True)
class FrequencyMeasurement:
    target_hz: float
    measured_hz: float
    error_hz: float
    refresh_rate_hz: float
    frame_jitter_ms: float
    sample_count: int


def luminance_at(elapsed_seconds: float, frequency_hz: float, phase_radians: float = 0.0) -> float:
    """Return sinusoidal luminance in the inclusive range 0..1."""

    value = 0.5 + 0.5 * math.sin(2.0 * math.pi * frequency_hz * elapsed_seconds + phase_radians)
    return max(0.0, min(1.0, value))


def measure_frequency(
    frame_times: Sequence[float], luminance: Sequence[float], target_hz: float,
) -> tuple[FrequencyMeasurement, np.ndarray, np.ndarray]:
    """Estimate the rendered frequency from non-uniform frame timestamps."""

    count = min(len(frame_times), len(luminance))
    if count < 16:
        raise ValueError("至少需要 16 帧才能测量刺激频率")
    times = np.asarray(frame_times[:count], dtype=float)
    values = np.asarray(luminance[:count], dtype=float)
    valid = np.isfinite(times) & np.isfinite(values)
    times, values = times[valid], values[valid]
    order = np.argsort(times)
    times, values = times[order], values[order]
    keep = np.ones(len(times), dtype=bool)
    keep[1:] = np.diff(times) > 1e-6
    times, values = times[keep], values[keep]
    if len(times) < 16 or times[-1] - times[0] < 0.25:
        raise ValueError("刺激记录时间过短，无法测频")
    intervals = np.diff(times)
    median_interval = float(np.median(intervals))
    stable = intervals[(intervals > median_interval * 0.5) & (intervals < median_interval * 1.5)]
    if len(stable) < 3:
        stable = intervals
    refresh_rate = 1.0 / float(np.median(stable))
    jitter_ms = float(np.std(stable) * 1000.0)

    uniform_times = np.arange(times[0], times[-1], 1.0 / refresh_rate)
    if len(uniform_times) < 16:
        raise ValueError("刺激重采样点不足")
    uniform_values = np.interp(uniform_times, times, values)
    uniform_values -= np.mean(uniform_values)
    spectrum = np.abs(np.fft.rfft(uniform_values)) ** 2
    frequencies = np.fft.rfftfreq(len(uniform_values), d=1.0 / refresh_rate)
    band = (frequencies >= 5.0) & (frequencies <= 30.0)
    if not np.any(band):
        raise ValueError("刺激频谱不包含 5-30Hz")
    band_indices = np.flatnonzero(band)
    peak_index = int(band_indices[int(np.argmax(spectrum[band]))])
    measured_hz = float(frequencies[peak_index])
    measurement = FrequencyMeasurement(
        target_hz=float(target_hz),
        measured_hz=measured_hz,
        error_hz=abs(measured_hz - float(target_hz)),
        refresh_rate_hz=refresh_rate,
        frame_jitter_ms=jitter_ms,
        sample_count=len(uniform_values),
    )
    return measurement, frequencies, spectrum


class StimulusSessionRecorder:
    """Save frame timing, measured spectra and trial markers for audit."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        targets: Sequence[StimulusTarget],
        frame_times: Sequence[float],
        luminance_by_target: Sequence[Sequence[float]],
        true_label: str,
        started_at_unix: float,
    ) -> dict[str, object]:
        session_id = f"stim-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        measurements: list[dict[str, float | int]] = []
        spectra: dict[str, np.ndarray] = {}
        for index, target in enumerate(targets):
            measurement, frequencies, power = measure_frequency(
                frame_times, luminance_by_target[index], target.frequency_hz,
            )
            measurements.append(
                {
                    "label": target.label,
                    "target_hz": measurement.target_hz,
                    "measured_hz": measurement.measured_hz,
                    "error_hz": measurement.error_hz,
                    "refresh_rate_hz": measurement.refresh_rate_hz,
                    "frame_jitter_ms": measurement.frame_jitter_ms,
                    "sample_count": measurement.sample_count,
                }
            )
            spectra[f"frequency_{index}"] = frequencies.astype(np.float32)
            spectra[f"power_{index}"] = power.astype(np.float32)

        npz_path = self.root / f"{session_id}.npz"
        np.savez_compressed(
            npz_path,
            frame_times=np.asarray(frame_times, dtype=np.float64),
            luminance=np.asarray(luminance_by_target, dtype=np.float32),
            **spectra,
        )
        payload = {
            "session_id": session_id,
            "started_at_unix": float(started_at_unix),
            "finished_at_unix": time.time(),
            "true_label": true_label,
            "frame_count": len(frame_times),
            "measurements": measurements,
            "raw_timing_path": npz_path.name,
        }
        json_path = self.root / f"{session_id}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["metadata_path"] = str(json_path)
        payload["raw_timing_path"] = str(npz_path)
        return payload
