"""Stateful safety evaluation for mapped Crazyflie telemetry."""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.adapters.real.crazyflie_contracts import CrazyflieSwarmConfig
from src.adapters.real.crazyflie_telemetry import TelemetryFacts


@dataclass(frozen=True)
class TelemetrySafetyDecision:
    battery_low_active: bool = False
    low_voltage_warning: str | None = None
    positioning_failure_trip: bool = False
    firmware_locked: bool = False


class CrazyflieTelemetrySafetyMonitor:
    """Track debounced voltage and positioning failures across samples."""

    def __init__(self) -> None:
        self.battery_low_latched = False
        self.last_low_voltage: float | None = None
        self._battery_low_since: dict[str, float] = {}
        self._positioning_failure_counts: dict[str, int] = {}

    def reset(self) -> None:
        self.battery_low_latched = False
        self.last_low_voltage = None
        self._battery_low_since.clear()
        self._positioning_failure_counts.clear()

    def evaluate(
        self,
        uri: str,
        facts: TelemetryFacts,
        *,
        airborne: bool,
        config: CrazyflieSwarmConfig,
    ) -> TelemetrySafetyDecision:
        low_voltage_warning = None
        battery_low_active = False
        now = time.monotonic()
        voltage = facts.voltage
        threshold = config.minimum_battery_voltage
        if airborne and voltage < threshold:
            started = self._battery_low_since.setdefault(uri, now)
            confirmed = now - started >= max(0.0, config.battery_low_confirm_s)
            if confirmed and not self.battery_low_latched:
                self.battery_low_latched = True
                self.last_low_voltage = voltage
                low_voltage_warning = f"电量过低：{voltage:.2f}V，请立即受控降落"
            elif self.battery_low_latched:
                self.last_low_voltage = min(self.last_low_voltage or voltage, voltage)
            battery_low_active = self.battery_low_latched
        elif voltage >= threshold + 0.05:
            self._battery_low_since.pop(uri, None)

        if airborne:
            failures = 0 if facts.positioning_ok else (
                self._positioning_failure_counts.get(uri, 0) + 1
            )
            self._positioning_failure_counts[uri] = failures
        else:
            failures = 0
            self._positioning_failure_counts[uri] = 0

        return TelemetrySafetyDecision(
            battery_low_active=battery_low_active,
            low_voltage_warning=low_voltage_warning,
            positioning_failure_trip=(
                airborne and failures >= config.flow_failure_samples
            ),
            firmware_locked=facts.firmware_locked,
        )


__all__ = ["CrazyflieTelemetrySafetyMonitor", "TelemetrySafetyDecision"]
