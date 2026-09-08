"""Confidence gate for decoded intents; this service never calls a device gateway."""

from __future__ import annotations

from dataclasses import dataclass

from src.bci.config import FBCCA_MIN_MARGIN, FBCCA_MIN_SCORE


@dataclass(frozen=True)
class IntentSafetyDecision:
    accepted: bool
    reason: str


class IntentSafetyService:
    def __init__(self, min_confidence: float = FBCCA_MIN_SCORE, min_margin: float = FBCCA_MIN_MARGIN) -> None:
        self.min_confidence = float(min_confidence)
        self.min_margin = float(min_margin)

    def evaluate(self, confidence: float, margin: float) -> IntentSafetyDecision:
        if confidence < self.min_confidence:
            return IntentSafetyDecision(False, "置信度不足")
        if margin < self.min_margin:
            return IntentSafetyDecision(False, "区分度不足")
        return IntentSafetyDecision(True, "通过安全阈值")
