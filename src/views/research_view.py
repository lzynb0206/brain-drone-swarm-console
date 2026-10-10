"""Researcher dashboard rendering."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.bci.config import SSVEP_PROFILE_NAME, TARGET_FREQUENCIES_HZ
from src.widgets.scifi_canvas import (
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class ResearchViewMixin:
    """Render researcher EEG, paradigm, algorithm, control, mapping and data pages."""
    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        nav = [
            ("采集", 231), ("实验", 297), ("范式", 363), ("算法", 429),
            ("控制", 495), ("映射", 561), ("数据", 627),
        ]
        for name, x in nav:
            self._hit(x, 0, 60, 57, f"research:{name}")
        self._hit(710, 0, 60, 56, "self-test")
        self._hit(778, 0, 60, 56, "workspace-help")
        self._hit(1020, 8, 36, 38, "logout")
        self._hit(1068, 8, 36, 38, "workspace-summary")
        self._hit(1117, 8, 36, 38, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        {
            "采集": self._research_acquisition,
            "实验": self._research_replay,
            "范式": self._research_paradigm,
            "算法": self._research_algorithm,
            "控制": self._research_controller,
            "映射": self._research_mapping,
            "数据": self._research_data,
        }[self.research_page](c)

    def _research_acquisition(self, c: Canvas) -> None:
        """OpenBCI-style live acquisition shell backed by the existing LSL worker."""
        light_bg, panel, border = "#eef2f5", "#ffffff", "#cbd3dc"
        ink, sub, blue = "#24313d", "#64717e", "#2f5f89"
        c.rect(0, 56, 1280, 664, light_bg)
        source = self._pipeline_worker.source_name if self._pipeline_worker is not None else "未配置"
        channel_count = len(self._eeg_channel_names) or len(self._eeg_history)
        csv_stats = self._raw_csv_recorder.snapshot()

        c.rect(16, 72, 320, 632, panel, border, 3)
        c.rect(16, 72, 320, 42, blue, blue, 3)
        c.text(32, 72, 288, 42, "OPENBCI SESSION CONTROL", 13, "#ffffff", True)
        fields = (
            ("DATA SOURCE", "CYTON / ExpandableEEG"),
            ("TRANSFER PROTOCOL", self._openbci_transport),
            ("LSL STREAM", source.replace("LSL · ", "")),
            ("CHANNEL COUNT", f"{channel_count or self._openbci_channel_count} CHANNELS"),
            ("SAMPLE RATE", f"{self._eeg_sample_rate:.1f} Hz" if self._eeg_sample_rate else "WAITING"),
            ("WIFI IP (manual)", self._openbci_ip),
        )
        for index, (label, value) in enumerate(fields):
            y = 126 + index * 55
            c.text(31, y, 274, 17, label, 9, sub, True)
            c.rect(31, y + 19, 274, 29, "#f8fafb", border, 2)
            c.text(41, y + 19, 254, 29, value, 11, ink)

        self._light_button(c, 31, 462, 126, 34, "REFRESH LSL", self._lsl_discovery_in_progress)
        self._light_button(c, 166, 462, 139, 34, "START SESSION", False, "#2d8a57")
        self._light_button(c, 31, 504, 84, 34, "DISCONNECT", False, "#bd4b4b")
        self._light_button(
            c, 124, 504, 181, 34,
            "STOP CSV RECORD" if self._continuous_recording else "START CSV RECORD",
            self._continuous_recording,
            "#c94e4e",
        )
        self._hit(31, 462, 126, 34, "lsl-refresh")
        self._hit(166, 462, 139, 34, "eeg-connect")
        self._hit(31, 504, 84, 34, "eeg-disconnect")
        self._hit(124, 504, 181, 34, "continuous-record-toggle")
        self._light_button(c, 31, 546, 274, 32, "OPENBCI / LSL 接入说明")
        self._hit(31, 546, 274, 32, "openbci-guide")

        connected = bool(self._eeg_health.get("connected", False)) or self._eeg_status.startswith("已连接")
        c.circle(41, 603, 6, "#2fa866" if connected else "#c34f4f")
        c.text(55, 588, 250, 30, self._eeg_status, 11, ink, True)
        c.text(31, 620, 274, 20, f"Samples  {self._eeg_health.get('total_samples', 0)}", 10, sub)
        c.text(31, 641, 274, 20, f"CSV  {csv_stats.get('sample_count', 0)} samples", 10, sub)
        path = str(csv_stats.get("path") or "等待首帧后创建文件")
        c.text(31, 664, 274, 28, path[-47:], 9, sub)

        c.rect(352, 72, 912, 632, panel, border, 3)
        tabs = ("Time Series", "FFT Plot", "Focus", "Accelerometer", "LSL", "Packet Loss", "Hardware")
        for index, tab in enumerate(tabs):
            x = 362 + index * 126
            active = self.acquisition_tab == tab
            c.rect(x, 80, 118, 31, blue if active else "#edf1f4", border, 2)
            c.text(x, 80, 118, 31, tab, 10, "#ffffff" if active else ink, active, Qt.AlignCenter)
            self._hit(x, 80, 118, 31, f"acquisition-tab:{tab}")
        c.line(352, 120, 1264, 120, border)

        renderers = {
            "Time Series": self._acquisition_time_series,
            "FFT Plot": self._acquisition_fft,
            "Focus": self._acquisition_focus,
            "Accelerometer": self._acquisition_accelerometer,
            "LSL": self._acquisition_lsl,
            "Packet Loss": self._acquisition_packet_loss,
            "Hardware": self._acquisition_hardware,
        }
        renderers[self.acquisition_tab](c, 370, 134, 876, 550)

    def _research_replay(self, c: Canvas) -> None:
        """CSV experiment replay is deliberately separate from live acquisition."""
        light_bg, panel, border = "#eef2f5", "#ffffff", "#cbd3dc"
        ink, sub, blue = "#24313d", "#64717e", "#2f5f89"
        c.rect(0, 56, 1280, 664, light_bg)
        snapshot = self._csv_playback_snapshot()
        is_csv = snapshot.get("backend") == "csv"
        c.rect(16, 72, 1248, 96, panel, border, 3)
        c.text(31, 78, 250, 31, "CSV 实验回放", 17, ink, True)
        self._light_button(c, 31, 116, 116, 34, "导入 EEG CSV", False, blue)
        self._light_button(
            c, 158, 116, 94, 34,
            "继续回放" if self._csv_replay_paused else "暂停回放",
            False,
            "#2d8a57",
        )
        self._light_button(c, 263, 116, 84, 34, "回到开头")
        self._hit(31, 116, 116, 34, "csv-import")
        self._hit(158, 116, 94, 34, "csv-playback-toggle")
        self._hit(263, 116, 84, 34, "csv-rewind")
        for index, speed in enumerate((0.5, 1.0, 2.0)):
            x = 1081 + index * 53
            active = abs(float(snapshot.get("speed", 1.0) or 1.0) - speed) < 0.01
            self._light_button(c, x, 116, 47, 34, f"{speed:g}x", active, blue)
            self._hit(x, 116, 47, 34, f"csv-speed:{speed}")
        file_name = str(snapshot.get("file_name") or "尚未导入 CSV")
        c.text(368, 82, 680, 25, file_name, 12, ink, True)
        elapsed = float(snapshot.get("elapsed_seconds", 0.0) or 0.0)
        duration = float(snapshot.get("duration_seconds", 0.0) or 0.0)
        progress = float(snapshot.get("progress", 0.0) or 0.0)
        c.text(368, 108, 650, 19, f"{elapsed:7.1f}s / {duration:7.1f}s", 10, sub)
        c.line(384, 143, 1034, 143, "#d4d9de", 8)
        c.line(384, 143, 384 + 650 * progress, 143, blue, 8)
        c.circle(384 + 650 * progress, 143, 7, blue)
        self._hit(376, 132, 666, 24, "csv-seek")

        c.rect(16, 180, 900, 524, panel, border, 3)
        c.text(34, 186, 540, 32, "原始 EEG 波形 · CSV Replay", 14, ink, True)
        c.text(710, 186, 185, 32, f"{snapshot.get('channel_count', 0)} ch  ·  {float(snapshot.get('sample_rate_hz', 0.0) or 0.0):.1f} Hz", 10, sub, False, Qt.AlignRight)
        self._draw_light_eeg(c, 36, 226, 860, 452)

        c.rect(928, 180, 336, 524, panel, border, 3)
        c.rect(928, 180, 336, 42, blue, blue, 3)
        c.text(946, 180, 300, 42, "EXPERIMENT REVIEW", 12, "#ffffff", True)
        c.text(946, 238, 290, 19, "数据来源", 9, sub, True)
        c.text(946, 258, 290, 42, str(snapshot.get("path") or "请导入 CSV 文件")[-48:], 10, ink)
        c.text(946, 312, 290, 19, "回放状态", 9, sub, True)
        state = "PAUSED" if self._csv_replay_paused else ("PLAYING" if is_csv else "NO FILE")
        c.text(946, 333, 290, 30, state, 18, "#2d8a57" if is_csv else "#bd4b4b", True)
        c.text(946, 380, 290, 19, "范式 / 解码", 9, sub, True)
        mode = self.actions.paradigm_type
        decoder = "FBCCA（仅 SSVEP）" if mode == "SSVEP" else "运动想象：仅回放和标注"
        c.text(946, 402, 290, 44, f"{mode}\n{decoder}", 11, ink)
        c.line(946, 463, 1244, 463, border)
        if self._latest_recognition is None:
            c.text(946, 482, 290, 54, "等待完整解码窗口…", 12, sub)
        else:
            result = self._latest_recognition
            label = result.label if result.accepted else f"候选：{result.label}"
            c.text(946, 478, 290, 38, label, 20, "#2d8a57", True)
            c.text(946, 518, 290, 23, f"Score {result.best_score:.3f}  Margin {result.margin:.3f}", 10, sub)
            c.text(946, 545, 290, 38, result.reason, 10, ink)
        c.rect(946, 606, 298, 69, "#f7f9fa", border, 2)
        c.text(958, 613, 274, 55, "CSV 回放不会直接启用无人机脑控。\n它用于复盘、算法验证和结果对比。", 10, "#9b6a19")

    def _light_button(
        self, c: Canvas, x: float, y: float, w: float, h: float, label: str,
        active: bool = False, accent: str = "#2f5f89",
    ) -> None:
        c.rect(x, y, w, h, accent if active else "#f7f9fa", accent, 2)
        c.text(x, y, w, h, label, 10, "#ffffff" if active else accent, active, Qt.AlignCenter)

    def _draw_light_eeg(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        histories = [list(values) for values in self._eeg_history]
        if not histories or not any(histories):
            c.text(x, y, w, h, "Waiting for EEG data…", 15, "#7b8792", False, Qt.AlignCenter)
            return
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x + 45, baseline, x + w, baseline, "#e1e5e8")
            label = self._eeg_channel_names[index] if index < len(self._eeg_channel_names) else f"CH{index + 1}"
            c.circle(x + 15, baseline, 11, palette[index % len(palette)])
            c.text(x + 4, baseline - 10, 22, 20, str(index + 1), 9, "#ffffff", True, Qt.AlignCenter)
            c.text(x + 31, baseline - 11, 52, 20, label[:8], 8, "#697580")
            if len(values) < 2:
                continue
            data = np.asarray(values, dtype=float)
            center = float(np.median(data))
            amplitude = max(float(np.percentile(np.abs(data - center), 95)), 1e-9)
            path = QPainterPath()
            for sample_index, value in enumerate(data):
                px = x + 83 + sample_index / max(1, len(data) - 1) * (w - 86)
                py = baseline - (float(value) - center) / amplitude * row_height * 0.34
                path.moveTo(px, py) if sample_index == 0 else path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index % len(palette)]), 1.05))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.p.restore()

    def _acquisition_time_series(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, 350, 28, "Time Series", 16, "#24313d", True)
        c.text(x + w - 270, y, 270, 28, "Vert Scale  Auto   ·   Window  5 sec", 10, "#64717e", False, Qt.AlignRight)
        self._draw_light_eeg(c, x, y + 38, w, h - 42)

    def _acquisition_fft(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, 350, 28, "FFT Plot", 16, "#24313d", True)
        c.text(x + w - 310, y, 310, 28, "Max Freq 60 Hz   ·   LogLin Smooth", 10, "#64717e", False, Qt.AlignRight)
        plot_x, plot_y, plot_w, plot_h = x + 58, y + 48, w - 72, h - 86
        c.rect(plot_x, plot_y, plot_w, plot_h, "#ffffff", "#cbd3dc")
        for index in range(1, 6):
            gx = plot_x + plot_w * index / 6
            c.line(gx, plot_y, gx, plot_y + plot_h, "#e5e9ec")
            c.text(gx - 18, plot_y + plot_h + 5, 36, 20, f"{index * 10}", 9, "#75808a", False, Qt.AlignCenter)
        histories = [np.asarray(values, dtype=float) for values in self._eeg_history if len(values) >= 32]
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        for index, values in enumerate(histories[:8]):
            centered = values - np.mean(values)
            spectrum = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
            frequencies = np.fft.rfftfreq(len(centered), 1.0 / max(self._eeg_sample_rate, 1.0))
            mask = frequencies <= 60.0
            spectrum = np.log1p(spectrum[mask])
            frequencies = frequencies[mask]
            if not len(spectrum) or float(np.max(spectrum)) <= 0:
                continue
            spectrum = spectrum / float(np.max(spectrum))
            path = QPainterPath()
            for point, (frequency, amplitude) in enumerate(zip(frequencies, spectrum)):
                px = plot_x + float(frequency) / 60.0 * plot_w
                py = plot_y + plot_h - float(amplitude) * plot_h * 0.9
                path.moveTo(px, py) if point == 0 else path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index]), 1.2))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.text(plot_x, plot_y + plot_h + 27, plot_w, 20, "Frequency (Hz)", 10, "#64717e", False, Qt.AlignCenter)

    def _acquisition_focus(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Focus / Signal Quality", 16, "#24313d", True)
        quality_status = str(self._eeg_quality.get("status", "waiting"))
        usable = int(self._eeg_quality.get("usable_channel_count", 0) or 0)
        count = max(1, len(self._eeg_channel_names))
        c.text(x, y + 48, w, 42, f"{usable} / {count} usable channels", 27, "#2f5f89", True, Qt.AlignCenter)
        c.text(x, y + 91, w, 24, f"Quality state: {quality_status}", 11, "#64717e", False, Qt.AlignCenter)
        for index in range(count):
            row_y = y + 145 + index * min(42, 310 / count)
            values = list(self._eeg_history[index]) if index < len(self._eeg_history) else []
            spread = float(np.std(values)) if values else 0.0
            score = min(1.0, spread / (spread + 10.0)) if spread else 0.0
            label = self._eeg_channel_names[index] if index < len(self._eeg_channel_names) else f"CH{index + 1}"
            c.text(x + 70, row_y, 90, 24, label, 10, "#24313d", True)
            c.rect(x + 160, row_y + 6, w - 260, 12, "#e2e7ea", None, 6)
            c.rect(x + 160, row_y + 6, (w - 260) * score, 12, "#49a66e", None, 6)
            c.text(x + w - 90, row_y, 60, 24, f"{spread:.1f}", 10, "#64717e", False, Qt.AlignRight)

    def _acquisition_accelerometer(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Accelerometer", 16, "#24313d", True)
        c.rect(x + 40, y + 54, w - 80, h - 130, "#ffffff", "#cbd3dc")
        for index, (axis, color) in enumerate((("X", "#d84e4e"), ("Y", "#48a15f"), ("Z", "#3c75ba"))):
            baseline = y + 120 + index * 90
            c.line(x + 70, baseline, x + w - 70, baseline, "#e1e5e8")
            c.text(x + 54, baseline - 14, 30, 28, axis, 12, color, True)
        c.text(x + 60, y + h - 58, w - 120, 34, "当前 LSL 流只有 EEG 通道；若 OpenBCI 输出 AUX 通道，将在这里显示 XYZ。", 11, "#8a6c2b", False, Qt.AlignCenter)

    def _acquisition_lsl(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Networking · LSL", 16, "#24313d", True)
        c.text(x, y + 35, w, 24, "OpenBCI / ExpandableEEG → LSL → 本上位机 → CSV / 解码器", 11, "#2f5f89", True)
        headers = ("", "Stream 1", "Stream 2", "Stream 3")
        col_x = (x + 38, x + 185, x + 365, x + 545)
        for value, left in zip(headers, col_x):
            c.text(left, y + 92, 160, 28, value, 11, "#24313d", True, Qt.AlignCenter)
        rows = (
            ("Data Type", "TimeSeriesRaw", "None", "None"),
            ("Name", str(self._pipeline_worker.source_name if self._pipeline_worker else "obci_eeg1").replace("LSL · ", ""), "", ""),
            ("Type", "EEG", "", ""),
        )
        for row_index, row in enumerate(rows):
            row_y = y + 124 + row_index * 45
            for column, value in enumerate(row):
                left = col_x[column]
                c.rect(left, row_y, 160, 32, "#f7f9fa", "#cbd3dc", 1)
                c.text(left + 6, row_y, 148, 32, value, 10, "#24313d", column == 0)
        self._light_button(c, x + 245, y + 295, 180, 38, "REFRESH LSL STREAMS", self._lsl_discovery_in_progress)
        self._hit(x + 245, y + 295, 180, 38, "lsl-refresh")
        c.text(x + 38, y + 370, w - 76, 70, "说明书中的 Start LSL Stream 位于 OpenBCI GUI 输出端。\n本页面负责发现并接收该流，同时原样保存 sample_index、LSL timestamp 和全部 EEG 通道。", 11, "#64717e", False, Qt.AlignCenter)

    def _acquisition_packet_loss(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Packet Loss / Timestamp Health", 16, "#24313d", True)
        health = self._eeg_health
        cards = (
            ("TOTAL SAMPLES", str(health.get("total_samples", 0))),
            ("NON-MONOTONIC", str(health.get("non_monotonic_timestamps", 0))),
            ("GAPS", str(health.get("gap_count", 0))),
            ("MAX GAP", f"{float(health.get('max_gap_seconds', 0.0) or 0.0):.4f}s"),
        )
        for index, (label, value) in enumerate(cards):
            card_x = x + 18 + index * 210
            c.rect(card_x, y + 58, 190, 94, "#f7f9fa", "#cbd3dc", 3)
            c.text(card_x, y + 69, 190, 22, label, 9, "#64717e", True, Qt.AlignCenter)
            c.text(card_x, y + 94, 190, 40, value, 22, "#2f5f89", True, Qt.AlignCenter)
        gaps = int(health.get("gap_count", 0) or 0)
        c.circle(x + w / 2, y + 245, 64, "#54ad75" if gaps == 0 else "#d3a13a")
        c.text(x + w / 2 - 64, y + 210, 128, 70, "PASS" if gaps == 0 else "CHECK", 24, "#ffffff", True, Qt.AlignCenter)
        c.text(x + 70, y + 345, w - 140, 70, "根据逐样本 LSL 时间戳检测乱序、重复和超出 2 倍采样间隔的数据缺口。\n这些指标会随实验一起记录，用于判断数据是否适合训练和论文分析。", 11, "#64717e", False, Qt.AlignCenter)

    def _acquisition_hardware(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Hardware Settings · ADS1299", 16, "#24313d", True)
        headers = ("CH", "PGA Gain", "Input Type", "Bias Include", "SRB2", "SRB1")
        starts = (x + 20, x + 85, x + 205, x + 365, x + 535, x + 665)
        widths = (55, 110, 150, 160, 120, 120)
        for label, left, width in zip(headers, starts, widths):
            c.text(left, y + 40, width, 28, label, 9, "#64717e", True, Qt.AlignCenter)
        for index, settings in enumerate(self._hardware_channel_settings):
            row_y = y + 73 + index * 47
            c.circle(x + 47, row_y + 16, 13, ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")[index])
            c.text(x + 34, row_y + 3, 26, 26, str(index + 1), 9, "#ffffff", True, Qt.AlignCenter)
            for key, left, width in zip(("gain", "input", "bias", "srb2", "srb1"), starts[1:], widths[1:]):
                fill = "#e9f5e9" if settings[key] in {"Yes", "On"} else ("#f8e8e8" if settings[key] == "Off" else "#f7f9fa")
                c.rect(left, row_y, width, 31, fill, "#cbd3dc", 1)
                c.text(left, row_y, width, 31, settings[key], 9, "#24313d", False, Qt.AlignCenter)
        c.text(x + 18, y + 468, w - 220, 45, "当前 LSL 为只读数据通道。需要直接下发 x / b / s 等 Cyton 命令时，\n必须启用 BrainFlow/串口硬件后端；此版本不会伪造“发送成功”。", 10, "#9b6a19")
        self._light_button(c, x + w - 180, y + 468, 160, 38, "SEND（硬件后端未启用）", False, "#9aa3aa")

    def _draw_live_eeg(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        histories = [list(values) for values in self._eeg_history]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 EEG 数据…", 15, MUTED, False, Qt.AlignCenter)
            return
        palette = ("#ef514d", "#f1c64b", "#62df8a", "#65d9e9", "#6c9bd9", "#987bd8", "#ef7ac8", "#e5e9f0")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#1d2c45")
            label = (
                self._eeg_channel_names[index]
                if index < len(self._eeg_channel_names)
                else f"CH{index + 1}"
            )
            c.text(x, baseline - 11, 38, 20, label[:7], 8, MUTED)
            if len(values) < 2:
                continue
            center = sum(values) / len(values)
            amplitude = max(max(abs(value - center) for value in values), 1e-9)
            path = QPainterPath()
            for sample_index, value in enumerate(values):
                px = x + 40 + sample_index / max(1, len(values) - 1) * (w - 42)
                py = baseline - (value - center) / amplitude * row_height * 0.39
                if sample_index == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index % len(palette)]), 1.1))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.p.restore()

    def _research_paradigm(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-06 / M4.6：“应用配置”应调用 ParadigmService：
        # 校验 SSVEP 频率/刺激尺寸/占空比，保存到 YAML/JSON，并通知刺激程序；
        # 每次范式开始/结束还应向 LSL 写入 SSVEP_8Hz_start 等事件标记。
        c.rect(207, 262, 384, 252, PANEL, BORDER, 4)
        c.rect(232, 287, 47, 202, "#111c31", BLUE, 3)
        c.p.save()
        c.p.translate(255, 388)
        c.p.rotate(90)
        c.text(-55, -17, 110, 34, "SSVEP", 15, CYAN, True, Qt.AlignCenter)
        c.p.restore()
        options = [("SSVEP", 287), ("MI", 361), ("SSVEP+MI混合", 434)]
        for name, y in options:
            active = self.actions.paradigm_type == name
            c.rect(296, y, 270, 57, "#1a263a" if active else "#172236", BLUE if active else None, 3)
            c.text(312, y, 210, 57, name, 15, TEXT if active else MUTED)
            c.text(528, y, 22, 57, "⌄", 16, MUTED, False, Qt.AlignCenter)
            self._hit(296, y, 270, 57, f"paradigm-type:{name}")
        c.rect(623, 205, 450, 366, PANEL, BORDER, 4)
        if self.actions.paradigm_type == "SSVEP":
            c.text(656, 229, 380, 42, "SSVEP参数配置", 19, TEXT, True)
            c.text(656, 282, 130, 27, "频率配置", 14, MUTED)
            for index, frequency in enumerate(TARGET_FREQUENCIES_HZ):
                x, width = 656 + index * 63, 58
                c.button(x, 322, width, 42, f"{frequency:g}Hz", self.actions.paradigm_frequency == frequency)
                self._hit(x, 322, width, 42, f"paradigm-frequency:{frequency}")
            knobs = (
                ("刺激尺寸", 390, 739 + 299 * self.actions.stimulus_size / 100, "paradigm-size"),
                ("占空比", 437, 739 + 299 * self.actions.duty_cycle / 100, "paradigm-duty"),
            )
            for label, y, knob, action in knobs:
                c.text(656, y - 8, 88, 34, label, 14, MUTED)
                c.line(739, y + 7, 1038, y + 7, "#e6e8eb", 7)
                c.line(739, y + 7, knob, y + 7, BLUE, 7)
                c.circle(knob, y + 7, 8, "#477af0")
                c.text(1044, y - 8, 45, 30, f"{self.actions.stimulus_size if action == 'paradigm-size' else self.actions.duty_cycle}%", 11, TEXT)
                self._hit(730, y - 5, 318, 25, action)
            c.button(656, 492, 116, 46, "应用配置", True)
            c.button(783, 492, 116, 46, "恢复默认")
            c.button(910, 492, 130, 46, "全屏刺激", True, accent=CYAN)
            self._hit(656, 492, 116, 46, "paradigm-apply")
            self._hit(783, 492, 116, 46, "paradigm-reset")
            self._hit(910, 492, 130, 46, "paradigm-start")
        else:
            c.text(656, 229, 380, 42, "运动想象（MI）采集配置", 19, TEXT, True)
            c.text(656, 278, 380, 24, "当前阶段：采集与标注，不生成无人机指令", 12, YELLOW, True)
            for index, label in enumerate(("静息", "想象左手", "想象右手", "想象双脚")):
                x = 656 + (index % 2) * 188
                y = 318 + (index // 2) * 52
                c.rect(x, y, 174, 39, "#152239", "#2b3b58", 3)
                c.text(x, y, 174, 39, label, 12, TEXT, True, Qt.AlignCenter)
            c.text(656, 428, 380, 23, "建议关注：C3 / Cz / C4（以实际电极帽标注为准）", 11, CYAN)
            c.text(656, 456, 380, 46, "操作：连接 LSL → 回到“实验”检查质量 →\n点击右上角“添加标签” → 完成 4 秒想象", 11, MUTED)
            c.button(656, 515, 180, 38, "启用 MI 采集模式", True, accent=CYAN)
            c.text(848, 515, 190, 38, "FBCCA 已停用", 12, GREEN, True, Qt.AlignCenter)
            self._hit(656, 515, 180, 38, "paradigm-start")

    def _research_algorithm(self, c: Canvas) -> None:
        # This build intentionally uses only the non-trained FBCCA path.
        c.rect(48, 104, 1184, 69, PANEL, BORDER, 3)
        c.text(66, 116, 92, 40, "当前算法:", 14, TEXT)
        c.button(158, 121, 191, 36, self.actions.algorithm)
        radios = ["固定解码"]
        x = 404
        for label in radios:
            active = self.actions.algorithm_mode == label
            c.circle(x + 9, 139, 7, BLUE if active else None, "#738098", 2)
            c.text(x + 23, 121, 87, 36, label, 13, TEXT)
            self._hit(x, 116, 101, 46, f"algorithm-mode:{label}")
            x += 101
        c.rect(48, 189, 1184, 171, PANEL, BORDER, 3)
        metadata = [("名称:", "纯 FBCCA", 73, 207), ("类型:", "无训练模型", 356, 207),
                    ("数据源:", "CSV / LSL", 640, 207), ("准确率:", "待真实标签评估", 922, 207),
                    ("识别窗:", "4秒", 73, 256), ("更新步长:", "1秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA"]
        for i, name in enumerate(algorithms):
            x = 73 + i * 144
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            c.rect(x, 441, 128, 96, "#17243a", BLUE if selected else "#2b3a55", 3, 2 if selected else 1)
            c.text(x, 452, 128, 37, name, 14, TEXT, True, Qt.AlignCenter)
            c.rect(x + 36, 504, 32, 16, GREEN if loaded else "#526078", None, 8)
            c.circle(x + 61 if loaded else x + 45, 512, 5, TEXT)
            c.circle(x + 84, 512, 8, YELLOW if default else "#2b3954")
            self._hit(x, 441, 128, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_controller(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-08 / M6.5：滑块和本地应用动作已可操作；正式版
        # ControllerService 还需将 Kp/Ki/Kd 校验后交给实现 BaseController 的
        # PID/MPC 控制器；参数写入 YAML，并保留版本和操作者审计记录。
        c.rect(16, 72, 64, 648, "#101b30", "#1d2d4b")
        c.rect(31, 96, 32, 3, BLUE)
        for y in (124, 172, 220, 268, 316, 364):
            c.rect(36, y, 24, 24, "#253b60", None, 4)
        c.text(112, 97, 260, 36, "控制器参数工作台", 15, TEXT, True)
        c.rect(112, 152, 1120, 69, PANEL, BORDER, 3)
        c.text(130, 165, 106, 40, "当前控制器:", 14, TEXT)
        c.button(238, 169, 128, 36, self.actions.controller)
        modes = [("● 手动选择", True), ("□ 场景自动匹配", False), ("□ 自适应切换", False), ("□ 双控制器热备", False)]
        x = 402
        for label, active in modes:
            c.button(x, 171, 126, 31, label, False, not active)
            x += 133
        c.rect(112, 246, 1120, 324, PANEL, BORDER, 3)
        c.text(145, 268, 300, 40, f"{self.actions.controller}参数配置", 17, TEXT, True)
        for label, y in (("Kp", 346), ("Ki", 402), ("Kd", 458)):
            value = self.actions.controller_values[label]
            end = 203 + 707 * value / 100
            c.text(145, y - 18, 45, 35, label, 14, TEXT)
            c.line(203, y, 910, y, "#6d6f70", 7)
            c.line(203, y, end, y, "#eeeeee", 7)
            c.circle(end, y, 8, "#f2f2f2")
            c.text(932, y - 17, 74, 34, f"{value / 100:.2f}", 13, TEXT)
            self._hit(196, y - 14, 721, 28, f"controller-slider:{label}")
        for i, (label, action) in enumerate((("应用参数", "controller-apply"), ("保存预设", "controller-save"), ("恢复默认", "controller-reset"))):
            x = 486 + i * 137
            c.button(x, 502, 121, 36, label, i == 0)
            self._hit(x, 502, 121, 36, action)
        c.rect(112, 595, 1120, 160, PANEL, BORDER, 3)
        c.text(137, 606, 200, 42, "控制器库", 16, TEXT, True)
        controllers = ("PID", "MPC", "新增", "滑模控制", "鲁棒控制", "自适应控制")
        for i, name in enumerate(controllers):
            x = 137 + i * 128
            disabled = name == "新增"
            c.button(x, 659, 104, 40, name, name == self.actions.controller, disabled, "#69c77a")
            if not disabled:
                self._hit(x, 659, 104, 40, f"controller-select:{name}")

    def _research_mapping(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-09 / M4.5：映射表应来自 CommandMappingRepository。
        # 解码 label/频率先经本映射转换为 SwarmCommand，再由安全仲裁器检查
        # 置信度、二次确认、Flow 质量、电量、解锁和急停状态，
        # 最后才调用 swarm_gateway.send_command。
        panels = [(192, 112, 896, 302), (192, 438, 896, 184), (192, 646, 896, 150)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(217, 128, 430, 42, "SSVEP 识别与安全动作映射", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 185, 104, 34, text, 13, TEXT, True, Qt.AlignCenter)
        c.line(433, 216, 847, 216, "#273752")
        for i, row in enumerate(self.actions.mappings[:6]):
            y = 214 + i * 27
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 26, 847, y + 26, "#273752")
        c.button(576, 380, 128, 28, "添加映射", True)
        self._hit(576, 380, 128, 28, "mapping-add")
        c.text(217, 454, 360, 42, "Flow Deck 单机验收", 16, TEXT, True)
        acceptance = (
            ("LED 无桨测试", "led_test"),
            ("保持悬停", "hover"),
            ("受控降落", "land"),
        )
        for index, (name, action) in enumerate(acceptance):
            button_x = 310 + index * 190
            c.button(button_x, 510, 170, 42, name)
            self._hit(button_x, 510, 170, 42, f"swarm-command:{action}")
        c.text(235, 575, 830, 23, "连接、Flow Deck 测距/光流、解锁和急停检查通过后才会执行", 11, "#596477", False, Qt.AlignCenter)
        c.text(217, 663, 360, 42, "L3 战术意图 (占位)", 16, TEXT, True)

    def _research_data(self, c: Canvas) -> None:
        # The left list and counts come from the recorder index, not painted demo sessions.
        c.rect(16, 72, 1248, 35, "#0b1425")
        c.text(39, 72, 360, 35, "实验数据工作台", 17, TEXT, True)
        for i, name in enumerate(("数据浏览", "标签管理", "采集说明")):
            x = 543 + i * 106
            c.button(x, 72, 90, 34, name, self.actions.data_tab == name)
            self._hit(x, 72, 90, 34, f"data-tab:{name}")
        c.text(1185, 72, 62, 34, "⌕  ⇧", 20, MUTED, False, Qt.AlignCenter)
        c.rect(32, 123, 254, 597, "#0b1425", BORDER)
        c.text(40, 125, 190, 43, "数据浏览", 14, TEXT, True)
        c.text(260, 125, 20, 43, "×", 16, TEXT, True, Qt.AlignCenter)
        c.line(32, 168, 286, 168, BORDER)
        c.text(40, 169, 210, 38, "实验会话", 13, MUTED)
        trials = self.experiment_recorder.recent_trials(12) if self.experiment_recorder is not None else []
        names = [f"{row.get('timestamp', '')[5:19]}  {row.get('true_label', '')}" for row in trials]
        if not names:
            names = ["暂无带标签原始试次"]
        for i, name in enumerate(names):
            y = 211 + i * 40
            if trials and i == self.actions.selected_session:
                c.rect(41, y, 238, 36, BLUE, None, 3)
            c.text(52, y, 195, 36, name, 13, TEXT)
            if trials:
                c.text(250, y, 20, 36, "›", 18, MUTED, False, Qt.AlignCenter)
                self._hit(41, y, 238, 36, f"data-session:{i}")
        c.rect(304, 123, 944, 597, "#071020", BORDER)
        c.text(317, 124, 420, 44, "当前 EEG 缓存与保存状态", 14, TEXT, True)
        c.text(1203, 124, 34, 44, "⌕×", 18, MUTED, False, Qt.AlignCenter)
        c.line(304, 168, 1248, 168, BORDER)
        self._draw_live_eeg(c, 335, 205, 880, 330)
        stats = self.experiment_recorder.stats() if self.experiment_recorder is not None else {"recognitions": 0, "labeled_trials": 0}
        c.rect(335, 557, 880, 116, "#0c1628", "#263752", 3)
        c.text(355, 567, 840, 27, f"识别日志：{stats['recognitions']} 条    带真实标签试次：{stats['labeled_trials']} 条", 13, TEXT, True)
        c.text(355, 599, 840, 23, "点击右上角“添加标签”后，将重新采集并保存连续 4 秒带标签 EEG。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------
