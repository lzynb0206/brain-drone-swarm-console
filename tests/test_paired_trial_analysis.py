from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from src.bci.montage import HYBRID
from src.services.mi_trial_analysis import motor_features
from src.services.trial_analysis_service import TrialAnalysisService
from src.views.main_view import MainView


def write_pair(root):
    rng = np.random.default_rng(71)
    run = 'hybrid-20261102-120000-fixture'
    pair = run + '-pair-0001'
    for stage, seconds, onset, label in [('ssvep', 5, 100, '起飞'), ('mi', 3, 106, '想象左手')]:
        t = np.arange(round(seconds * 250)) / 250
        eeg = np.stack([15 * np.sin(2 * np.pi * (8 if ch < 5 else 10 + 5 * (ch - 5)) * t + ch * .2)
                        + rng.normal(0, 3, len(t)) for ch in range(8)])
        context = dict(run_id=run, pair_id=pair, trial_id=pair + '-' + stage, stage=stage,
                       record_kind='serial_pair', montage_id=HYBRID.id, paradigm_type=stage.upper(),
                       ssvep_true_label='起飞', mi_true_label='想象左手',
                       preceding_ssvep_trial_id=pair + '-ssvep' if stage == 'mi' else '')
        np.savez_compressed(root / (pair + '-' + stage + '.npz'), eeg=eeg, fs=250.,
                            true_label=label, channel_names=HYBRID.names, sample_timestamps=onset + t,
                            trial_context=json.dumps(context))
    return root / (pair + '-ssvep.npz'), root / (pair + '-mi.npz')


class PairedTrialAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mi_uses_named_motor_channels_and_does_not_call_fbcca(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, path = write_pair(root)
            service = TrialAnalysisService(root / 'reports')
            record = service.read_metadata(path)
            self.assertEqual('mi', record.analysis_kind)
            self.assertTrue(record.is_serial_pair)
            with patch('src.services.trial_analysis_service.FBCCADecoder.decode') as decoder:
                report = service.generate_report(record, 0, 3)
            decoder.assert_not_called()
            metrics = json.loads(report.metrics_path.read_text())
            self.assertEqual('mi', report.analysis_kind)
            self.assertNotIn('fbcca', metrics)
            self.assertFalse(metrics['mi']['classifier_applied'])
            self.assertFalse(metrics['mi']['erd_computed'])
            self.assertEqual(['C3', 'Cz', 'C4'], metrics['mi']['channel_names'])
            self.assertTrue(report.figure_png.startswith(b'\x89PNG'))

    def test_absolute_band_power_follows_channel_names_after_reordering(self):
        fs = 250
        t = np.arange(750) / fs
        names = ('C4', 'C3', 'Cz')
        x = np.stack([8 * np.sin(2 * np.pi * 20 * t), 10 * np.sin(2 * np.pi * 10 * t), np.zeros(len(t))])
        result = motor_features(x, names, fs)
        self.assertEqual((1, 2, 0), result['indices'])
        self.assertAlmostEqual(50, result['power'][0, 0], delta=2)
        self.assertAlmostEqual(32, result['power'][2, 1], delta=2)
        with self.assertRaisesRegex(ValueError, '不能按位置猜测'):
            motor_features(x, ('CH1', 'CH2', 'CH3'), fs)

    def test_pairing_requires_explicit_identity_and_ordered_timestamps(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sp, mp = write_pair(root)
            ss, mi = map(TrialAnalysisService.read_metadata, (sp, mp))
            TrialAnalysisService.validate_pair(ss, mi)
            for changed in (replace(mi, experiment_id='other'), replace(mi, subject_id='other'),
                            replace(mi, preceding_ssvep_trial_id='other'), replace(mi, mi_true_label='想象右手')):
                with self.assertRaises(ValueError):
                    TrialAnalysisService.validate_pair(ss, changed)
            with np.load(mp, allow_pickle=False) as p:
                values = {key: p[key] for key in p.files}
            values['sample_timestamps'] -= 10
            np.savez_compressed(mp, **values)
            with self.assertRaisesRegex(ValueError, 'MI 起点'):
                TrialAnalysisService.validate_pair(ss, mi)

    def test_selecting_mi_displays_ssvep_first_and_mi_second(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sp, mp = write_pair(root)
            view = MainView()
            try:
                browser = view.trial_analysis
                browser.service.output_root = root / 'reports'
                browser.load_source(root)
                mi_index = next(i for i, row in enumerate(browser.records) if row.stage == 'mi')
                browser.select_record(mi_index)
                browser.window_start_seconds = .5
                browser.window_end_seconds = 2.5
                browser.generate_report()
                self.assertIsNotNone(browser.paired_report_pixmap)
                self.assertEqual('mi', browser.last_report.analysis_kind)
                self.assertGreater(browser.report_pixmap.height(), browser.paired_report_pixmap.height())
                self.assertEqual('', browser.report_note)
                mi_metrics = json.loads(browser.last_report.metrics_path.read_text())
                self.assertEqual({'start': .5, 'end': 2.5}, mi_metrics['analysis_window_seconds'])
                ss_metrics = json.loads(next((root / 'reports' / '起飞').glob('*.json')).read_text())
                self.assertEqual({'start': 0., 'end': 5.}, ss_metrics['analysis_window_seconds'])
                self.assertEqual(list(HYBRID.ssvep), ss_metrics['fbcca']['channel_names'])
                browser._clear_report()
                self.assertIsNone(browser.paired_report_pixmap)
            finally:
                view.close()

    def test_missing_or_ambiguous_pair_never_uses_an_unrelated_trial(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sp, mp = write_pair(root)
            view = MainView()
            try:
                browser = view.trial_analysis
                ss, mi = map(browser.service.read_metadata, (sp, mp))
                browser.records = [ss, replace(mi, pair_id='different')]
                self.assertIsNone(browser.paired_record(ss))
                browser.records = [ss, mi, replace(mi, path=root / 'duplicate.npz')]
                self.assertIsNone(browser.paired_record(ss))
                browser.records = [ss, mi]
                self.assertEqual(mi, browser.paired_record(ss))
            finally:
                view.close()


if __name__ == '__main__':
    unittest.main()
