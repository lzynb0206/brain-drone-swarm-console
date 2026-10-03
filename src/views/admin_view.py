"""Administrator dashboard rendering."""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.widgets.scifi_canvas import (
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class AdminViewMixin:
    """Render administrator overview and tables."""
    def _paint_admin(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-12：四类真实服务：DeviceRegistry（设备/固件/在线）、
        # UserRepository（用户与 RBAC）、StorageService（XDF/SQLite 容量）、
        # BackupService（备份/恢复）。这些是高风险管理操作，必须二次确认、
        # 角色鉴权和写审计日志，不能只修改当前画面上的数字。
        c.fill("#070d1f")
        stats = [("▦", "设备总数", "12"), ("▤", "在线设备", "10"), ("!", "告警", "2"),
                 ("▱", "存储使用率", f"{self.actions.storage_percent}%")]
        for i, (icon, label, value) in enumerate(stats):
            x = 33 + i * 310
            c.rect(x, 32, 285, 90, PANEL, BORDER, 3)
            c.rect(x + 16, 53, 48, 48, "#13264a", "#254783", 3)
            c.text(x + 16, 53, 48, 48, icon, 24, BLUE, False, Qt.AlignCenter)
            c.text(x + 80, 48, 175, 28, label, 13, MUTED)
            c.text(x + 80, 72, 175, 34, value, 25, TEXT, True)
        c.panel(33, 155, 590, 263, "设备管理")
        self._table(c, 49, 224, 558, ["名称", "类型", "固件版本", "在线状态", "操作"],
                    [["1   DeepBCI 采集板", "无", "v2.1", "在线", "查看"],
                     ["2   Crazyflie-01", "无人机", "v1.3", "在线", "查看"],
                     ["3   智能头盔", "头盔", "v1.0", "离线", "查看"]],
                    [190, 88, 104, 104, 70])
        for i, name in enumerate(("DeepBCI采集板", "Crazyflie-01", "智能头盔")):
            self._hit(537, 268 + i * 45, 70, 44, f"admin-device:{name}")
        c.panel(657, 155, 590, 263, "用户管理")
        self._table(c, 673, 224, 558, ["用户名", "角色", "最后登录", "操作"],
                    [["张伟", self.actions.user_roles["张伟"], "2026-08-30", "编辑"],
                     ["李明", self.actions.user_roles["李明"], "2026-08-29", "编辑"],
                     ["王强", self.actions.user_roles["王强"], "2026-08-31", "编辑"]],
                    [120, 145, 194, 99])
        for i, name in enumerate(("张伟", "李明", "王强")):
            self._hit(1132, 268 + i * 45, 99, 44, f"admin-user:{name}")
        c.panel(33, 452, 590, 263, "存储管理")
        c.text(151, 590, 100, 50, f"{self.actions.storage_percent}%", 26, TEXT, True, Qt.AlignCenter)
        c.text(245, 674, 75, 35, f"{100 - self.actions.storage_percent}%", 13, MUTED, False, Qt.AlignCenter)
        c.button(412, 599, 139, 44, "▱  清理缓存")
        self._hit(412, 599, 139, 44, "admin-clean-cache")
        c.panel(657, 452, 590, 263, "系统备份")
        c.text(822, 547, 260, 32, "最近备份时间", 13, MUTED, False, Qt.AlignCenter)
        c.text(807, 581, 290, 35, self.actions.last_backup, 18, TEXT, False, Qt.AlignCenter)
        c.button(760, 642, 179, 46, "立即备份", True)
        c.button(963, 642, 181, 46, "恢复")
        self._hit(760, 642, 179, 46, "admin-backup")
        self._hit(963, 642, 181, 46, "admin-restore")

    def _table(self, c: Canvas, x: float, y: float, w: float, headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
        c.rect(x, y, w, 44, "#17243a")
        cursor = x
        for title, width in zip(headers, widths):
            c.text(cursor + 11, y, width - 12, 44, title, 13, TEXT, True)
            cursor += width
        for r, row in enumerate(rows):
            row_y = y + 44 + r * 45
            cursor = x
            for col, (value, width) in enumerate(zip(row, widths)):
                color = BLUE if col == len(row) - 1 else ("#606b80" if value == "离线" else TEXT)
                c.text(cursor + 11, row_y, width - 12, 44, value, 13, color)
                cursor += width
            c.line(x, row_y + 44, x + w, row_y + 44, "#273752")

    # ---------- Engineering dashboard ----------


