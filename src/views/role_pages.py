"""Concrete role widgets for the canvas-based dashboards."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from src.views.admin_view import AdminViewMixin
from src.views.engineer_canvas_view import EngineerCanvasViewMixin
from src.views.research_view import ResearchViewMixin
from src.views.soldier_view import SoldierViewMixin
from src.widgets.scifi_canvas import Canvas


class RoleCanvasPage(QWidget):
    """Own canvas input/painting and publish semantic actions through a signal."""

    action_requested = Signal(str, float, float)

    def __init__(self, context: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self._regions = []
        self.setMouseTracking(True)

    def __getattr__(self, name: str):
        context = self.__dict__.get("context")
        if context is not None:
            return getattr(context, name)
        raise AttributeError(name)

    @property
    def actions(self):
        # QWidget already defines actions(); the role renderers need the
        # workbench service stored on the shell under the same name.
        return self.context.actions

    def _hit(self, x: float, y: float, w: float, h: float, action: str) -> None:
        from PySide6.QtCore import QRectF

        self._regions.append((QRectF(x, y, w, h), action))

    def _render(self, canvas: Canvas) -> None:
        raise NotImplementedError

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            canvas = Canvas(painter, self.width(), self.height())
            self._regions = []
            self._render(canvas)
            canvas.logout()
            self._hit(1224, 663, 56, 57, "logout")
            if self.context._toast:
                self.context._paint_toast(canvas)
        finally:
            painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        point = QPointF(x, y)
        for rect, action in reversed(self._regions):
            if not rect.contains(point):
                continue
            if action in {"manual-stick-height", "manual-stick-direction"}:
                self.context._active_joystick = action
                self.context._update_manual_stick(action, x, y)
                self.grabMouse()
                return
            self.action_requested.emit(action, x, y)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        if self.context._active_joystick is not None:
            self.context._update_manual_stick(self.context._active_joystick, x, y)
            return
        interactive = any(rect.contains(QPointF(x, y)) for rect, _ in self._regions)
        self.setCursor(Qt.PointingHandCursor if interactive else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        active = self.context._active_joystick
        if active is None:
            super().mouseReleaseEvent(event)
            return
        self.context._active_joystick = None
        if active == "manual-stick-height":
            self.context._manual_vertical = 0.0
        else:
            self.context._manual_forward = 0.0
            self.context._manual_lateral = 0.0
        self.releaseMouse()
        self.context._send_manual_input()
        self.update()


class ResearchPage(ResearchViewMixin, RoleCanvasPage):
    def _render(self, canvas: Canvas) -> None:
        self._paint_research(canvas)


class SoldierPage(SoldierViewMixin, RoleCanvasPage):
    def _render(self, canvas: Canvas) -> None:
        self._paint_soldier(canvas)


class AdminPage(AdminViewMixin, RoleCanvasPage):
    def _render(self, canvas: Canvas) -> None:
        self._paint_admin(canvas)


class EngineerCanvasPage(EngineerCanvasViewMixin, RoleCanvasPage):
    def _render(self, canvas: Canvas) -> None:
        self._paint_engineer(canvas)


__all__ = [
    "AdminPage", "EngineerCanvasPage", "ResearchPage", "RoleCanvasPage", "SoldierPage",
]
