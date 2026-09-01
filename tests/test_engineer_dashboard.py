from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.adapters.mock import MockEngineerDataProvider, MockSystemMonitor
from src.views.engineer_dashboard import EngineerDashboard


class EngineerDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.dashboard = EngineerDashboard(MockSystemMonitor(), MockEngineerDataProvider())
        self.dashboard.resize(1280, 720)
        self.dashboard.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.dashboard.close()

    def test_left_module_navigation_changes_real_stacked_page(self) -> None:
        self.assertEqual(7, self.dashboard.module_list.count())
        self.dashboard.module_list.setCurrentRow(0)
        self.app.processEvents()
        self.assertEqual("蜂群作战管理", self.dashboard.center_title.text())
        self.assertEqual(0, self.dashboard.monitor_stack.currentIndex())
        self.assertGreater(self.dashboard.module_tables["swarm"].rowCount(), 0)

    def test_log_tabs_and_diagnostics_buttons_are_interactive(self) -> None:
        self.dashboard.log_tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertIn("WARN", self.dashboard.log_views["WARN"].toPlainText())

        QTest.mouseClick(self.dashboard.diagnostics_button, Qt.LeftButton)
        self.assertEqual(1, self.dashboard.mode_stack.currentIndex())
        QTest.mouseClick(self.dashboard.self_test_button, Qt.LeftButton)
        self.assertIn("自检完成", self.dashboard.self_test_result.text())


if __name__ == "__main__":
    unittest.main()
