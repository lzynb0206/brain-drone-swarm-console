"""Passive confidence feedback policy for offline study; no device interface."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FeedbackDecision:
    display_state: str
    consecutive_low_mi: int
    reason: str
    simulation_only: bool = True


class ResearchFeedbackPolicy:
    def __init__(self,ssvep_threshold=.70,mi_threshold=.55,low_mi_required=2,cooldown_seconds=5.):
        if not 0<ssvep_threshold<1 or not 0<mi_threshold<1 or low_mi_required<1 or cooldown_seconds<0:
            raise ValueError("Invalid feedback thresholds")
        self.ssvep_threshold=ssvep_threshold;self.mi_threshold=mi_threshold
        self.low_mi_required=low_mi_required;self.cooldown=cooldown_seconds
        self.streak=0;self.last_time=None;self.last_hint=-float("inf")

    def update(self,p_ssvep,p_mi,timestamp,ssvep_valid=True,mi_valid=True):
        timestamp=float(timestamp)
        if not math.isfinite(timestamp) or self.last_time is not None and timestamp<=self.last_time:
            raise ValueError("Feedback replay timestamps must strictly increase")
        self.last_time=timestamp
        def valid(value):return value is not None and math.isfinite(value) and 0<=value<=1
        if not ssvep_valid or not mi_valid or not valid(p_ssvep) or not valid(p_mi):
            self.streak=0;return FeedbackDecision("check_signal",0,"Missing/invalid confidence; no ability or attention inference")
        if p_ssvep<self.ssvep_threshold:
            self.streak=0;return FeedbackDecision("visual_refocus",0,"Preceding SSVEP relative margin below configured threshold")
        self.streak=self.streak+1 if p_mi<self.mi_threshold else 0
        if self.streak>=self.low_mi_required and timestamp-self.last_hint>=self.cooldown:
            self.last_hint=timestamp;self.streak=0
            return FeedbackDecision("mi_guidance_candidate",self.low_mi_required,"Offline marker only; repeated low MI score with high preceding SSVEP margin")
        return FeedbackDecision("observe",self.streak,"No new feedback candidate")
