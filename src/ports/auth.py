from __future__ import annotations

from typing import Protocol

from src.domain import Role, User


class AuthError(RuntimeError):
    """可安全显示给用户的认证错误。"""


class AuthGateway(Protocol):
    """登录认证边界。

    【真实接口接入点】正式版在 ``src/adapters/real/`` 实现本协议：
    - 单机演示可用 SQLite 保存用户、密码哈希和盐值；
    - 多人/联网系统可调用 HTTPS 登录 API，保存服务端返回的 JWT；
    - 无论哪种实现，都只返回 ``User``，界面不应直接访问数据库或 HTTP。
    """

    def authenticate(self, username: str, password: str) -> User | None:
        """认证成功返回用户和角色，失败返回 ``None``。"""
        ...

    def register(
        self, email: str, password: str, display_name: str, role: Role = Role.SOLDIER
    ) -> User:
        """创建账号并返回已登录用户。"""
        ...

    def send_password_reset(self, email: str) -> None:
        """向指定邮箱发送密码重置邮件。"""
        ...

    def logout(self) -> None:
        """清除当前客户端会话。"""
        ...

    def role_is_configured(self, user: User) -> bool:
        """当前账号是否已经绑定应用身份。"""
        ...

    def assign_role(self, user: User, role: Role) -> User:
        """为演示账号绑定应用身份并返回更新后的用户。"""
        ...
