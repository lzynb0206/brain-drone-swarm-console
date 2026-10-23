from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.app import CommandConsoleWindow
from src.container import create_demo_container
from src.domain import Role


class LoginFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = CommandConsoleWindow(create_demo_container())

    def tearDown(self) -> None:
        self.window.close()

    def test_role_routing_matches_reference_prototype(self) -> None:
        login = self.window.login_view
        login.username_input.setText("researcher")
        login.password_input.setText("wrong")
        login._attempt_login()

        self.assertIs(self.window.pages.currentWidget(), self.window.main_view)
        self.assertEqual("科研人员", self.window.main_view.user.role_label)

        self.window.main_view.logout_requested.emit()
        self.assertIs(self.window.pages.currentWidget(), login)

    def test_empty_fields_are_rejected(self) -> None:
        login = self.window.login_view
        login.username_input.clear()
        login.password_input.clear()
        login._attempt_login()
        self.assertIn("请输入", login.error_label.text())
        self.assertIs(self.window.pages.currentWidget(), login)

    def test_research_shortcut_enters_research_workspace_without_password(self) -> None:
        login = self.window.login_view
        login.username_input.setText("research")
        login.password_input.clear()

        with patch.object(login.auth_service, "login") as backend_login:
            login._attempt_login()

        backend_login.assert_not_called()
        self.assertIs(self.window.pages.currentWidget(), self.window.main_view)
        self.assertEqual(Role.RESEARCHER, self.window.main_view.user.role)
        self.assertIs(
            self.window.main_view._role_stack.currentWidget(),
            self.window.main_view.research_widget,
        )

    def test_soldier_shortcut_enters_soldier_workspace_without_password(self) -> None:
        login = self.window.login_view
        login.username_input.setText("soldier")
        login.password_input.clear()

        with patch.object(login.auth_service, "login") as backend_login:
            login._submit_username()

        backend_login.assert_not_called()
        self.assertIs(self.window.pages.currentWidget(), self.window.main_view)
        self.assertEqual(Role.SOLDIER, self.window.main_view.user.role)
        self.assertIs(
            self.window.main_view._role_stack.currentWidget(),
            self.window.main_view.soldier_widget,
        )


if __name__ == "__main__":
    unittest.main()
