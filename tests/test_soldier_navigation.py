"""Navigation and viewport behavior; these tests never connect to hardware."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.adapters.mock import MockSwarmGateway
from src.domain import Role, User
from src.views.main_view import MainView


class SoldierNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.gateway = MockSwarmGateway()
        self.view = MainView(swarm_gateway=self.gateway)
        self.view.resize(1280, 720)
        self.view.set_user(User("operator", "测试", Role.SOLDIER))
        self.view.show()
        self.page = self.view.soldier_widget
        self.sidebar = self.page.sidebar
        self.app.processEvents()
        QTest.mouseMove(self.page, QPoint(950, 500))
        self.sidebar.animation.stop()
        self.sidebar.expanded = False
        self.sidebar.set_panel_width(64)

    def tearDown(self):
        self.view.close()

    def test_clicking_each_page_has_exactly_one_matching_highlight(self):
        before = len(self.gateway.command_log)
        for key in ("vision", "map", "status", "brain", "vision", "vision"):
            QTest.mouseClick(self.sidebar.buttons[key], Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(self.page.active_section, key)
            selected = [name for name, button in self.sidebar.buttons.items() if button.isChecked()]
            self.assertEqual(selected, [key])
            self.assertEqual(self.page.vision_panel.isVisible(), key == "vision")
        self.assertEqual(len(self.gateway.command_log), before)
        self.assertFalse(any(action.startswith("soldier-") for _, action in self.page._regions))

    def test_edge_hover_expands_and_leaving_collapses_without_shifting_content(self):
        rect = self.page.content_rect()
        QTest.mouseMove(self.page, QPoint(70, 400))
        QTest.qWait(220)
        self.assertEqual(self.sidebar.width(), 220)
        self.assertEqual(self.page.content_rect(), rect)
        QTest.mouseMove(self.page, QPoint(950, 500))
        QTest.qWait(650)
        self.assertEqual(self.sidebar.width(), 64)
        self.assertEqual(self.page.content_rect(), rect)

    def test_manual_pin_and_collapse_survive_pointer_motion(self):
        self.sidebar.toggle_pin()
        QTest.mouseMove(self.page, QPoint(950, 500))
        QTest.qWait(650)
        self.assertTrue(self.sidebar.pinned)
        self.assertEqual(self.sidebar.width(), 220)
        self.assertEqual(self.page.content_rect().x(), 220)
        QTest.mouseClick(self.sidebar.toggle_button, Qt.LeftButton)
        QTest.qWait(220)
        self.assertFalse(self.sidebar.pinned)
        self.assertEqual(self.sidebar.width(), 64)
        self.assertEqual(self.page.content_rect().x(), 64)

    def test_canvas_clicks_use_content_coordinates_in_both_sidebar_modes(self):
        self.gateway.disarm()
        self.view._activate("soldier-page:脑控")
        for pinned in (False, True):
            if pinned:
                self.sidebar.toggle_pin()
            self.app.processEvents()
            self.page.repaint()
            region = next(rect for rect, action in self.page._regions if action == "swarm-height-increase")
            content = self.page.content_rect()
            point = QPoint(round(content.x() + region.center().x() * content.width() / 1280),
                           round(content.y() + region.center().y() * content.height() / 720))
            before = self.view.actions.swarm_takeoff_height_m()
            QTest.mouseClick(self.page, Qt.LeftButton, pos=point)
            self.assertAlmostEqual(self.view.actions.swarm_takeoff_height_m(), before + 0.05)

    def test_dragged_joystick_blocks_hover_and_page_switch_recenters(self):
        self.view._activate("soldier-page:脑控")
        self.view._active_joystick = "manual-stick-direction"
        QTest.mouseMove(self.page, QPoint(70, 400))
        QTest.qWait(220)
        self.assertEqual(self.sidebar.width(), 64)
        with patch.object(self.view, "_center_joysticks", wraps=self.view._center_joysticks) as center:
            self.view._activate("soldier-page:视觉手势")
            center.assert_called_once_with(send=True)
        self.assertIsNone(self.view._active_joystick)

    def test_sidebar_logout_uses_existing_deactivation(self):
        with patch.object(self.view, "deactivate") as deactivate:
            QTest.mouseClick(self.sidebar.logout_button, Qt.LeftButton)
            deactivate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
