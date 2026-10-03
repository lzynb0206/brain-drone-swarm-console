"""Pure conversion of cflib log dictionaries into typed telemetry facts."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from src.domain import Position


@dataclass(frozen=True)
class TelemetryFacts:
    name: str
    position: Position
    speed_mps: float
    voltage: float
    battery_percent: float
    local_flow_ok: bool
    flow_deck_present: bool
    lighthouse_deck_present: bool
    lighthouse_ready: bool
    active_base_stations: int
    positioning_ok: bool
    positioning_source: str
    firmware_locked: bool
    yaw_deg: float
    timestamp: float


class CrazyflieTelemetryMapper:
    """Interpret one transport payload without mutating gateway state."""

    def map(self, uri: str, timestamp: float, data: dict[str, float]) -> TelemetryFacts:
        position = Position(
            float(data.get("stateEstimate.x", 0.0)),
            float(data.get("stateEstimate.y", 0.0)),
            float(data.get("stateEstimate.z", 0.0)),
        )
        speed = math.sqrt(
            sum(
                float(data.get(key, 0.0)) ** 2
                for key in ("stateEstimate.vx", "stateEstimate.vy", "stateEstimate.vz")
            )
        )
        voltage = float(data.get("pm.vbat", 0.0))
        battery = max(0.0, min(100.0, (voltage - 3.0) / 1.2 * 100.0))
        local_flow_ok = (
            all(key in data for key in ("stateEstimate.x", "stateEstimate.y", "stateEstimate.z"))
            and all(math.isfinite(value) for value in (position.x, position.y, position.z))
            and 0.0 < float(data.get("range.zrange", 0.0)) < 4000.0
            and float(data.get("motion.squal", 0.0)) > 0.0
        )
        flow_deck_present = bool(int(data.get("deck.bcFlow", 0.0))) or bool(
            int(data.get("deck.bcFlow2", 0.0))
        )
        lighthouse_status = int(data.get("lighthouse.status", 0.0))
        base_station_mask = int(data.get("lighthouse.bsActive", 0.0))
        active_base_stations = base_station_mask.bit_count()
        lighthouse_deck_present = (
            bool(int(data.get("deck.bcLighthouse4", 0.0)))
            or lighthouse_status > 0
            or active_base_stations > 0
        )
        lighthouse_ready = lighthouse_status == 2 and active_base_stations > 0
        positioning_ok = lighthouse_ready or local_flow_ok
        return TelemetryFacts(
            name=uri.rsplit("/", 1)[-1],
            position=position,
            speed_mps=speed,
            voltage=voltage,
            battery_percent=battery,
            local_flow_ok=local_flow_ok,
            flow_deck_present=flow_deck_present,
            lighthouse_deck_present=lighthouse_deck_present,
            lighthouse_ready=lighthouse_ready,
            active_base_stations=active_base_stations,
            positioning_ok=positioning_ok,
            positioning_source=(
                "lighthouse" if lighthouse_ready else "flow" if local_flow_ok else "unavailable"
            ),
            firmware_locked=bool(int(data.get("supervisor.info", 0)) & (1 << 6)),
            yaw_deg=float(data.get("stateEstimate.yaw", 0.0)),
            timestamp=float(timestamp) if timestamp > 1_000_000_000 else time.time(),
        )


__all__ = ["CrazyflieTelemetryMapper", "TelemetryFacts"]
