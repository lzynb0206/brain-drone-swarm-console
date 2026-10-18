"""Bounded, compact persistence for high-frequency recognition decisions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Mapping

from src.services.experiment_record_schema import (
    RECOGNITION_FIELDS,
    append_csv,
    data_row_count,
)


class RecognitionLogRepository:
    """Write a small operational summary and rotate the active CSV by size.

    Full per-channel quality diagnostics still live in labelled trial NPZ files.
    Repeating them four times per second in the recognition log made one file
    grow without bound while adding no new raw EEG evidence.
    """

    DEFAULT_MAX_BYTES = 16 * 1024 * 1024
    _SUMMARY_KEYS = (
        "status",
        "usable_channel_count",
        "total_channel_count",
        "usable_mask",
        "redundant_channels",
        "requested_channels",
        "used_channels",
        "excluded_requested_channels",
        "score_threshold",
        "margin_threshold",
        "template_stable_channels",
        "template_usable_stable_channels",
        "fbcca_consensus_label",
        "fbcca_consensus_agreed",
        "fbcca_consensus_accepted",
        "experimental_override",
        "quality_gate_applied",
        "quality_warning",
    )

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "recognition_log.csv"
        self.max_bytes = max(1024, int(max_bytes))

    @classmethod
    def compact_signal_quality(cls, quality: Mapping[str, object]) -> dict[str, object]:
        summary = {
            key: quality[key]
            for key in cls._SUMMARY_KEYS
            if key in quality
        }
        channel_issues: dict[str, list[str]] = {}
        channels = quality.get("channels", ())
        if isinstance(channels, (list, tuple)):
            for item in channels:
                if not isinstance(item, Mapping):
                    continue
                issues = [str(value) for value in item.get("issues", ()) if str(value)]
                if issues:
                    channel_issues[str(item.get("channel", "?"))] = issues
        if channel_issues:
            summary["channel_issues"] = channel_issues
        return summary

    @classmethod
    def compact_quality_json(cls, quality: Mapping[str, object]) -> str:
        return json.dumps(
            cls.compact_signal_quality(quality),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def append(self, row: dict[str, object]) -> None:
        self._rotate_if_needed()
        append_csv(self.path, RECOGNITION_FIELDS, row)

    def row_count(self) -> int:
        return sum(
            data_row_count(path)
            for path in self.directory.glob("recognition_log*.csv")
        )

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        self.path.replace(self.directory / f"recognition_log-{stamp}.csv")


__all__ = ["RecognitionLogRepository"]
