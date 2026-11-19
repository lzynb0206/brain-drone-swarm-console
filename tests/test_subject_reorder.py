from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from src.services.subject_catalog import SubjectCatalog
from src.services.trial_analysis_service import TrialAnalysisRecord
from src.views.main_view import MainView
from src.widgets.scifi_canvas import Canvas


class SubjectReorderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.view = MainView()
        self.view.research_page = '数据分析'
        self.browser = self.view.trial_analysis
        self.browser.catalog = SubjectCatalog(self.root)
        self.ids = []
        for i, name in enumerate(('甲', '乙', '丙', '丁', '戊')):
            assigned = self.browser.catalog.assign_run(f'run-{i}', '2026-11-02', name)
            self.ids.append(assigned.subject_id)
            self.browser.records.append(TrialAnalysisRecord(
                self.root / f'{i}.npz', f'trial-{i}', '起飞', 250., ('O1',), 1, 1250, 5.,
                experiment_id=f'run-{i}', subject_id=assigned.subject_id, subject_name=name,
                acquisition_date='2026-11-02'))
        self.browser.catalog.set_subject_order(self.ids)
        self.browser.select_subject(self.ids[0])
        self.page = self.view.research_widget
        self.view.resize(1280, 720)
        self.view.show()
        self.app.processEvents()
        self.page.resize(1280, 720)
        self.render()

    def tearDown(self):
        self.view.close()
        self.tmp.cleanup()

    def render(self):
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        self.page._regions = []
        try:
            self.page._paint_research(Canvas(painter, 1280, 720))
        finally:
            painter.end()

    def card_point(self, subject_id, after=False):
        rect = next(r for r, a in self.page._regions if a == f'analysis-subject:{subject_id}')
        return QPointF(rect.x() + (rect.width() - 10 if after else 10), rect.center().y())

    def mouse(self, kind, point, button=Qt.LeftButton, buttons=Qt.LeftButton):
        return QMouseEvent(kind, point, point, button, buttons, Qt.NoModifier)

    def test_order_persists_without_changing_selection_report_or_ownership(self):
        ownership = self.browser.catalog.runs.copy()
        selected = self.browser.selected_record_index
        report = object()
        self.browser.last_report = report
        self.browser.move_subject(self.ids[2], self.ids[0])
        self.assertEqual([self.ids[2], self.ids[0], self.ids[1], *self.ids[3:]], [s.subject_id for s in self.browser.subjects])
        self.assertEqual(self.ids[0], self.browser.selected_subject_id)
        self.assertEqual(selected, self.browser.selected_record_index)
        self.assertIs(report, self.browser.last_report)
        reloaded = SubjectCatalog(self.root)
        self.assertEqual(tuple(s.subject_id for s in self.browser.subjects), reloaded.subject_order)
        self.assertEqual(ownership, reloaded.runs)
        reloaded.rename_subject(self.ids[2], '新的名字')
        self.assertEqual(self.ids[2], SubjectCatalog(self.root).subject_order[0])

    def test_save_failure_keeps_previous_order(self):
        with patch.object(self.browser.catalog, 'set_subject_order', side_effect=OSError('read only')):
            self.browser.move_subject(self.ids[2], self.ids[0])
        self.assertEqual(self.ids, [s.subject_id for s in self.browser.subjects])

    def test_click_selects_on_release_but_drag_reorders_without_selecting(self):
        point = self.card_point(self.ids[1])
        self.page.mousePressEvent(self.mouse(QMouseEvent.MouseButtonPress, point))
        self.assertEqual(self.ids[0], self.browser.selected_subject_id)
        self.page.mouseReleaseEvent(self.mouse(QMouseEvent.MouseButtonRelease, point, buttons=Qt.NoButton))
        self.assertEqual(self.ids[1], self.browser.selected_subject_id)
        self.render()
        source = self.card_point(self.ids[2])
        target = self.card_point(self.ids[0])
        mime = QMimeData()
        mime.setData(self.page.SUBJECT_MIME, self.ids[2].encode())
        drop = SimpleNamespace(position=lambda: target, source=lambda: self.page, mimeData=lambda: mime,
                               setDropAction=MagicMock(), accept=MagicMock(), ignore=MagicMock())
        with patch('src.views.role_pages.QDrag') as drag:
            drag.return_value.exec.side_effect = lambda _: self.page.dropEvent(drop)
            self.page.mousePressEvent(self.mouse(QMouseEvent.MouseButtonPress, source))
            self.page.mouseMoveEvent(self.mouse(QMouseEvent.MouseMove, source + QPointF(30, 0), button=Qt.NoButton))
        drop.accept.assert_called_once()
        self.assertEqual(self.ids[2], self.browser.subjects[0].subject_id)
        self.assertEqual(self.ids[1], self.browser.selected_subject_id)

    def test_scrolled_scaled_hit_testing_and_cross_page_drop(self):
        self.page._research_scroll_offset = 120
        self.render()
        self.page.resize(1600, 900)
        point = self.card_point(self.ids[1])
        screen = QPointF(point.x() * 1.25, point.y() * 1.25)
        self.assertEqual(self.ids[1], self.page._subject_at(screen)[0])
        rect = next(r for r, a in self.page._regions if a == 'analysis-subject-page-next')
        target = self.page._subject_drop_at(QPointF(rect.center().x() * 1.25, rect.center().y() * 1.25))
        self.assertEqual((self.ids[4], True), target)
        self.browser.move_subject(self.ids[0], *target)
        self.assertEqual(1, self.browser.subject_page)
        self.assertEqual(self.ids[0], self.browser.visible_subjects[-1].subject_id)


if __name__ == '__main__':
    unittest.main()
