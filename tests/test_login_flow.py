from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from src.app import CommandConsoleWindow
from src.container import create_demo_container


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


if __name__ == "__main__":
    unittest.main()
