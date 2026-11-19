"""Faithful PySide6 translation of the supplied React login screen."""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src import APP_TITLE, __version__
from src.ports import AuthError
from src.domain import Role, User
from src.services.auth_service import AuthService


ROLE_CHOICES = (
    ("士兵 / 作战操作员", Role.SOLDIER),
    ("科研人员", Role.RESEARCHER),
    ("指挥官", Role.COMMANDER),
)

# Explicit local entry points requested for the two day-to-day workspaces.
# Keep this list narrow: every other account still uses the configured auth
# gateway and therefore continues to require its normal password.
PASSWORDLESS_LOCAL_ACCOUNTS = {
    "research": ("科研人员", Role.RESEARCHER),
    "soldier": ("作战操作员", Role.SOLDIER),
}


class AuthTaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class AuthTask(QRunnable):
    """Run a blocking authentication HTTP operation away from the GUI thread."""

    def __init__(self, operation) -> None:
        super().__init__()
        self.operation = operation
        self.signals = AuthTaskSignals()

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.operation())
        except AuthError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:
            self.signals.failed.emit("认证服务暂时不可用，请稍后重试")
        finally:
            self.signals.finished.emit()


class RegisterDialog(QDialog):
    """Registration form for accounts stored by the local backend."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("注册系统账号")
        self.setModal(True)
        self.setMinimumWidth(390)
        form = QFormLayout(self)
        form.setContentsMargins(28, 24, 28, 22)
        form.setSpacing(15)
        self.name_input = QLineEdit()
        self.email_input = QLineEdit()
        self.password_input = QLineEdit()
        self.confirm_input = QLineEdit()
        self.role_input = QComboBox()
        for label, role in ROLE_CHOICES:
            self.role_input.addItem(label, role.value)
        self.email_input.setPlaceholderText("name@example.com")
        self.password_input.setPlaceholderText("8 到 72 位")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.confirm_input.setEchoMode(QLineEdit.Password)
        form.addRow("显示名称", self.name_input)
        form.addRow("邮箱", self.email_input)
        form.addRow("密码", self.password_input)
        form.addRow("确认密码", self.confirm_input)
        form.addRow("演示身份", self.role_input)
        note = QLabel("身份将写入 PostgreSQL。当前允许选择身份仅用于本地课题演示。")
        note.setObjectName("registerNote")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("注册")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class BackdropWidget(QWidget):
    """The three-stop background used by LoginView.tsx."""

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor("#0a1835"))
        gradient.setColorAt(0.5, QColor("#092131"))
        gradient.setColorAt(1.0, QColor("#073024"))
        painter.fillRect(self.rect(), gradient)


class ShieldMark(QWidget):
    """White shield/star mark from the original login SVG."""

    def __init__(self, size: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 64.0, self.height() / 64.0)
        shield = QPainterPath()
        shield.moveTo(32, 3)
        shield.cubicTo(22, 9, 14, 10, 9, 11)
        shield.lineTo(9, 29)
        shield.cubicTo(9, 44, 18, 55, 32, 61)
        shield.cubicTo(46, 55, 55, 44, 55, 29)
        shield.lineTo(55, 11)
        shield.cubicTo(50, 10, 42, 9, 32, 3)
        shield.closeSubpath()
        painter.setPen(QPen(Qt.white, 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(shield)
        points = []
        for i in range(10):
            angle = math.radians(-90 + i * 36)
            radius = 13 if i % 2 == 0 else 5.4
            points.append(QPointF(32 + math.cos(angle) * radius, 30 + math.sin(angle) * radius))
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawPolygon(QPolygonF(points))


class LoginView(BackdropWidget):
    login_succeeded = Signal(object)

    def __init__(self, auth_service: AuthService) -> None:
        super().__init__()
        self.auth_service = auth_service
        self.thread_pool = QThreadPool.globalInstance()
        self._auth_task: AuthTask | None = None
        self.setObjectName("loginRoot")
        self._build_ui()
        if self.auth_service.is_remote:
            self.username_label.setText("账号")
            self.username_input.setPlaceholderText("注册邮箱，或 research / soldier")
        QTimer.singleShot(100, self.username_input.setFocus)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 36, 24, 34)
        root.setSpacing(0)
        root.addStretch(1)

        content = QWidget()
        content.setFixedWidth(440)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        mark_row = QHBoxLayout()
        mark_row.addStretch()
        mark_row.addWidget(ShieldMark(64))
        mark_row.addStretch()
        column.addLayout(mark_row)
        column.addSpacing(13)
        title = QLabel(APP_TITLE)
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignCenter)
        column.addWidget(title)
        column.addSpacing(39)

        card = QFrame()
        card.setObjectName("loginCard")
        card.setFixedSize(440, 360)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 15)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)
        form = QVBoxLayout(card)
        form.setContentsMargins(40, 42, 40, 38)
        form.setSpacing(0)
        self.username_input = self._add_row(form, "用户名", "请输入用户名")
        form.addSpacing(37)
        self.password_input = self._add_row(form, "密码", "请输入密码", password=True)
        form.addSpacing(39)
        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.login_button = QPushButton("登录")
        self.login_button.setObjectName("loginButton")
        self.login_button.setFixedHeight(44)
        self.login_button.setCursor(Qt.PointingHandCursor)
        self.login_button.clicked.connect(self._attempt_login)
        self.register_button = QPushButton("注册")
        self.register_button.setObjectName("registerButton")
        self.register_button.setFixedHeight(44)
        self.register_button.setCursor(Qt.PointingHandCursor)
        self.register_button.clicked.connect(self._open_registration)
        action_row.addWidget(self.login_button, 2)
        action_row.addWidget(self.register_button, 1)
        form.addLayout(action_row)
        form.addSpacing(18)
        self.forgot_button = QPushButton("忘记密码？")
        self.forgot_button.setObjectName("forgotButton")
        self.forgot_button.setCursor(Qt.PointingHandCursor)
        self.forgot_button.clicked.connect(self._send_password_reset)
        form.addWidget(self.forgot_button, 0, Qt.AlignHCenter)
        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setFixedHeight(20)
        form.addWidget(self.error_label)
        column.addWidget(card)
        root.addWidget(content, 0, Qt.AlignHCenter)
        root.addStretch(2)
        version = QLabel(f"V{__version__} 版权所有")
        version.setObjectName("loginVersion")
        version.setAlignment(Qt.AlignCenter)
        version.setFixedHeight(24)
        root.addWidget(version)

        self.username_input.returnPressed.connect(self._submit_username)
        self.password_input.returnPressed.connect(self._attempt_login)
        self.username_input.textChanged.connect(self._clear_error)
        self.password_input.textChanged.connect(self._clear_error)

    def _add_row(
        self, layout: QVBoxLayout, label: str, placeholder: str, password: bool = False
    ) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(16)
        field_label = QLabel(label)
        field_label.setObjectName("loginFieldLabel")
        field_label.setFixedWidth(56)
        field_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if label == "用户名":
            self.username_label = field_label
        field = QLineEdit()
        field.setObjectName("loginInput")
        field.setPlaceholderText(placeholder)
        field.setFixedHeight(42)
        if password:
            field.setEchoMode(QLineEdit.Password)
        row.addWidget(field_label)
        row.addWidget(field, 1)
        layout.addLayout(row)
        return field

    def _attempt_login(self) -> None:
        username = self.username_input.text().strip()
        local_user = self._passwordless_local_user(username)
        if local_user is not None:
            self._clear_error()
            self.login_succeeded.emit(local_user)
            return

        password = self.password_input.text()
        if not username or not password:
            self.error_label.setText("请输入用户名和密码")
            return
        # UI 只调用 AuthService。默认容器连接本机 Spring Boot 后端，
        # BCrypt、JWT 和 PostgreSQL 均由后端处理，不在界面中保存密码。
        self._run_auth(
            lambda: self.auth_service.login(username, password),
            self._login_completed,
            "登录中…",
        )

    def _submit_username(self) -> None:
        """Enter a local workspace immediately or continue to the password."""

        username = self.username_input.text().strip()
        if self._passwordless_local_user(username) is not None:
            self._attempt_login()
            return
        self.password_input.setFocus()

    @staticmethod
    def _passwordless_local_user(username: str) -> User | None:
        account = PASSWORDLESS_LOCAL_ACCOUNTS.get(username.casefold())
        if account is None:
            return None
        display_name, role = account
        normalized_username = username.casefold()
        return User(
            normalized_username,
            display_name,
            role,
            uid=f"local-{normalized_username}",
        )

    def _login_completed(self, user) -> None:
        if user is None:
            self.error_label.setText("用户名或密码错误")
            return
        if not self.auth_service.role_is_configured(user):
            labels = [label for label, _ in ROLE_CHOICES]
            selected, ok = QInputDialog.getItem(
                self,
                "绑定演示身份",
                "该账号尚未绑定身份，请选择：",
                labels,
                0,
                False,
            )
            if not ok:
                self.auth_service.logout()
                self.error_label.setText("需要绑定演示身份后才能进入系统")
                return
            role = dict(ROLE_CHOICES)[selected]
            user = self.auth_service.assign_role(user, role)
        self._clear_error()
        self.login_succeeded.emit(user)

    def _open_registration(self) -> None:
        dialog = RegisterDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        email = dialog.email_input.text().strip()
        password = dialog.password_input.text()
        if password != dialog.confirm_input.text():
            self.error_label.setText("两次输入的密码不一致")
            return
        self._run_auth(
            lambda: self.auth_service.register(
                email,
                password,
                dialog.name_input.text(),
                Role(str(dialog.role_input.currentData())),
            ),
            self._registration_completed,
            "注册中…",
        )

    def _registration_completed(self, user) -> None:
        self._clear_error()
        self.login_succeeded.emit(user)

    def _send_password_reset(self) -> None:
        email = self.username_input.text().strip()
        if "@" not in email:
            self.error_label.setText("请先在邮箱输入框中填写注册邮箱")
            return
        self._run_auth(
            lambda: self.auth_service.send_password_reset(email),
            lambda _: self._password_reset_sent(email),
            "发送中…",
        )

    def _password_reset_sent(self, email: str) -> None:
        self.error_label.setStyleSheet("color: #2ea866;")
        self.error_label.setText(f"重置邮件已发送到 {email}")

    def _run_auth(self, operation, on_success, busy_text: str) -> None:
        self._clear_error()
        self._set_busy(True, busy_text)
        if not self.auth_service.is_remote:
            try:
                on_success(operation())
            except AuthError as exc:
                self._show_auth_error(str(exc))
            finally:
                self._set_busy(False)
            return
        task = AuthTask(operation)
        self._auth_task = task
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(self._show_auth_error)
        task.signals.finished.connect(lambda: self._set_busy(False))
        self.thread_pool.start(task)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.login_button.setEnabled(not busy)
        self.register_button.setEnabled(not busy)
        self.forgot_button.setEnabled(not busy)
        self.login_button.setText(text if busy else "登录")

    def _show_auth_error(self, message: str) -> None:
        self.error_label.setStyleSheet("")
        self.error_label.setText(message)

    def _clear_error(self) -> None:
        self.error_label.setStyleSheet("")
        self.error_label.clear()

    def reset(self) -> None:
        self.password_input.clear()
        self._clear_error()
        self.username_input.setFocus()
