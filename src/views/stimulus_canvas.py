"""Frame-swapped stimulus drawing, with a raster fallback for headless tests.

Qt swap timestamps are presentation diagnostics, not photodiode measurements.
"""
from __future__ import annotations

import os
import math
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QWidget


if os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal"}:
    class StimulusCanvas(QWidget):
        presentation_source = "headless-raster-test"

        def paintEvent(self, event):
            self.paint_stimulus()
else:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    class StimulusCanvas(QOpenGLWidget):
        presentation_source = "qt-frame-swapped-screen-paced-vsync-requested"

        def __init__(self, parent=None, flags=Qt.Window):
            super().__init__(parent, flags)
            surface = QSurfaceFormat.defaultFormat()
            surface.setSwapInterval(1)
            self.setFormat(surface)

        def paintGL(self):
            self.paint_stimulus()


class StimulusFrameDriver(QObject):
    """Pace swap-driven repaint requests at the screen's reported refresh rate.

    Some macOS Qt compositors emit frameSwapped faster than physical refresh.
    Therefore the signal alone is not a display clock. Use absolute deadlines
    as an additional rate cap; luminance still uses actual monotonic time.
    """
    timeout = Signal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._active = False
        self._next_frame = 0.0
        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setTimerType(Qt.PreciseTimer)
        self._request_timer.timeout.connect(self._frame)
        swapped = getattr(canvas, "frameSwapped", None)
        self._headless = swapped is None
        if swapped is not None:
            swapped.connect(self._schedule)

    def start(self):
        self._active = True
        self._next_frame = time.monotonic()
        self._frame()

    def stop(self):
        self._active = False
        self._request_timer.stop()

    def isActive(self):
        return self._active

    def _frame(self):
        if not self._active:
            return
        now = time.monotonic()
        if now < self._next_frame:
            self._schedule()
            return
        # Headless raster tests have no physical display and must not query a
        # platform screen through a hidden widget (unstable on macOS offscreen).
        screen = None if self._headless else self._canvas.screen()
        hz = float(screen.refreshRate()) if screen is not None else 60.0
        hz = hz if math.isfinite(hz) and hz >= 30.0 else 60.0
        period = 1.0 / hz
        self._next_frame += (math.floor((now - self._next_frame) / period) + 1) * period
        self.timeout.emit()
        if self._headless:
            self._schedule()

    def _schedule(self):
        if self._active and not self._request_timer.isActive():
            delay_ms = max(0, math.ceil((self._next_frame - time.monotonic()) * 1000.0))
            self._request_timer.start(delay_ms)
