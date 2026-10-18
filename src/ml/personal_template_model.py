"""Compatibility facade for the split personal-template implementation."""

from src.ml.personal_template_contract import (
    GUIDED_CALIBRATION_CONDITION,
    ISOLATED_CALIBRATION_CONDITION,
    MODEL_VERSION,
)
from src.ml.personal_template_features import extract_template_feature
from src.ml.personal_template_runtime import PersonalTemplateModel
from src.ml.personal_template_training import train_personal_template_model

__all__ = [
    "MODEL_VERSION",
    "GUIDED_CALIBRATION_CONDITION",
    "ISOLATED_CALIBRATION_CONDITION",
    "PersonalTemplateModel",
    "extract_template_feature",
    "train_personal_template_model",
]
