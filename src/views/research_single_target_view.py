"""Research page for isolated single-target calibration and template checks."""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.widgets.scifi_canvas import (
    CYAN, GREEN, MUTED, RED, TEXT, YELLOW, Canvas,
)


class ResearchSingleTargetViewMixin:
    def _research_single_target_calibration(self, c: Canvas) -> None:
        controller = self.context.single_target_calibration
        c.text(42, 76, 760, 38, "六目标引导校准与单目标诊断", 21, TEXT, True)
        c.text(
            42, 112, 910, 28,
            "正式模板：六块与测试页同时闪烁并提示注视目标；单目标独闪仅保留作硬件诊断。",
            11, CYAN,
        )

        c.panel(42, 158, 360, 420, "采集流程")
        steps = (
            ("01", "准备", "先在“采集”页启动8通道实时数据流"),
            ("02", "六块同时闪烁", "蓝框提示目标；场景与正式测试一致"),
            ("03", "筛选稳定通道", "跨试次通过率不足80%的通道不进模板"),
            ("04", "先验证再启用", "留一试次一致性不足50%会拒绝坏模板"),
        )
        for index, (number, title, body) in enumerate(steps):
            y = 205 + index * 79
            c.circle(74, y + 19, 18, "#14375a", CYAN)
            c.text(56, y + 1, 36, 36, number, 10, TEXT, True, Qt.AlignCenter)
            c.text(105, y - 2, 260, 25, title, 13, TEXT, True)
            c.text(105, y + 23, 260, 36, body, 9, MUTED)
        c.text(64, 526, 315, 34, "Esc 可随时中止；已完成的试次仍保留", 10, YELLOW, True)

        c.panel(424, 158, 814, 245, "本次六目标引导校准")
        for index, (label, frequency) in enumerate(zip(COMMANDS, TARGET_FREQUENCIES_HZ)):
            x = 448 + (index % 3) * 252
            y = 206 + (index // 3) * 72
            c.rect(x, y, 228, 54, "#0c1729", "#2d4668", 4)
            c.text(x + 13, y + 4, 110, 25, label, 13, TEXT, True)
            c.text(x + 129, y + 4, 82, 25, f"{frequency:g} Hz", 12, CYAN, True, Qt.AlignRight)
            c.text(x + 13, y + 29, 198, 18, "六块同闪 · 蓝框提示 × 3轮", 9, MUTED)
        c.text(
            448, 354, 744, 28,
            "共18轮 · 提示1.0s · 闪烁/记录3.0s · 丢弃前0.5s · 休息1.5s",
            10, MUTED, False, Qt.AlignCenter,
        )

        c.panel(424, 423, 814, 155, "运行状态与算法结果")
        c.text(449, 461, 105, 24, "状态", 10, MUTED, True)
        status_color = RED if "失败" in controller.status or "中止" in controller.status else GREEN
        c.text(548, 454, 645, 36, controller.status, 12, status_color, True)
        c.text(449, 500, 105, 24, "进度", 10, MUTED, True)
        c.text(548, 496, 130, 31, controller.progress, 15, TEXT, True)
        c.text(690, 500, 105, 24, "在线准确率", 10, MUTED, True)
        c.text(792, 496, 160, 31, "待独立测试", 13, CYAN, True)
        run_text = controller.latest_run_id or "尚无记录"
        c.text(449, 535, 744, 24, f"最近数据：{run_text}", 9, MUTED)

        start_disabled = controller.running
        c.button(424, 603, 390, 62, "正在采集…" if start_disabled else "开始18轮六目标引导校准", not start_disabled, start_disabled, GREEN)
        c.button(832, 603, 196, 62, "单目标诊断", False, start_disabled, CYAN)
        c.button(1038, 603, 200, 62, "重新生成模板", False, not bool(controller.latest_run_directory), CYAN)
        self._hit(424, 603, 390, 62, "guided-calibration-start")
        self._hit(832, 603, 196, 62, "single-target-calibration-start")
        self._hit(1038, 603, 200, 62, "single-target-calibration-analyze")
        c.text(
            424, 673, 814, 22,
            "单目标独闪数据不会再生成正式模板；内部一致性不是准确率，准确率仍由独立测试产生。",
            9, YELLOW, False, Qt.AlignCenter,
        )


__all__ = ["ResearchSingleTargetViewMixin"]
