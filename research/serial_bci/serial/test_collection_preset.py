"""Headless tests of the serial study's GUI preset, on-disk record and startup gate."""
import json
import os
import tempfile
import time
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLineEdit
from src.bci.montage import HYBRID
from src.controllers.experiment_controller import ExperimentController
from src.domain.models import IntentResult
from src.services.acquisition_profiles import PROFILES_BY_ID
from src.services.acquisition_preflight import AcquisitionProbe, save_probe
from src.services.collection_plans import hybrid_collection_config
from src.services.experiment_recorder import ExperimentRecorder
from src.services.hybrid_experiment_protocol import HybridExperimentConfig, build_hybrid_plan
from src.services.subject_catalog import SubjectCatalog
from src.views.hybrid_experiment_window import HybridExperimentWindow


class SerialCollectionPresetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.recorder = ExperimentRecorder(folder.name)
        profile = PROFILES_BY_ID['hybrid8']
        self.actions = SimpleNamespace(acquisition_profile=profile, decoder_settings=profile.default_settings(),
                                       hybrid_experiment_config=HybridExperimentConfig(), paradigm_type='SSVEP+MI混合',
                                       collection_scheme='formal', recording_mode='local_eeg', montage_id=HYBRID.id)
        self.host = SimpleNamespace(actions=self.actions, _notify=MagicMock(), update=MagicMock(), experiment_recorder=self.recorder,
                                    acquisition=SimpleNamespace(last_frame_monotonic=time.monotonic()),
                                    _pipeline_worker=MagicMock(source_name='LSL · fixture'),
                                    _eeg_channel_names=HYBRID.names, _eeg_sample_rate=250., _on_eeg_status=MagicMock())
        self.controller = ExperimentController(self.host)
        self.controller.select_subject(SubjectCatalog(folder.name).create_subject('fixture'))
        self.controller.host.acquisition.last_frame_monotonic = time.monotonic()

    def configure(self, seed):
        def edit(dialog):
            opt_in = next(w for w in dialog.findChildren(QCheckBox) if '论文串行' in w.text())
            opt_in.setChecked(True)
            entry = next(w for w in dialog.findChildren(QLineEdit) if '预先分配' in w.placeholderText())
            entry.setText(str(seed))
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', edit):
            self.controller.configure_collection()
        return hybrid_collection_config(self.actions.hybrid_experiment_config, 'SSVEP+MI混合', 'formal')

    def test_gui_plan_and_recorded_npz_metadata(self):
        ordinary = hybrid_collection_config(self.actions.hybrid_experiment_config, 'SSVEP+MI混合', 'formal')
        self.assertEqual(len(build_hybrid_plan(ordinary)), 84)
        config = self.configure(711)
        window = HybridExperimentWindow(config, preflight_id='preflight-software-fixture')
        self.addCleanup(window.deleteLater)
        plan = window.protocol.plan
        self.assertEqual(config.serial_study_seed, 711)
        self.assertEqual(config.baseline_seconds, 5)
        self.assertEqual(len(plan), 98)
        self.assertEqual(Counter(t.block for t in plan if t.mi_label is None), {0:8,1:1,2:1,3:1,4:1,5:1,6:1})
        for block in range(1,8):
            pairs = [(t.ssvep_label, t.mi_label) for t in plan if t.block == block and t.mi_label]
            self.assertEqual(len(pairs),12)
            self.assertEqual(len(set(pairs)),12)
        self.assertNotEqual(plan, build_hybrid_plan(self.configure(712)))

        self.assertTrue(self.controller._bind_run(window,'SSVEP+MI混合','formal'))
        window.protocol.index = 8
        metadata = {**window.metadata('ssvep'), **self.controller._run_context}
        marker = IntentResult(timestamp=0.,label='fixture',confidence=0.,margin=0.,accepted=False,
                              reason='test',frequency_hz=0.,scores={}, sample_rate_hz=250.,sample_count=750,
                              used_channels_1based=tuple(range(1,9)))
        record = self.recorder.record(marker,np.zeros((8,750)),true_label=metadata['true_label'],
                                      channel_names=HYBRID.names,channel_units=['uV']*8,log_recognition=False,
                                      sample_timestamps=np.arange(750)/250,trial_metadata=metadata)
        with np.load(self.recorder.root/record['raw_trial_path'],allow_pickle=False) as saved:
            context = json.loads(str(saved['trial_context']))
            self.assertEqual(context['record_kind'],'serial_pair')
            self.assertEqual(context['block_index'],1)
            self.assertEqual(context['config']['serial_study_seed'],711)
            self.assertEqual(context['random_seed'],711)
            self.assertEqual(context['config']['idle_trials'],8)
            self.assertEqual(context['preflight_id'],'preflight-software-fixture')
            self.assertEqual(str(saved['subject_id']),self.controller.selected_subject_id)

    def test_probe_must_pass_before_window_is_created(self):
        config = self.configure(721)
        with patch('src.controllers.experiment_controller.AcquisitionPreflightDialog') as probe, \
                patch('src.controllers.experiment_controller.HybridExperimentWindow') as window, \
                patch('src.controllers.experiment_controller.QTimer.singleShot'):
            self.actions.recording_mode = 'external_bdf'
            self.controller.start_collection()
            probe.assert_not_called()
            window.assert_not_called()
            self.actions.recording_mode = 'local_eeg'
            probe.return_value.exec.return_value = QDialog.Accepted
            for rejected in (None, {'formal_ready':False}):
                probe.return_value.probe_result = rejected
                self.controller.start_collection()
                window.assert_not_called()
            probe.return_value.probe_result = {'study_technical_ready':True,'formal_ready':False,
                                               'signal_quality_warnings':['fixture warning'],'id':'preflight-software-fixture'}
            probe.return_value.exec.return_value = QDialog.Rejected
            self.controller.start_collection()
            window.assert_not_called()
            probe.return_value.exec.return_value = QDialog.Accepted
            window.return_value.run_id = 'hybrid-20261119-120000-fixture'
            self.controller.start_collection()
            self.assertEqual(window.call_args.args[0],config)
            self.assertEqual(window.call_args.kwargs['preflight_id'],'preflight-software-fixture')

    def test_probe_persists_quality_warning_separately_from_technical_failure(self):
        t = 100 + np.arange(1500)/250
        x = np.random.default_rng(991).normal(0,10,(8,len(t)))
        x[2] = 0
        frame = SimpleNamespace(samples=x, channel_names=HYBRID.names, sample_timestamps=t,
                                original_channel_names=HYBRID.names, channel_units=['uV']*8,
                                sample_rate_hz=250., source_id='unit-test')
        probe = AcquisitionProbe()
        probe.feed(frame,t,t[-1]+.01)
        journal = self.recorder.root/'unit-markers.jsonl'
        markers = [dict(name=f'm{i}',lsl_timestamp=v,local_marker_path=str(journal)) for i,v in enumerate([99.,102.,107.])]
        journal.write_text(''.join(json.dumps(m)+'\n' for m in markers))
        report = save_probe(self.recorder.root,probe,markers,True,'pylsl.local_clock')
        stored = json.loads((self.recorder.root/'research_preflight'/(report['id']+'.json')).read_text())
        self.assertTrue(stored['study_technical_ready'])
        self.assertFalse(stored['formal_ready'])
        self.assertEqual(stored['technical_errors'],[])
        self.assertTrue(stored['signal_quality_warnings'])
        journal.write_text('')
        failure = save_probe(self.recorder.root,probe,markers,True,'pylsl.local_clock')
        self.assertFalse(failure['study_technical_ready'])
        self.assertTrue(failure['technical_errors'])


if __name__ == '__main__':
    unittest.main()
