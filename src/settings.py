"""Application runtime configuration."""

from __future__ import annotations

import os


# 默认连接本机 Spring Boot 认证服务；YY_AUTH_BACKEND=mock 可离线演示。
AUTH_BACKEND = os.getenv("YY_AUTH_BACKEND", "http").strip().lower()
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8080").strip()
