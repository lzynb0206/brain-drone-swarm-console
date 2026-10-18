"""Stable on-disk contract shared by personal-template training and runtime."""

MODEL_VERSION = "personal_template_ssvep_v5"
GUIDED_CALIBRATION_CONDITION = "six_targets_all_on_cued_v1"
ISOLATED_CALIBRATION_CONDITION = "isolated_single_target_v1"
MIN_RELIABLE_CHANNEL_FRACTION = 0.80
MIN_LOO_CONSISTENCY = 0.50

__all__ = [
    "GUIDED_CALIBRATION_CONDITION",
    "ISOLATED_CALIBRATION_CONDITION",
    "MIN_LOO_CONSISTENCY",
    "MIN_RELIABLE_CHANNEL_FRACTION",
    "MODEL_VERSION",
]

