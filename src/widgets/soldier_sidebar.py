"""Collapsible monochrome navigation for the operator workspace."""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QSizePolicy, QToolButton, QVBoxLayout

SECTIONS = (
    ("map", "作战地图", "soldier-root:作战地图"),
    ("brain", "脑控驾驶", "soldier-page:脑控"),
    ("vision", "视觉手势", "soldier-page:视觉手势"),
    ("status", "状态监控", "soldier-page:状态监控"),
)


def navigation_icon(name, color="#b5b5b5"):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == "map":
        path = QPainterPath(QPointF(3, 5))
        for x, y in ((9, 3), (15, 6), (21, 4), (21, 19), (15, 21), (9, 18), (3, 20), (3, 5)):
            path.lineTo(x, y)
        painter.drawPath(path)
        painter.drawLine(9, 3, 9, 18)
        painter.drawLine(15, 6, 15, 21)
    elif name == "brain":
        painter.drawRoundedRect(3, 6, 18, 13, 4, 4)
        painter.drawLine(6, 12, 12, 12)
        painter.drawLine(9, 9, 9, 15)
        painter.drawEllipse(16, 10, 2, 2)
        painter.drawEllipse(18, 14, 1, 1)
    elif name == "vision":
        painter.drawRoundedRect(2, 6, 15, 13, 3, 3)
        path = QPainterPath(QPointF(17, 10))
        for x, y in ((22, 7), (22, 18), (17, 15)):
            path.lineTo(x, y)
        painter.drawPath(path)
    elif name == "status":
        path = QPainterPath(QPointF(2, 12))
        for x, y in ((6, 12), (9, 4), (14, 20), (17, 12), (22, 12)):
            path.lineTo(x, y)
        painter.drawPath(path)
    elif name == "logout":
        painter.drawLine(11, 4, 4, 4)
        painter.drawLine(4, 4, 4, 20)
        painter.drawLine(4, 20, 11, 20)
        painter.drawLine(10, 12, 21, 12)
        painter.drawLine(17, 8, 21, 12)
        painter.drawLine(17, 16, 21, 12)
    elif name == "pin":
        painter.drawRect(8, 3, 8, 8)
        painter.drawLine(6, 14, 18, 14)
        painter.drawLine(8, 11, 6, 14)
        painter.drawLine(16, 11, 18, 14)
        painter.drawLine(12, 14, 12, 22)
    else:
        for y in (6, 12, 18):
            painter.drawLine(4, y, 20, y)
    painter.end()
    return QIcon(pixmap)


class SoldierSidebar(QFrame):
    action_requested = Signal(str)
    pinned_changed = Signal()
    COLLAPSED = 64
    EXPANDED = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("soldierSidebar")
        self.setMouseTracking(True)
        self.pinned = False
        self.expanded = False
        self._suppress_hover = False
        self.setStyleSheet("""
            QFrame#soldierSidebar { background: #171717; border: 0; border-right: 1px solid #303030; }
            QFrame#soldierSidebar QLabel { color: #777777; font-size: 10px; letter-spacing: 1px; }
            QFrame#soldierSidebar QToolButton { color: #b5b5b5; background: transparent;
                border: 0; border-radius: 7px; text-align: left; padding: 10px; font-size: 13px; }
            QFrame#soldierSidebar QToolButton:hover { color: #ffffff; background: #292929; }
            QFrame#soldierSidebar QToolButton:checked { color: #171717; background: #f1f1f1; font-weight: 600; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(8)
        self.toggle_button = self._button("menu", "展开导航")
        self.toggle_button.clicked.connect(self.toggle)
        layout.addWidget(self.toggle_button)
        self.caption = QLabel("工作空间")
        self.caption.setFixedHeight(32)
        self.caption.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(self.caption)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for key, title, action in SECTIONS:
            button = self._button(key, title)
            button.setCheckable(True)
            self.group.addButton(button)
            self.buttons[key] = button
            button.clicked.connect(lambda checked=False, command=action: self.action_requested.emit(command))
            layout.addWidget(button)
        layout.addStretch()
        self.pin_button = self._button("pin", "固定展开")
        self.pin_button.clicked.connect(self.toggle_pin)
        layout.addWidget(self.pin_button)
        self.logout_button = self._button("logout", "退出登录")
        self.logout_button.clicked.connect(lambda: self.action_requested.emit("logout"))
        layout.addWidget(self.logout_button)
        self.animation = QPropertyAnimation(self, b"panel_width", self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.collapse_timer = QTimer(self)
        self.collapse_timer.setSingleShot(True)
        self.collapse_timer.setInterval(420)
        self.collapse_timer.timeout.connect(self._auto_collapse)
        self.set_panel_width(self.COLLAPSED)
        self.set_current("map")

    def _button(self, icon, title):
        button = QToolButton(self)
        button.setFixedHeight(44)
        button.setMinimumWidth(44)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setIcon(navigation_icon(icon))
        button.setIconSize(QSize(24, 24))
        button.setToolTip(title)
        button.setAccessibleName(title)
        button.setCursor(Qt.PointingHandCursor)
        button.setMouseTracking(True)
        return button

    def get_panel_width(self):
        return self.width()

    def set_panel_width(self, width):
        self.setFixedWidth(width)
        self.resize(width, self.parentWidget().height())
        full = width >= 180
        self.caption.setText("工作空间" if full else "")
        labels = {self.toggle_button: "收起导航", self.pin_button: "取消固定" if self.pinned else "固定展开",
                  self.logout_button: "退出登录"}
        labels.update({self.buttons[key]: title for key, title, _ in SECTIONS})
        for button, title in labels.items():
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if full else Qt.ToolButtonIconOnly)
            button.setText(title if full else "")

    panel_width = Property(int, get_panel_width, set_panel_width)

    def reveal(self, expanded):
        self.collapse_timer.stop()
        self.expanded = expanded
        self.toggle_button.setToolTip("收起导航" if expanded else "展开导航")
        self.animation.stop()
        self.animation.setStartValue(self.width())
        self.animation.setEndValue(self.EXPANDED if expanded else self.COLLAPSED)
        self.animation.start()
        self.raise_()

    def hover_near(self):
        self.collapse_timer.stop()
        if not self.expanded and not self._suppress_hover:
            self.reveal(True)

    def hover_away(self):
        self._suppress_hover = False
        if self.expanded and not self.pinned and not self.collapse_timer.isActive():
            self.collapse_timer.start()

    def _auto_collapse(self):
        if not self.pinned:
            self.reveal(False)

    def toggle(self):
        if self.expanded:
            self.pinned = False
            self._suppress_hover = True
            self.reveal(False)
        else:
            self.pinned = True
            self.reveal(True)
        self.pinned_changed.emit()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.reveal(True)
        self.set_panel_width(self.width())
        self.pinned_changed.emit()

    def set_current(self, section):
        for key, button in self.buttons.items():
            button.setChecked(key == section)
            button.setIcon(navigation_icon(key, "#171717" if key == section else "#b5b5b5"))

    def enterEvent(self, event):
        self.hover_near()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_away()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.collapse_timer.stop()
        self.animation.stop()
        self.expanded = self.pinned
        self.set_panel_width(self.EXPANDED if self.pinned else self.COLLAPSED)
        super().hideEvent(event)
