"""Profile changes must configure real decoding and preserve experiment records."""
from dataclasses import replace
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDoubleSpinBox, QSpinBox

from src.bci.decoder_settings import DecoderSettings
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.montage import HYBRID, LEGACY, research_decoder
from src.services.experiment_recorder import ExperimentRecorder
from src.services.subject_catalog import SubjectCatalog
from src.services.trial_analysis_service import TrialAnalysisService
from src.views.main_view import MainView


class AcquisitionProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = MainView()

    def tearDown(self):
        self.view.experiment.automatic_window = None
        self.view._pipeline_worker = None
        self.view.close()

    def test_mode_applies_wiring_and_parameters_without_model_loading(self):
        configure = MagicMock()
        self.view.acquisition.stream = SimpleNamespace(configure_montage=configure)
        self.view._activate('acquisition-profile:hybrid8')
        configure.assert_called_once_with(HYBRID.names)
        self.assertEqual(HYBRID.names, self.view._eeg_channel_names)
        self.assertEqual('SSVEP+MI混合', self.view.actions.paradigm_type)
        settings = DecoderSettings(harmonics=2, filter_method='butter4', min_score=.47, min_margin=.08)
        self.view.acquisition_profile.configure_decoder(settings)
        self.view._activate('acquisition-profile:ssvep8')
        self.assertEqual(LEGACY.names, self.view._eeg_channel_names)
        self.assertEqual('fft', self.view.actions.decoder_settings.filter_method)
        self.assertEqual('SSVEP', self.view.actions.paradigm_type)
        self.view._activate('acquisition-profile:hybrid8')
        self.assertEqual(settings, self.view.actions.decoder_settings)
        decoder = research_decoder(HYBRID.id, settings)
        rng = np.random.default_rng(901)
        t = np.arange(1000) / 250
        signal = rng.normal(0, 2, (8, 1000)) + 30 * np.sin(2 * np.pi * 10 * t)
        result = decoder.decode(signal, 250)
        self.assertEqual(2, result.signal_quality['fbcca_harmonics'])
        self.assertEqual('butter4', result.signal_quality['fbcca_filter_method'])
        self.assertEqual(.47, decoder.min_score)
        self.assertTrue(all(i <= 5 for i in result.used_channels_1based))
        self.assertFalse(result.accepted)  # No idle background has been observed.

    def test_running_session_rejects_mode_and_decoder_changes_atomically(self):
        self.view._activate('acquisition-profile:ssvep8')
        settings = self.view.actions.decoder_settings
        decoder = self.view.intent_decoder
        worker = MagicMock()
        worker.isRunning.return_value = True
        self.view._pipeline_worker = worker
        self.view._activate('acquisition-profile:external32')
        self.assertFalse(self.view.acquisition_profile.configure_decoder(replace(settings, harmonics=2)))
        self.assertEqual('ssvep8', self.view.actions.acquisition_profile.id)
        self.assertIs(decoder, self.view.intent_decoder)
        self.assertEqual(settings, self.view.actions.decoder_settings)
        worker.stop.assert_not_called()

    def test_failed_device_reconfiguration_preserves_previous_mode(self):
        self.view._activate('acquisition-profile:ssvep8')
        self.view.acquisition.stream = SimpleNamespace(configure_montage=MagicMock(side_effect=RuntimeError('busy')))
        self.assertFalse(self.view.acquisition_profile.select('hybrid8'))
        self.assertEqual('ssvep8', self.view.actions.acquisition_profile.id)
        self.assertEqual(LEGACY.names, self.view._eeg_channel_names)

    def test_external_mode_never_opens_or_relabels_hardware(self):
        self.view._activate('acquisition-profile:ssvep8')
        configure = MagicMock()
        self.view.acquisition.stream = SimpleNamespace(configure_montage=configure)
        self.view._activate('acquisition-profile:external32')
        configure.assert_not_called()
        self.assertTrue(self.view.experiment.external_recording)
        self.assertEqual('SSVEP+MI混合', self.view.actions.paradigm_type)
        self.assertIsNone(self.view._pipeline_worker)

    def test_parameter_dialog_saves_real_decoder_values_and_cancel_does_not(self):
        self.view._activate('acquisition-profile:ssvep8')
        def edit(dialog):
            dialog.findChild(QSpinBox, 'decoderHarmonics').setValue(3)
            dialog.findChild(QComboBox, 'decoderFilter').setCurrentIndex(1)
            dialog.findChild(QDoubleSpinBox, 'decoderMinScore').setValue(.42)
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', edit):
            self.view._activate('decoder-settings')
        decoder = self.view.intent_decoder
        self.assertIsInstance(decoder, FBCCADecoder)
        self.assertEqual((3, 'butter4', .42), (decoder.harmonics, decoder.filter_method, decoder.safety_service.min_confidence))
        with patch.object(QDialog, 'exec', return_value=QDialog.Rejected):
            self.view._activate('decoder-settings')
        self.assertIs(decoder, self.view.intent_decoder)

    def test_external_log_and_analysis_retain_chosen_parameters(self):
        from src.services.bdf_import import BDFRecording, ManualAlignment, import_bdf
        from tests.test_external_bdf import write_bdf
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.view.experiment_recorder = ExperimentRecorder(root)
            self.view.experiment.select_subject(SubjectCatalog(root).create_subject('P01'))
            self.view._activate('acquisition-profile:external32')
            settings = DecoderSettings(harmonics=3, filter_method='butter4', min_score=.48)
            self.view.acquisition_profile.configure_decoder(settings)
            window = SimpleNamespace(run_id='hybrid-20261119-profile', config=self.view.actions.hybrid_experiment_config)
            self.assertTrue(self.view.experiment._bind_run(window, 'SSVEP+MI混合', 'formal'))
            self.view.experiment.automatic_window = window
            log = self.view.experiment.external_journal
            payload = dict(trial_id='trial-ssvep', run_id=window.run_id, stage='ssvep', true_label='起飞',
                           duration_seconds=2., presentation_monotonic=log.origin+1.)
            self.view.experiment._capture_trial(payload)
            log.phase('hybrid_stage_end', 0, {**payload, 'presentation_end_monotonic': log.origin+3.})
            bdf = root/'input.bdf'
            names = ('O1', 'Oz', 'O2', 'C3', 'Cz', 'C4') + tuple(f'EEG{i}' for i in range(7, 33)) + ('Trigger',)
            write_bdf(bdf, names=names)
            paths = import_bdf(BDFRecording(bdf), log.path, root/'imports', ManualAlignment(0, note='fixture'),
                               ('O1', 'O2'), ('C3', 'Cz', 'C4'))
            record = TrialAnalysisService.read_metadata(paths[0])
            self.assertEqual(settings, record.decoder_settings)
            self.assertEqual(32, record.channel_count)
            report = TrialAnalysisService(root/'analysis').generate_report(record, 0, 2)
            metrics = json.loads(report.metrics_path.read_text())
            self.assertEqual(settings.to_dict(), metrics['fbcca']['decoder_settings'])
            self.assertEqual(.48, metrics['fbcca']['score_threshold'])

    def test_pure_ssvep_rest_keeps_its_eight_channel_wiring(self):
        self.view._activate('acquisition-profile:ssvep8')
        self.view._activate('collection-scheme:rest_open')
        worker = MagicMock(source_name='LSL fixture')
        worker.isRunning.return_value = True
        self.view._pipeline_worker = worker
        self.view._eeg_sample_rate = 250
        self.view.acquisition.last_frame_monotonic = time.monotonic()
        with TemporaryDirectory() as tmp:
            self.view.experiment_recorder = ExperimentRecorder(tmp)
            self.view.experiment.select_subject(SubjectCatalog(tmp).create_subject('R01'))
            with patch('src.controllers.experiment_controller.HybridExperimentWindow') as window, \
                    patch('src.controllers.experiment_controller.QTimer.singleShot'):
                window.return_value.run_id = 'rest-20261119-fixture'
                self.view._activate('collection-start')
                window.assert_called_once()
                self.assertEqual('rest', window.call_args.args[0].mode)
                self.assertEqual(LEGACY.id, self.view.experiment._run_context['montage_id'])
                self.view.experiment._capture_trial(dict(true_label='睁眼静息', duration_seconds=5., stage='rest_open'))
                self.assertEqual(LEGACY.id, worker.capture_experiment_trial.call_args.kwargs['metadata']['montage_id'])

    def test_invalid_parameters_are_rejected(self):
        for bad in (DecoderSettings(harmonics=0), DecoderSettings(harmonics=2.5),
                    DecoderSettings(min_score=float('nan')), DecoderSettings(filter_method='invalid')):
            with self.subTest(settings=bad), self.assertRaises(ValueError):
                bad.validate()
