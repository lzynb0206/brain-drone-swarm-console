"""Pure parameter validation shared by the Crazyflie gateway."""

from __future__ import annotations

import math


class CrazyflieSafetyPolicy:
    @staticmethod
    def validate_takeoff_height(height: float, minimum: float, maximum: float) -> None:
        if not math.isfinite(height) or not minimum <= height <= maximum:
            raise RuntimeError(
                f"Flow Hover 目标高度必须在 {minimum:.1f}–{maximum:.1f}m 之间"
            )

    @staticmethod
    def validate_relative_movement(dx: float, dy: float, dz: float) -> float:
        if not all(math.isfinite(value) for value in (dx, dy, dz)):
            raise RuntimeError("相对位移参数必须是有效数字")
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance <= 1e-6:
            raise RuntimeError("相对位移距离不能为 0")
        if distance > 5.0:
            raise RuntimeError("单次相对位移不能超过 5.0m")
        return distance


__all__ = ["CrazyflieSafetyPolicy"]
