"""CSV-backed command execution audit repository."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from src.services.experiment_record_schema import append_csv


class CommandAuditRepository:
    FIELDS = (
        "timestamp",
        "command_id",
        "action",
        "uav_id",
        "status",
        "message",
        "source",
    )

    def __init__(self, path: Path, lock: threading.Lock) -> None:
        self.path = path
        self._lock = lock

    def record(
        self,
        status: str,
        message: str,
        action: str = "",
        command_id: str = "",
        uav_id: str = "",
        source: str = "swarm_gateway",
    ) -> None:
        with self._lock:
            append_csv(
                self.path,
                self.FIELDS,
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "command_id": command_id,
                    "action": action,
                    "uav_id": uav_id,
                    "status": status,
                    "message": message,
                    "source": source,
                },
            )


__all__ = ["CommandAuditRepository"]
