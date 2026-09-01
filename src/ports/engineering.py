"""Replaceable data boundary for the engineering dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StreamStatus:
    name: str
    source: str
    sample_rate_hz: float
    packet_loss_percent: float
    online: bool


@dataclass(frozen=True)
class ModuleRecord:
    name: str
    status: str
    metric: str
    updated_at: str


class EngineerDataProvider(Protocol):
    """Data queried by Qt widgets; a real adapter can replace the demo one.

    A real implementation must return an already refreshed cache. Device,
    database and network I/O belongs in worker threads, never in these getters.
    """

    def streams(self) -> list[StreamStatus]:
        ...

    def module_records(self, module_key: str) -> list[ModuleRecord]:
        ...

    def logs(self, level: str) -> list[str]:
        ...
