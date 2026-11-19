"""Neutral, high-contrast primitives scoped to soldier/commander pages."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from src.widgets.scifi_canvas import Canvas

BG = "#f5f5f5"
PANEL = "#ffffff"
BORDER = "#dedede"
TEXT = "#202020"
MUTED = "#737373"
ACCENT = "#303030"


class SoldierCanvas(Canvas):
    monochrome = True

    def panel(self, x, y, w, h, title=""):
        self.rect(x, y, w, h, PANEL, BORDER, 8)
        if title:
            self.text(x + 18, y + 5, w - 36, 32, title, 14, TEXT, True)
            self.line(x + 16, y + 41, x + w - 16, y + 41, BORDER)

    def button(self, x, y, w, h, label, active=False, disabled=False, accent=ACCENT, radius=5):
        if disabled:
            fill, border, text = "#f1f1f1", "#e4e4e4", "#a0a0a0"
        elif active:
            fill, border, text = "#242424", "#242424", "#ffffff"
        else:
            fill, border, text = "#ffffff", "#cecece", TEXT
        self.rect(x, y, w, h, fill, border, radius)
        self.text(x, y, w, h, label, 13, text, active, Qt.AlignCenter)

    def gauge(self, cx, cy, radius, value, label, color, compact=False):
        # Deliberately neutral; status is also written in text.
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPen

        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        self.p.setBrush(Qt.NoBrush)
        self.p.setPen(QPen(QColor("#e5e5e5"), 8, Qt.SolidLine, Qt.RoundCap))
        self.p.drawArc(rect, 210 * 16, -240 * 16)
        self.p.setPen(QPen(QColor(ACCENT), 8, Qt.SolidLine, Qt.RoundCap))
        self.p.drawArc(rect, 210 * 16, int(-240 * 16 * value / 100))
        self.text(cx - 55, cy - 23, 110, 48, f"{value}%", 28, TEXT, True, Qt.AlignCenter)
        self.text(cx - 70, cy + radius, 140, 30, label, 14, MUTED, False, Qt.AlignCenter)
