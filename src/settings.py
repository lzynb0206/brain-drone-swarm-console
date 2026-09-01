"""Runtime configuration read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


AUTH_BACKEND = os.getenv("YY_AUTH_BACKEND", "mock").strip().lower()
FIREBASE_API_KEY = os.getenv(
    "FIREBASE_API_KEY",
    "",
).strip()
FIREBASE_PROJECT_ID = "fir-project-8fa13"
ROLE_PROFILE_PATH = Path(
    os.getenv(
        "YY_ROLE_PROFILE_PATH",
        str(Path.home() / ".brain_control_console" / "firebase_roles.json"),
    )
)
