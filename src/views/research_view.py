"""Researcher dashboard rendering."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.bci.config import (
    DECISION_STEP_SECONDS,
    SSVEP_PROFILE_NAME,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.widgets.scifi_canvas import (
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, RESEARCH_NAV_ITEMS, TEXT,
    YELLOW, Canvas,
)


class ResearchViewMixin:
    """Render researcher EEG, paradigm, algorithm, control, mapping and data pages."""
    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        for name, x, width in RESEARCH_NAV_ITEMS:
            if name == "帮助":
                self._hit(x, 0, width, 56, "openbci-guide")
                continue
            self._hit(x, 0, width, 57, f"research:{name}")
        self._hit(1020, 9, 36, 36, "logout")
        self._hit(1068, 9, 36, 36, "workspace-summary")
        self._hit(1117, 9, 36, 36, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        {
            "采集": self._research_acquisition,
            "LSL收集": self._research_lsl_collector,
            "实验": self._research_replay,
            "范式": self._research_paradigm,
            "算法": self._research_algorithm,
            "控制": self._research_controller,
            "映射": self._research_mapping,
            "数据": self._research_data,
        }[self.research_page](c)

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

        c.rect(0, 56, 1280, 664, BG)
        c.text(32, 69, 420, 32, "LSL 脑电收集", 20, TEXT, True)
        c.text(32, 100, 840, 21, "发现并接收 type=EEG 的 LSL 数据流，同时保留原始时间戳并写入 CSV。", 10, MUTED)

        c.rect(32, 132, 1216, 104, PANEL, BORDER, 5)
        stream_name = str(
            getattr(self._lsl_collector_stream, "source_name", "LSL · obci_eeg1")
        ).replace("LSL · ", "")
        c.text(50, 143, 90, 24, "LSL 数据流", 9, MUTED, True)
        c.rect(50, 170, 486, 36, "#0b1728", BORDER, 3)
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
        c.text(50, 210, 720, 18, self._lsl_collector_status, 9, CYAN)
        c.text(790, 210, 438, 18, "默认：obci_eeg1 · EEG · 8 CH", 9, MUTED, False, Qt.AlignRight)

        running = self._lsl_collector_worker is not None and self._lsl_collector_worker.isRunning()
        stats = self._lsl_collector_recorder.snapshot()
        health_getter = getattr(self._lsl_collector_stream, "health_snapshot", None)
        try:
            health = dict(health_getter()) if callable(health_getter) else {}
        except Exception:
            health = {}
        metrics = (
            ("状态", "正在收集" if running else "等待"),
            ("样本数", str(int(stats.get("sample_count", 0) or 0))),
            ("时长", f"{float(stats.get('duration_seconds', 0.0) or 0.0):.1f} s"),
            ("实际采样率", f"{(self._lsl_collector_sample_rate if running else 0.0):.1f} Hz"),
        )
        for index, (label, value) in enumerate(metrics):
            x = 32 + index * 304
            c.rect(x, 250, 288, 62, "#0b1728", BORDER, 3)
            c.text(x + 14, 257, 120, 20, label, 9, MUTED)
            c.text(x + 14, 279, 260, 25, value, 14, TEXT, True)

        c.text(32, 326, 900, 24, f"数据文件：{stats.get('path') or '开始收集后自动创建 CSV'}", 9, MUTED)
        self._light_button(
            c, 1084, 322, 164, 32,
            "恢复波形" if self._lsl_collector_waveform_paused else "暂停波形",
            self._lsl_collector_waveform_paused,
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
        c.text(1070, 410, 160, 20, f"通道：{len(self._lsl_collector_channel_names)}", 9, MUTED, False, Qt.AlignRight)
        c.line(886, 438, 1230, 438, BORDER)
        events = list(self._lsl_collector_events)[:11] or ["等待发现LSL EEG数据流…"]
        for index, event in enumerate(events):
            c.text(886, 446 + index * 20, 344, 18, str(event)[:52], 8, TEXT)
        c.text(886, 662, 344, 24, "暂停波形不影响 CSV 持续写入。", 8, YELLOW, False, Qt.AlignCenter)

    def _draw_lsl_collector_eeg(
        self, c: Canvas, x: float, y: float, w: float, h: float,
    ) -> None:
        histories = [list(values) for values in self._lsl_collector_history[:16]]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 LSL EEG 数据…", 14, MUTED, False, Qt.AlignCenter)
            return
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#243452")
            label = self._lsl_collector_channel_names[index] if index < len(self._lsl_collector_channel_names) else f"CH{index + 1}"
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

    def _acquisition_legacy_setup(self, c: Canvas) -> None:
        """Port the official OpenBCI GUI v6 control-panel state layout."""
        navy, dark_blue, bright_blue = "#00172b", "#0b507c", "#078bd1"
        panel, border, ink = "#c9c9c9", "#1f3445", "#10283d"
        active, selected, white = "#94acd0", "#a9efa2", "#f7f7f7"
        c.rect(0, 56, 1280, 664, navy)
        c.rect(0, 56, 1280, 48, dark_blue, border, 0, 2)
        c.rect(16, 58, 315, 44, active, border, 0, 2)
        c.text(16, 58, 315, 44, "System Control Panel", 18, "#ffffff", True, Qt.AlignCenter)
        c.text(530, 58, 220, 44, "◉ OPENBCI", 22, "#ffffff", True, Qt.AlignCenter)
        for index, label in enumerate(("Update", "Shop", "Issues", "Help", "…")):
            widths = (78, 78, 92, 78, 38)
            x_positions = (826, 908, 990, 1086, 1170)
            c.rect(x_positions[index], 61, widths[index], 38, dark_blue, border, 0, 2)
            c.text(x_positions[index], 61, widths[index], 38, label, 15, "#ffffff", True, Qt.AlignCenter)
        c.rect(0, 104, 1280, 40, bright_blue)
        c.rect(1174, 108, 90, 32, bright_blue, "#ffffff", 0, 1)
        c.text(1174, 108, 90, 32, "Settings", 13, "#ffffff", False, Qt.AlignCenter)

        def box(x: float, y: float, w: float, h: float, title: str) -> None:
            c.rect(x, y, w, h, panel, border, 0, 1)
            c.text(x + 12, y + 7, w - 24, 27, title, 15, ink, True)

        def button(
            x: float,
            y: float,
            w: float,
            h: float,
            label: str,
            is_active: bool = False,
            action: str = "",
            green: bool = False,
        ) -> None:
            fill = selected if green else (active if is_active else white)
            c.rect(x, y, w, h, fill, border, 0, 1)
            c.text(x, y, w, h, label, 12, ink, False, Qt.AlignCenter)
            if action:
                self._hit(x, y, w, h, action)

        def field(x: float, y: float, w: float, h: float, value: str) -> None:
            c.rect(x, y, w, h, white, border, 0, 1)
            c.text(x + 7, y, w - 14, h, value, 12, ink)

        def session_box(x: float, y: float, w: float, h: float) -> None:
            box(x, y, w, h, "SESSION DATA")
            c.text(x + 12, y + 35, 58, 27, "Name", 12, ink)
            field(x + 70, y + 35, w - 82, 27, self._openbci_session_name)
            button(x + 12, y + 68, w - 24, 28, "GENERATE SESSION NAME", action="openbci-generate-session")
            half = (w - 36) / 2
            button(x + 12, y + 103, half, 28, "OpenBCI", self._openbci_session_format == "OpenBCI", "openbci-session-format:OpenBCI")
            button(x + 24 + half, y + 103, half, 28, "BDF+", self._openbci_session_format == "BDF+", "openbci-session-format:BDF+")
            c.text(x + 12, y + 136, 145, 28, "Max File Duration", 11, ink)
            field(x + 156, y + 136, w - 168, 28, "60 Minutes   ⌃")

        def streamer_box(x: float, y: float, w: float, h: float) -> None:
            box(x, y, w, h, "BRAINFLOW STREAMER")
            half = (w - 36) / 2
            button(x + 12, y + 39, half, 28, "Network", self._openbci_streamer_mode == "Network", "openbci-streamer-mode:Network")
            button(x + 24 + half, y + 39, half, 28, "File", self._openbci_streamer_mode == "File", "openbci-streamer-mode:File")
            if self._openbci_streamer_mode == "Network":
                c.text(x + 12, y + 72, 28, 28, "IP", 12, ink)
                field(x + 40, y + 72, 145, 28, "225.1.1.1")
                c.text(x + 198, y + 72, 40, 28, "Port", 12, ink)
                field(x + 238, y + 72, w - 250, 28, "6677")
            else:
                c.text(x + 12, y + 72, 75, 28, "Location", 12, ink)
                field(x + 87, y + 72, w - 99, 28, "Default       ⌃")

        def channel_box(x: float, y: float, w: float, counts: tuple[int, ...]) -> None:
            box(x, y, w, 72, f"CHANNEL COUNT ({self._openbci_channel_count})")
            gap = 10
            bw = (w - 24 - gap * (len(counts) - 1)) / len(counts)
            for index, count in enumerate(counts):
                bx = x + 12 + index * (bw + gap)
                button(bx, y + 37, bw, 27, f"{count} CHANNELS", self._openbci_channel_count == count, f"openbci-channel-count:{count}")

        def wifi_box(x: float, y: float, w: float) -> None:
            box(x, y, w, 200, "WIFI SHIELDS")
            half = (w - 36) / 2
            button(x + 12, y + 39, half, 28, "DYNAMIC IP", self._openbci_wifi_mode == "Dynamic IP", "openbci-wifi-mode:Dynamic IP")
            button(x + 24 + half, y + 39, half, 28, "STATIC IP", self._openbci_wifi_mode == "Static IP", "openbci-wifi-mode:Static IP")
            c.rect(x + 12, y + 76, w - 24, 70, "#d8d8d8")
            if self._openbci_wifi_mode == "Static IP":
                field(x + 22, y + 95, w - 44, 30, self._openbci_ip)
            button(x + 12, y + 151, w - 49, 28, "START SEARCH", action="openbci-wifi-search")
            c.circle(x + w - 25, y + 165, 7, "#999999")
            c.text(x + 12, y + 178, w - 24, 20, "BOARD IP:", 14, ink, True, Qt.AlignCenter)

        def sample_rate_box(x: float, y: float, w: float, rates: tuple[int, ...]) -> None:
            box(x, y, w, 82, "SAMPLE RATE")
            gap = 10
            bw = (w - 24 - gap * (len(rates) - 1)) / len(rates)
            for index, rate in enumerate(rates):
                bx = x + 12 + index * (bw + gap)
                button(bx, y + 39, bw, 28, f"{rate}Hz", self._openbci_sample_rate == rate, f"openbci-sample-rate:{rate}")

        left_x, left_w, middle_x, middle_w, third_x = 16, 315, 331, 315, 646
        source_rows = (
            ("CYTON (live)", "CYTON"),
            ("GANGLION (live)", "GANGLION"),
            ("PLAYBACK (from file)", "PLAYBACK"),
            ("SYNTHETIC (algorithmic)", "SYNTHETIC"),
            ("STREAMING (from external)", "STREAMING"),
        )
        box(left_x, 104, left_w, 186, "DATA SOURCE")
        for index, (label, value) in enumerate(source_rows):
            button(left_x + 12, 140 + index * 27, left_w - 24, 27, label, False, f"openbci-source:{value}", self._openbci_source == value)
        c.rect(left_x, 290, left_w, 58, panel, border, 0, 1)
        button(left_x + 12, 303, left_w - 24, 34, "START SESSION", action="openbci-start-session")

        if self._openbci_source == "CYTON":
            box(middle_x, 104, middle_w, 88, "PICK TRANSFER PROTOCOL")
            button(middle_x + 12, 140, middle_w - 24, 27, "Serial (from Dongle)", self._openbci_transport == "Serial", "openbci-transport:Serial")
            button(middle_x + 12, 170, middle_w - 24, 27, "Wifi (from Wifi Shield)", self._openbci_transport == "WiFi", "openbci-transport:WiFi")
            if self._openbci_transport == "Serial":
                box(middle_x, 192, middle_w, 70, "SERIAL CONNECT")
                button(middle_x + 12, 226, 190, 27, "AUTO-CONNECT", action="openbci-serial-autoconnect", green=True)
                button(middle_x + 213, 226, 90, 27, "Manual >")
                channel_box(middle_x, 262, middle_w, (8, 16))
                session_box(middle_x, 334, middle_w, 170)
                streamer_box(middle_x, 504, middle_w, 105)
                box(middle_x, 609, middle_w, 84, "WRITE TO SD CARD?")
                field(middle_x + 12, 648, middle_w - 24, 28, "Do not write to SD...          ⌃")
            else:
                channel_box(middle_x, 192, middle_w, (8, 16))
                session_box(middle_x, 264, middle_w, 170)
                streamer_box(middle_x, 434, middle_w, 105)
                box(middle_x, 539, middle_w, 84, "WRITE TO SD CARD?")
                field(middle_x + 12, 578, middle_w - 24, 28, "Do not write to SD...          ⌃")
                wifi_box(third_x, 104, middle_w)
                sample_rate_box(third_x, 304, middle_w, (250, 500, 1000))
        elif self._openbci_source == "GANGLION":
            box(middle_x, 104, middle_w, 124, "PICK TRANSFER PROTOCOL")
            protocols = (
                ("Bluetooth (Native)", "Native BLE"),
                ("Bluetooth (BLED112 Dongle)", "BLED112"),
                ("Wifi (from Wifi Shield)", "WiFi"),
            )
            for index, (label, value) in enumerate(protocols):
                button(middle_x + 12, 140 + index * 30, middle_w - 24, 27, label, self._openbci_ganglion_transport == value, f"openbci-ganglion-transport:{value}")
            if self._openbci_ganglion_transport == "WiFi":
                session_box(middle_x, 228, middle_w, 170)
                streamer_box(middle_x, 398, middle_w, 105)
                wifi_box(third_x, 104, middle_w)
                sample_rate_box(third_x, 304, middle_w, (200, 1600))
            else:
                box(middle_x, 228, middle_w, 180, "BLE DEVICES")
                c.rect(middle_x + 12, 264, middle_w - 24, 94, "#d8d8d8")
                button(middle_x + 12, 366, middle_w - 55, 28, "SEARCHING...", action="openbci-ble-search")
                c.circle(middle_x + middle_w - 27, 380, 8, active, border)
                session_box(middle_x, 408, middle_w, 170)
                streamer_box(middle_x, 578, middle_w, 105)
        elif self._openbci_source == "PLAYBACK":
            box(middle_x, 104, 425, 100, "PLAYBACK FILE")
            button(middle_x + 300, 113, 113, 26, "Sample Data", True)
            file_label = self._openbci_playback_path.rsplit("/", 1)[-1] if self._openbci_playback_path else "SELECT OPENBCI PLAYBACK FILE"
            button(middle_x + 12, 145, 401, 31, file_label, action="openbci-playback-select")
            box(middle_x, 204, 425, 72, "PLAYBACK HISTORY")
            field(middle_x + 12, 240, 401, 28, "Select Recent Playback File                  ⌃")
        elif self._openbci_source == "SYNTHETIC":
            channel_box(middle_x, 104, middle_w, (4, 8, 16))
            streamer_box(middle_x, 176, middle_w, 105)
        else:
            box(middle_x, 104, middle_w, 120, "STREAMING BOARD CONFIG")
            c.text(middle_x + 12, 140, 24, 28, "IP", 12, ink)
            field(middle_x + 36, 140, 102, 28, "")
            c.text(middle_x + 152, 140, 48, 28, "PORT", 12, ink)
            field(middle_x + 200, 140, 64, 28, "0")
            c.text(middle_x + 12, 176, 70, 28, "BOARD", 12, ink)
            field(middle_x + 82, 176, 221, 28, f"{self._openbci_board_type}                         ⌃")

        status = self._openbci_status_message
        status_fill = "#fac5cc" if status.startswith("No ") or "disabled" in status else white
        c.rect(16, 697, 1248, 19, status_fill, "#25b9df", 0, 1)
        c.text(24, 697, 1232, 19, status, 10, ink)

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
            c.text(656, 456, 380, 46, "操作：连接 EEG 采集 → 检查信号质量 →\n点击右上角“添加标签” → 完成 4 秒想象", 11, MUTED)
            c.button(656, 515, 180, 38, "启用 MI 采集模式", True, accent=CYAN)
            c.text(848, 515, 190, 38, "FBCCA 已停用", 12, GREEN, True, Qt.AlignCenter)
            self._hit(656, 515, 180, 38, "paradigm-start")

    def _research_algorithm(self, c: Canvas) -> None:
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
        transformer_selected = self.actions.algorithm == "Transformer-SSVEP"
        selected_name = "Transformer-SSVEP" if transformer_selected else "纯 FBCCA"
        selected_type = "监督训练模型" if transformer_selected else "无训练模型"
        metadata = [("名称:", selected_name, 73, 207), ("类型:", selected_type, 356, 207),
                    ("数据源:", "硬件 / LSL / CSV", 640, 207), ("准确率:", "待真实标签评估", 922, 207),
                    ("识别窗:", f"{WINDOW_SECONDS:g}秒", 73, 256),
                    ("更新步长:", f"{DECISION_STEP_SECONDS:g}秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA", "Transformer-SSVEP"]
        status_reader = getattr(self.intent_decoder, "algorithm_statuses", None)
        statuses = status_reader() if callable(status_reader) else {}
        for i, name in enumerate(algorithms):
            x = 73 + i * 176
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            available = bool(statuses.get(name, {}).get("available", name == "FBCCA"))
            c.rect(x, 441, 160, 96, "#17243a", BLUE if selected else "#2b3a55", 3, 2 if selected else 1)
            c.text(x, 452, 160, 37, name, 13, TEXT, True, Qt.AlignCenter)
            c.rect(x + 47, 504, 32, 16, GREEN if loaded else "#526078", None, 8)
            c.circle(x + 72 if loaded else x + 56, 512, 5, TEXT)
            c.circle(x + 98, 512, 8, YELLOW if default else "#2b3954")
            c.circle(x + 124, 512, 7, CYAN if available else RED)
            self._hit(x, 441, 160, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_controller(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-08 / M6.5：滑块和本地应用动作已可操作；正式版
        # ControllerService 还需将 Kp/Ki/Kd 校验后交给实现 BaseController 的
        # PID/MPC 控制器；参数写入 YAML，并保留版本和操作者审计记录。
        c.text(32, 72, 320, 36, "控制器参数工作台", 19, TEXT, True)
        c.text(32, 101, 640, 22, "统一管理飞行控制器、参数预设与切换策略。", 10, MUTED)
        c.rect(32, 132, 1216, 72, PANEL, BORDER, 3)
        c.text(52, 148, 106, 40, "当前控制器", 12, MUTED)
        c.button(164, 150, 128, 36, self.actions.controller)
        modes = [("● 手动选择", True), ("□ 场景自动匹配", False), ("□ 自适应切换", False), ("□ 双控制器热备", False)]
        x = 348
        for label, active in modes:
            c.button(x, 171, 126, 31, label, False, not active)
            x += 133
        c.rect(32, 220, 1216, 296, PANEL, BORDER, 3)
        c.text(56, 236, 300, 40, f"{self.actions.controller} 参数配置", 16, TEXT, True)
        for label, y in (("Kp", 310), ("Ki", 370), ("Kd", 430)):
            value = self.actions.controller_values[label]
            end = 203 + 707 * value / 100
            c.text(145, y - 18, 45, 35, label, 13, TEXT)
            c.line(203, y, 910, y, "#6d6f70", 7)
            c.line(203, y, end, y, "#eeeeee", 7)
            c.circle(end, y, 8, "#f2f2f2")
            c.text(932, y - 17, 74, 34, f"{value / 100:.2f}", 13, TEXT)
            self._hit(196, y - 14, 721, 28, f"controller-slider:{label}")
        for i, (label, action) in enumerate((("应用参数", "controller-apply"), ("保存预设", "controller-save"), ("恢复默认", "controller-reset"))):
            x = 486 + i * 137
            c.button(x, 468, 121, 34, label, i == 0)
            self._hit(x, 468, 121, 34, action)
        c.rect(32, 532, 1216, 164, PANEL, BORDER, 3)
        c.text(56, 545, 200, 42, "控制器库", 16, TEXT, True)
        controllers = ("PID", "MPC", "新增", "滑模控制", "鲁棒控制", "自适应控制")
        for i, name in enumerate(controllers):
            x = 56 + i * 144
            disabled = name == "新增"
            c.button(x, 622, 120, 42, name, name == self.actions.controller, disabled, "#69c77a")
            if not disabled:
                self._hit(x, 622, 120, 42, f"controller-select:{name}")

    def _research_mapping(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-09 / M4.5：映射表应来自 CommandMappingRepository。
        # 解码 label/频率先经本映射转换为 SwarmCommand，再由安全仲裁器检查
        # 置信度、二次确认、Flow 质量、电量、解锁和急停状态，
        # 最后才调用 swarm_gateway.send_command。
        c.text(32, 72, 320, 36, "识别与动作映射", 19, TEXT, True)
        c.text(32, 101, 640, 22, "将脑电识别结果转换为经过安全仲裁的飞行动作。", 10, MUTED)
        panels = [(32, 132, 1216, 276), (32, 424, 1216, 156), (32, 596, 1216, 100)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(56, 143, 430, 42, "SSVEP 识别与安全动作映射", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 181, 104, 30, text, 12, TEXT, True, Qt.AlignCenter)
        c.line(433, 210, 847, 210, "#273752")
        for i, row in enumerate(self.actions.mappings[:6]):
            y = 209 + i * 27
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 26, 847, y + 26, "#273752")
        c.button(576, 371, 128, 28, "添加映射", True)
        self._hit(576, 371, 128, 28, "mapping-add")
        c.text(56, 436, 360, 42, "Flow Deck 单机验收", 16, TEXT, True)
        acceptance = (
            ("LED 无桨测试", "led_test"),
            ("保持悬停", "hover"),
            ("受控降落", "land"),
        )
        for index, (name, action) in enumerate(acceptance):
            button_x = 310 + index * 220
            c.button(button_x, 480, 190, 40, name)
            self._hit(button_x, 480, 190, 40, f"swarm-command:{action}")
        c.text(200, 538, 880, 23, "连接、Flow Deck 测距/光流、解锁和急停检查通过后才会执行", 10, MUTED, False, Qt.AlignCenter)
        c.text(56, 606, 360, 32, "L3 战术意图", 15, TEXT, True)
        c.text(56, 640, 850, 28, "预留更高层战术意图映射；当前版本仅开放经过单机安全验收的动作。", 10, MUTED)
        c.rect(1064, 620, 150, 34, "#0b1728", BORDER, 17)
        c.text(1064, 620, 150, 34, "规划中", 10, YELLOW, True, Qt.AlignCenter)

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
        c.text(355, 599, 840, 23, "添加标签后保存：SSVEP连续2秒；MI连续4秒。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------
