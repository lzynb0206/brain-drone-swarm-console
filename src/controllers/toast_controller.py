"""Timed, horizontally scrolling dashboard notification controller."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF

from src.widgets.scifi_canvas import (
    CYAN,
    TEXT,
    RESEARCH_BLUE,
    RESEARCH_TEXT,
    Canvas,
)


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
        self._update_toast_region()

    def paint(self, canvas: Canvas) -> None:
        if getattr(canvas, "monochrome", False):
            fill, border, text_color = "#ffffff", "#bdbdbd", "#252525"
        elif canvas.light_theme:
            fill, border, text_color = "#ffffff", RESEARCH_BLUE, RESEARCH_TEXT
        else:
            fill, border, text_color = "#15243a", CYAN, TEXT
        canvas.rect(390, 655, 500, 45, fill, border, 7, 1.5)
        viewport = QRectF(410, 655, 460, 45)
        font = QFont(canvas.p.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Bold)
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(self.message)
        if text_width <= viewport.width():
            canvas.text(410, 655, 460, 45, self.message, 12, text_color, True, Qt.AlignCenter)
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
            canvas.p.setPen(QColor(text_color))
            canvas.p.drawText(QPointF(x, baseline), self.message)
            canvas.p.drawText(QPointF(x + cycle, baseline), self.message)
        finally:
            canvas.p.restore()

    def animate(self) -> None:
        if self.message:
            self._update_toast_region()
        else:
            self.timer.stop()

    def clear(self, generation: int) -> None:
        if generation == self.generation:
            self.message = ""
            self.timer.stop()
            self._update_toast_region()

    def _update_toast_region(self) -> None:
        """Repaint only the toast instead of a potentially huge report."""

        role_stack = getattr(self.host, "_role_stack", None)
        widget = role_stack.currentWidget() if role_stack is not None else None
        if widget is None:
            self.host.update()
            return
        content_rect = getattr(widget, "content_rect", None)
        if callable(content_rect):
            rect = content_rect()
            widget.update(QRect(
                rect.x() + int(375 * rect.width() / 1280),
                rect.y() + int(642 * rect.height() / 720),
                int(530 * rect.width() / 1280) + 2,
                int(69 * rect.height() / 720) + 2,
            ))
            return
        width = max(1, widget.width())
        height = max(1, widget.height())
        left = int(375 * width / 1280)
        top = int(642 * height / 720)
        right = int(905 * width / 1280) + 1
        bottom = int(711 * height / 720) + 1
        widget.update(QRect(left, top, right - left, bottom - top))


__all__ = ["ToastController"]
