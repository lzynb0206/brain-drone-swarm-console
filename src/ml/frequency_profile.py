"""Prevent cross-frequency label reuse after a stimulus-profile change."""
from __future__ import annotations

import json
import math

from src.bci.config import LABEL_TO_FREQUENCY, TARGET_FREQUENCIES_HZ


def matches_current_profile(frequencies) -> bool:
    try:
        values = tuple(float(value) for value in frequencies)
        return len(values) == len(TARGET_FREQUENCIES_HZ) and all(
            math.isclose(value, expected, abs_tol=1e-6, rel_tol=0.0)
            for value, expected in zip(values, TARGET_FREQUENCIES_HZ)
        )
    except (TypeError, ValueError):
        return False


def record_matches_current_profile(row) -> bool:
    # Even idle trials depend on whether the old or new targets were flickering.
    try:
        metadata = json.loads(row.get("trial_metadata_json") or "{}")
        if not isinstance(metadata, dict):
            return False
        if not matches_current_profile(metadata.get("target_frequencies_hz")):
            return False
        if row.get("true_label") == "空闲":
            return metadata.get("idle_targets_flicker") is True
        expected = LABEL_TO_FREQUENCY.get(row.get("true_label"))
        return expected is not None and math.isclose(
            float(row.get("target_frequency_hz", "nan")), expected,
            abs_tol=1e-6, rel_tol=0.0,
        )
    except (TypeError, ValueError):
        return False
