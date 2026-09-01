from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from src.adapters.firebase import FirebaseAuthGateway
from src.domain import Role
from src.ports import AuthError
from src.services.role_profile_store import LocalRoleProfileStore


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
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, request, timeout: float):
        payload = json.loads(request.data.decode("utf-8"))
        headers = dict(request.header_items())
        self.calls.append((request.full_url, payload, headers))
        if "signInWithPassword" in request.full_url:
            return FakeResponse({
                "localId": "uid-1", "email": "user@example.com", "displayName": "测试用户",
                "idToken": "id-token", "refreshToken": "refresh-token", "expiresIn": "3600",
            })
        if "signUp" in request.full_url:
            return FakeResponse({
                "localId": "uid-2", "email": payload["email"], "idToken": "signup-token",
                "refreshToken": "signup-refresh", "expiresIn": "3600",
            })
        if "accounts:update" in request.full_url:
            return FakeResponse({
                "localId": "uid-2", "email": "new@example.com", "displayName": payload["displayName"],
                "idToken": "updated-token", "refreshToken": "updated-refresh", "expiresIn": "3600",
            })
        return FakeResponse({"email": payload.get("email", "user@example.com")})


class FirebaseAuthGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.opener = RecordingOpener()
        self.gateway = FirebaseAuthGateway("test-api-key", opener=self.opener)

    def test_login_creates_in_memory_session(self) -> None:
        user = self.gateway.authenticate("User@Example.com", "secret1")
        self.assertEqual("user@example.com", user.email)
        self.assertEqual("测试用户", user.display_name)
        self.assertEqual(Role.SOLDIER, user.role)
        self.assertEqual("id-token", self.gateway.session.id_token)
        self.assertIn("accounts:signInWithPassword?key=test-api-key", self.opener.calls[0][0])
        self.gateway.logout()
        self.assertIsNone(self.gateway.session)

    def test_registration_updates_display_name(self) -> None:
        user = self.gateway.register("NEW@example.com", "secret1", "新用户")
        self.assertEqual("新用户", user.display_name)
        self.assertEqual("new@example.com", user.email)
        self.assertEqual(2, len(self.opener.calls))
        self.assertIn("accounts:update", self.opener.calls[1][0])

    def test_selected_role_is_saved_by_uid_and_restored_on_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalRoleProfileStore(Path(directory) / "roles.json")
            gateway = FirebaseAuthGateway("test-api-key", opener=self.opener, role_store=store)
            registered = gateway.register("new@example.com", "secret1", "科研员", Role.RESEARCHER)
            self.assertEqual(Role.RESEARCHER, registered.role)
            self.assertEqual(Role.RESEARCHER, store.role_for("uid-2"))
            self.assertTrue(gateway.role_is_configured(registered))

            store.assign("uid-1", "user@example.com", "管理员", Role.ADMIN)
            logged_in = gateway.authenticate("user@example.com", "secret1")
            self.assertEqual(Role.ADMIN, logged_in.role)
            changed = gateway.assign_role(logged_in, Role.ENGINEER)
            self.assertEqual(Role.ENGINEER, changed.role)
            self.assertEqual(Role.ENGINEER, store.role_for("uid-1"))

    def test_password_reset_requests_chinese_email(self) -> None:
        self.gateway.send_password_reset("user@example.com")
        _, payload, headers = self.opener.calls[-1]
        self.assertEqual("PASSWORD_RESET", payload["requestType"])
        self.assertEqual("zh-CN", headers["X-firebase-locale"])

    def test_firebase_error_is_translated(self) -> None:
        def failing_opener(request, timeout: float):
            body = io.BytesIO(json.dumps({"error": {"message": "EMAIL_EXISTS"}}).encode("utf-8"))
            raise HTTPError(request.full_url, 400, "Bad Request", {}, body)

        gateway = FirebaseAuthGateway("test-api-key", opener=failing_opener)
        with self.assertRaisesRegex(AuthError, "已经注册"):
            gateway.register("user@example.com", "secret1", "")


if __name__ == "__main__":
    unittest.main()
