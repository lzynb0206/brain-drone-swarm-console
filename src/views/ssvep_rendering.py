"""Shared acquisition-style rendering for every six-target SSVEP window."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen


SSVEP_CANVAS_BACKGROUND = "#050914"
SSVEP_HEADER_BACKGROUND = QColor(5, 11, 24, 245)
SSVEP_TARGET_BORDER = "#34435b"
SSVEP_ACCENT = "#00e5ff"
SSVEP_OVERLAY_ACCENT = "#00d8f0"


def target_rectangles(width: int, height: int) -> tuple[QRectF, ...]:
    """Return six spatial command slots in the canonical command order."""

    width_f = max(1.0, float(width))
    height_f = max(1.0, float(height))
    # Match the large, widely separated visual targets used by the inherited
    # PsychoPy experiment, scaled to the current display instead of assuming a
    # fixed 1920x1080/2560x1600 canvas.  Both calibration and online testing
    # call this function, so target size and retinal position stay identical.
    block_width = max(240.0, width_f * 0.24)
    block_height = max(180.0, height_f * 0.28)
    centers_x = (width_f * 0.14, width_f * 0.50, width_f * 0.86)
    centers_y = (height_f * 0.20, height_f * 0.80)
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


def draw_ssvep_target(
    painter: QPainter,
    rect: QRectF,
    label: str,
    frequency_hz: float,
    luminance: float,
    *,
    border_color: QColor | str = SSVEP_TARGET_BORDER,
    border_width: int = 4,
    border_style: Qt.PenStyle = Qt.SolidLine,
) -> None:
    """Draw one target exactly as it appears in the acquisition experiment."""

    gray = int(round(255 * max(0.0, min(1.0, float(luminance)))))
    painter.fillRect(rect, QColor(gray, gray, gray))
    painter.setPen(QPen(QColor(border_color), border_width, border_style))
    painter.drawRect(rect)
    text_color = QColor(SSVEP_CANVAS_BACKGROUND) if gray > 150 else QColor("#eef6ff")
    painter.setPen(text_color)
    painter.setFont(QFont("PingFang SC", max(17, int(rect.height() / 9)), QFont.Bold))
    painter.drawText(
        QRectF(
            rect.left(), rect.top() + rect.height() * 0.10,
            rect.width(), rect.height() * 0.24,
        ),
        Qt.AlignCenter,
        str(label),
    )
    painter.setFont(QFont("Arial", max(14, int(rect.height() / 12)), QFont.Bold))
    painter.drawText(
        QRectF(
            rect.left(), rect.top() + rect.height() * 0.70,
            rect.width(), rect.height() * 0.18,
        ),
        Qt.AlignCenter,
        f"{float(frequency_hz):g} Hz",
    )
    draw_fixation_cross(
        painter,
        rect.center(),
        15.0,
        text_color,
        SSVEP_CANVAS_BACKGROUND,
    )


def draw_ssvep_header(
    painter: QPainter,
    width: int,
    title: str,
    subtitle: str,
) -> None:
    """Draw the two-line acquisition header shared by all SSVEP modes."""

    painter.fillRect(QRectF(0, 0, width, 70), SSVEP_HEADER_BACKGROUND)
    painter.setPen(QColor("#e9f2ff"))
    painter.setFont(QFont("PingFang SC", 17, QFont.Bold))
    painter.drawText(QRectF(20, 6, width - 40, 30), Qt.AlignCenter, title)
    painter.setPen(QColor("#91a4bd"))
    painter.setFont(QFont("PingFang SC", 12))
    painter.drawText(QRectF(20, 37, width - 40, 26), Qt.AlignCenter, subtitle)


def ssvep_status_banner_rect(width: int, height: int) -> QRectF:
    """Return the acquisition experiment's central status-banner geometry."""

    return QRectF(width * 0.25, height * 0.43, width * 0.50, height * 0.15)


def draw_ssvep_status_banner(
    painter: QPainter,
    width: int,
    height: int,
    text: str,
    *,
    border_color: QColor | str = SSVEP_OVERLAY_ACCENT,
    text_color: QColor | str = "#e9f2ff",
) -> QRectF:
    """Draw the centered phase/result card used by the acquisition experiment."""

    banner = ssvep_status_banner_rect(width, height)
    painter.fillRect(banner, QColor(7, 16, 32, 248))
    painter.setPen(QPen(QColor(border_color), 3))
    painter.drawRect(banner)
    painter.setPen(QColor(text_color))
    painter.setFont(QFont("PingFang SC", max(20, int(height * 0.027)), QFont.Bold))
    painter.drawText(
        banner.adjusted(18, 10, -18, -10),
        Qt.AlignCenter | Qt.TextWordWrap,
        text,
    )
    return banner


__all__ = [
    "SSVEP_ACCENT",
    "SSVEP_CANVAS_BACKGROUND",
    "SSVEP_HEADER_BACKGROUND",
    "SSVEP_OVERLAY_ACCENT",
    "SSVEP_TARGET_BORDER",
    "draw_fixation_cross",
    "draw_ssvep_header",
    "draw_ssvep_status_banner",
    "draw_ssvep_target",
    "ssvep_status_banner_rect",
    "target_rectangles",
]
