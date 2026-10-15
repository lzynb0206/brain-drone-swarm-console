"""Virtual-joystick state and manual flight command dispatch."""

from __future__ import annotations

import math
from typing import Protocol


class ManualFlightHost(Protocol):
    actions: object

    def _notify(self, message: str) -> None: ...
    def releaseMouse(self) -> None: ...
    def update(self, *args) -> None: ...


class ManualFlightController:
    """Own normalized joystick axes and safely recenter on command errors."""

    def __init__(self, host: ManualFlightHost) -> None:
        self.host = host
        self.active_stick: str | None = None
        self.vertical = 0.0
        self.forward = 0.0
        self.lateral = 0.0

    def update_stick(self, stick: str, x: float, y: float) -> None:
        if stick == "manual-stick-height":
            center_y, travel = 386.0, 82.0
            self.vertical = max(-1.0, min(1.0, (center_y - y) / travel))
        else:
            center_x, center_y, travel = 760.0, 386.0, 82.0
            dx, dy = x - center_x, y - center_y
            length = math.hypot(dx, dy)
            if length > travel:
                dx, dy = dx * travel / length, dy * travel / length
            self.lateral = max(-1.0, min(1.0, dx / travel))
            self.forward = max(-1.0, min(1.0, -dy / travel))
        self.send()
        self.host.update()

    def send(self) -> None:
        message = self.host.actions.set_manual_control(
            self.vertical,
            self.forward,
            self.lateral,
        )
        if message:
            self.active_stick = None
            self.vertical = self.forward = self.lateral = 0.0
            self.host.releaseMouse()
            self.host._notify(message)

    def center(self, *, send: bool = False) -> None:
        self.active_stick = None
        self.vertical = self.forward = self.lateral = 0.0
        if send:
            self.host.actions.set_manual_control(0.0, 0.0, 0.0)
        self.host.update()


__all__ = ["ManualFlightController"]
