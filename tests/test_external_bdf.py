"""External presentation must never fabricate EEG, timing certainty or P."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from src.services.bdf_import import BDFRecording, ManualAlignment, import_bdf, plan_bdf_segments
from src.services.external_recording import ExternalRecordingJournal, load_external_segments
from src.services.hybrid_experiment_protocol import HybridExperimentConfig, HybridPhase
from src.services.ssvep_experiment_protocol import AutomaticExperimentConfig
from src.services.experiment_recorder import ExperimentRecorder
from src.services.subject_catalog import SubjectCatalog
from src.services.trial_analysis_service import TrialAnalysisService
from src.services.mi_trial_analysis import motor_features
from src.views.main_view import MainView


def write_bdf(path, seconds=20, fs=200):
    names = ('O1','Oz','O2','C3','Cz','C4','FC3','CP3','Trigger')
    count=len(names)
    def fixed(value, size):
        return str(value).encode().ljust(size,b' ')
    h = (b'\xffBIOSEMI'+fixed('',80)+fixed('',80)+fixed('10.11.26',8)+fixed('12.00.00',8)
         +fixed(256*(count+1),8)+fixed('24BIT',44)+fixed(seconds,8)+fixed(1,8)+fixed(count,4))
    fields=[(names,16),(['']*count,80),(['uV']*8+[''],8),([-8388608]*count,8),
            ([8388607]*count,8),([-8388608]*count,8),([8388607]*count,8),(['']*count,80),
            ([fs]*count,8),(['']*count,32)]
    for values,width in fields:
        h+=b''.join(fixed(v,width) for v in values)
    data = np.arange(seconds*count*fs,dtype=np.int32).reshape(seconds,count,fs)%257-128
    data[0,0,:4] = [-8388608,-1,0,8388607]
    packed=np.stack([data & 255,(data>>8)&255,(data>>16)&255],axis=-1).astype(np.uint8)
    path.write_bytes(h+packed.tobytes())
    return data.transpose(1,0,2).reshape(count,-1)


class BDFImportTest(unittest.TestCase):
    def test_signed_24_bit_scaling_cross_record_and_integrity(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.bdf'; expected=write_bdf(path)
            b=BDFRecording(path)
            self.assertEqual(8,len(b.eeg_names));self.assertEqual(200,b.fs)
            np.testing.assert_array_equal(b.read_samples(0,250),expected[:8,:250])
            np.testing.assert_array_equal(b.read_samples(193,611),expected[:8,193:611])
            with self.assertRaises(ValueError):b.read_samples(-1,300)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError,'不完整'):BDFRecording(path)

    def make_log(self, root):
        with patch('src.services.external_recording.time.monotonic',return_value=100.):
            log=ExternalRecordingJournal(root,'hybrid-20261110-fixture',
                {'run_id':'hybrid-20261110-fixture','acquisition_subject_id':'subject-test','subject_name':'测试'},
                HybridExperimentConfig())
        for stage,start,end in [('ssvep',101.,103.),('mi',104.,106.)]:
            context={'trial_id':'trial-'+stage,'pair_id':'pair-1','block_id':'block-1',
                     'stage':stage,'true_label':'起飞' if stage=='ssvep' else '想象左手',
                     'ssvep_true_label':'起飞','mi_true_label':'想象左手','record_kind':'serial_pair',
                     'duration_seconds':2.,'presentation_monotonic':start,
                     'preceding_ssvep_trial_id':'trial-ssvep' if stage=='mi' else ''}
            log.capture(context)
            log.phase('hybrid_stage_end',0,{**context,'presentation_end_monotonic':end})
        return log

    def test_explicit_alignment_preserves_labels_all_channels_and_pair(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'input.bdf';expected=write_bdf(p)
            log=self.make_log(root); b=BDFRecording(p)
            paths=import_bdf(b,log.path,root/'imports',ManualAlignment(2,note='fixture only'),
                             ('O1','O2'),('FC3','C3','CP3'))
            records=[TrialAnalysisService.read_metadata(p) for p in paths]
            self.assertEqual(2,len(records))
            TrialAnalysisService.validate_pair(records[0],records[1])
            for record in records:
                self.assertEqual(8,record.channel_count)
                self.assertEqual('manual_unverified',record.alignment_status)
            self.assertEqual(('FC3','C3','CP3'),records[1].mi_channel_names)
            with np.load(paths[0],allow_pickle=False) as data:
                np.testing.assert_array_equal(data['eeg'],expected[:8,600:1000])
                ctx=json.loads(str(data['trial_context'].item()))
                self.assertFalse(ctx['p_ssvep_valid']);self.assertIsNone(ctx['p_ssvep'])
                self.assertEqual(600,ctx['bdf_sample_start'])
            with self.assertRaisesRegex(ValueError,'对齐依据'):
                plan_bdf_segments(b,log.path,ManualAlignment(0))
            with self.assertRaisesRegex(ValueError,'超出'):
                plan_bdf_segments(b,log.path,ManualAlignment(100,note='test'))
            self.assertEqual(1,len(list((root/'imports').iterdir())))

    def test_incomplete_stage_excluded_and_configurable_motor_electrodes(self):
        with TemporaryDirectory() as tmp:
            log=self.make_log(Path(tmp))
            log.capture({'trial_id':'incomplete','duration_seconds':3.,'true_label':'想象右手',
                         'presentation_monotonic':107.})
            log.phase('hybrid_stage_end',0,{'trial_id':'incomplete','presentation_end_monotonic':108.})
            self.assertEqual(2,len(load_external_segments(log.path)))
            x=np.random.default_rng(1).normal(size=(4,600))
            features=motor_features(x,('FC3','CP3','O1','FC4'),200,('FC3','CP3','FC4'))
            self.assertEqual((0,1,3),features['indices'])
            self.assertEqual((3,2),features['power'].shape)

    def test_32_channel_2000hz_reports_use_selected_electrodes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            names=('Fp1','Fp2','F3','F4','C3','C4','P3','P4','O1','O2','F7','F8','T7','T8',
                   'P7','P8','Fz','Cz','A1','A2','FC3','FC4','CP3','CP4','FT7','FT8',
                   'TP7','TP8','Fpz','CPz','POz','Oz')
            x=np.random.default_rng(7).normal(0,5,(32,4000))
            selected=('FC3','C3','CP3','Cz','CPz','FC4','C4','CP4')
            service=TrialAnalysisService(root/'reports');service.prepare_renderer()
            for stage,label in [('ssvep','起飞'),('mi','想象左手')]:
                path=root/(stage+'.npz')
                np.savez_compressed(path,eeg=x,fs=2000.,channel_names=names,true_label=label,
                    trial_context=json.dumps({'stage':stage,'paradigm_type':stage.upper(),
                        'ssvep_channel_names':['Oz','O1'],'mi_channel_names':selected,
                        'alignment_status':'manual_unverified','synthetic_test_only':True}))
                record=service.read_metadata(path)
                report=service.generate_report(record,0,2)
                metrics=json.loads(report.metrics_path.read_text())
                self.assertTrue(report.figure_png.startswith(b'\x89PNG'))
                self.assertEqual('manual_unverified',metrics['quality']['alignment_status'])
                if stage=='mi':
                    self.assertEqual(list(selected),metrics['mi']['channel_names'])
                    self.assertFalse(metrics['mi']['classifier_applied'])
                else:self.assertEqual(['Oz','O1'],metrics['fbcca']['channel_names'])


class ExternalPresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_without_eeg_runs_all_paradigms_and_writes_only_logs(self):
        cases = [(paradigm, scheme) for paradigm in ('SSVEP','MI','SSVEP+MI混合')
                 for scheme in ('formal', 'rest_open', 'rest_closed')]
        cases += [('MI', 'pilot'), ('SSVEP+MI混合', 'pilot')]
        for paradigm, scheme in cases:
            with self.subTest(paradigm=paradigm, scheme=scheme), TemporaryDirectory() as tmp:
                recorder=ExperimentRecorder(tmp)
                view=MainView(experiment_recorder=recorder)
                controller=view.experiment
                self.assertTrue(controller.external_recording)
                controller.select_subject(SubjectCatalog(tmp).create_subject('测试'))
                view.actions.paradigm_type=paradigm
                view.actions.automatic_experiment_config=AutomaticExperimentConfig(
                    repetitions_per_label=1,labels=('起飞',))
                view.actions.hybrid_experiment_config=HybridExperimentConfig(
                    repetitions=1, include_dedicated_rest=True, idle_trials=8,
                    serial_pair_count=84 if paradigm=='SSVEP+MI混合' and scheme=='formal' else None)
                view.actions.collection_schemes[paradigm]=scheme
                clock=[100.]
                with patch('time.monotonic',side_effect=lambda:clock[0]), \
                     patch('src.controllers.experiment_controller.QTimer.singleShot'):
                    controller.start_collection()
                    w=controller.automatic_window
                    self.assertIsNotNone(w)
                    journal=controller.external_journal
                    w.start_experiment();w._timer.stop()
                    controller.notify_error('unrelated local EEG error')
                    controller.handle_worker_finished()
                    self.assertTrue(w.running)
                    phases=[]
                    for _ in range(1000):
                        if not w.running:break
                        phases.append(w.protocol.phase)
                        if hasattr(w,'_waiting_presentation') and w._waiting_presentation:
                            w._painted_epoch=w._epoch;w._presented()
                        self.assertNotEqual(HybridPhase.REST_READY,w.protocol.phase)
                        deadline=getattr(w.protocol,'deadline',None)
                        if deadline is None:
                            deadline=getattr(w.protocol,'_phase_deadline',None)
                        if deadline is None:
                            clock[0]+=10
                        else:clock[0]=deadline+.01
                        w._tick()
                    self.assertFalse(w.running)
                    rows=[json.loads(line) for line in journal.path.read_text().splitlines()]
                    self.assertEqual('completed',rows[-1]['metadata']['status'])
                    self.assertEqual(0,rows[-1]['metadata']['saved_trials'])
                    segments=load_external_segments(journal.path)
                    self.assertGreater(len(segments),0)
                    if scheme.startswith('rest_'):
                        self.assertEqual([scheme],[segment['stage'] for segment in segments])
                        self.assertEqual(140.,segments[0]['duration_seconds'])
                        self.assertEqual(scheme,rows[0]['metadata']['collection_scheme'])
                    elif paradigm!='SSVEP' and scheme=='formal':
                        self.assertEqual(HybridPhase.BASELINE,phases[0])
                        self.assertNotIn(HybridPhase.REST_OPEN,phases)
                        self.assertNotIn(HybridPhase.REST_CLOSED,phases)
                        self.assertTrue(all(trial.mi_label for trial in w.protocol.plan))
                        self.assertEqual(w.config.pair_count,w.completed_pairs)
                        if paradigm=='SSVEP+MI混合':
                            self.assertEqual(84,w.completed_pairs)
                            self.assertEqual('已完成 84 / 84 组',w.progress_text)
                            self.assertEqual(84,rows[-1]['metadata']['completed_pairs'])
                            self.assertEqual(169,len(segments))
                    self.assertEqual([],list(Path(tmp).rglob('*.npz')))
                    self.assertIsNone(view._pipeline_worker)
                controller.deactivate();view.close();w.deleteLater()


if __name__=='__main__':
    unittest.main()
