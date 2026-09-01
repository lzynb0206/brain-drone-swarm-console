"""Local role profiles for the multi-role research demonstration."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from src.domain import Role


class LocalRoleProfileStore:
    """Persist a Firebase UID -> application role mapping as local JSON.

    This is a prototype adapter. A production deployment must replace it with
    roles assigned by a trusted backend or Firebase Custom Claims.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def role_for(self, uid: str) -> Role:
        with self._lock:
            profile = self._read().get(uid, {})
        try:
            return Role(str(profile.get("role", Role.SOLDIER.value)))
        except ValueError:
            return Role.SOLDIER

    def has_profile(self, uid: str) -> bool:
        with self._lock:
            return bool(uid) and uid in self._read()

    def assign(self, uid: str, email: str, display_name: str, role: Role) -> None:
        if not uid:
            return
        with self._lock:
            profiles = self._read()
            profiles[uid] = {
                "email": email,
                "display_name": display_name,
                "role": role.value,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(profiles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
