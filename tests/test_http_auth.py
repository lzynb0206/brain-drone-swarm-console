from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError, URLError

from src.adapters.http import HttpAuthGateway
from src.domain import Role
from src.ports import AuthError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class RecordingOpener:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request, timeout: float):
        payload = json.loads(request.data.decode("utf-8"))
        self.calls.append((request.full_url, payload))
        return FakeResponse({
            "accessToken": "signed.jwt.token",
            "tokenType": "Bearer",
            "expiresIn": 3600,
            "user": {
                "id": "9d7a93cc-6337-4fd6-8e99-28d64a3e318f",
                "email": payload["email"],
                "displayName": payload.get("displayName", "测试用户"),
                "role": payload.get("role", "engineer"),
            },
        })


class HttpAuthGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.opener = RecordingOpener()
        self.gateway = HttpAuthGateway("http://127.0.0.1:8080/", opener=self.opener)

    def test_login_saves_jwt_session(self) -> None:
        user = self.gateway.authenticate("User@Example.com", "secret123")
        self.assertEqual("user@example.com", user.email)
        self.assertEqual(Role.ENGINEER, user.role)
        self.assertEqual("signed.jwt.token", self.gateway.session.access_token)
        self.assertEqual("http://127.0.0.1:8080/api/auth/login", self.opener.calls[0][0])

    def test_registration_sends_selected_role(self) -> None:
        user = self.gateway.register("NEW@example.com", "secret123", "科研员", Role.RESEARCHER)
        _, payload = self.opener.calls[0]
        self.assertEqual("new@example.com", payload["email"])
        self.assertEqual("researcher", payload["role"])
        self.assertEqual(Role.RESEARCHER, user.role)

    def test_backend_error_message_is_shown(self) -> None:
        def failing_opener(request, timeout: float):
            body = io.BytesIO(json.dumps({"message": "该邮箱已经注册"}).encode("utf-8"))
            raise HTTPError(request.full_url, 409, "Conflict", {}, body)

        gateway = HttpAuthGateway("http://127.0.0.1:8080", opener=failing_opener)
        with self.assertRaisesRegex(AuthError, "已经注册"):
            gateway.register("user@example.com", "secret123", "用户")

    def test_connection_error_mentions_spring_backend(self) -> None:
        def offline_opener(request, timeout: float):
            raise URLError("offline")

        gateway = HttpAuthGateway("http://127.0.0.1:8080", opener=offline_opener)
        with self.assertRaisesRegex(AuthError, "Spring Boot"):
            gateway.authenticate("user@example.com", "secret123")


if __name__ == "__main__":
    unittest.main()
