"""Legacy canvas-based engineer dashboard rendering."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.widgets.scifi_canvas import (
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class EngineerCanvasViewMixin:
    """Render the canvas fallback for engineer monitoring and diagnostics."""
    def _paint_engineer(self, c: Canvas) -> None:
        if self.engineer_page == "深度诊断":
            self._engineer_diagnostics(c)
        else:
            self._engineer_monitor(c)
        self._hit(550, 674, 96, 40, "engineer:系统监控")
        self._hit(648, 674, 94, 40, "engineer:深度诊断")

    def _engineer_monitor(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-11：CPU/GPU/内存/延迟/吞吐量来自
        # self.system_monitor.snapshot() 的定时缓存；LSL 卡片来自流发现服务，
        # 日志来自 logging/loguru 的队列处理器。不要在绘图线程读取日志文件。
        c.fill("#080e20")
        c.rect(0, 0, 255, 720, "#0d172a")
        c.text(24, 18, 180, 42, "模块树", 17, TEXT, True)
        modules = ["M1  蜂群作战管理", "M2  目标识别与打击", "M4  脑电解码引擎", "M4  脑电解码引擎",
                   "M6  设备协同管理", "M7  设备协同监控", "M7  信号质量监控", "M11  数据策管"]
        for i, name in enumerate(modules):
            y = 70 + i * 32
            if i == 6:
                c.rect(24, y, 231, 29, "#1a2c58")
                c.rect(24, y, 4, 29, BLUE)
            c.text(39, y, 205, 29, "• " + name, 12, TEXT if i == 6 else "#aeb7c8")
        c.text(280, 17, 520, 43, "LSL数据流监控", 20, TEXT, True)
        c.panel(280, 77, 654, 249, "LSL数据流监控")
        streams = [("EEG", "250Hz", "0.1%"), ("Crazyflie\n遥测流", "100Hz", "0.3%"), ("fNIRS\n流", "50Hz", "0.0%")]
        for i, (name, rate, loss) in enumerate(streams):
            x = 297 + i * 215
            c.rect(x, 133, 191, 178, "#17243a", "#2a3a57", 3)
            c.text(x + 16, 145, 104, 39, name, 14, TEXT, True)
            c.text(x + 109, 145, 70, 25, "采样率", 14, TEXT, True, Qt.AlignRight)
            c.text(x + 109, 175, 70, 27, rate, 20, TEXT, True, Qt.AlignRight)
            c.line(x + 16, 236, x + 175, 236, "#263750")
            c.text(x + 16, 242, 80, 28, f"丢包率{loss}", 11, MUTED)
            c.text(x + 126, 242, 49, 28, loss, 13, CYAN, False, Qt.AlignRight)
        c.rect(314, 174, 127, 114, "#07101f", BLUE, 3)
        for i, name in enumerate(("实时预处理", "实时预处理", "算法推理", "置信度输出")):
            c.text(334, 177 + i * 25, 102, 23, "• " + name, 11, TEXT if i < 2 else MUTED)
        c.panel(280, 354, 654, 335, "性能监控")
        c.text(767, 357, 151, 32, "━ CPU    ━ 内存    ━ 延迟", 11, "#72dc83", False, Qt.AlignRight)
        c.chart(361, 421, 553, 210, [BLUE, "#efaa42", GREEN], amp=.19, points=40)
        c.text(280, 689, 260, 27, "运行时间 01:23:45", 11, MUTED)
        c.text(530, 689, 160, 27, "帧率 60 FPS", 11, TEXT, True)
        c.text(788, 689, 146, 27, "数据吞吐量 4.2 MB/s", 11, MUTED, False, Qt.AlignRight)
        c.rect(960, 0, 320, 720, "#0d172a")
        for i, name in enumerate(("INFO", "WARN", "ERROR")):
            active = self.actions.log_filter == name
            c.text(960 + i * 106, 0, 106, 53, name, 13, TEXT if active else MUTED, active, Qt.AlignCenter)
            if active:
                c.rect(960 + i * 106, 52, 106, 3, BLUE)
            self._hit(960 + i * 106, 0, 106, 55, f"log-filter:{name}")
        log_sets = {
            "INFO": ["INFO: 算法已加载 GraSPNet  14:30:05", "INFO: LSL流已连接  14:30:01"],
            "WARN": ["WARN: 信号质量下降  14:30:12", "WARN: UAV-03 丢包率偏高  14:31:08"],
            "ERROR": ["ERROR: 暂无未处理错误  14:31:30"],
        }
        logs = log_sets[self.actions.log_filter]
        for i in range(19):
            value = logs[i % len(logs)]
            color = RED if value.startswith("ERROR") else (YELLOW if value.startswith("WARN") else TEXT)
            c.text(977, 67 + i * 24, 290, 22, value, 10, color, "算法" in value)
        c.button(550, 674, 96, 31, "系统监控", True, accent=CYAN)
        c.button(648, 674, 94, 31, "深度诊断")

    def _engineer_diagnostics(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-13 / M10：资源仪表复用 SystemMonitor；通信诊断
        # 读取各设备丢包率和往返延迟；干扰曲线来自 EEG 频谱/50 Hz 检测。
        # “一键自检”应调用 DiagnosticService，异步返回逐项结果和错误详情。
        c.fill("#050b16")
        c.rect(8, 8, 1263, 704, None, BORDER)
        c.rect(440, 0, 400, 40, "#0f1c31", CYAN, 12, 2)
        c.text(440, 0, 400, 39, "系统深度诊断", 17, CYAN, True, Qt.AlignCenter)
        c.panel(33, 66, 594, 296, "资源监控")
        c.gauge(168, 211, 70, 62, "CPU", "#5dbcd4")
        c.gauge(356, 211, 70, 45, "GPU", "#66c88a")
        c.gauge(549, 211, 70, 58, "内存", "#5dbcd4")
        c.panel(653, 66, 594, 296, "通信诊断")
        # bars
        bars = [(42, 67), (20, 53), (52, 56), (44, 40), (31, 45)]
        for i, (a, b) in enumerate(bars):
            x = 744 + i * 98
            c.rect(x, 242 - a * 1.3, 37, a * 1.3, CYAN)
            c.rect(x + 42, 242 - b * 1.3, 37, b * 1.3, GREEN)
        c.line(734, 242, 1224, 242, "#263752")
        path = QPainterPath(QPointF(734, 312))
        for i in range(110):
            xx = 734 + i
            y_coord = 312 - 31 * math.exp(-((i - 52) / 19) ** 2) + max(0, i - 84) * .25
            path.lineTo(xx, y_coord)
        c.p.setPen(QPen(QColor(YELLOW), 1.2))
        c.p.setBrush(QColor(219, 154, 55, 45))
        path.lineTo(843, 362)
        path.lineTo(734, 362)
        path.closeSubpath()
        c.p.drawPath(path)
        c.panel(33, 389, 594, 298, "干扰检测")
        c.chart(94, 444, 513, 228, [CYAN], amp=.05, points=90)
        c.rect(320, 458, 62, 196, QColor(241, 78, 80, 25))
        c.line(332, 444, 332, 663, RED, 1, Qt.DashLine)
        c.text(287, 462, 100, 42, "50Hz工频干扰\n幅值 12.5µV", 11, RED, True, Qt.AlignCenter)
        c.panel(653, 389, 594, 298, "自检报告")
        c.button(831, 473, 238, 46, "重新自检" if self.actions.self_test_run else "一键自检", True)
        self._hit(831, 473, 238, 46, "self-test")
        checks = [("电源系统  通过", "通过", GREEN), ("通信链路  通过", "通过", GREEN),
                  ("信号质量  已复测" if self.actions.self_test_run else "信号质量  警告 (阻抗偏高)",
                   "通过" if self.actions.self_test_run else "警告",
                   GREEN if self.actions.self_test_run else YELLOW)]
        for i, (label, state, color) in enumerate(checks):
            y = 551 + i * 66
            c.rect(831, y, 238, 49, "#0d172a", BORDER, 3)
            c.text(845, y, 174, 49, label, 13, TEXT)
            c.text(1018, y, 39, 49, state, 13, color, True, Qt.AlignRight)
        c.button(550, 674, 96, 31, "系统监控")
        c.button(648, 674, 94, 31, "深度诊断", True, accent=CYAN)

    # ---------- Soldier/commander dashboards ----------


