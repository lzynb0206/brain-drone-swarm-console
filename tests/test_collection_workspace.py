"""Selection, preview and runner must agree on the acquisition being launched."""

from dataclasses import replace
from collections import Counter
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QSpinBox

from src.bci.montage import HYBRID, LEGACY
from src.services.collection_plans import collection_schemes, hybrid_collection_config
from src.services.hybrid_experiment_protocol import HybridExperimentConfig, build_hybrid_plan
from src.services.experiment_recorder import ExperimentRecorder
from src.services.subject_catalog import SubjectCatalog
from src.views.main_view import MainView
from src.widgets.scifi_canvas import Canvas


class CollectionPlanTest(unittest.TestCase):
    def test_eighty_pairs_are_balanced_including_the_partial_block(self):
        from src.bci.config import COMMANDS
        for count in (1, 2, 7, 8, 12, 13, 79, 80, 81, 84, 600):
            for seed in (1, 7, 20261101):
                with self.subTest(count=count, seed=seed):
                    config=hybrid_collection_config(HybridExperimentConfig(serial_pair_count=count, random_seed=seed),
                                                    "SSVEP+MI混合", "formal")
                    plan=build_hybrid_plan(config)
                    self.assertEqual(count,len(plan))
                    self.assertEqual(list(range(1,count+1)),[t.index for t in plan])
                    self.assertEqual(plan,build_hybrid_plan(config))
                    combos=Counter((t.ssvep_label,t.mi_label) for t in plan)
                    targets=Counter(t.ssvep_label for t in plan)
                    hands=Counter(t.mi_label for t in plan)
                    for counts in ([combos[s,m] for s in COMMANDS for m in config.mi_labels],
                                   [targets[s] for s in COMMANDS], [hands[m] for m in config.mi_labels]):
                        self.assertLessEqual(max(counts)-min(counts),1)
                    if count==80:
                        self.assertEqual([40,40],sorted(hands.values()))
                        self.assertEqual([13,13,13,13,14,14],sorted(targets.values()))
                        self.assertEqual({6,7},set(combos.values()))
                    if count==84:
                        self.assertEqual([42,42],sorted(hands.values()))
                        self.assertEqual({14},set(targets.values()))
                        self.assertEqual({7},set(combos.values()))

    def test_preview_overrides_preserve_formal_config_and_match_protocol(self):
        base = HybridExperimentConfig(repetitions=9, mi_seconds=4, baseline_seconds=6, include_dedicated_rest=True)
        for paradigm in ("MI", "SSVEP+MI混合"):
            for scheme in collection_schemes(paradigm):
                with self.subTest(paradigm=paradigm, scheme=scheme.id):
                    config = hybrid_collection_config(base, paradigm, scheme.id)
                    plan = build_hybrid_plan(config)
                    self.assertEqual(config.pair_count, sum(t.mi_label is not None for t in plan))
                    if scheme.id == "pilot":
                        self.assertEqual(2, config.pair_count)
                        self.assertFalse(config.include_dedicated_rest)
                        self.assertEqual("pilot", config.purpose)
                    if scheme.id in ("rest_open", "rest_closed"):
                        self.assertEqual((), plan)
                        self.assertTrue(config.include_dedicated_rest)
                        self.assertEqual(scheme.id.removeprefix("rest_"), config.rest_eye_state)
                    if scheme.id == "formal":
                        self.assertFalse(config.include_dedicated_rest)
                        self.assertTrue(all(t.mi_label is not None for t in plan))
                    if paradigm == "MI":
                        self.assertTrue(all(t.ssvep_label is None for t in plan))
        self.assertEqual(9, base.repetitions)
        self.assertTrue(base.include_dedicated_rest)
        self.assertEqual("formal", base.purpose)


class CollectionWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = MainView()
        self.view.actions.recording_mode = "local_eeg"
        self.view.research_page = "数据采集"

    def tearDown(self):
        self.view.experiment.automatic_window = None
        self.view._pipeline_worker = None
        self.view.close()

    def render(self):
        page = self.view.research_widget
        page._regions = []
        image = QImage(1280, 720, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            canvas = Canvas(painter, 1280, 720)
            with patch.object(canvas, "text", wraps=canvas.text) as text:
                page._research_data_collection(canvas)
            labels = [call.args[4] for call in text.call_args_list]
        finally:
            painter.end()
        return {action for _, action in page._regions}, labels

    def test_serial_formal_defaults_to_eighty_four_and_can_edit_the_total(self):
        self.view._activate("paradigm-type:SSVEP+MI混合")
        _,labels=self.render()
        self.assertIn("84 组",labels)
        self.assertNotIn("每组合次数",labels)
        def edit_count(dialog):
            labels={label.text() for label in dialog.findChildren(QLabel)}
            self.assertIn("正式采集总组数",labels)
            fields=dialog.findChildren(QSpinBox)
            self.assertEqual(1,len(fields))
            self.assertEqual(84,fields[0].value())
            fields[0].setValue(82)
            return QDialog.Accepted
        with patch.object(QDialog,"exec",edit_count):
            self.view._activate("collection-config")
        config=hybrid_collection_config(self.view.actions.hybrid_experiment_config,"SSVEP+MI混合")
        self.assertEqual(82,len(build_hybrid_plan(config)))
        self.view._activate("collection-scheme:pilot")
        _,labels=self.render()
        self.assertIn("2 组",labels)

    def test_switching_on_collection_page_retains_parameters_and_scheme_per_paradigm(self):
        base = replace(self.view.actions.hybrid_experiment_config, repetitions=11)
        self.view.actions.hybrid_experiment_config = base
        self.view._activate("paradigm-type:MI")
        self.view._activate("collection-scheme:pilot")
        actions, labels = self.render()
        self.assertIn("2 组", labels)
        self.assertNotIn("SSVEP 时间", labels)
        self.assertIn("collection-start", actions)
        self.view._activate("paradigm-type:SSVEP+MI混合")
        self.assertEqual("formal", self.view.actions.collection_scheme)
        self.view._activate("collection-scheme:rest_closed")
        self.view._activate("paradigm-type:MI")
        self.assertEqual("pilot", self.view.actions.collection_scheme)
        self.assertEqual(base, self.view.actions.hybrid_experiment_config)
        self.assertEqual("数据采集", self.view.research_page)
        self.view._activate("paradigm-type:SSVEP")
        self.view._activate("collection-scheme:rest_open")
        self.assertEqual("rest_open", self.view.actions.collection_scheme)
        self.view._activate("paradigm-type:SSVEP+MI混合")
        self.assertEqual("rest_closed", self.view.actions.collection_scheme)

    def test_all_schemes_share_controls_without_overlapping_click_regions(self):
        expected_switches = {"paradigm-type:" + p for p in ("SSVEP", "MI", "SSVEP+MI混合")}
        for paradigm in ("SSVEP", "MI", "SSVEP+MI混合"):
            self.view._activate("paradigm-type:" + paradigm)
            for scheme in collection_schemes(paradigm):
                with self.subTest(paradigm=paradigm, scheme=scheme.id):
                    self.view._activate("collection-scheme:" + scheme.id)
                    actions, labels = self.render()
                    self.assertTrue(expected_switches <= actions)
                    self.assertTrue({"collection-config", "collection-start"} <= actions)
                    self.assertNotIn("hybrid-preflight", actions)
                    self.assertIn("实验数据采集", labels)
                    self.assertFalse(any("待完成硬件" in label or "待完成当前范式" in label for label in labels))
                    regions = self.view.research_widget._regions
                    for i, (rect, action) in enumerate(regions):
                        self.assertGreaterEqual(rect.top(), 56)
                        self.assertLessEqual(rect.bottom(), 720)
                        self.assertLessEqual(rect.right(), 1280)
                        for other, other_action in regions[i + 1:]:
                            self.assertFalse(rect.intersects(other), (action, other_action))

    def test_running_experiment_locks_scheme_paradigm_layout_and_configuration(self):
        self.view._activate("paradigm-type:MI")
        self.view.experiment.automatic_window = SimpleNamespace(running=True)
        actions, _ = self.render()
        self.assertFalse(any(a.startswith(("paradigm-type:", "collection-scheme:", "montage:")) for a in actions))
        self.assertNotIn("collection-config", actions)
        self.view._activate("paradigm-type:SSVEP")
        self.view._activate("collection-scheme:rest_open")
        self.view._activate("montage:" + HYBRID.id)
        with patch.object(self.view.experiment, "configure_hybrid") as configure:
            self.view._activate("collection-config")
        configure.assert_not_called()
        self.assertEqual("MI", self.view.actions.paradigm_type)
        self.assertEqual("formal", self.view.actions.collection_scheme)
        self.assertEqual(LEGACY.id, self.view.actions.montage_id)

    def test_applying_hybrid_layout_preserves_mi_selection(self):
        self.view._activate("paradigm-type:MI")
        self.view.acquisition.stream = SimpleNamespace(configure_montage=MagicMock())
        self.view._activate("montage:" + HYBRID.id)
        self.assertEqual("MI", self.view.actions.paradigm_type)
        self.assertEqual(HYBRID.names, self.view._eeg_channel_names)

    def test_all_schemes_start_directly_and_use_preview_config(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.view.experiment_recorder = ExperimentRecorder(directory.name)
        catalog = SubjectCatalog(directory.name)
        self.view.experiment.select_subject(catalog.create_subject("受试者1号"))
        worker = MagicMock()
        worker.source_name = "LSL · fixture"
        worker.isRunning.return_value = True
        self.view._pipeline_worker = worker
        self.view._eeg_channel_names = HYBRID.names
        self.view._eeg_sample_rate = 250
        self.view.actions.montage_id = HYBRID.id
        self.view.acquisition.last_frame_monotonic = time.monotonic()
        with patch("src.controllers.experiment_controller.HybridExperimentWindow") as window, \
             patch("src.controllers.experiment_controller.QTimer.singleShot"):
            for paradigm in ("MI", "SSVEP+MI混合"):
                for scheme in collection_schemes(paradigm):
                    self.view.experiment.automatic_window = None
                    self.view._activate("paradigm-type:" + paradigm)
                    self.view._activate("collection-scheme:" + scheme.id)
                    expected = hybrid_collection_config(self.view.actions.hybrid_experiment_config, paradigm, scheme.id)
                    window.reset_mock()
                    window.return_value.run_id = f"hybrid-20261102-120000-{paradigm}-{scheme.id}"
                    self.view._activate("collection-start")
                    window.assert_called_once()
                    self.assertEqual(expected, window.call_args.args[0])
                    self.assertFalse(self.view.actions.bci_control_enabled)
                    self.assertEqual(catalog.subjects.keys(), {self.view.experiment.selected_subject_id})
        self.view.experiment.automatic_window = None
        self.view._activate("paradigm-type:SSVEP")
        with patch.object(self.view.experiment, "start_automatic") as automatic:
            self.view._activate("collection-start")
        automatic.assert_called_once_with()

    def test_configuration_only_shows_fields_for_selected_scheme(self):
        def inspect_dialog(dialog):
            self.dialog_labels = {label.text() for label in dialog.findChildren(QLabel)}
            return QDialog.Rejected

        with patch.object(QDialog, "exec", inspect_dialog):
            self.view._activate("paradigm-type:MI")
            self.view._activate("collection-config")
            self.assertIn("每种 MI 动作次数", self.dialog_labels)
            self.assertIn("MI 动作提示 / 秒", self.dialog_labels)
            self.assertIn("MI 想象采集 / 秒", self.dialog_labels)
            self.assertIn("开始前休息 / 秒", self.dialog_labels)
            self.assertNotIn("专门睁眼静息 / 秒", self.dialog_labels)
            self.assertNotIn("专门闭眼静息 / 秒", self.dialog_labels)
            self.assertNotIn("SSVEP 目标提示 / 秒", self.dialog_labels)
            self.assertNotIn("SSVEP / 秒", self.dialog_labels)
            self.view._activate("collection-scheme:pilot")
            self.view._activate("collection-config")
            self.assertNotIn("每种 MI 动作次数", self.dialog_labels)
            self.assertNotIn("专门睁眼静息 / 秒", self.dialog_labels)
            self.view._activate("collection-scheme:rest_open")
            self.view._activate("collection-config")
            self.assertIn("专门睁眼静息 / 秒", self.dialog_labels)
            self.assertNotIn("专门闭眼静息 / 秒", self.dialog_labels)
            self.assertNotIn("MI 想象采集 / 秒", self.dialog_labels)
            self.view._activate("collection-scheme:rest_closed")
            self.view._activate("collection-config")
            self.assertIn("专门闭眼静息 / 秒", self.dialog_labels)
            self.assertNotIn("专门睁眼静息 / 秒", self.dialog_labels)
        with patch.object(self.view, "_configure_automatic_ssvep_experiment") as configure:
            self.view._activate("paradigm-type:SSVEP")
            self.view._activate("collection-config")
        configure.assert_called_once_with()

    def test_editing_one_rest_duration_preserves_the_other_state_and_task_settings(self):
        before=self.view.actions.hybrid_experiment_config
        self.view._activate("paradigm-type:SSVEP+MI混合")
        self.view._activate("collection-scheme:rest_closed")
        def edit_duration(dialog):
            fields=dialog.findChildren(QSpinBox)
            self.assertEqual(1,len(fields))
            fields[0].setValue(180)
            return QDialog.Accepted
        with patch.object(QDialog,"exec",edit_duration):
            self.view._activate("collection-config")
        self.assertEqual(replace(before,resting_closed_seconds=180),self.view.actions.hybrid_experiment_config)


if __name__ == "__main__":
    unittest.main()
