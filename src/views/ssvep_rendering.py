"""Shared geometry and primitives for SSVEP stimulus windows."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen


def target_rectangles(width: int, height: int) -> tuple[QRectF, ...]:
    """Return six spatial command slots in the canonical command order."""

    width_f = max(1.0, float(width))
    height_f = max(1.0, float(height))
    # Keep every target small and separated by a wide black field. The four
    # outer targets sit close to the screen corners; forward/backward retain
    # their intuitive upper/lower centre positions. Calibration uses this
    # exact function, so train and online geometry cannot drift apart again.
    block_width = max(140.0, width_f * 0.14)
    block_height = max(100.0, height_f * 0.18)
    centers_x = (width_f * 0.09, width_f * 0.50, width_f * 0.91)
    centers_y = (height_f * 0.18, height_f * 0.82)
    slots = tuple(
        QRectF(
            center_x - block_width / 2.0,
            center_y - block_height / 2.0,
            block_width,
            block_height,
        )
        for center_y in centers_y
        for center_x in centers_x
    )
    # COMMANDS = 起飞、前进、后退、左移、右移、降落.
    return tuple(slots[index] for index in (0, 1, 4, 3, 5, 2))


def draw_fixation_cross(
    painter: QPainter,
    center: QPointF,
    half_length: float,
    color: QColor,
    dark_background: str,
) -> None:
    """Draw a high-contrast fixation cross over either luminance extreme."""

    outline = QColor(dark_background) if color.lightness() > 128 else QColor("#ffffff")
    horizontal = (
        QPointF(center.x() - half_length, center.y()),
        QPointF(center.x() + half_length, center.y()),
    )
    vertical = (
        QPointF(center.x(), center.y() - half_length),
        QPointF(center.x(), center.y() + half_length),
    )
    painter.setPen(QPen(outline, 6))
    painter.drawLine(*horizontal)
    painter.drawLine(*vertical)
    painter.setPen(QPen(color, 3))
    painter.drawLine(*horizontal)
    painter.drawLine(*vertical)


__all__ = ["draw_fixation_cross", "target_rectangles"]
