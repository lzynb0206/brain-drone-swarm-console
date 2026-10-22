"""Concrete role widgets for the canvas-based dashboards."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from src.views.admin_view import AdminViewMixin
from src.views.engineer_canvas_view import EngineerCanvasViewMixin
from src.views.research_view import (
    RESEARCH_COMBINED_MAX_SCROLL,
    RESEARCH_COMBINED_PAGE,
    ResearchViewMixin,
)
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
    """Research dashboard with an explicit, read-only shell boundary."""

    def __init__(self, context: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._research_scroll_offset = 0.0
        self._research_content_scrolling = False
        self._research_section_y_offset = 0.0

    @property
    def research_scroll_offset(self) -> float:
        return self._research_scroll_offset

    def reset_research_scroll(self) -> None:
        self._research_scroll_offset = 0.0
        self.update()

    def _hit(self, x: float, y: float, w: float, h: float, action: str) -> None:
        if self._research_content_scrolling:
            y += self._research_section_y_offset - self._research_scroll_offset
            if y + h < 56 or y > 720:
                return
        super()._hit(x, y, w, h, action)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        current_page = {
            "范式": RESEARCH_COMBINED_PAGE,
            "算法": RESEARCH_COMBINED_PAGE,
        }.get(self.research_page, self.research_page)
        if current_page != RESEARCH_COMBINED_PAGE:
            super().wheelEvent(event)
            return

        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        movement = pixel_delta if pixel_delta else angle_delta / 120.0 * 72.0
        if not movement:
            super().wheelEvent(event)
            return
        self._research_scroll_offset = max(
            0.0,
            min(
                RESEARCH_COMBINED_MAX_SCROLL,
                self._research_scroll_offset - movement,
            ),
        )
        self.update()
        event.accept()

    @property
    def acquisition_stage(self):
        return self.context.acquisition_stage

    @property
    def acquisition_tab(self):
        return self.context.acquisition_tab

    @property
    def research_page(self):
        return self.context.research_page

    @property
    def eeg_stream(self):
        return self.context.eeg_stream

    @property
    def intent_decoder(self):
        return self.context.intent_decoder

    @property
    def experiment_recorder(self):
        return self.context.experiment_recorder

    @property
    def trial_analysis(self):
        return self.context.trial_analysis

    @property
    def lsl_collector(self):
        return self.context.lsl_collector

    @property
    def _collector_selected_device(self):
        return self.context.acquisition.selected_device

    @property
    def _collector_source_mode(self):
        return self.context.acquisition.source_mode

    @property
    def _collector_waveform_paused(self):
        return self.context.acquisition.waveform_paused

    @property
    def _continuous_recording(self):
        return self.context.acquisition.continuous_recording

    @property
    def _csv_replay_paused(self):
        return self.context.csv_replay.paused

    def _csv_playback_snapshot(self):
        return self.context.csv_replay.snapshot()

    @property
    def _eeg_channel_names(self):
        return self.context._eeg_channel_names

    @property
    def _eeg_events(self):
        return self.context._eeg_events

    @property
    def _eeg_health(self):
        return self.context._eeg_health

    @property
    def _eeg_history(self):
        return self.context._eeg_history

    @property
    def _eeg_display_history(self):
        return self.context._eeg_display_history

    @property
    def _eeg_quality(self):
        return self.context._eeg_quality

    @property
    def _eeg_sample_rate(self):
        return self.context._eeg_sample_rate

    @property
    def _eeg_status(self):
        return self.context._eeg_status

    @property
    def _hardware_channel_settings(self):
        return self.context.acquisition.hardware_channel_settings

    @property
    def _latest_recognition(self):
        return self.context._latest_recognition

    @property
    def _lsl_outlet(self):
        return self.context.acquisition.outlet

    @property
    def _openbci_sample_rate(self):
        return self.context.acquisition.sample_rate

    @property
    def _pipeline_worker(self):
        return self.context._pipeline_worker

    @property
    def _raw_csv_recorder(self):
        return self.context.acquisition.raw_recorder

    def _render(self, canvas: Canvas) -> None:
        self._paint_research(canvas)


class SoldierPage(SoldierViewMixin, RoleCanvasPage):
    """Soldier dashboard with explicit operational dependencies."""

    @property
    def soldier_page(self):
        return self.context.soldier_page

    @property
    def soldier_root(self):
        return self.context.soldier_root

    @property
    def eeg_stream(self):
        return self.context.eeg_stream

    @property
    def intent_decoder(self):
        return self.context.intent_decoder

    @property
    def swarm_gateway(self):
        return self.context.swarm_gateway

    @property
    def _latest_recognition(self):
        return self.context._latest_recognition

    @property
    def _manual_forward(self):
        return self.context._manual_forward

    @property
    def _manual_lateral(self):
        return self.context._manual_lateral

    @property
    def _manual_vertical(self):
        return self.context._manual_vertical

    @property
    def _pipeline_worker(self):
        return self.context._pipeline_worker

    @property
    def _soldier_bci_chain_active(self):
        return self.context.soldier_bci.chain_active

    @property
    def _soldier_bci_start_pending(self):
        return self.context.soldier_bci.start_pending

    def _soldier_live_chain_ready(self):
        return self.context.soldier_bci.live_chain_ready()

    def _render(self, canvas: Canvas) -> None:
        self._paint_soldier(canvas)


class AdminPage(AdminViewMixin, RoleCanvasPage):
    def _render(self, canvas: Canvas) -> None:
        self._paint_admin(canvas)


class EngineerCanvasPage(EngineerCanvasViewMixin, RoleCanvasPage):
    @property
    def engineer_page(self):
        return self.context.engineer_page

    def _render(self, canvas: Canvas) -> None:
        self._paint_engineer(canvas)


__all__ = [
    "AdminPage", "EngineerCanvasPage", "ResearchPage", "RoleCanvasPage", "SoldierPage",
]
