"""Firebase Authentication adapter for the native PySide6 client."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.domain import Role, User
from src.ports import AuthError
from src.services.role_profile_store import LocalRoleProfileStore


@dataclass(frozen=True)
class FirebaseSession:
    uid: str
    email: str
    id_token: str
    refresh_token: str
    expires_in: int


class FirebaseAuthGateway:
    """Email/password authentication through Firebase Auth REST API."""

    is_remote = True
    _BASE_URL = "https://identitytoolkit.googleapis.com/v1"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 15.0,
        opener: Callable[..., Any] = urlopen,
        role_store: LocalRoleProfileStore | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Firebase Web API Key 未配置")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._opener = opener
        self._role_store = role_store
        self.session: FirebaseSession | None = None

    def authenticate(self, username: str, password: str) -> User:
        email = username.strip().lower()
        self._validate_credentials(email, password)
        data = self._post(
            "accounts:signInWithPassword",
            {"email": email, "password": password, "returnSecureToken": True},
        )
        self._save_session(data)
        display_name = str(data.get("displayName") or "").strip()
        if not display_name and self.session is not None:
            profile = self._post("accounts:lookup", {"idToken": self.session.id_token})
            users = profile.get("users") or []
            if users:
                display_name = str(users[0].get("displayName") or "").strip()
        uid = str(data.get("localId", ""))
        role = self._role_store.role_for(uid) if self._role_store else Role.SOLDIER
        return self._to_user(data, display_name, role)

    def register(
        self, email: str, password: str, display_name: str, role: Role = Role.SOLDIER
    ) -> User:
        normalized_email = email.strip().lower()
        self._validate_credentials(normalized_email, password)
        clean_name = display_name.strip()
        data = self._post(
            "accounts:signUp",
            {"email": normalized_email, "password": password, "returnSecureToken": True},
        )
        if clean_name:
            try:
                updated = self._post(
                    "accounts:update",
                    {"idToken": data["idToken"], "displayName": clean_name, "returnSecureToken": True},
                )
                data.update(updated)
            except AuthError:
                # The account was created; a profile-name failure must not
                # incorrectly report the whole registration as failed.
                pass
        self._save_session(data)
        uid = str(data.get("localId", ""))
        if self._role_store:
            self._role_store.assign(uid, normalized_email, clean_name, role)
        return self._to_user(data, clean_name, role)

    def send_password_reset(self, email: str) -> None:
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise AuthError("请输入有效的邮箱地址")
        self._post(
            "accounts:sendOobCode",
            {"requestType": "PASSWORD_RESET", "email": normalized_email},
            locale="zh-CN",
        )

    def logout(self) -> None:
        # Firebase REST has no server-side sign-out endpoint for a client
        # session. Removing both tokens is the native client's sign-out.
        self.session = None

    def role_is_configured(self, user: User) -> bool:
        return bool(self._role_store and self._role_store.has_profile(user.uid or ""))

    def assign_role(self, user: User, role: Role) -> User:
        if not self._role_store or not user.uid:
            return user
        self._role_store.assign(user.uid, user.email or user.username, user.display_name, role)
        return User(user.username, user.display_name, role, uid=user.uid, email=user.email)

    def _save_session(self, data: dict[str, Any]) -> None:
        self.session = FirebaseSession(
            uid=str(data.get("localId", "")),
            email=str(data.get("email", "")),
            id_token=str(data.get("idToken", "")),
            refresh_token=str(data.get("refreshToken", "")),
            expires_in=int(data.get("expiresIn", 3600)),
        )

    def _to_user(
        self, data: dict[str, Any], display_name: str = "", role: Role = Role.SOLDIER
    ) -> User:
        email = str(data.get("email", "")).strip().lower()
        name = display_name or email.split("@", 1)[0]
        return User(email, name, role, uid=str(data.get("localId", "")), email=email)

    def _post(self, endpoint: str, payload: dict[str, Any], locale: str | None = None) -> dict[str, Any]:
        url = f"{self._BASE_URL}/{endpoint}?key={self._api_key}"
        headers = {"Content-Type": "application/json"}
        if locale:
            headers["X-Firebase-Locale"] = locale
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                code = str(body.get("error", {}).get("message", "AUTH_FAILED"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                code = "AUTH_FAILED"
            raise AuthError(self._friendly_error(code)) from exc
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise AuthError("无法连接 Firebase，请检查网络后重试") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("Firebase 返回了无法解析的数据") from exc

    @staticmethod
    def _validate_credentials(email: str, password: str) -> None:
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise AuthError("请输入有效的邮箱地址")
        if len(password) < 6:
            raise AuthError("密码至少需要 6 位")

    @staticmethod
    def _friendly_error(code: str) -> str:
        normalized = code.split(" : ", 1)[0]
        messages = {
            "EMAIL_EXISTS": "该邮箱已经注册，请直接登录",
            "INVALID_EMAIL": "邮箱格式不正确",
            "WEAK_PASSWORD": "密码强度不足，至少需要 6 位",
            "EMAIL_NOT_FOUND": "邮箱或密码错误",
            "INVALID_PASSWORD": "邮箱或密码错误",
            "INVALID_LOGIN_CREDENTIALS": "邮箱或密码错误",
            "USER_DISABLED": "该账号已被停用",
            "OPERATION_NOT_ALLOWED": "Firebase 尚未启用邮箱/密码登录",
            "TOO_MANY_ATTEMPTS_TRY_LATER": "尝试次数过多，请稍后再试",
            "API_KEY_INVALID": "Firebase API Key 无效，请检查项目配置",
            "CONFIGURATION_NOT_FOUND": "Firebase Authentication 尚未完成配置",
            "PROJECT_NOT_FOUND": "找不到对应的 Firebase 项目",
            "QUOTA_EXCEEDED": "Firebase 认证配额已用尽，请稍后再试",
        }
        return messages.get(normalized, f"认证失败：{normalized}")
