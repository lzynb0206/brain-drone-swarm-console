"""Timed, horizontally scrolling dashboard notification controller."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF

from src.widgets.scifi_canvas import CYAN, TEXT, Canvas


class ToastController:
    def __init__(self, host) -> None:
        self.host = host
        self.message = ""
        self.generation = 0
        self.scroll_started = 0.0
        self.timer = QTimer(host)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.animate)

    def notify(self, message: str) -> None:
        self.message = str(message)
        self.generation += 1
        self.scroll_started = time.monotonic()
        self.timer.start()
        generation = self.generation
        display_ms = max(2600, min(18000, 2200 + max(0, len(self.message) - 28) * 260))
        QTimer.singleShot(display_ms, lambda: self.clear(generation))
        self.host.update()

    def paint(self, canvas: Canvas) -> None:
        canvas.rect(390, 655, 500, 45, "#15243a", CYAN, 7, 1.5)
        viewport = QRectF(410, 655, 460, 45)
        font = QFont(canvas.p.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Bold)
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(self.message)
        if text_width <= viewport.width():
            canvas.text(410, 655, 460, 45, self.message, 12, TEXT, True, Qt.AlignCenter)
            return

        elapsed = max(0.0, time.monotonic() - self.scroll_started)
        offset = max(0.0, elapsed - 0.8) * 45.0
        gap = 90.0
        cycle = text_width + gap
        x = viewport.left() - (offset % cycle)
        baseline = viewport.top() + (viewport.height() - metrics.height()) / 2 + metrics.ascent()
        canvas.p.save()
        try:
            canvas.p.setClipRect(viewport)
            canvas.p.setFont(font)
            canvas.p.setPen(QColor(TEXT))
            canvas.p.drawText(QPointF(x, baseline), self.message)
            canvas.p.drawText(QPointF(x + cycle, baseline), self.message)
        finally:
            canvas.p.restore()

    def animate(self) -> None:
        if self.message:
            self.host.update()
        else:
            self.timer.stop()

    def clear(self, generation: int) -> None:
        if generation == self.generation:
            self.message = ""
            self.timer.stop()
            self.host.update()


__all__ = ["ToastController"]
