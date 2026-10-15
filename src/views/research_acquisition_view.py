"""Focused researcher rendering for  research acquisition."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.widgets.scifi_canvas import (
    BG, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class ResearchAcquisitionViewMixin:
    """Rendering behavior extracted from the researcher page shell."""
    def _research_acquisition(self, c: Canvas) -> None:
        """Render the hardware acquisition workflow in the research theme."""
        if self.acquisition_stage == "setup":
            self._acquisition_setup(c)
            return
        self._acquisition_runtime_shell(c)
        if self.acquisition_tab == "Dashboard":
            self._acquisition_dashboard_page(c)
            return
        c.rect(32, 218, 1216, 478, PANEL, BORDER, 4)
        renderers = {
            "Focus": self._acquisition_focus,
            "LSL": self._acquisition_lsl,
            "Packet Loss": self._acquisition_packet_loss,
            "Hardware": self._acquisition_hardware,
        }
        renderers[self.acquisition_tab](c, 52, 234, 1176, 446)

    def _acquisition_runtime_shell(self, c: Canvas) -> None:
        """Shared, aligned header and tab bar for every live acquisition view."""

        c.rect(0, 56, 1280, 664, BG)
        streaming = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        connected = bool(self._eeg_health.get("connected", False)) or self._eeg_status.startswith("已连接")
        csv_stats = self._raw_csv_recorder.snapshot()
        source = (
            self._pipeline_worker.source_name
            if self._pipeline_worker is not None and streaming
            else self._collector_selected_device or "未选择设备"
        ).replace("OpenBCI Direct · ", "")
        channel_count = len(self._eeg_channel_names) or len(self._eeg_history) or 8
        sample_rate = self._eeg_sample_rate or self._openbci_sample_rate

        c.text(32, 69, 310, 31, "实时脑电采集", 20, TEXT, True)
        c.circle(350, 84, 5, GREEN if connected else RED)
        c.text(363, 70, 410, 28, self._eeg_status, 10, TEXT if connected else MUTED)
        self._light_button(c, 820, 70, 112, 32, "返回连接配置")
        self._light_button(
            c, 944, 70, 132, 32,
            "停止数据流" if streaming else "启动数据流",
            streaming,
            "#2d8a57",
        )
        self._light_button(
            c, 1088, 70, 160, 32,
            "停止 CSV 记录" if self._continuous_recording else "开始 CSV 记录",
            self._continuous_recording,
            "#bd4b4b",
        )
        self._hit(820, 70, 112, 32, "openbci-control-panel")
        self._hit(944, 70, 132, 32, "openbci-data-stream-toggle")
        self._hit(1088, 70, 160, 32, "continuous-record-toggle")

        summary = (
            ("设备", source[-28:], 32, 318),
            ("通道", f"{channel_count} CH", 362, 138),
            ("采样率", f"{sample_rate:.0f} Hz", 512, 160),
            ("LSL 输出", "已开启" if self._lsl_outlet.active else "未开启", 684, 170),
            ("CSV 样本", str(csv_stats.get("sample_count", 0)), 866, 188),
            ("数据包", str(self._eeg_health.get("total_samples", 0)), 1066, 182),
        )
        for label, value, x, width in summary:
            c.rect(x, 112, width, 38, "#0c1729", BORDER, 3)
            c.text(x + 12, 112, width * 0.4, 38, label, 9, MUTED, True)
            c.text(x + width * 0.38, 112, width * 0.56, 38, value, 10, TEXT, True, Qt.AlignRight | Qt.AlignVCenter)

        tabs = (
            ("采集总览", "Dashboard"),
            ("信号质量", "Focus"),
            ("LSL 输出", "LSL"),
            ("丢包检测", "Packet Loss"),
            ("硬件设置", "Hardware"),
        )
        tab_width = 1216 / len(tabs)
        for index, (label, tab) in enumerate(tabs):
            x = 32 + index * tab_width
            active = self.acquisition_tab == tab
            c.rect(x, 164, tab_width, 38, "#14233b" if active else "#0b1425", BORDER)
            if active:
                c.line(x + 18, 200, x + tab_width - 18, 200, CYAN, 2)
            c.text(x, 164, tab_width, 36, label, 11, TEXT if active else MUTED, active, Qt.AlignCenter)
            self._hit(x, 164, tab_width, 38, f"acquisition-tab:{tab}")

    def _acquisition_setup(self, c: Canvas) -> None:
        """Direct connection page styled like the rest of the research console."""

        c.rect(0, 56, 1280, 664, BG)
        c.text(32, 72, 430, 34, "建立脑电采集会话", 21, TEXT, True)
        c.text(32, 105, 690, 24, "连接 ExpandableEEG / Cyton 兼容设备，确认参数后进入实时监测。", 11, MUTED)
        c.rect(1018, 76, 230, 30, "#0c1729", BORDER, 15)
        c.circle(1038, 91, 5, YELLOW)
        c.text(1052, 76, 176, 30, "等待设备连接", 10, TEXT, True)

        card_y, card_h = 150, 396
        cards = ((32, 350), (398, 430), (844, 404))
        for x, width in cards:
            c.rect(x, card_y, width, card_h, PANEL, BORDER, 5)

        c.text(52, 168, 310, 26, "01  采集配置", 15, TEXT, True)
        c.text(52, 201, 310, 22, "数据源", 9, MUTED, True)
        c.rect(52, 228, 310, 46, "#13253d", "#31527b", 3)
        c.circle(72, 251, 6, GREEN)
        c.text(90, 228, 254, 46, "ExpandableEEG / Cyton", 11, TEXT, True)
        c.text(52, 294, 310, 22, "传输协议", 9, MUTED, True)
        c.rect(52, 321, 310, 42, "#0b1728", BORDER, 3)
        c.text(66, 321, 282, 42, "Bluetooth COM / Native RFCOMM", 10, TEXT)
        for index, (label, value) in enumerate((("通道数", "8 CH"), ("采样率", "250 Hz"))):
            x = 52 + index * 160
            c.rect(x, 388, 150, 66, "#0b1728", BORDER, 3)
            c.text(x + 12, 396, 126, 19, label, 9, MUTED)
            c.text(x + 12, 418, 126, 26, value, 15, TEXT, True)
        c.text(52, 478, 310, 44, "固定使用 8 通道硬件配置，避免会话间参数漂移。", 10, MUTED)

        c.text(418, 168, 390, 26, "02  选择并连接设备", 15, TEXT, True)
        c.text(418, 204, 390, 22, "系统蓝牙配对后，设备会映射为 COM 或 /dev/cu.*", 9, MUTED)
        port = self._collector_selected_device or "尚未选择串口设备"
        c.rect(418, 236, 390, 48, "#0b1728", BORDER, 3)
        c.text(434, 236, 358, 48, port[-48:], 10, TEXT)
        self._light_button(c, 418, 300, 184, 40, "刷新 / 选择设备")
        self._light_button(c, 618, 300, 190, 40, "开始采集会话", False, "#2d8a57")
        self._hit(418, 300, 184, 40, "openbci-serial-autoconnect")
        self._hit(618, 300, 190, 40, "openbci-start-session")
        c.rect(418, 362, 390, 94, "#0b1728", BORDER, 3)
        c.circle(440, 389, 6, YELLOW)
        c.text(456, 374, 332, 30, self._eeg_status, 10, TEXT, True)
        c.text(438, 411, 350, 29, "只接入真实硬件，不自动播放 CSV 或生成模拟 EEG。", 9, MUTED)
        self._light_button(c, 418, 478, 136, 34, "接入说明")
        self._hit(418, 478, 136, 34, "openbci-guide")

        c.text(864, 168, 364, 26, "03  会话数据链路", 15, TEXT, True)
        steps = (
            ("连接", "蓝牙串口直连采集板"),
            ("监测", "8 通道波形与频谱"),
            ("质检", "信号质量与丢包检测"),
            ("广播", "按需启动 LSL 输出"),
            ("保存", "LSL 收集页写入 CSV"),
        )
        for index, (label, description) in enumerate(steps):
            y = 211 + index * 58
            c.circle(884, y + 17, 13, "#183255", "#31527b")
            c.text(871, y + 4, 26, 26, str(index + 1), 9, CYAN, True, Qt.AlignCenter)
            c.text(912, y, 58, 22, label, 10, TEXT, True)
            c.text(970, y, 248, 22, description, 9, MUTED)
            if index < len(steps) - 1:
                c.line(884, y + 31, 884, y + 48, "#31527b", 2)

        c.rect(32, 566, 1216, 112, "#0b1728", BORDER, 5)
        c.text(56, 580, 240, 28, "清晰的数据职责", 13, TEXT, True)
        responsibilities = (
            ("采集", "硬件连接与实时监测"),
            ("LSL 收集", "网络流接收与 CSV 保存"),
            ("实验", "CSV 回放与算法验证"),
        )
        for index, (label, value) in enumerate(responsibilities):
            x = 318 + index * 294
            c.rect(x, 582, 270, 64, "#101c32", BORDER, 3)
            c.text(x + 16, 590, 86, 20, label, 10, CYAN, True)
            c.text(x + 16, 613, 238, 22, value, 10, TEXT)

    def _acquisition_dashboard_page(self, c: Canvas) -> None:
        """Compact three-panel runtime overview in the shared dark theme."""

        c.rect(32, 218, 736, 478, PANEL, BORDER, 4)
        c.text(50, 226, 230, 30, "8 通道实时波形", 13, TEXT, True)
        c.text(496, 226, 252, 30, "自动量程  ·  最近 5 秒", 9, MUTED, False, Qt.AlignRight)
        c.line(50, 260, 750, 260, BORDER)
        self._draw_light_eeg(c, 50, 270, 700, 388)
        c.text(50, 664, 700, 20, "-5s                                  时间                                  0s", 8, MUTED, False, Qt.AlignCenter)

        c.rect(784, 218, 464, 224, PANEL, BORDER, 4)
        c.text(802, 226, 180, 30, "实时频谱", 13, TEXT, True)
        c.text(1024, 226, 206, 30, "0–60 Hz  ·  Log 平滑", 9, MUTED, False, Qt.AlignRight)
        c.line(802, 260, 1230, 260, BORDER)
        self._acquisition_fft(c, 802, 266, 428, 162)

        c.rect(784, 456, 464, 240, PANEL, BORDER, 4)
        c.text(802, 464, 190, 30, "辅助通道 / 加速度", 13, TEXT, True)
        c.text(1040, 464, 190, 30, "XYZ  ·  最近 5 秒", 9, MUTED, False, Qt.AlignRight)
        c.line(802, 498, 1230, 498, BORDER)
        self._acquisition_accelerometer(c, 802, 504, 428, 176)

    def _research_replay(self, c: Canvas) -> None:
        """CSV experiment replay is deliberately separate from live acquisition."""
        panel, border = PANEL, BORDER
        ink, sub, blue = TEXT, MUTED, "#3769ef"
        c.rect(0, 56, 1280, 664, BG)
        snapshot = self._csv_playback_snapshot()
        is_csv = snapshot.get("backend") == "csv"
        c.rect(16, 72, 1248, 96, panel, border, 3)
        c.text(31, 78, 250, 31, "CSV 实验回放", 17, ink, True)
        self._light_button(c, 31, 116, 116, 34, "导入 EEG CSV", False, blue)
        playback_running = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        playback_label = (
            "暂停回放"
            if playback_running
            else ("继续回放" if self._csv_replay_paused else "开始回放")
        )
        self._light_button(
            c, 158, 116, 94, 34,
            playback_label,
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
        state = (
            "PLAYING"
            if playback_running and is_csv
            else (
                "PAUSED"
                if self._csv_replay_paused and is_csv
                else ("READY" if is_csv else "NO FILE")
            )
        )
        c.text(946, 333, 290, 30, state, 18, "#2d8a57" if is_csv else "#bd4b4b", True)
        c.text(946, 380, 290, 19, "范式 / 解码", 9, sub, True)
        mode = self.actions.paradigm_type
        active_decoder = str(getattr(self.intent_decoder, "active_name", "FBCCA"))
        decoder = f"{active_decoder}（仅 SSVEP）" if mode == "SSVEP" else "运动想象：仅回放和标注"
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
        c.rect(946, 606, 298, 69, "#0b1728", border, 2)
        c.text(958, 613, 274, 55, "CSV 回放不会直接启用无人机脑控。\n它用于复盘、算法验证和结果对比。", 10, YELLOW)

    def _light_button(
        self, c: Canvas, x: float, y: float, w: float, h: float, label: str,
        active: bool = False, accent: str = "#2f5f89",
    ) -> None:
        c.rect(x, y, w, h, accent if active else "#111c31", accent, 3)
        c.text(x, y, w, h, label, 10, TEXT if active else QColor(accent).lighter(150), active, Qt.AlignCenter)

    def _draw_light_eeg(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        histories = [list(values) for values in self._eeg_history]
        if not histories:
            histories = [[] for _ in range(8)]
        waiting = not any(histories)
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x + 83, baseline, x + w, baseline, "#243452")
            label = self._eeg_channel_names[index] if index < len(self._eeg_channel_names) else f"CH{index + 1}"
            c.circle(x + 15, baseline, 11, palette[index % len(palette)])
            c.text(x + 4, baseline - 10, 22, 20, str(index + 1), 9, "#ffffff", True, Qt.AlignCenter)
            c.text(x + 31, baseline - 11, 52, 20, label[:8], 8, MUTED)
            if waiting:
                c.text(x + w - 154, baseline - 11, 146, 20, "等待数据 · 0.00 μVrms", 8, MUTED, False, Qt.AlignRight)
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
        plot_x, plot_y, plot_w, plot_h = x + 42, y + 8, w - 50, h - 42
        c.rect(plot_x, plot_y, plot_w, plot_h, "#071020", BORDER)
        for index in range(1, 6):
            gx = plot_x + plot_w * index / 6
            c.line(gx, plot_y, gx, plot_y + plot_h, "#1c2b45")
            c.text(gx - 18, plot_y + plot_h + 3, 36, 18, f"{index * 10}", 8, MUTED, False, Qt.AlignCenter)
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
        c.text(plot_x, plot_y + plot_h + 20, plot_w, 16, "频率 (Hz)", 8, MUTED, False, Qt.AlignCenter)

    def _acquisition_focus(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "信号质量", 16, TEXT, True)
        quality_status = str(self._eeg_quality.get("status", "waiting"))
        usable = int(self._eeg_quality.get("usable_channel_count", 0) or 0)
        count = max(1, len(self._eeg_channel_names))
        c.text(x, y + 43, w, 42, f"{usable} / {count} 个通道可用", 25, CYAN, True, Qt.AlignCenter)
        c.text(x, y + 84, w, 24, f"质量状态：{quality_status}", 10, MUTED, False, Qt.AlignCenter)
        for index in range(count):
            row_y = y + 145 + index * min(42, 310 / count)
            values = list(self._eeg_history[index]) if index < len(self._eeg_history) else []
            spread = float(np.std(values)) if values else 0.0
            score = min(1.0, spread / (spread + 10.0)) if spread else 0.0
            label = self._eeg_channel_names[index] if index < len(self._eeg_channel_names) else f"CH{index + 1}"
            c.text(x + 70, row_y, 90, 24, label, 10, TEXT, True)
            c.rect(x + 160, row_y + 6, w - 260, 12, "#1d2c45", None, 6)
            c.rect(x + 160, row_y + 6, (w - 260) * score, 12, "#49a66e", None, 6)
            c.text(x + w - 90, row_y, 60, 24, f"{spread:.1f}", 10, MUTED, False, Qt.AlignRight)

    def _acquisition_accelerometer(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        if h <= 260:
            # Compact OpenBCI dashboard widget: XYZ values above a shared timeline.
            for index, (axis, color) in enumerate((('X', '#d84e4e'), ('Y', '#48a15f'), ('Z', '#3c75ba'))):
                c.text(x + 25, y + 4 + index * 27, 150, 24, f"{axis} = 0.000 g", 11, color, True)
            circle_x, circle_y = x + w - 78, y + 42
            c.circle(circle_x, circle_y, 32, "#071020", BORDER)
            c.line(circle_x - 32, circle_y, circle_x + 32, circle_y, BORDER)
            c.line(circle_x, circle_y - 32, circle_x, circle_y + 32, BORDER)
            plot_y = y + 88
            plot_h = max(45, h - 102)
            c.rect(x + 25, plot_y, w - 50, plot_h, "#071020", BORDER)
            for index, color in enumerate(("#d84e4e", "#48a15f", "#3c75ba")):
                baseline = plot_y + plot_h * (index + 1) / 4
                c.line(x + 34, baseline, x + w - 34, baseline, color, 1)
            c.text(x + 25, y + h - 16, w - 50, 16, "-5s                         时间                         0s", 8, MUTED, False, Qt.AlignCenter)
            return
        c.text(x, y, w, 30, "加速度计", 16, TEXT, True)
        c.rect(x + 40, y + 54, w - 80, h - 130, "#071020", BORDER)
        for index, (axis, color) in enumerate((("X", "#d84e4e"), ("Y", "#48a15f"), ("Z", "#3c75ba"))):
            baseline = y + 120 + index * 90
            c.line(x + 70, baseline, x + w - 70, baseline, "#243452")
            c.text(x + 54, baseline - 14, 30, 28, axis, 12, color, True)
        c.text(x + 60, y + h - 58, w - 120, 34, "ExpandableEEG 主数据为 8 路 EEG；硬件包含 AUX 通道时在这里显示 XYZ。", 10, YELLOW, False, Qt.AlignCenter)

    def _acquisition_lsl(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "LSL 实时输出", 16, TEXT, True)
        c.text(x, y + 34, w, 24, "ExpandableEEG 直采  →  LSL 广播  →  “LSL 收集”接收  →  CSV", 10, CYAN, True)
        outlet = self._lsl_outlet.snapshot()
        rows = (
            ("Data Type", "TimeSeriesRaw"),
            ("Name", str(outlet.get("stream_name", "obci_eeg1"))),
            ("Type", str(outlet.get("stream_type", "EEG"))),
            ("Channels", str(outlet.get("channel_count", 0) or 8)),
            ("Sample Rate", f"{float(outlet.get('sample_rate_hz', 0.0) or 0.0):.1f} Hz"),
            ("Published", f"{int(outlet.get('total_samples', 0) or 0)} samples"),
        )
        for row_index, (label, value) in enumerate(rows):
            row_y = y + 72 + row_index * 38
            c.rect(x + 110, row_y, 190, 30, "#14233b", BORDER, 2)
            c.text(x + 122, row_y, 166, 30, label, 9, MUTED, True)
            c.rect(x + 300, row_y, w - 410, 30, "#0b1728", BORDER, 2)
            c.text(x + 314, row_y, w - 438, 30, value, 10, TEXT)
        active = bool(outlet.get("active", False))
        button_text = "停止 LSL 广播" if active else "启动 LSL 广播"
        self._light_button(c, x + w / 2 - 110, y + 318, 220, 40, button_text, active)
        self._hit(x + w / 2 - 110, y + 318, 220, 40, "lsl-outlet-toggle")
        status = "正在广播：LSL收集页可以发现并接收该数据流" if active else "尚未广播；硬件采集仍可独立运行"
        c.text(x + 45, y + 368, w - 90, 28, status, 10, CYAN if active else MUTED, True, Qt.AlignCenter)
        c.text(x + 45, y + 400, w - 90, 32, "停止 LSL 只会停止网络广播，不会停止 ExpandableEEG 硬件采集。", 9, MUTED, False, Qt.AlignCenter)

    def _acquisition_packet_loss(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "丢包与时间戳健康度", 16, TEXT, True)
        health = self._eeg_health
        cards = (
            ("总样本数", str(health.get("total_samples", 0))),
            ("时间戳乱序", str(health.get("non_monotonic_timestamps", 0))),
            ("数据缺口", str(health.get("gap_count", 0))),
            ("最大缺口", f"{float(health.get('max_gap_seconds', 0.0) or 0.0):.4f}s"),
        )
        for index, (label, value) in enumerate(cards):
            card_w = (w - 90) / 4
            card_x = x + 18 + index * (card_w + 18)
            c.rect(card_x, y + 55, card_w, 94, "#0b1728", BORDER, 3)
            c.text(card_x, y + 66, card_w, 22, label, 9, MUTED, True, Qt.AlignCenter)
            c.text(card_x, y + 92, card_w, 40, value, 22, CYAN, True, Qt.AlignCenter)
        gaps = int(health.get("gap_count", 0) or 0)
        c.circle(x + w / 2, y + 245, 64, "#54ad75" if gaps == 0 else "#d3a13a")
        c.text(x + w / 2 - 64, y + 210, 128, 70, "正常" if gaps == 0 else "检查", 21, "#ffffff", True, Qt.AlignCenter)
        c.text(x + 70, y + 335, w - 140, 64, "按逐样本时间戳检测乱序、重复和超过 2 倍采样间隔的数据缺口。\n指标随实验记录，可用于判断数据是否适合训练和论文分析。", 10, MUTED, False, Qt.AlignCenter)

    def _acquisition_hardware(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "硬件通道设置 · ADS1299", 16, TEXT, True)
        headers = ("通道", "PGA 增益", "输入类型", "Bias", "SRB2", "SRB1")
        starts = (x + 20, x + 85, x + 205, x + 365, x + 535, x + 665)
        widths = (55, 110, 150, 160, 120, 120)
        for label, left, width in zip(headers, starts, widths):
            c.text(left, y + 36, width, 28, label, 9, MUTED, True, Qt.AlignCenter)
        for index, settings in enumerate(self._hardware_channel_settings):
            row_y = y + 66 + index * 37
            c.circle(x + 47, row_y + 16, 13, ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")[index])
            c.text(x + 34, row_y + 3, 26, 26, str(index + 1), 9, "#ffffff", True, Qt.AlignCenter)
            for key, left, width in zip(("gain", "input", "bias", "srb2", "srb1"), starts[1:], widths[1:]):
                enabled = settings[key] in {"Yes", "On"}
                fill = "#153228" if enabled else ("#321d25" if settings[key] == "Off" else "#0b1728")
                c.rect(left, row_y, width, 31, fill, BORDER, 2)
                c.text(left, row_y, width, 31, settings[key], 9, GREEN if enabled else TEXT, False, Qt.AlignCenter)
        c.text(x + 18, y + 374, w - 220, 45, "当前使用 ExpandableEEG 专用串口协议；逐通道 PGA、Bias、SRB 写命令需实机验证后开放。", 9, YELLOW)
        self._light_button(c, x + w - 180, y + 374, 160, 38, "发送（待验证）", False, "#69758a")

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



__all__ = ["ResearchAcquisitionViewMixin"]
