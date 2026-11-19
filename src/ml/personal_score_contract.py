"""On-disk contract for the subject-specific dual-FBCCA score calibrator."""

MODEL_VERSION = "personal_dual_fbcca_logreg_v1"
ALGORITHM_NAME = "个人校准融合"
FEATURE_NAMES = tuple(
    f"{branch}:{frequency:g}Hz"
    for branch in ("standard50", "equal46")
    for frequency in (8.0, 9.0, 10.0, 11.0, 12.0, 13.0)
)
REQUIRED_WINDOW_SECONDS = 5.0

__all__ = [
    "ALGORITHM_NAME",
    "FEATURE_NAMES",
    "MODEL_VERSION",
    "REQUIRED_WINDOW_SECONDS",
]
