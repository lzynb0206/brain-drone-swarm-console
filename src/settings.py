"""Application runtime configuration."""

from __future__ import annotations

import os


# 默认连接本机 Spring Boot 认证服务；BDS_AUTH_BACKEND=mock 可用于界面测试。
AUTH_BACKEND = os.getenv("BDS_AUTH_BACKEND", "http").strip().lower()
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8080").strip()
