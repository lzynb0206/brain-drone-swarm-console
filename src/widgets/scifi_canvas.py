"""Shared QPainter primitives for the React-to-PySide6 screen translation."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen


BG = QColor("#050b16")
BG_SOFT = QColor("#071020")
PANEL = QColor("#101c32")
PANEL_DARK = QColor("#07101f")
BORDER = QColor("#243452")
TEXT = QColor("#eef2f8")
MUTED = QColor("#8793a8")
CYAN = QColor("#63e6f5")
BLUE = QColor("#3769ef")
GREEN = QColor("#69ee90")
YELLOW = QColor("#f0ce54")
RED = QColor("#f14e50")


class Canvas:
    """Draw in the reference application's 1280 × 720 coordinate space."""

    def __init__(self, painter: QPainter, width: int, height: int) -> None:
        self.p = painter
        self.sx = width / 1280.0
        self.sy = height / 720.0
        painter.scale(self.sx, self.sy)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

    def fill(self, color: QColor | str = BG) -> None:
        self.p.fillRect(QRectF(0, 0, 1280, 720), QColor(color))

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: QColor | str | None = None,
        stroke: QColor | str | None = None,
        radius: float = 0,
        width: float = 1,
    ) -> None:
        self.p.setPen(QPen(QColor(stroke), width) if stroke else Qt.NoPen)
        self.p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        self.p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    def line(
        self, x1: float, y1: float, x2: float, y2: float, color: QColor | str, width: float = 1,
        style: Qt.PenStyle = Qt.SolidLine,
    ) -> None:
        self.p.setPen(QPen(QColor(color), width, style))
        self.p.setBrush(Qt.NoBrush)
        self.p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def text(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        value: str,
        size: float = 13,
        color: QColor | str = TEXT,
        bold: bool = False,
        align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
    ) -> None:
        font = QFont(self.p.font())
        font.setPixelSize(max(8, int(size)))
        font.setWeight(QFont.Bold if bold else QFont.Normal)
        self.p.setFont(font)
        self.p.setPen(QColor(color))
        self.p.drawText(QRectF(x, y, w, h), int(align), value)

    def circle(
        self, x: float, y: float, radius: float, fill: QColor | str | None,
        stroke: QColor | str | None = None, width: float = 1,
    ) -> None:
        self.p.setPen(QPen(QColor(stroke), width) if stroke else Qt.NoPen)
        self.p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        self.p.drawEllipse(QPointF(x, y), radius, radius)

    def panel(self, x: float, y: float, w: float, h: float, title: str = "") -> None:
        self.rect(x, y, w, h, PANEL_DARK, BORDER)
        if title:
            self.rect(x, y, w, 38, "#0c162a", BORDER)
            self.rect(x + 16, y + 13, 4, 13, CYAN)
            self.text(x + 28, y + 3, w - 34, 32, title, 14, TEXT, True)
        corner = 16
        for x0, y0, dx, dy in (
            (x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)
        ):
            self.line(x0, y0, x0 + dx * corner, y0, CYAN, 1.7)
            self.line(x0, y0, x0, y0 + dy * corner, CYAN, 1.7)

    def button(
        self, x: float, y: float, w: float, h: float, label: str,
        active: bool = False, disabled: bool = False, accent: QColor | str = BLUE,
        radius: float = 3,
    ) -> None:
        if disabled:
            fill, stroke, color = "#202a3d", "#344056", "#7f899c"
        elif active:
            fill, stroke, color = QColor(accent), QColor(accent).lighter(125), TEXT
        else:
            fill, stroke, color = "#111c31", "#3a4760", TEXT
        self.rect(x, y, w, h, fill, stroke, radius)
        self.text(x, y, w, h, label, 13, color, active, Qt.AlignCenter)

    def top_nav(self, active: str) -> None:
        self.rect(0, 0, 1280, 56, "#0b1425")
        self.line(0, 55, 1280, 55, "#20314f")
        # compact layers icon
        self.p.setPen(QPen(TEXT, 2))
        for offset in (0, 5, 10):
            self.p.drawLine(QPointF(25, 20 + offset), QPointF(35, 25 + offset))
            self.p.drawLine(QPointF(35, 25 + offset), QPointF(46, 19 + offset))
        self.text(56, 10, 170, 36, "脑控无人机蜂群作战系统", 13, TEXT, True)
        items = [
            ("采集", 242), ("实验", 308), ("范式", 374), ("算法", 440),
            ("控制", 506), ("映射", 572), ("数据", 638), ("工具", 704), ("帮助", 772),
        ]
        for name, x in items:
            if name == active:
                self.rect(x - 11, 0, 61, 56, "#111c31", "#78ddec", 2)
                self.line(x - 10, 53, x + 49, 53, RED, 2)
            self.text(x, 0, 38, 54, name, 13, TEXT if name == active else MUTED,
                      name == active, Qt.AlignCenter)
        self.circle(1038, 27, 17, "#ef4d3e")
        self.text(1023, 10, 30, 33, "⏻", 17, TEXT, False, Qt.AlignCenter)
        self.circle(1086, 27, 17, "#15233e")
        self.text(1071, 10, 30, 33, "$", 15, TEXT, False, Qt.AlignCenter)
        self.button(1118, 10, 34, 34, "▥")
        self.button(1166, 10, 89, 34, "添加标签")

    def chart(
        self, x: float, y: float, w: float, h: float,
        colors: Sequence[QColor | str], phases: Sequence[float] | None = None,
        amp: float = 0.32, points: int = 60, labels: bool = False,
    ) -> None:
        self.p.setClipRect(QRectF(x, y, w, h))
        for i in range(1, 10):
            gx = x + w * i / 10
            self.line(gx, y, gx, y + h, "#263249", 1, Qt.DashLine)
        for i in range(1, 5):
            gy = y + h * i / 5
            self.line(x, gy, x + w, gy, "#263249", 1, Qt.DashLine)
        phases = phases or [i * 0.9 for i in range(len(colors))]
        for series, color in enumerate(colors):
            path = QPainterPath()
            for i in range(points):
                t = i / (points - 1)
                spike = 0.0
                if 0.42 < t < 0.5:
                    spike = (0.5 - abs(t - 0.46)) * (0.58 if series % 2 == 0 else -0.52)
                v = math.sin(t * 8 * math.pi + phases[series]) * amp
                v += math.sin(t * 23 * math.pi + series) * 0.045 + spike
                px = x + t * w
                py = y + h * (0.5 - v)
                if i == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            self.p.setPen(QPen(QColor(color), 1.5))
            self.p.setBrush(Qt.NoBrush)
            self.p.drawPath(path)
        self.p.setClipping(False)
        if labels:
            self.text(x - 28, y - 8, 26, 20, "40", 10, "#566178", False, Qt.AlignRight)
            self.text(x - 28, y + h / 2 - 10, 26, 20, "0", 10, "#566178", False, Qt.AlignRight)
            self.text(x - 34, y + h - 10, 32, 20, "-20", 10, "#566178", False, Qt.AlignRight)

    def gauge(
        self, cx: float, cy: float, radius: float, value: int, label: str,
        color: QColor | str, compact: bool = False,
    ) -> None:
        pen_bg = QPen(QColor("#1d2c4a"), 11, Qt.SolidLine, Qt.RoundCap)
        pen_fg = QPen(QColor(color), 11, Qt.SolidLine, Qt.RoundCap)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        self.p.setBrush(Qt.NoBrush)
        self.p.setPen(pen_bg)
        self.p.drawArc(rect, 210 * 16, -240 * 16)
        self.p.setPen(pen_fg)
        self.p.drawArc(rect, 210 * 16, int(-240 * 16 * value / 100))
        self.text(cx - 55, cy - 23, 110, 48, f"{value}%", 28 if not compact else 23, TEXT, True, Qt.AlignCenter)
        self.text(cx - 70, cy + radius - 4, 140, 30, label, 14, TEXT, False, Qt.AlignCenter)

    def logout(self) -> None:
        self.circle(1247, 686, 17, "#101d32", "#263855")
        self.text(1231, 669, 32, 34, "↪", 18, "#6f7e97", False, Qt.AlignCenter)


def deterministic_wave(count: int, phase: float, scale: float = 1.0) -> list[float]:
    return [
        (math.sin(i * 0.37 + phase) + 0.38 * math.sin(i * 0.91 + phase * 0.7)) * scale
        for i in range(count)
    ]
