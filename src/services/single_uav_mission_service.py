"""Backward-compatible import for the renamed structured mission service."""

from src.services.structured_mission_service import MissionPlanningError, StructuredMissionService

SingleUAVMissionService = StructuredMissionService

__all__ = ["MissionPlanningError", "SingleUAVMissionService", "StructuredMissionService"]
