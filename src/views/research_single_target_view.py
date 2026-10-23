"""Research page for isolated single-target calibration and template checks."""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ
from src.widgets.scifi_canvas import (
    RESEARCH_BORDER as BORDER,
    RESEARCH_CYAN as CYAN,
    RESEARCH_GREEN as GREEN,
    RESEARCH_MUTED as MUTED,
    RESEARCH_PANEL_SOFT as PANEL_SOFT,
    RESEARCH_RED as RED,
    RESEARCH_TEXT as TEXT,
    RESEARCH_YELLOW as YELLOW,
    Canvas,
)


class ResearchSingleTargetViewMixin:
    def _research_single_target_calibration(self, c: Canvas) -> None:
        controller = self.context.single_target_calibration
        c.text(42, 76, 760, 38, "单目标个人模板采集", 21, TEXT, True)
        c.text(
            42, 112, 910, 28,
            "与正式六目标测试使用相同尺寸和位置；每轮只闪一个目标，其余五个位置保持纯黑。",
            11, CYAN,
        )

        c.panel(42, 158, 360, 420, "采集流程")
        steps = (
            ("01", "准备", "先在“采集”页启动8通道实时数据流"),
            ("02", "单目标独闪", "每类10次；只盯唯一色块内十字"),
            ("03", "三枕区通道", "使用 Oz/PO3/PO4；坏通道自动剔除"),
            ("04", "模板＋FBCCA", "相似度检索与固定频率参考必须一致"),
        )
        for index, (number, title, body) in enumerate(steps):
            y = 205 + index * 79
            c.circle(74, y + 19, 18, "#e7f4f8", CYAN)
            c.text(56, y + 1, 36, 36, number, 10, TEXT, True, Qt.AlignCenter)
            c.text(105, y - 2, 260, 25, title, 13, TEXT, True)
            c.text(105, y + 23, 260, 36, body, 9, MUTED)
        c.text(64, 526, 315, 34, "Esc 可随时中止；已完成的试次仍保留", 10, YELLOW, True)

        c.panel(424, 158, 814, 245, "本次单目标模板采集")
        for index, (label, frequency) in enumerate(zip(COMMANDS, TARGET_FREQUENCIES_HZ)):
            x = 448 + (index % 3) * 252
            y = 206 + (index // 3) * 72
            c.rect(x, y, 228, 54, PANEL_SOFT, BORDER, 4)
            c.text(x + 13, y + 4, 110, 25, label, 13, TEXT, True)
            c.text(x + 129, y + 4, 82, 25, f"{frequency:g} Hz", 12, CYAN, True, Qt.AlignRight)
            c.text(x + 13, y + 29, 198, 18, "固定位置独闪 × 10轮", 9, MUTED)
        c.text(
            448, 354, 744, 28,
            f"共{controller.config.trial_count}轮 · 提示{controller.config.cue_seconds:g}s · "
            f"闪烁/记录{controller.config.stimulus_seconds:g}s · "
            f"分析起点{controller.config.discard_seconds:g}s · "
            f"休息{controller.config.rest_seconds:g}s",
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
        c.button(424, 603, 604, 62, "正在采集…" if start_disabled else "开始60轮单目标模板采集", not start_disabled, start_disabled, GREEN)
        c.button(1038, 603, 200, 62, "重新生成模板", False, not bool(controller.latest_run_directory), CYAN)
        self._hit(424, 603, 604, 62, "single-target-calibration-start")
        self._hit(1038, 603, 200, 62, "single-target-calibration-analyze")
        c.text(
            424, 673, 814, 22,
            "留一试次内部一致性只用于筛掉坏模板；真实准确率仍以采集完成后的六目标独立测试为准。",
            9, YELLOW, False, Qt.AlignCenter,
        )


__all__ = ["ResearchSingleTargetViewMixin"]
