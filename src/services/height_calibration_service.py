"""Reviewable per-aircraft Lighthouse height calibration."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.domain import UAVTelemetry


@dataclass(frozen=True)
class HeightCalibrationResult:
    uav_id: str
    valid_samples: int
    median_height_m: float | None
    standard_deviation_m: float | None
    suggested_offset_m: float | None
    accepted: bool


class HeightCalibrationService:
    """Calculate offsets from a stable hover without applying them automatically."""

    def __init__(
        self,
        output_dir: Path | str,
        target_height_m: float = 0.5,
        minimum_samples: int = 18,
        maximum_std_m: float = 0.02,
        maximum_offset_m: float = 0.10,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.target_height_m = float(target_height_m)
        self.minimum_samples = int(minimum_samples)
        self.maximum_std_m = float(maximum_std_m)
        self.maximum_offset_m = float(maximum_offset_m)
        self._samples: dict[str, list[float]] = {}

    def reset(self) -> None:
        self._samples.clear()

    def add_telemetry(self, telemetry: Iterable[UAVTelemetry]) -> None:
        for item in telemetry:
            if item.positioning_ok and math.isfinite(item.altitude_m):
                self._samples.setdefault(item.uav_id, []).append(float(item.altitude_m))

    def finish(self) -> tuple[dict[str, HeightCalibrationResult], Path]:
        if not self._samples:
            raise ValueError("没有可用的 Lighthouse 高度样本")
        results: dict[str, HeightCalibrationResult] = {}
        for uav_id, values in self._samples.items():
            if len(values) < self.minimum_samples:
                results[uav_id] = HeightCalibrationResult(
                    uav_id, len(values), None, None, None, False,
                )
                continue
            median = float(statistics.median(values))
            deviation = float(statistics.pstdev(values))
            offset = self.target_height_m - median
            accepted = deviation <= self.maximum_std_m and abs(offset) <= self.maximum_offset_m
            results[uav_id] = HeightCalibrationResult(
                uav_id, len(values), median, deviation, offset, accepted,
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"height-calibration-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = {
            "target_height_m": self.target_height_m,
            "minimum_samples": self.minimum_samples,
            "maximum_std_m": self.maximum_std_m,
            "maximum_offset_m": self.maximum_offset_m,
            "results": {key: asdict(value) for key, value in results.items()},
            "automatic_application": False,
            "review_note": "至少重复三次且经现场复核后，才能写入 BDS_HEIGHT_OFFSETS_JSON。",
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return results, path
