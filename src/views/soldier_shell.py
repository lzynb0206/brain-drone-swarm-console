"""Operator shell: native sidebar/header and a separate painted content viewport."""
from PySide6.QtCore import QEvent, QPointF, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.views.soldier_vision_panel import SoldierVisionPanel
from src.views.soldier_haptic_panel import SoldierHapticPanel
from src.widgets.soldier_canvas import BG, SoldierCanvas
from src.widgets.soldier_sidebar import SECTIONS, SoldierSidebar


class SoldierShellMixin:
    show_corner_logout = False
    HEADER_HEIGHT = 76

    def __init__(self, context, parent=None):
        super().__init__(context, parent)
        self.vision_panel = SoldierVisionPanel(context.gesture, self)
        self.vision_panel.hide()
        self.haptic_panel = SoldierHapticPanel(context.haptic, self)
        self.haptic_panel.hide()
        self.header = QFrame(self)
        self.header.setObjectName("soldierHeader")
        self.header.setStyleSheet("""
            #soldierHeader { background: #ffffff; border-bottom: 1px solid #e1e1e1; }
            #soldierHeader QLabel { color: #777777; font-size: 11px; border: 0; }
            #soldierHeader QLabel#sectionTitle { color: #202020; font-size: 20px; font-weight: 600; }
            #soldierHeader QLabel#connectionBadge { background: #f3f3f3; color: #555555;
                padding: 8px 14px; border-radius: 14px; }
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 10, 24, 10)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.section_title = QLabel()
        self.section_title.setObjectName("sectionTitle")
        self.section_description = QLabel()
        titles.addWidget(self.section_title)
        titles.addWidget(self.section_description)
        header_layout.addLayout(titles, 1)
        self.connection_badge = QLabel()
        self.connection_badge.setObjectName("connectionBadge")
        header_layout.addWidget(self.connection_badge)
        self.sidebar = SoldierSidebar(self)
        self.sidebar.action_requested.connect(self._navigate)
        self.sidebar.pinned_changed.connect(self._layout_shell)
        self.vision_toast = QLabel(self)
        self.vision_toast.setWordWrap(True)
        self.vision_toast.setStyleSheet("background: white; color: #252525; border: 1px solid #bdbdbd; border-radius: 7px; padding: 12px;")
        self.vision_toast.hide()
        self._section = ""
        QApplication.instance().installEventFilter(self)
        self.sync_native_panels()

    @property
    def active_section(self):
        if self.soldier_root == "作战地图":
            return "map"
        return {"脑控": "brain", "视觉手势": "vision", "触觉反馈": "haptic", "状态监控": "status"}.get(self.soldier_page, "brain")

    def _navigate(self, action):
        self.action_requested.emit(action, 0, 0)

    def content_rect(self):
        margin = self.sidebar.EXPANDED if self.sidebar.pinned else self.sidebar.COLLAPSED
        return QRect(margin, self.HEADER_HEIGHT, max(1, self.width() - margin),
                     max(1, self.height() - self.HEADER_HEIGHT))

    def _layout_shell(self):
        rect = self.content_rect()
        self.header.setGeometry(rect.x(), 0, rect.width(), self.HEADER_HEIGHT)
        self.vision_panel.setGeometry(rect.adjusted(18, 18, -18, -18))
        self.haptic_panel.setGeometry(rect.adjusted(18, 18, -18, -18))
        self.sidebar.resize(self.sidebar.width(), self.height())
        self.sidebar.raise_()
        self.update()

    def sync_native_panels(self):
        section = self.active_section
        if section != self._section:
            self._regions = []
            if self.context._active_joystick is not None:
                self.releaseMouse()
                self.context._center_joysticks(send=True)
            self._section = section
            self.sidebar.set_current(section)
        self.vision_panel.setVisible(section == "vision")
        self.haptic_panel.setVisible(section == "haptic")
        self.section_title.setText(next(title for key, title, _ in SECTIONS if key == section))
        self.section_description.setText({
            "map": "单兵控制台  /  飞行器位置与链路概览",
            "brain": "单兵控制台  /  实机连接、手动操控与 SSVEP 驾驶",
            "vision": "单兵控制台  /  摄像头画面与手势识别",
            "haptic": "单兵控制台  /  左右手触觉设备与手动反馈",
            "status": "单兵控制台  /  操作员状态与监测信息",
        }[section])
        status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        if section == "haptic":
            connected = sum(s.state == "ready" for s in self.context.haptic.hands.values())
            self.connection_badge.setText(f"触觉设备 {connected} / 2")
        else:
            self.connection_badge.setText("遥测已连接" if status and status.connected else "遥测未连接")
        self._layout_shell()

    def resizeEvent(self, event):
        self._layout_shell()
        super().resizeEvent(event)

    def _canvas_position(self, position):
        rect = self.content_rect()
        return QPointF((position.x() - rect.x()) * 1280 / rect.width(),
                       (position.y() - rect.y()) * 720 / rect.height())

    def paintEvent(self, event):
        vision_message = self.context._toast if self.active_section in {"vision", "haptic"} else ""
        self.vision_toast.setText(vision_message)
        self.vision_toast.setVisible(bool(vision_message))
        if vision_message:
            rect = self.content_rect()
            width = min(640, rect.width() - 48)
            height = max(48, self.vision_toast.heightForWidth(width))
            self.vision_toast.setGeometry(rect.center().x() - width // 2, rect.bottom() - height - 20, width, height)
            self.vision_toast.raise_()
            self.sidebar.raise_()
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(BG))
            rect = self.content_rect()
            painter.translate(rect.x(), rect.y())
            painter.setClipRect(QRect(0, 0, rect.width(), rect.height()))
            canvas = SoldierCanvas(painter, rect.width(), rect.height())
            self._regions = []
            self._render(canvas)
            if self.context._toast and self.active_section not in {"vision", "haptic"}:
                self.context._paint_toast(canvas)
        finally:
            painter.end()

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.MouseMove and self.isVisible()
                and isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched))
                and self.context._active_joystick is None):
            # A global position also covers native controls inside the camera panel.
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            if self.rect().contains(point) and not event.buttons():
                if point.x() <= self.sidebar.COLLAPSED + 12:
                    self.sidebar.hover_near()
                elif self.sidebar.expanded and point.x() < self.sidebar.width():
                    self.sidebar.collapse_timer.stop()
                else:
                    self.sidebar.hover_away()
        return super().eventFilter(watched, event)
