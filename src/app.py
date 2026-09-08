"""Top-level window and page routing."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from src.container import ApplicationContainer, create_default_container
from src.domain import User
from src.views.login_view import LoginView
from src.views.main_view import MainView


class CommandConsoleWindow(QMainWindow):
    """Routes between the translated login and role-specific dashboards."""

    def __init__(self, container: ApplicationContainer | None = None) -> None:
        super().__init__()
        self.container = container or create_default_container()
        self.setWindowTitle("脑控无人机蜂群作战系统 v1.0")
        self.resize(1280, 720)
        self.setMinimumSize(1024, 576)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.setCentralWidget(self.pages)

        self.login_view = LoginView(self.container.auth_service)
        # 所有外部能力从容器注入主界面；页面里不硬编码设备连接。
        self.main_view = MainView(
            eeg_stream=self.container.eeg_stream,
            intent_decoder=self.container.intent_decoder,
            experiment_recorder=self.container.experiment_recorder,
            swarm_gateway=self.container.swarm_gateway,
            system_monitor=self.container.system_monitor,
            engineer_data_provider=self.container.engineer_data_provider,
        )
        self.pages.addWidget(self.login_view)
        self.pages.addWidget(self.main_view)

        self.login_view.login_succeeded.connect(self._show_main_view)
        self.main_view.logout_requested.connect(self._show_login_view)

    def _show_main_view(self, user: User) -> None:
        self.main_view.set_user(user)
        self.pages.setCurrentWidget(self.main_view)
        self.setWindowTitle("脑控无人机蜂群作战系统 v1.0")

    def _show_login_view(self) -> None:
        self.main_view.deactivate()
        self.container.auth_service.logout()
        self.login_view.reset()
        self.pages.setCurrentWidget(self.login_view)
        self.setWindowTitle("脑控无人机蜂群作战系统 v1.0")

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape and self.pages.currentWidget() is self.main_view:
            self._show_login_view()
            return
        super().keyPressEvent(event)
