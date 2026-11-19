"""Automatic capture keeps participant ownership through saving and analysis."""

import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from src.bci.montage import HYBRID
from src.services.acquisition_identity import acquisition_directory
from src.services.experiment_recorder import ExperimentRecorder
from src.services.experiment_timing import ExperimentClock
from src.services.subject_catalog import SubjectCatalog
from src.services.trial_analysis_service import TrialAnalysisService
from src.views.main_view import MainView
from src.workers import EEGWorker
from tests.test_experiment_timing import _result


class AcquisitionIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.recorder = ExperimentRecorder(self.root)
        self.catalog = SubjectCatalog(self.root)
        self.subject_id = self.catalog.create_subject("受试者1号")
        self.view = MainView(experiment_recorder=self.recorder)
        self.view.actions.recording_mode = "local_eeg"
        self.view.trial_analysis.service = TrialAnalysisService(self.root / "reports")
        self.controller = self.view.experiment

    def tearDown(self):
        self.controller.automatic_window = None
        self.view._pipeline_worker = None
        self.view.close()
        self.directory.cleanup()

    def bind(self, run="hybrid-20261102-120000-fixture", paradigm="SSVEP+MI混合", scheme="formal"):
        self.controller.select_subject(self.subject_id)
        window = SimpleNamespace(run_id=run, running=True, notify_trial_save_error=MagicMock())
        self.assertTrue(self.controller._bind_run(window, paradigm, scheme))
        self.controller.automatic_window = window
        return dict(self.controller._run_context)

    def record(self, context, stage="ssvep", onset=100., seconds=3., trial_number=1, serial=True):
        run = context["run_id"]
        pair = run + f"-pair-{trial_number:04d}"
        count = round(seconds * 250)
        metadata = {**context, "trial_id": pair + "-" + stage, "stage": stage,
                    "paradigm_type": stage.upper(), "pair_id": pair, "montage_id": HYBRID.id,
                    "record_kind": "serial_pair" if serial else "ssvep_idle", "ssvep_true_label": "起飞",
                    "mi_true_label": "想象左手" if serial else "",
                    "preceding_ssvep_trial_id": pair + "-ssvep" if stage == "mi" else ""}
        t = np.arange(count) / 250
        eeg = np.stack([12 * np.sin(2 * np.pi * (8 if i < 5 else 10) * t + .2 * i)
                        + np.random.default_rng(i).normal(0, 2, count) for i in range(8)])
        saved = self.recorder.record(_result(count), eeg,
            true_label="起飞" if stage == "ssvep" else "想象左手", source="synthetic-test-only",
            subject_id="login-operator", session_id="login-session", channel_names=HYBRID.names,
            sample_timestamps=onset + t, paradigm_type=stage.upper(), trial_metadata=metadata)
        return self.root / saved["raw_trial_path"]

    def test_bound_pair_auto_archives_and_appears_without_prior_analysis_load(self):
        other = self.catalog.create_subject("受试者0号")
        other_context = self.catalog.bind_acquisition(other, "auto-20261102-110000-other",
                                                       "2026-11-02", "SSVEP", "formal")
        self.record(other_context)
        previous = self.catalog.bind_acquisition(self.subject_id, "auto-20261102-110000-previous",
                                                 "2026-11-02", "SSVEP", "formal")
        self.record(previous)
        context = self.bind()
        self.record(context, onset=90., trial_number=0, serial=False)
        ss = self.record(context, seconds=5)
        mi = self.record(context, "mi", onset=106)
        self.assertEqual(ss.parent, mi.parent)
        self.assertEqual(("raw_trials", "受试者1号__" + self.subject_id, "2026-11-02", "SSVEP+MI混合",
                          context["run_id"]), ss.relative_to(self.root).parts[:-1])
        for path, stage in ((ss, "SSVEP"), (mi, "MI")):
            with np.load(path, allow_pickle=False) as payload:
                self.assertEqual(self.subject_id, payload["subject_id"].item())
                self.assertEqual(context["run_id"], payload["session_id"].item())
                self.assertEqual("SSVEP+MI混合", payload["collection_type"].item())
                self.assertEqual(stage, payload["paradigm_type"].item())
        browser = self.view.trial_analysis
        self.assertIsNone(browser.source_path)
        with patch("src.controllers.experiment_controller.QInputDialog.getText") as dialog:
            self.controller._finish("aborted", {"run_id": context["run_id"], "saved_trials": 3})
        dialog.assert_not_called()
        self.assertEqual(self.subject_id, browser.selected_subject_id)
        self.assertEqual("2026-11-02", browser.selected_date)
        self.assertEqual(ss, browser.selected_record.path)
        self.assertEqual(mi, browser.paired_record(browser.selected_record).path)
        # Selecting MI still renders the complete SSVEP report first.
        browser.select_record(next(i for i, row in enumerate(browser.records) if row.path == mi))
        with patch.object(browser.service, "generate_report", wraps=browser.service.generate_report) as generate:
            browser.generate_report()
        self.assertEqual({"ssvep", "mi"}, {call.args[0].analysis_kind for call in generate.call_args_list})
        self.assertFalse(browser.report_pixmap.isNull())
        self.assertFalse(browser.paired_report_pixmap.isNull())
        self.assertEqual("SSVEP+MI混合", browser.selected_record.collection_label)

    def test_capture_and_markers_use_frozen_participant_not_login_context(self):
        context = self.bind()
        marker = SimpleNamespace(publish=MagicMock(return_value=True), stop=lambda: None)
        worker = EEGWorker(SimpleNamespace(source_name="synthetic-test-only", flush=lambda: 0),
            SimpleNamespace(reset=lambda: None), self.recorder,
            experiment_clock=ExperimentClock(local_clock=lambda: 100., unix_clock=lambda: 1000.),
            marker_outlet=marker)
        self.view._pipeline_worker = worker
        worker.set_experiment_context("operator-A", "login-A")
        worker.set_paradigm_type("SSVEP+MI混合")
        worker._channel_names = HYBRID.names
        payload = {"run_id": context["run_id"], "trial_id": context["run_id"] + "-mi",
                   "true_label": "想象左手", "paradigm_type": "MI", "stage": "mi",
                   "montage_id": HYBRID.id, "duration_seconds": 3., "marker_timestamp": 100.}
        self.controller._record_phase("cue", 99., payload)
        self.controller._capture_trial(payload)
        # A login/context update after queuing must not relabel the EEG.
        worker.set_experiment_context("operator-B", "login-B")
        worker._apply_pending_stimulus(None, np.array([]), 0)
        worker._collect_labeled_trial(np.random.default_rng(4).normal(0, 8, (8, 750)),
                                     100. + np.arange(750) / 250, 250., 750)
        self.controller.automatic_window.notify_trial_save_error.assert_not_called()
        files = list(self.recorder.raw_trial_dir.rglob("*.npz"))
        self.assertEqual(1, len(files))
        with np.load(files[0]) as p:
            self.assertEqual(self.subject_id, p["subject_id"].item())
            self.assertEqual("SSVEP+MI混合", p["collection_type"].item())
        events = [json.loads(line) for line in self.recorder.marker_log_path.read_text().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(e["subject_id"] == self.subject_id for e in events))
        self.assertEqual(self.subject_id, marker.publish.call_args_list[0].args[2]["subject_id"])

    def test_nested_import_uses_catalog_and_copied_run_recovers_embedded_identity(self):
        context = self.bind()
        path = self.record(context)
        browser = self.view.trial_analysis
        browser.load_source(path.parent)
        self.assertEqual(self.catalog.path, browser.catalog.path)
        self.assertEqual(self.subject_id, browser.selected_subject_id)
        with TemporaryDirectory() as external:
            copied = Path(external) / path.name
            shutil.copy2(path, copied)
            browser.load_source(copied)
            self.assertEqual(self.subject_id, browser.selected_subject_id)
            self.assertEqual("受试者1号", browser.selected_subject.display_name)

    def test_identity_is_locked_during_run_and_can_change_after_closing(self):
        self.bind()
        other = self.catalog.create_subject("受试者2号")
        with self.assertRaisesRegex(ValueError, "关闭当前实验窗口"):
            self.controller.select_subject(other)
        self.controller._window_destroyed()
        self.controller.select_subject(other)
        self.assertEqual(other, self.controller.selected_subject_id)
        self.assertEqual({}, self.controller._run_context)

    def test_start_requires_participant_and_dialog_cancel_preserves_selection(self):
        with patch("src.controllers.experiment_controller.AutomaticSSVEPExperimentWindow") as window:
            self.controller.start_automatic()
        window.assert_not_called()
        self.controller.select_subject(self.subject_id)
        with patch("src.controllers.experiment_controller.QInputDialog.getItem", return_value=("", False)):
            self.view._activate("collection-subject")
        self.assertEqual(self.subject_id, self.controller.selected_subject_id)
        with patch("src.controllers.experiment_controller.QInputDialog.getText", return_value=("受试者2号", True)):
            self.view._activate("collection-subject-new")
        self.assertEqual("受试者2号", self.controller.selected_subject_name)

    def test_rename_and_stale_catalog_preserve_folder_and_new_run_ownership(self):
        context = self.bind()
        before = self.record(context)
        stale = SubjectCatalog(self.root)
        other_catalog = SubjectCatalog(self.root)
        other = other_catalog.create_subject("受试者2号")
        other_catalog.bind_acquisition(other, "auto-20261102-130000-other", "2026-11-02", "SSVEP", "formal")
        stale.rename_subject(self.subject_id, "受试者1号（改名）")
        self.assertIsNotNone(SubjectCatalog(self.root).assignment_for_run("auto-20261102-130000-other"))
        self.controller._window_destroyed()
        new = self.bind(run="auto-20261103-120000-next", paradigm="SSVEP")
        after = self.record(new)
        self.assertEqual(before.relative_to(self.root).parts[1], after.relative_to(self.root).parts[1])
        self.assertEqual("受试者1号（改名）", new["subject_name"])
        self.assertTrue(before.exists())

    def test_late_marker_after_restart_keeps_index_tags_and_rejects_escape_paths(self):
        context = self.bind()
        path = self.record(context)
        reopened = ExperimentRecorder(self.root)
        reopened.record_marker("rest", 104., metadata={"trial_id": path.stem, "phase": "rest"})
        with np.load(path) as p:
            self.assertEqual(104., p["rest_lsl_time"].item())
            self.assertEqual("SSVEP+MI混合", p["collection_type"].item())
        with self.recorder.raw_trial_index_path.open(encoding="utf-8-sig") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual("SSVEP+MI混合", row["collection_type"])
        self.assertEqual("受试者1号", row["subject_name"])
        self.assertEqual(str(path.relative_to(self.root)), row["path"])
        with self.assertRaisesRegex(ValueError, "无效路径"):
            acquisition_directory(self.recorder.raw_trial_dir, {**context, "subject_folder": "../escape"})

    def test_ssvep_and_mi_only_keep_their_own_collection_tags(self):
        for paradigm, scheme in (("SSVEP", "formal"), ("MI", "pilot"), ("MI", "rest")):
            with self.subTest(paradigm=paradigm, scheme=scheme):
                self.controller._window_destroyed()
                context = self.bind(f"auto-20261102-120000-{paradigm}-{scheme}", paradigm, scheme)
                path = self.record(context, "ssvep" if paradigm == "SSVEP" else "mi")
                record = TrialAnalysisService.read_metadata(path)
                self.assertEqual(paradigm, record.collection_type)
                self.assertEqual(scheme, record.collection_scheme)


if __name__ == "__main__":
    unittest.main()
