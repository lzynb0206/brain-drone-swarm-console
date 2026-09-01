"""Authentication use case independent from the storage/API implementation."""

from __future__ import annotations

from src.domain import Role, User
from src.ports import AuthGateway


class AuthService:
    def __init__(self, gateway: AuthGateway) -> None:
        self._gateway = gateway

    def login(self, username: str, password: str) -> User | None:
        return self._gateway.authenticate(username, password)

    def register(
        self, email: str, password: str, display_name: str, role: Role = Role.SOLDIER
    ) -> User:
        return self._gateway.register(email, password, display_name, role)

    def send_password_reset(self, email: str) -> None:
        self._gateway.send_password_reset(email)

    def logout(self) -> None:
        self._gateway.logout()

    def role_is_configured(self, user: User) -> bool:
        return self._gateway.role_is_configured(user)

    def assign_role(self, user: User, role: Role) -> User:
        return self._gateway.assign_role(user, role)

    @property
    def is_remote(self) -> bool:
        return bool(getattr(self._gateway, "is_remote", False))
