"""Application runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path


# 私有仓库演示版默认直连 Firebase；如需离线演示，可在运行配置中设置
# YY_AUTH_BACKEND=mock。环境变量 FIREBASE_API_KEY 仍可用于临时覆盖此配置。
AUTH_BACKEND = os.getenv("YY_AUTH_BACKEND", "firebase").strip().lower()
FIREBASE_API_KEY = os.getenv(
    "FIREBASE_API_KEY",
    "AIzaSyAGH1cRNVuUbpQzDq1JYu2xIqldQcRQxq0",
).strip()
FIREBASE_PROJECT_ID = "fir-project-8fa13"
ROLE_PROFILE_PATH = Path(
    os.getenv(
        "YY_ROLE_PROFILE_PATH",
        str(Path.home() / ".brain_control_console" / "firebase_roles.json"),
    )
)
