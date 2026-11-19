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

# Match the operator workspace's neutral surfaces without changing the
# research layout. Signal traces keep their own channel palettes.
RESEARCH_BG = QColor("#f5f5f5")
RESEARCH_BG_SOFT = QColor("#fafafa")
RESEARCH_PANEL = QColor("#ffffff")
RESEARCH_PANEL_SOFT = QColor("#f7f7f7")
RESEARCH_SELECTION = QColor("#ededed")
RESEARCH_BORDER = QColor("#dedede")
RESEARCH_TEXT = QColor("#202020")
RESEARCH_MUTED = QColor("#737373")
RESEARCH_CYAN = QColor("#303030")
RESEARCH_BLUE = QColor("#242424")
RESEARCH_GREEN = QColor("#404040")
RESEARCH_YELLOW = QColor("#666666")
RESEARCH_RED = QColor("#595959")


# Keep drawing and hit testing on the same grid.  The research header used to
# duplicate these values in two modules, which made the clickable regions drift
# away from the labels whenever one side was adjusted.
RESEARCH_NAV_ITEMS = (
    ("采集", 238, 112),
    ("LSL收集", 350, 112),
    ("CSV回放", 462, 112),
    ("范式算法", 574, 112),
    ("数据采集", 686, 112),
    ("数据分析", 798, 112),
    ("单目标校准", 910, 112),
    ("SSVEP测试", 1022, 112),
)


class Canvas:
    """Draw in the reference application's 1280 × 720 coordinate space."""

    def __init__(self, painter: QPainter, width: int, height: int) -> None:
        self.p = painter
        self.sx = width / 1280.0
        self.sy = height / 720.0
        self._light_theme = False
        painter.scale(self.sx, self.sy)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

    @property
    def light_theme(self) -> bool:
        return self._light_theme

    def use_research_theme(self) -> None:
        """Use the light palette for the remainder of this paint pass."""

        self._light_theme = True

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
        panel = RESEARCH_PANEL if self._light_theme else PANEL_DARK
        border = RESEARCH_BORDER if self._light_theme else BORDER
        header = RESEARCH_PANEL_SOFT if self._light_theme else QColor("#0c162a")
        accent = RESEARCH_BLUE if self._light_theme else CYAN
        text = RESEARCH_TEXT if self._light_theme else TEXT
        self.rect(x, y, w, h, panel, border)
        if title:
            self.rect(x, y, w, 38, header, border)
            self.rect(x + 16, y + 13, 4, 13, accent)
            self.text(x + 28, y + 3, w - 34, 32, title, 14, text, True)
        if self._light_theme:
            return
        corner = 16
        for x0, y0, dx, dy in (
            (x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)
        ):
            self.line(x0, y0, x0 + dx * corner, y0, accent, 1.7)
            self.line(x0, y0, x0, y0 + dy * corner, accent, 1.7)

    def button(
        self, x: float, y: float, w: float, h: float, label: str,
        active: bool = False, disabled: bool = False, accent: QColor | str = BLUE,
        radius: float = 3,
    ) -> None:
        if self._light_theme and disabled:
            fill, stroke, color = "#f1f1f1", "#e4e4e4", "#a0a0a0"
        elif self._light_theme and active:
            fill, stroke, color = RESEARCH_BLUE, RESEARCH_BLUE, QColor("#ffffff")
        elif self._light_theme:
            fill, stroke, color = RESEARCH_PANEL, "#cecece", RESEARCH_TEXT
        elif disabled:
            fill, stroke, color = "#202a3d", "#344056", "#7f899c"
        elif active:
            fill, stroke, color = QColor(accent), QColor(accent).lighter(125), TEXT
        else:
            fill, stroke, color = "#111c31", "#3a4760", TEXT
        self.rect(x, y, w, h, fill, stroke, radius)
        self.text(x, y, w, h, label, 13, color, active, Qt.AlignCenter)

    def top_nav(self, active: str) -> None:
        # Preserve the existing top navigation grid and hit regions.
        self.rect(0, 0, 1280, 56, "#171717")
        self.line(0, 55, 1280, 55, "#333333")
        # compact layers icon
        self.p.setPen(QPen(QColor("#fafafa"), 2))
        for offset in (0, 5, 10):
            self.p.drawLine(QPointF(25, 20 + offset), QPointF(35, 25 + offset))
            self.p.drawLine(QPointF(35, 25 + offset), QPointF(46, 19 + offset))
        self.text(56, 10, 170, 36, "脑控无人机蜂群作战系统", 13, "#fafafa", True)
        for name, x, width in RESEARCH_NAV_ITEMS:
            if name == active:
                self.rect(x, 0, width, 55, "#f1f1f1")
                self.line(x, 54, x + width, 54, RESEARCH_BLUE, 2)
            self.text(x, 1, width, 53, name, 11, RESEARCH_TEXT if name == active else "#a3a3a3",
                      name == active, Qt.AlignCenter)
        self.rect(1154, 10, 101, 34, "#262626", "#525252", 3)
        self.text(1154, 10, 101, 34, "退出登录", 12, "#ffffff", True, Qt.AlignCenter)

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

    def logout_button(self) -> None:
        if self._light_theme:
            self.rect(1154, 674, 101, 34, RESEARCH_BLUE, RESEARCH_BLUE, 3)
            self.text(1154, 674, 101, 34, "退出登录", 12, "#ffffff", True, Qt.AlignCenter)
            return
        self.rect(1154, 674, 101, 34, "#a92f36", "#d3545a", 3)
        self.text(1154, 674, 101, 34, "退出登录", 12, "#ffffff", True, Qt.AlignCenter)
