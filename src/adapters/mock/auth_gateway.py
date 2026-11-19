from __future__ import annotations

from src.domain import Role, User
from src.ports import AuthError


class MockAuthGateway:
    """Replicates the role routing used by the supplied React prototype.

    The reference front-end is a visual prototype: it infers a role from the
    username and does not validate credentials against a server yet.  Keeping
    that behaviour here makes the Python translation match it exactly; this
    adapter is the place to replace when a real login API becomes available.
    """

    def authenticate(self, username: str, password: str) -> User | None:
        # 【真实接口接入点】这里只为复刻原前端而按用户名猜角色，并没有校验
        # 密码。正式版必须删除这段规则，改由 SQLite 哈希校验或 HTTPS/JWT。
        name = username.strip()
        if not name or not password:
            return None
        lowered = name.lower()
        if "research" in lowered or "科研" in name:
            role = Role.RESEARCHER
        elif "commander" in lowered or "指挥" in name:
            role = Role.COMMANDER
        else:
            role = Role.SOLDIER
        return User(name, name, role)

    def register(
        self, email: str, password: str, display_name: str, role: Role = Role.SOLDIER
    ) -> User:
        if "@" not in email:
            raise AuthError("请输入有效的邮箱地址")
        if len(password) < 6:
            raise AuthError("密码至少需要 6 位")
        name = display_name.strip() or email.split("@", 1)[0]
        return User(email, name, role, email=email)

    def send_password_reset(self, email: str) -> None:
        if "@" not in email:
            raise AuthError("请输入有效的邮箱地址")

    def logout(self) -> None:
        return None

    def role_is_configured(self, user: User) -> bool:
        return True

    def assign_role(self, user: User, role: Role) -> User:
        return User(user.username, user.display_name, role, uid=user.uid, email=user.email)
