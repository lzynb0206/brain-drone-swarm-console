"""Focused researcher rendering for  research integrated collector."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.widgets.scifi_canvas import (
    RESEARCH_BG as BG,
    RESEARCH_BORDER as BORDER,
    RESEARCH_CYAN as CYAN,
    RESEARCH_GREEN as GREEN,
    RESEARCH_MUTED as MUTED,
    RESEARCH_PANEL as PANEL,
    RESEARCH_PANEL_SOFT as PANEL_SOFT,
    RESEARCH_RED as RED,
    RESEARCH_TEXT as TEXT,
    RESEARCH_YELLOW as YELLOW,
    Canvas,
)


RESEARCH_TRACE_COLORS = (
    "#4b5563", "#7c3aed", "#2563eb", "#059669",
    "#ca8a04", "#ea580c", "#dc2626", "#92400e",
)


class ResearchCollectionViewMixin:
    """Rendering behavior extracted from the researcher page shell."""
    def _research_integrated_collector(self, c: Canvas) -> None:
        """One-stop direct hardware/LSL recorder based on the standalone tool."""

        bg, panel, border = "#eef1f4", "#ffffff", "#ccd3da"
        ink, sub, blue = "#26323c", "#697681", "#2e628f"
        c.rect(0, 56, 1280, 664, bg)
        c.text(24, 67, 600, 32, "EEG 一体化采集器", 18, ink, True)
        c.text(24, 96, 860, 21, "ExpandableEEG / OpenBCI直连 · LSL兼容输入 · 8通道实时波形 · CSV自动保存", 10, sub)

        # Source and session controls.
        c.rect(24, 126, 1232, 104, panel, border, 5)
        c.text(39, 136, 88, 24, "数据来源", 10, ink, True)
        direct_active = self._collector_source_mode == "direct"
        self._light_button(c, 128, 133, 172, 31, "直接连接设备", direct_active, blue)
        self._light_button(c, 308, 133, 145, 31, "接收 LSL 流", not direct_active, blue)
        self._hit(128, 133, 172, 31, "collector-source:direct")
        self._hit(308, 133, 145, 31, "collector-source:lsl")
        source_value = (
            self._collector_selected_device or "未选择蓝牙COM/串口"
            if direct_active
            else str(getattr(self.eeg_stream, "source_name", "obci_eeg1")).replace("LSL · ", "")
        )
        c.text(474, 136, 66, 24, "当前设备", 9, sub, True)
        c.rect(540, 133, 260, 31, "#f7f9fa", border, 2)
        c.text(550, 133, 240, 31, source_value, 9, ink)
        self._light_button(c, 815, 133, 118, 31, "刷新/选择", False, blue)
        self._light_button(c, 941, 133, 134, 31, "开始采集", False, "#31875a")
        self._light_button(c, 1083, 133, 116, 31, "停止采集", False, "#bd4b4b")
        self._hit(815, 133, 118, 31, "collector-refresh")
        self._hit(941, 133, 134, 31, "collector-start")
        self._hit(1083, 133, 116, 31, "collector-stop")
        c.text(39, 177, 250, 22, "设备：ExpandableEEG（Cyton兼容）", 9, sub)
        c.text(315, 177, 140, 22, "通道：固定8通道", 9, sub)
        c.text(485, 177, 140, 22, "采样率：250 Hz", 9, sub)
        c.text(650, 177, 270, 22, "连接：蓝牙虚拟COM/串口（115200）", 9, sub)
        running = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        c.circle(1105, 188, 6, "#39a96b" if running else "#c45555")
        c.text(1118, 176, 114, 24, "采集中" if running else "未采集", 9, ink, True)
        c.text(39, 203, 1170, 20, self._eeg_status, 9, "#3b6f95")

        csv_stats = self._raw_csv_recorder.snapshot()
        health = self._eeg_health
        sample_count = int(csv_stats.get("sample_count", 0) or health.get("total_samples", 0) or 0)
        duration = float(csv_stats.get("duration_seconds", 0.0) or 0.0)
        actual_rate = (
            float(health.get("actual_sample_rate_hz", 0.0) or self._eeg_sample_rate or 0.0)
            if running or bool(health.get("connected", False))
            else 0.0
        )
        metrics = (
            ("状态", "正在采集" if running else "等待"),
            ("样本数", f"{sample_count}"),
            ("时长", f"{duration:.1f} s"),
            ("实际采样率", f"{actual_rate:.1f} Hz"),
        )
        for index, (label, value) in enumerate(metrics):
            x = 24 + index * 306
            c.text(x, 238, 294, 20, label, 9, sub)
            c.rect(x, 260, 294, 48, "#e3e7ea", border, 3)
            c.text(x, 260, 294, 48, value, 13, ink, True, Qt.AlignCenter)

        # The standalone LSL recorder's file and plot controls are retained.
        c.text(24, 317, 760, 24, f"数据文件：{csv_stats.get('path') or '点击开始采集后自动创建CSV'}", 9, ink)
        self._light_button(c, 938, 314, 122, 30, "打开数据目录")
        self._light_button(
            c, 1068, 314, 164, 30,
            "恢复波形" if self._collector_waveform_paused else "暂停波形",
            self._collector_waveform_paused,
            blue,
        )
        self._hit(938, 314, 122, 30, "collector-open-directory")
        self._hit(1068, 314, 164, 30, "collector-pause-waveform")

        c.rect(24, 350, 892, 350, panel, border, 4)
        c.text(38, 354, 450, 27, "最近5秒原始脑电波形（8通道）", 11, ink, True)
        c.text(690, 354, 210, 27, "单位 μV · 自动纵向缩放", 9, sub, False, Qt.AlignRight)
        self._draw_light_eeg(c, 37, 386, 866, 297)
        c.text(37, 677, 866, 18, "-5s                                  时间                                  0s", 8, sub, False, Qt.AlignCenter)

        c.rect(928, 350, 328, 350, panel, border, 4)
        c.text(942, 354, 300, 27, "运行日志 / 数据质量", 11, ink, True)
        gaps = int(health.get("gap_count", 0) or 0)
        c.text(942, 385, 140, 20, f"数据缺口：{gaps}", 9, "#b44d4d" if gaps else "#31875a", True)
        c.text(1090, 385, 150, 20, f"通道：{len(self._eeg_channel_names) or 8}", 9, sub, False, Qt.AlignRight)
        c.line(942, 410, 1242, 410, border)
        events = list(self._eeg_events)[:11]
        if not events:
            events = ["等待开始采集…"]
        for index, event in enumerate(events):
            c.text(942, 419 + index * 24, 300, 22, str(event)[:46], 8, ink)
        c.text(
            942, 660, 300, 28,
            "暂停波形只停止绘图，CSV仍持续保存全部样本。",
            8, "#8b6b2e", False, Qt.AlignCenter,
        )

    def _research_lsl_collector(self, c: Canvas) -> None:
        """Independent LSL receiver and raw CSV recorder."""

        collector = self.lsl_collector
        c.rect(0, 56, 1280, 664, BG)
        c.text(32, 69, 420, 32, "LSL 脑电收集", 20, TEXT, True)
        c.text(32, 100, 840, 21, "发现并接收 type=EEG 的 LSL 数据流，同时保留原始时间戳并写入 CSV。", 10, MUTED)

        c.rect(32, 132, 1216, 104, PANEL, BORDER, 5)
        stream_name = str(
            getattr(collector.stream, "source_name", "LSL · obci_eeg1")
        ).replace("LSL · ", "")
        c.text(50, 143, 90, 24, "LSL 数据流", 9, MUTED, True)
        c.rect(50, 170, 486, 36, PANEL_SOFT, BORDER, 3)
        c.text(64, 170, 458, 36, stream_name, 10, TEXT)
        self._light_button(c, 552, 170, 140, 36, "刷新 / 选择")
        self._light_button(c, 704, 170, 128, 36, "开始收集", False, "#31875a")
        self._light_button(c, 844, 170, 128, 36, "停止收集", False, "#bd4b4b")
        self._light_button(c, 984, 170, 152, 36, "打开数据目录")
        self._hit(552, 170, 140, 36, "lsl-collector-refresh")
        self._hit(704, 170, 128, 36, "lsl-collector-start")
        self._hit(844, 170, 128, 36, "lsl-collector-stop")
        self._hit(984, 170, 152, 36, "lsl-collector-open-directory")
        c.circle(1160, 188, 5, CYAN)
        c.text(1172, 170, 58, 36, "EEG", 10, TEXT, True)
        c.text(50, 210, 720, 18, collector.status, 9, CYAN)
        c.text(790, 210, 438, 18, "默认：obci_eeg1 · EEG · 8 CH", 9, MUTED, False, Qt.AlignRight)

        running = collector.running
        stats = collector.recorder.snapshot()
        health_getter = getattr(collector.stream, "health_snapshot", None)
        try:
            health = dict(health_getter()) if callable(health_getter) else {}
        except Exception:
            health = {}
        metrics = (
            ("状态", "正在收集" if running else "等待"),
            ("样本数", str(int(stats.get("sample_count", 0) or 0))),
            ("时长", f"{float(stats.get('duration_seconds', 0.0) or 0.0):.1f} s"),
            ("实际采样率", f"{(collector.sample_rate if running else 0.0):.1f} Hz"),
        )
        for index, (label, value) in enumerate(metrics):
            x = 32 + index * 304
            c.rect(x, 250, 288, 62, PANEL_SOFT, BORDER, 3)
            c.text(x + 14, 257, 120, 20, label, 9, MUTED)
            c.text(x + 14, 279, 260, 25, value, 14, TEXT, True)

        c.text(32, 326, 900, 24, f"数据文件：{stats.get('path') or '开始收集后自动创建 CSV'}", 9, MUTED)
        self._light_button(
            c, 1084, 322, 164, 32,
            "恢复波形" if collector.waveform_paused else "暂停波形",
            collector.waveform_paused,
            "#2e628f",
        )
        self._hit(1084, 322, 164, 32, "lsl-collector-pause")

        c.rect(32, 368, 820, 328, PANEL, BORDER, 4)
        c.text(50, 376, 470, 28, "最近 5 秒 LSL 原始脑电（最多 16 通道）", 12, TEXT, True)
        c.text(628, 376, 206, 28, "μV  ·  自动纵向缩放", 9, MUTED, False, Qt.AlignRight)
        c.line(50, 408, 834, 408, BORDER)
        self._draw_lsl_collector_eeg(c, 50, 416, 784, 252)
        c.text(50, 670, 784, 18, "-5s                                  时间                                  0s", 8, MUTED, False, Qt.AlignCenter)

        c.rect(868, 368, 380, 328, PANEL, BORDER, 4)
        c.text(886, 376, 344, 28, "运行日志 / 时间戳质量", 12, TEXT, True)
        gaps = int(health.get("gap_count", 0) or 0)
        c.text(886, 410, 160, 20, f"数据缺口：{gaps}", 9, RED if gaps else GREEN, True)
        c.text(1070, 410, 160, 20, f"通道：{len(collector.channel_names)}", 9, MUTED, False, Qt.AlignRight)
        c.line(886, 438, 1230, 438, BORDER)
        events = list(collector.events)[:11] or ["等待发现LSL EEG数据流…"]
        for index, event in enumerate(events):
            c.text(886, 446 + index * 20, 344, 18, str(event)[:52], 8, TEXT)
        c.text(886, 662, 344, 24, "暂停波形不影响 CSV 持续写入。", 8, YELLOW, False, Qt.AlignCenter)

    def _draw_lsl_collector_eeg(
        self, c: Canvas, x: float, y: float, w: float, h: float,
    ) -> None:
        collector = self.lsl_collector
        histories = [list(values) for values in collector.history[:16]]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 LSL EEG 数据…", 14, MUTED, False, Qt.AlignCenter)
            return
        palette = RESEARCH_TRACE_COLORS
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#e1e7ee")
            label = collector.channel_names[index] if index < len(collector.channel_names) else f"CH{index + 1}"
            c.text(x, baseline - 10, 54, 20, label[:8], 8, MUTED)
            if len(values) < 2:
                continue
            data = np.asarray(values, dtype=float)
            center = float(np.median(data))
            amplitude = max(float(np.percentile(np.abs(data - center), 95)), 1e-9)
            path = QPainterPath()
            for sample_index, value in enumerate(data):
                px = x + 58 + sample_index / max(1, len(data) - 1) * (w - 62)
                py = baseline - (float(value) - center) / amplitude * row_height * 0.34
                path.moveTo(px, py) if sample_index == 0 else path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index % len(palette)]), 1.0))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.p.restore()



__all__ = ["ResearchCollectionViewMixin"]
