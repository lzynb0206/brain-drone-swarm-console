"""Authentication client for the Spring Boot/PostgreSQL backend."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.domain import Role, User
from src.ports import AuthError


@dataclass(frozen=True)
class JwtSession:
    access_token: str
    token_type: str
    expires_in: int


class HttpAuthGateway:
    """Call the local REST API and keep the short-lived JWT in memory."""

    is_remote = True

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._opener = opener
        self.session: JwtSession | None = None

    def authenticate(self, username: str, password: str) -> User:
        email = username.strip().lower()
        self._validate_credentials(email, password, validate_length=False)
        data = self._request("POST", "/api/auth/login", {"email": email, "password": password})
        return self._save_session_and_user(data)

    def register(
        self, email: str, password: str, display_name: str, role: Role = Role.SOLDIER
    ) -> User:
        normalized_email = email.strip().lower()
        self._validate_credentials(normalized_email, password, validate_length=True)
        clean_name = display_name.strip()
        if not clean_name:
            raise AuthError("请输入显示名称")
        data = self._request(
            "POST",
            "/api/auth/register",
            {
                "email": normalized_email,
                "password": password,
                "displayName": clean_name,
                "role": role.value,
            },
        )
        return self._save_session_and_user(data)

    def send_password_reset(self, email: str) -> None:
        raise AuthError("本地系统暂未配置邮件服务，请联系管理员重置密码")

    def logout(self) -> None:
        self.session = None

    def role_is_configured(self, user: User) -> bool:
        return True

    def assign_role(self, user: User, role: Role) -> User:
        if user.role != role:
            raise AuthError("账号身份必须由后端数据库授权")
        return user

    def _save_session_and_user(self, data: dict[str, Any]) -> User:
        token = str(data.get("accessToken", ""))
        if not token:
            raise AuthError("认证服务返回的数据不完整")
        self.session = JwtSession(
            access_token=token,
            token_type=str(data.get("tokenType", "Bearer")),
            expires_in=int(data.get("expiresIn", 3600)),
        )
        profile = data.get("user") or {}
        try:
            role = Role(str(profile.get("role", Role.SOLDIER.value)))
        except ValueError as exc:
            raise AuthError("认证服务返回了未知身份") from exc
        email = str(profile.get("email", "")).strip().lower()
        display_name = str(profile.get("displayName", "")).strip() or email.split("@", 1)[0]
        uid = str(profile.get("id", ""))
        return User(email, display_name, role, uid=uid, email=email)

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method=method,
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                message = str(body.get("message") or "认证失败")
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = "认证失败，请检查输入"
            raise AuthError(message) from exc
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise AuthError("无法连接本地认证服务，请先启动 Spring Boot 后端") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("认证服务返回了无法解析的数据") from exc

    @staticmethod
    def _validate_credentials(email: str, password: str, validate_length: bool) -> None:
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise AuthError("请输入有效的邮箱地址")
        if validate_length and not 8 <= len(password) <= 72:
            raise AuthError("密码长度必须为 8 到 72 位")
