"""Thread-safe Crazyflie events and command-result subscriptions."""

from __future__ import annotations

import threading
import time
from typing import Callable

from src.domain import SwarmCommand


class CrazyflieEventJournal:
    """Keep the bounded operator log and isolate listener failures."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = max(1, int(capacity))
        self._lock = threading.RLock()
        self._events: list[tuple[str, str, float]] = []
        self._event_listeners: list[Callable[[str, str], None]] = []
        self._command_listeners: list[Callable[[str, str, SwarmCommand], None]] = []

    def add_event_listener(self, listener: Callable[[str, str], None]) -> None:
        with self._lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def add_command_listener(
        self,
        listener: Callable[[str, str, SwarmCommand], None],
    ) -> None:
        with self._lock:
            if listener not in self._command_listeners:
                self._command_listeners.append(listener)

    def record(self, level: str, message: str) -> None:
        normalized = level.upper()
        with self._lock:
            self._events.insert(0, (normalized, message, time.time()))
            del self._events[self._capacity:]
            listeners = tuple(self._event_listeners)
        for listener in listeners:
            try:
                listener(normalized, message)
            except Exception:
                pass

    def notify_command(self, status: str, message: str, command: SwarmCommand) -> None:
        with self._lock:
            listeners = tuple(self._command_listeners)
        for listener in listeners:
            try:
                listener(status, message, command)
            except Exception:
                pass

    def formatted_events(self, level: str | None = None) -> list[str]:
        with self._lock:
            values = list(self._events)
        if level:
            values = [value for value in values if value[0] == level]
        return [
            f"{time.strftime('%H:%M:%S', time.localtime(stamp))}  {kind}  {message}"
            for kind, message, stamp in values
        ]


__all__ = ["CrazyflieEventJournal"]
