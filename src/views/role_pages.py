"""Concrete role widgets for the canvas-based dashboards."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from src.views.research_view import ResearchViewMixin
from src.views.soldier_view import SoldierViewMixin
from src.views.soldier_shell import SoldierShellMixin
from src.widgets.scifi_canvas import Canvas


class RoleCanvasPage(QWidget):
    """Own canvas input/painting and publish semantic actions through a signal."""

    action_requested = Signal(str, float, float)
    show_corner_logout = True

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
            if self.show_corner_logout:
                canvas.logout_button()
                self._hit(1154, 674, 101, 34, "logout")
            if self.context._toast:
                self.context._paint_toast(canvas)
        finally:
            painter.end()

    def _canvas_position(self, position):
        return QPointF(position.x() * 1280 / max(1, self.width()),
                       position.y() * 720 / max(1, self.height()))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        point = self._canvas_position(event.position())
        x, y = point.x(), point.y()
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
        point = self._canvas_position(event.position())
        x, y = point.x(), point.y()
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

    show_corner_logout = False
    subject_reorder_requested = Signal(str, str, bool)
    SUBJECT_MIME = "application/x-brain-drone-subject-card"

    @property
    def acquisition_profile(self):
        return self.context.acquisition_profile

    def __init__(self, context: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self.collection_section = ""
        self._research_scroll_offset = 0.0
        self._research_effective_scroll_offset = 0.0
        self._research_content_scrolling = False
        self._subject_press = None
        self._subject_drag_id = ""
        self._subject_drop_target = None
        self.setAcceptDrops(True)

    def _subject_at(self, position):
        point = QPointF(position.x() * 1280 / max(1, self.width()), position.y() * 720 / max(1, self.height()))
        if self.research_page != "数据分析" or point.y() < 56:
            return None
        for rect, action in reversed(self._regions):
            if action.startswith("analysis-subject:") and rect.contains(point):
                return action.split(":", 1)[1], rect, point.x() >= rect.center().x()
        return None

    def mousePressEvent(self, event) -> None:
        subject = self._subject_at(event.position())
        if event.button() == Qt.LeftButton and subject is not None:
            self._subject_press = (subject[0], event.position().toPoint(), subject[1])
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._subject_press is not None and event.buttons() & Qt.LeftButton:
            subject_id, start, rect = self._subject_press
            if (event.position().toPoint() - start).manhattanLength() < QApplication.startDragDistance():
                return
            self._subject_press = None
            self._subject_drag_id = subject_id
            mime = QMimeData()
            mime.setData(self.SUBJECT_MIME, subject_id.encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            scaled = QRect(round(rect.x() * self.width() / 1280), round(rect.y() * self.height() / 720),
                           round(rect.width() * self.width() / 1280), round(rect.height() * self.height() / 720))
            drag.setPixmap(self.grab(scaled))
            drag.setHotSpot(start - scaled.topLeft())
            self.setCursor(Qt.ClosedHandCursor)
            try:
                drag.exec(Qt.MoveAction)
            finally:
                drag.deleteLater()
                self._subject_drag_id = ""
                self._subject_drop_target = None
                self.unsetCursor()
                self.update()
            return
        if self._subject_at(event.position()) is not None:
            self.setCursor(Qt.OpenHandCursor)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._subject_press is not None:
            subject_id, _, _ = self._subject_press
            self._subject_press = None
            target = self._subject_at(event.position())
            if event.button() == Qt.LeftButton and target is not None and target[0] == subject_id:
                self.action_requested.emit(f"analysis-subject:{subject_id}", 0, 0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.source() is self and event.mimeData().hasFormat(self.SUBJECT_MIME) and self.research_page == "数据分析":
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _subject_drop_at(self, position):
        if self.research_page != "数据分析":
            return None
        subject = self._subject_at(position)
        if subject is not None:
            return subject[0], subject[2]
        # Dropping onto a page button moves the card across page boundaries.
        point = QPointF(position.x() * 1280 / max(1, self.width()), position.y() * 720 / max(1, self.height()))
        groups = self.trial_analysis.subjects
        size = self.trial_analysis.SUBJECT_PAGE_SIZE
        page = self.trial_analysis.subject_page
        for rect, action in self._regions:
            if not rect.contains(point):
                continue
            if action == "analysis-subject-page-previous" and page > 0:
                return groups[page * size - 1].subject_id, False
            if action == "analysis-subject-page-next" and page + 1 < self.trial_analysis.subject_page_count:
                return groups[(page + 1) * size].subject_id, True
        return None

    def dragMoveEvent(self, event) -> None:
        target = self._subject_drop_at(event.position()) if event.source() is self else None
        self._subject_drop_target = target
        self.update()
        if target is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._subject_drop_target = None
        self.update()
        event.accept()

    def dropEvent(self, event) -> None:
        target = self._subject_drop_at(event.position())
        if event.source() is self and event.mimeData().hasFormat(self.SUBJECT_MIME) and target is not None:
            subject_id = bytes(event.mimeData().data(self.SUBJECT_MIME)).decode("utf-8")
            self.subject_reorder_requested.emit(subject_id, target[0], target[1])
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()
        self._subject_drop_target = None
        self.update()

    @property
    def research_scroll_offset(self) -> float:
        return self._research_scroll_offset

    def reset_research_scroll(self, repaint: bool = True) -> None:
        self._research_scroll_offset = 0.0
        self._research_effective_scroll_offset = 0.0
        if repaint:
            self.update()

    def _hit(self, x: float, y: float, w: float, h: float, action: str) -> None:
        if self._research_content_scrolling:
            # Painting clamps the stored scroll position to the current page's
            # content height.  Hit testing must use that same effective value;
            # otherwise clearing a tall report leaves invisible, displaced hit
            # regions behind even though the page has visibly returned to top.
            y -= self._research_effective_scroll_offset
            if y + h < 56 or y > 720:
                return
        super()._hit(x, y, w, h, action)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.research_page == "数据分析":
            max_scroll = self._research_data_max_scroll()
        else:
            super().wheelEvent(event)
            return
        if max_scroll <= 0:
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
                max_scroll,
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
    def experiment(self):
        return self.context.experiment

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


class SoldierPage(SoldierShellMixin, SoldierViewMixin, RoleCanvasPage):
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


__all__ = [
    "ResearchPage", "RoleCanvasPage", "SoldierPage",
]
