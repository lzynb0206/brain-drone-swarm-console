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
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class ResearchViewMixin:
    """Render researcher EEG, paradigm, algorithm, control, mapping and data pages."""
    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        nav = [
            ("采集", 238, 54), ("LSL收集", 294, 78), ("实验", 374, 54),
            ("范式", 430, 54), ("算法", 486, 54), ("控制", 542, 54),
            ("映射", 598, 54), ("数据", 654, 54),
        ]
        for name, x, width in nav:
            self._hit(x, 0, width, 57, f"research:{name}")
        self._hit(724, 0, 54, 56, "openbci-guide")
        self._hit(1020, 8, 36, 38, "logout")
        self._hit(1068, 8, 36, 38, "workspace-summary")
        self._hit(1117, 8, 36, 38, "workspace-layout")
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

        bg, panel, border = "#eef1f4", "#ffffff", "#ccd3da"
        ink, sub, blue = "#26323c", "#697681", "#2e628f"
        c.rect(0, 56, 1280, 664, bg)
        c.text(24, 67, 600, 32, "LSL EEG 收集器", 18, ink, True)
        c.text(24, 96, 920, 21, "接收“采集 → Networking”或其他软件发布的 type=EEG 数据流，并原样保存CSV", 10, sub)

        c.rect(24, 126, 1232, 104, panel, border, 5)
        stream_name = str(
            getattr(self._lsl_collector_stream, "source_name", "LSL · obci_eeg1")
        ).replace("LSL · ", "")
        c.text(39, 138, 75, 24, "LSL数据流", 10, ink, True)
        c.rect(122, 133, 500, 32, "#f7f9fa", border, 2)
        c.text(134, 133, 476, 32, stream_name, 10, ink)
        self._light_button(c, 642, 133, 142, 32, "刷新/选择数据流", False, blue)
        self._light_button(c, 794, 133, 132, 32, "开始收集", False, "#31875a")
        self._light_button(c, 936, 133, 132, 32, "停止收集", False, "#bd4b4b")
        self._light_button(c, 1078, 133, 154, 32, "打开数据目录")
        self._hit(642, 133, 142, 32, "lsl-collector-refresh")
        self._hit(794, 133, 132, 32, "lsl-collector-start")
        self._hit(936, 133, 132, 32, "lsl-collector-stop")
        self._hit(1078, 133, 154, 32, "lsl-collector-open-directory")
        c.text(39, 178, 1180, 22, self._lsl_collector_status, 9, "#3b6f95")
        c.text(39, 203, 1180, 18, "默认匹配：Name=obci_eeg1 · Type=EEG · Channel Count=8", 9, sub)

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
            x = 24 + index * 306
            c.text(x, 238, 294, 20, label, 9, sub)
            c.rect(x, 260, 294, 48, "#e3e7ea", border, 3)
            c.text(x, 260, 294, 48, value, 13, ink, True, Qt.AlignCenter)

        c.text(24, 317, 880, 24, f"数据文件：{stats.get('path') or '开始收集后自动创建CSV'}", 9, ink)
        self._light_button(
            c, 1068, 314, 164, 30,
            "恢复波形" if self._lsl_collector_waveform_paused else "暂停波形",
            self._lsl_collector_waveform_paused,
            blue,
        )
        self._hit(1068, 314, 164, 30, "lsl-collector-pause")

        c.rect(24, 350, 892, 350, panel, border, 4)
        c.text(38, 354, 470, 27, "最近5秒LSL原始脑电波形（最多显示16通道）", 11, ink, True)
        c.text(690, 354, 210, 27, "单位 μV · 自动纵向缩放", 9, sub, False, Qt.AlignRight)
        self._draw_lsl_collector_eeg(c, 37, 386, 866, 297)
        c.text(37, 677, 866, 18, "-5s                                  时间                                  0s", 8, sub, False, Qt.AlignCenter)

        c.rect(928, 350, 328, 350, panel, border, 4)
        c.text(942, 354, 300, 27, "运行日志 / 时间戳质量", 11, ink, True)
        gaps = int(health.get("gap_count", 0) or 0)
        c.text(942, 385, 140, 20, f"数据缺口：{gaps}", 9, "#b44d4d" if gaps else "#31875a", True)
        c.text(1090, 385, 150, 20, f"通道：{len(self._lsl_collector_channel_names)}", 9, sub, False, Qt.AlignRight)
        c.line(942, 410, 1242, 410, border)
        events = list(self._lsl_collector_events)[:11] or ["等待发现LSL EEG数据流…"]
        for index, event in enumerate(events):
            c.text(942, 419 + index * 24, 300, 22, str(event)[:46], 8, ink)
        c.text(942, 660, 300, 28, "暂停波形不影响CSV持续写入。", 8, "#8b6b2e", False, Qt.AlignCenter)

    def _draw_lsl_collector_eeg(
        self, c: Canvas, x: float, y: float, w: float, h: float,
    ) -> None:
        histories = [list(values) for values in self._lsl_collector_history[:16]]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 LSL EEG 数据…", 15, "#77838f", False, Qt.AlignCenter)
            return
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#e3e7ea")
            label = self._lsl_collector_channel_names[index] if index < len(self._lsl_collector_channel_names) else f"CH{index + 1}"
            c.text(x, baseline - 10, 54, 20, label[:8], 8, "#697580")
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
        """OpenBCI-style acquisition shell backed by the native serial adapter."""
        if self.acquisition_stage == "setup":
            self._acquisition_setup(c)
            return
        if self.acquisition_tab == "Dashboard":
            self._acquisition_dashboard_page(c)
            return
        light_bg, panel, border = "#eef2f5", "#ffffff", "#cbd3dc"
        ink, sub, blue = "#24313d", "#64717e", "#2f5f89"
        c.rect(0, 56, 1280, 664, light_bg)
        backend = self._csv_playback_snapshot().get("backend")
        source = (
            self._pipeline_worker.source_name
            if backend == "hardware" and self._pipeline_worker is not None
            else self._collector_selected_device or "等待选择硬件"
        )
        channel_count = len(self._eeg_channel_names) or len(self._eeg_history)
        csv_stats = self._raw_csv_recorder.snapshot()
        transport = self._eeg_health.get("transport")
        transfer_label = (
            "macOS Native RFCOMM"
            if transport == "macos-native-rfcomm"
            else "Bluetooth COM / Serial"
        )

        c.rect(16, 72, 320, 632, panel, border, 3)
        c.rect(16, 72, 320, 42, blue, blue, 3)
        c.text(32, 72, 288, 42, "OPENBCI SESSION CONTROL", 13, "#ffffff", True)
        fields = (
            ("DATA SOURCE", "CYTON / ExpandableEEG"),
            ("TRANSFER PROTOCOL", transfer_label),
            ("DEVICE", source.replace("OpenBCI Direct · ", "")),
            ("CHANNEL COUNT", f"{channel_count or self._openbci_channel_count} CHANNELS"),
            ("SAMPLE RATE", f"{self._eeg_sample_rate:.1f} Hz" if self._eeg_sample_rate else "WAITING"),
            ("LSL OUTPUT", "ACTIVE" if self._lsl_outlet.active else "STOPPED"),
        )
        for index, (label, value) in enumerate(fields):
            y = 126 + index * 55
            c.text(31, y, 274, 17, label, 9, sub, True)
            c.rect(31, y + 19, 274, 29, "#f8fafb", border, 2)
            c.text(41, y + 19, 254, 29, value, 11, ink)

        streaming = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        self._light_button(c, 31, 462, 126, 34, "SELECT DEVICE", False)
        self._light_button(c, 166, 462, 139, 34, "STOP STREAM" if streaming else "START STREAM", streaming, "#2d8a57")
        self._light_button(c, 31, 504, 84, 34, "DISCONNECT", False, "#bd4b4b")
        self._light_button(
            c, 124, 504, 181, 34,
            "STOP CSV RECORD" if self._continuous_recording else "START CSV RECORD",
            self._continuous_recording,
            "#c94e4e",
        )
        self._hit(31, 462, 126, 34, "openbci-serial-autoconnect")
        self._hit(166, 462, 139, 34, "openbci-data-stream-toggle")
        self._hit(31, 504, 84, 34, "eeg-disconnect")
        self._hit(124, 504, 181, 34, "continuous-record-toggle")
        self._light_button(c, 31, 546, 274, 32, "采集 / LSL / CSV 接入说明")
        self._hit(31, 546, 274, 32, "openbci-guide")

        connected = bool(self._eeg_health.get("connected", False)) or self._eeg_status.startswith("已连接")
        c.circle(41, 603, 6, "#2fa866" if connected else "#c34f4f")
        c.text(55, 588, 250, 30, self._eeg_status, 11, ink, True)
        c.text(31, 620, 274, 20, f"Samples  {self._eeg_health.get('total_samples', 0)}", 10, sub)
        c.text(31, 641, 274, 20, f"CSV  {csv_stats.get('sample_count', 0)} samples", 10, sub)
        path = str(csv_stats.get("path") or "等待首帧后创建文件")
        c.text(31, 664, 274, 28, path[-47:], 9, sub)

        c.rect(352, 72, 912, 632, panel, border, 3)
        tabs = ("Dashboard", "Focus", "LSL", "Packet Loss", "Hardware")
        for index, tab in enumerate(tabs):
            x = 362 + index * 174
            active = self.acquisition_tab == tab
            c.rect(x, 80, 164, 31, blue if active else "#edf1f4", border, 2)
            c.text(x, 80, 164, 31, tab, 10, "#ffffff" if active else ink, active, Qt.AlignCenter)
            self._hit(x, 80, 164, 31, f"acquisition-tab:{tab}")
        c.line(352, 120, 1264, 120, border)

        renderers = {
            "Focus": self._acquisition_focus,
            "LSL": self._acquisition_lsl,
            "Packet Loss": self._acquisition_packet_loss,
            "Hardware": self._acquisition_hardware,
        }
        renderers[self.acquisition_tab](c, 370, 134, 876, 550)

    def _acquisition_setup(self, c: Canvas) -> None:
        """Project-specific direct connection page for the 8-channel board."""

        navy, dark_blue, bright_blue = "#00172b", "#0b507c", "#078bd1"
        panel, border, ink = "#c9c9c9", "#1f3445", "#10283d"
        active, white = "#94acd0", "#f7f7f7"
        c.rect(0, 56, 1280, 664, navy)
        c.rect(0, 56, 1280, 48, dark_blue, border, 0, 2)
        c.rect(16, 58, 315, 44, active, border, 0, 2)
        c.text(16, 58, 315, 44, "System Control Panel", 18, "#ffffff", True, Qt.AlignCenter)
        c.text(520, 58, 250, 44, "◉ OPENBCI", 22, "#ffffff", True, Qt.AlignCenter)
        c.rect(0, 104, 1280, 40, bright_blue)
        c.text(1040, 106, 214, 34, "ExpandableEEG Direct", 12, "#ffffff", True, Qt.AlignCenter)

        # Fixed hardware profile: the manual's BT mode is a virtual COM port.
        c.rect(40, 170, 360, 330, panel, border, 0, 1)
        c.text(58, 184, 324, 32, "DATA SOURCE", 16, ink, True)
        c.rect(58, 224, 324, 42, "#a9efa2", border, 0, 1)
        c.text(58, 224, 324, 42, "EXPANDABLE EEG / CYTON", 13, ink, True, Qt.AlignCenter)
        c.text(58, 286, 324, 28, "TRANSFER PROTOCOL", 14, ink, True)
        c.rect(58, 320, 324, 40, active, border, 0, 1)
        c.text(58, 320, 324, 40, "Bluetooth COM / macOS Native RFCOMM", 11, ink, False, Qt.AlignCenter)
        c.text(58, 380, 115, 28, "CHANNEL COUNT", 10, ink, True)
        c.text(232, 380, 115, 28, "SAMPLE RATE", 10, ink, True)
        c.rect(58, 412, 140, 42, white, border, 0, 1)
        c.rect(224, 412, 158, 42, white, border, 0, 1)
        c.text(58, 412, 140, 42, "8 CHANNELS", 12, ink, True, Qt.AlignCenter)
        c.text(224, 412, 158, 42, "250 Hz", 12, ink, True, Qt.AlignCenter)

        c.rect(420, 170, 420, 330, panel, border, 0, 1)
        c.text(438, 184, 384, 32, "SERIAL CONNECT", 16, ink, True)
        c.text(438, 228, 384, 25, "系统蓝牙配对后将设备映射为 COM / /dev/cu.*", 10, ink)
        c.rect(438, 266, 384, 46, white, border, 0, 1)
        port = self._collector_selected_device or "No Serial/COM port selected"
        c.text(452, 266, 356, 46, port, 11, ink)
        self._light_button(c, 438, 330, 184, 42, "REFRESH / SELECT", False, "#2f6c99")
        self._light_button(c, 638, 330, 184, 42, "START SESSION", False, "#31875a")
        self._hit(438, 330, 184, 42, "openbci-serial-autoconnect")
        self._hit(638, 330, 184, 42, "openbci-start-session")
        c.text(438, 394, 384, 52, self._eeg_status, 10, "#335a74", True, Qt.AlignCenter)
        c.text(438, 454, 384, 28, "不会自动播放CSV，也不会生成模拟EEG", 9, "#6b7379", False, Qt.AlignCenter)

        c.rect(860, 170, 360, 330, panel, border, 0, 1)
        c.text(878, 184, 324, 32, "SESSION PIPELINE", 16, ink, True)
        steps = (
            "1  蓝牙COM/串口直连采集板",
            "2  显示8通道 Time Series",
            "3  实时计算 FFT / 信号质量",
            "4  Networking 启动 LSL 输出",
            "5  LSL收集页接收并保存 CSV",
        )
        for index, value in enumerate(steps):
            c.rect(878, 226 + index * 47, 324, 36, white, "#9ca6ad", 0, 1)
            c.text(892, 226 + index * 47, 296, 36, value, 10, ink)

        c.rect(40, 532, 1180, 112, "#f5f6f6", "#6f7c84", 0, 1)
        c.text(64, 544, 1132, 28, "采集页面负责硬件；LSL收集页面负责网络流与CSV；实验页面负责CSV回放。", 13, ink, True, Qt.AlignCenter)
        c.text(64, 580, 1132, 42, "开始 Session 后进入与 OpenBCI GUI 相同结构的 Time Series + FFT Plot + Accelerometer 页面。", 11, "#486377", False, Qt.AlignCenter)
        status = self._openbci_status_message
        c.rect(16, 697, 1248, 19, white, "#25b9df", 0, 1)
        c.text(24, 697, 1232, 19, status, 10, ink)

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
        """Three-panel runtime layout matching the OpenBCI GUI manual."""
        bg, blue, border, ink = "#f4f5f5", "#0d6094", "#8f999f", "#263139"
        c.rect(0, 56, 1280, 664, bg)
        c.rect(16, 70, 1248, 38, blue, border, 1)
        c.rect(16, 70, 260, 38, "#164d72", border, 1)
        c.text(16, 70, 260, 38, "System Control Panel", 13, "#ffffff", True, Qt.AlignCenter)
        self._hit(16, 70, 260, 38, "openbci-control-panel")
        c.text(488, 70, 300, 38, "◉ OPENBCI", 17, "#ffffff", True, Qt.AlignCenter)
        c.text(1052, 70, 196, 38, "Settings     Layout", 11, "#ffffff", True, Qt.AlignCenter)

        c.rect(16, 108, 1248, 44, "#1490ce", border, 1)
        streaming = self._pipeline_worker is not None and self._pipeline_worker.isRunning()
        c.rect(24, 114, 185, 32, "#e4e6e7", "#66747c", 1)
        c.text(24, 114, 185, 32, "Stop Data Stream" if streaming else "Start Data Stream", 11, ink, True, Qt.AlignCenter)
        self._hit(24, 114, 185, 32, "openbci-data-stream-toggle")
        c.rect(216, 114, 82, 32, "#2d8fd0", "#d4e7f1", 1)
        c.text(216, 114, 82, 32, "Filters", 11, "#ffffff", True, Qt.AlignCenter)
        nav_items = (("Dashboard", "Dashboard"), ("Focus", "Focus"), ("Networking", "LSL"),
                     ("Packet Loss", "Packet Loss"), ("Hardware", "Hardware"))
        for index, (label, tab) in enumerate(nav_items):
            x = 316 + index * 126
            c.rect(x, 114, 118, 32, "#f0f2f3", "#687780", 1)
            c.text(x, 114, 118, 32, label, 9, ink, False, Qt.AlignCenter)
            self._hit(x, 114, 118, 32, f"acquisition-tab:{tab}")
        self._light_button(
            c, 1050, 114, 198, 32,
            "STOP CSV RECORD" if self._continuous_recording else "START CSV RECORD",
            self._continuous_recording,
            "#bd4b4b",
        )
        self._hit(1050, 114, 198, 32, "continuous-record-toggle")

        c.rect(16, 158, 610, 526, "#ffffff", border, 1)
        c.rect(16, 158, 610, 30, "#d5d8da", border, 1)
        c.text(26, 158, 210, 30, "Time Series", 11, ink, True)
        c.text(410, 158, 198, 30, "Vert Scale  200 μV   Window 5 sec", 9, ink, False, Qt.AlignRight)
        c.rect(24, 192, 594, 27, "#eceeef", border, 1)
        c.text(33, 192, 160, 27, "Channels", 9, ink, True)
        c.text(210, 192, 180, 27, "Hardware Settings", 9, ink)
        self._draw_light_eeg(c, 26, 226, 584, 424)
        c.text(26, 651, 584, 22, "Time (s)      -5        -4        -3        -2        -1        0", 9, "#5d6870", False, Qt.AlignCenter)

        c.rect(638, 158, 626, 252, "#ffffff", border, 1)
        c.rect(638, 158, 626, 30, "#d5d8da", border, 1)
        c.text(648, 158, 200, 30, "FFT Plot", 11, ink, True)
        c.text(965, 158, 280, 30, "Max Freq 60 Hz   Max μV 100   Log/Lin", 9, ink, False, Qt.AlignRight)
        self._acquisition_fft(c, 650, 190, 598, 208)

        c.rect(638, 422, 626, 262, "#ffffff", border, 1)
        c.rect(638, 422, 626, 30, "#d5d8da", border, 1)
        c.text(648, 422, 220, 30, "Accelerometer", 11, ink, True)
        c.text(1080, 422, 164, 30, "Vert Scale Auto   5 sec", 9, ink, False, Qt.AlignRight)
        self._acquisition_accelerometer(c, 650, 454, 598, 218)
        c.rect(16, 688, 1248, 24, "#f7f8f8", border, 1)
        c.text(26, 688, 1228, 24, self._eeg_status, 9, ink)

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
        if not histories:
            histories = [[] for _ in range(8)]
        waiting = not any(histories)
        palette = ("#777777", "#7b3fa0", "#2d67a3", "#268264", "#d7a90b", "#ed5b35", "#d43b37", "#aa5d2b")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x + 83, baseline, x + w, baseline, "#e1e5e8")
            label = self._eeg_channel_names[index] if index < len(self._eeg_channel_names) else f"CH{index + 1}"
            c.circle(x + 15, baseline, 11, palette[index % len(palette)])
            c.text(x + 4, baseline - 10, 22, 20, str(index + 1), 9, "#ffffff", True, Qt.AlignCenter)
            c.text(x + 31, baseline - 11, 52, 20, label[:8], 8, "#697580")
            if waiting:
                c.text(x + w - 154, baseline - 11, 146, 20, "Waiting · 0.00 μVrms", 8, "#7b8792", False, Qt.AlignRight)
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
        if h <= 260:
            # Compact OpenBCI dashboard widget: XYZ values above a shared timeline.
            for index, (axis, color) in enumerate((('X', '#d84e4e'), ('Y', '#48a15f'), ('Z', '#3c75ba'))):
                c.text(x + 25, y + 4 + index * 27, 150, 24, f"{axis} = 0.000 g", 11, color, True)
            circle_x, circle_y = x + w - 78, y + 42
            c.circle(circle_x, circle_y, 32, "#ffffff", "#cbd3dc")
            c.line(circle_x - 32, circle_y, circle_x + 32, circle_y, "#cbd3dc")
            c.line(circle_x, circle_y - 32, circle_x, circle_y + 32, "#cbd3dc")
            plot_y = y + 88
            plot_h = max(45, h - 102)
            c.rect(x + 25, plot_y, w - 50, plot_h, "#ffffff", "#cbd3dc")
            for index, color in enumerate(("#d84e4e", "#48a15f", "#3c75ba")):
                baseline = plot_y + plot_h * (index + 1) / 4
                c.line(x + 34, baseline, x + w - 34, baseline, color, 1)
            c.text(x + 25, y + h - 16, w - 50, 16, "-5 s                                      Time                                      0 s", 8, "#64717e", False, Qt.AlignCenter)
            return
        c.text(x, y, w, 30, "Accelerometer", 16, "#24313d", True)
        c.rect(x + 40, y + 54, w - 80, h - 130, "#ffffff", "#cbd3dc")
        for index, (axis, color) in enumerate((("X", "#d84e4e"), ("Y", "#48a15f"), ("Z", "#3c75ba"))):
            baseline = y + 120 + index * 90
            c.line(x + 70, baseline, x + w - 70, baseline, "#e1e5e8")
            c.text(x + 54, baseline - 14, 30, 28, axis, 12, color, True)
        c.text(x + 60, y + h - 58, w - 120, 34, "ExpandableEEG 主数据为 8 路 EEG；若硬件数据包包含 AUX 加速度通道，将在这里显示 XYZ。", 11, "#8a6c2b", False, Qt.AlignCenter)

    def _acquisition_lsl(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        c.text(x, y, w, 30, "Networking · LSL", 16, "#24313d", True)
        c.text(x, y + 35, w, 24, "ExpandableEEG 直采 → LSL 实时输出 → 导航栏“LSL收集” → CSV", 11, "#2f5f89", True)
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
            row_y = y + 82 + row_index * 44
            c.rect(x + 90, row_y, 190, 32, "#eef2f5", "#cbd3dc", 1)
            c.text(x + 102, row_y, 166, 32, label, 10, "#24313d", True)
            c.rect(x + 280, row_y, w - 370, 32, "#ffffff", "#cbd3dc", 1)
            c.text(x + 294, row_y, w - 398, 32, value, 10, "#24313d")
        active = bool(outlet.get("active", False))
        button_text = "STOP LSL STREAM" if active else "START LSL STREAM"
        self._light_button(c, x + w / 2 - 110, y + 372, 220, 40, button_text, active)
        self._hit(x + w / 2 - 110, y + 372, 220, 40, "lsl-outlet-toggle")
        status = "正在广播：LSL收集页可以发现并接收该数据流" if active else "尚未广播；硬件采集仍可独立运行"
        c.text(x + 45, y + 426, w - 90, 28, status, 11, "#2f5f89" if active else "#64717e", True, Qt.AlignCenter)
        c.text(x + 45, y + 464, w - 90, 44, "这里是 LSL 输出端。停止 LSL 只会停止网络广播，不会停止 ExpandableEEG 硬件采集。", 10, "#64717e", False, Qt.AlignCenter)

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
        c.text(x + 70, y + 345, w - 140, 70, "根据设备逐样本时间戳检测乱序、重复和超出 2 倍采样间隔的数据缺口。\n这些指标会随实验一起记录，用于判断数据是否适合训练和论文分析。", 11, "#64717e", False, Qt.AlignCenter)

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
        c.text(x + 18, y + 468, w - 220, 45, "当前通过 ExpandableEEG 专用串口协议读取设备，已绕过标准 Cyton 握手。\n逐通道 PGA、Bias、SRB 写命令仍需实机验证后开放。", 10, "#9b6a19")
        self._light_button(c, x + w - 180, y + 468, 160, 38, "SEND（待实机验证）", False, "#9aa3aa")

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
        c.text(355, 599, 840, 23, "添加标签后保存：SSVEP连续2秒；MI连续4秒。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------
