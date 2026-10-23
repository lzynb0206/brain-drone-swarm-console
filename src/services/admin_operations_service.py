"""Administrative/demo operations kept separate from flight control."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime


class AdminOperationsService:
    def __init__(self, system_monitor, record: Callable[[str], str]) -> None:
        self.system_monitor = system_monitor
        self._record = record
        self.storage_percent = 68
        self.last_backup = "2026-08-30 22:00"
        self.user_roles = {"张伟": "士兵", "李明": "科研人员", "王强": "管理员"}
        self.self_test_run = False

    def run_self_test(self) -> str:
        self.self_test_run = True
        if self.system_monitor is not None:
            metrics = self.system_monitor.snapshot()
            return self._record(
                f"自检完成：CPU {metrics.cpu_percent:.0f}% / 内存 {metrics.memory_percent:.0f}% / 延迟 {metrics.latency_ms:.0f}ms"
            )
        return self._record("自检完成：演示环境各模块响应正常")

    def clean_cache(self) -> str:
        self.storage_percent = max(0, self.storage_percent - 6)
        return self._record(f"缓存清理完成，存储使用率降至 {self.storage_percent}%")

    def create_backup(self) -> str:
        self.last_backup = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._record(f"系统备份完成：{self.last_backup}")

    def restore_backup(self) -> str:
        return self._record(f"已校验备份 {self.last_backup}（演示环境未覆盖当前文件）")

    def update_user_role(self, username: str, role: str) -> str:
        self.user_roles[username] = role
        return self._record(f"用户 {username} 的角色已修改为 {role}")
