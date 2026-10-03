"""JSON artifact exports for the workbench."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class WorkbenchExportService:
    def __init__(self, research, record: Callable[[str], str]) -> None:
        self.research = research
        self._record = record

    def export_workspace_config(self, output_dir: Path | None = None) -> Path:
        directory = output_dir or Path.cwd() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"workspace-config-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = {
            "paradigm": {
                "type": self.research.paradigm_type,
                "frequency_hz": self.research.paradigm_frequency,
                "stimulus_size_percent": self.research.stimulus_size,
                "duty_cycle_percent": self.research.duty_cycle,
            },
            "algorithm": self.research.algorithm,
            "algorithm_mode": self.research.algorithm_mode,
            "default_algorithm": self.research.default_algorithm,
            "controller": self.research.controller,
            "controller_values": self.research.controller_values,
            "mappings": [entry.__dict__ for entry in self.research.mappings],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._record(f"工作台配置已导出：{path.name}")
        return path

    def export_soldier_report(self, output_dir: Path | None = None) -> Path:
        directory = output_dir or Path.cwd() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"soldier-report-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = {
            "soldier_id": "S-07",
            "name": "张伟",
            "heart_rate_bpm": 72,
            "attention": 85,
            "fatigue": 62,
            "stress": 40,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._record(f"士兵状态报告已导出：{path.name}")
        return path
