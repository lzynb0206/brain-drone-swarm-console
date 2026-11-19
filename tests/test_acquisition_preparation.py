import json
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QTimer, QEventLoop
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from src.domain import EEGFrame
from src.bci.montage import HYBRID, HYBRID_V1, MONTAGES, research_decoder
from src.services.acquisition_preflight import AcquisitionProbe, save_probe, audit_hybrid_run
from src.services.hybrid_experiment_protocol import HybridExperimentConfig as Config, HybridExperimentProtocol as Protocol, HybridPhase as P
from src.views.hybrid_experiment_window import HybridExperimentWindow
from src.controllers.experiment_controller import ExperimentController


def probe_frame(n=1500, start=100.):
    x=np.random.default_rng(8).normal(0,8,(8,n));t=start+np.arange(n)/250
    return EEGFrame(t[-1],250.,HYBRID.names,tuple(map(tuple,x)),tuple(t),("uV",)*8,"fixture"),x,t


class PreflightTest(unittest.TestCase):
    def good_probe(self):
        frame,x,t=probe_frame();probe=AcquisitionProbe();probe.feed(frame,t,t[-1]+.02)
        return probe

    def test_signal_and_sample_clock_errors_block_readiness(self):
        self.assertTrue(self.good_probe().report()["software_checks_passed"])
        frame,x,t=probe_frame()
        for kind in ("flat","gap","reverse","nan","clock","units","mapping","missing_times"):
            with self.subTest(kind=kind):
                xx=x.copy();tt=t.copy();f=frame;now=t[-1]+.02
                if kind=="flat":xx[6]=0
                if kind=="gap":tt[600:]+=.08
                if kind=="reverse":tt[700]=tt[699]
                if kind=="nan":xx[2,10]=np.nan
                if kind=="clock":now+=10
                if kind=="units":f=replace(f,channel_units=())
                if kind=="mapping":f=replace(f,channel_names=HYBRID_V1.names)
                if kind=="missing_times":f=replace(f,sample_timestamps=())
                f=replace(f,samples=tuple(map(tuple,xx)))
                probe=AcquisitionProbe();probe.feed(f,tt,now)
                self.assertFalse(probe.report()["software_checks_passed"])

    def test_marker_disk_evidence_and_manual_mapping_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/"events.jsonl"
            markers=[dict(name=f"probe{i}",lsl_timestamp=v,published_to_lsl=True,local_marker_path=str(journal)) for i,v in enumerate((99.9,102.5,106.1))]
            journal.write_text("".join(json.dumps(m)+"\n" for m in markers))
            good=save_probe(tmp,self.good_probe(),markers,True,"pylsl.local_clock")
            self.assertTrue(good["formal_ready"])
            self.assertFalse(good["physical_display_timing_verified"])
            self.assertFalse(good["lsl_receiver_delivery_verified"])
            self.assertTrue((Path(tmp)/"research_preflight"/good["raw_file"]).exists())
            no_mapping=save_probe(tmp,self.good_probe(),markers,False,"pylsl.local_clock")
            self.assertFalse(no_mapping["formal_ready"])
            journal.write_text("")
            self.assertFalse(save_probe(tmp,self.good_probe(),markers,True,"pylsl.local_clock")["formal_ready"])

    def test_legacy_combined_rest_ends_after_closed_eye_save_without_keypress(self):
        p=Protocol(Config(mode="rest",resting_open_seconds=120,resting_closed_seconds=60));self.assertEqual((),p.plan)
        self.assertEqual(P.REST_OPEN_CUE,p.start(0));p.tick(5);p.tick(125)
        self.assertEqual(P.SAVE_REST_OPEN,p.phase);self.assertIsNone(p.tick(999))
        p.saved("rest_open",125);p.tick(130);p.tick(190)
        self.assertEqual(P.SAVE_REST_CLOSED,p.phase)
        p.saved("rest_closed",190);self.assertEqual(P.FINISHED,p.phase)
        self.assertIsNone(p.tick(999))
        with self.assertRaises(ValueError):Config(resting_open_seconds=0).validate()

    def test_default_rest_durations_are_saved_without_truncating(self):
        c=Config(mode="rest").validate()
        self.assertEqual((140.,140.),(c.resting_open_seconds,c.resting_closed_seconds))
        p=Protocol(c);p.start(0);p.tick(5)
        self.assertIsNone(p.tick(144.999))
        self.assertEqual(P.SAVE_REST_OPEN,p.tick(145))
        p.saved("rest_open",145);p.tick(150)
        self.assertIsNone(p.tick(289.999))
        self.assertEqual(P.SAVE_REST_CLOSED,p.tick(290))

    def test_each_rest_state_is_standalone_and_waits_for_complete_save(self):
        for eye, cue, recording, saving in (
            ("open", P.REST_OPEN_CUE, P.REST_OPEN, P.SAVE_REST_OPEN),
            ("closed", P.REST_CLOSED_CUE, P.REST_CLOSED, P.SAVE_REST_CLOSED),
        ):
            with self.subTest(eye=eye):
                p=Protocol(Config(mode="rest",rest_eye_state=eye))
                self.assertEqual((),p.plan)
                self.assertEqual(cue,p.start(0))
                self.assertEqual(recording,p.tick(5))
                self.assertEqual(saving,p.tick(145))
                self.assertIsNone(p.tick(999))
                self.assertEqual(P.FINISHED,p.saved("rest_"+eye,999))
                self.assertFalse(p.running)

    def test_montage_versions_and_decoder_filters_remain_distinct(self):
        self.assertEqual("PO3",MONTAGES[HYBRID_V1.id].names[3])
        self.assertEqual("P3",MONTAGES[HYBRID.id].names[3])
        self.assertEqual("fft",research_decoder(HYBRID_V1.id)._fbcca.filter_method)
        self.assertEqual("butter4",research_decoder(HYBRID.id)._fbcca.filter_method)

    def test_pilot_pairs_are_explicitly_separate_from_formal_data(self):
        config=Config(purpose="pilot",idle_trials=2,include_dedicated_rest=False)
        p=Protocol(config)
        self.assertEqual(2,config.pair_count)
        self.assertEqual(2,sum(t.mi_label is not None for t in p.plan))
        self.assertEqual(2,sum(t.ssvep_label=="空闲" for t in p.plan))

    def test_historical_montage_metadata_is_read_without_relabeling(self):
        from src.ml.hybrid_pairs import read_stage
        with tempfile.TemporaryDirectory() as tmp:
            for montage in (HYBRID_V1,HYBRID):
                p=Path(tmp)/(montage.id+'.npz')
                np.savez_compressed(p,eeg=np.zeros((8,750)),sample_timestamps=np.arange(750)/250,
                    fs=250.,channel_names=montage.names,trial_context=json.dumps({'montage_id':montage.id}),
                    signal_quality='{}',subject_id='fixture')
                row=read_stage(p)
                self.assertEqual(montage.id,row['context']['montage_id'])
                with np.load(p, allow_pickle=False) as saved:
                    self.assertEqual(montage.names[3],str(saved['channel_names'][3]))


class PresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def present(self,w):
        w.repaint();self.app.processEvents();self.app.processEvents()

    def test_progress_advances_after_mi_and_not_after_ssvep_or_an_abort(self):
        w=HybridExperimentWindow(Config(serial_pair_count=84,idle_trials=0))
        w.protocol.index=0
        self.assertEqual("已完成 0 / 84 组",w.progress_text)
        w._transition(w.protocol.enter(P.SAVE_SSVEP,time.monotonic()))
        ctx=w.metadata()
        self.assertTrue(w.notify_trial_saved(ctx["true_label"],ctx["trial_id"]+".npz"))
        self.assertEqual("已完成 0 / 84 组",w.progress_text)
        w._transition(w.protocol.enter(P.SAVE_MI,time.monotonic()))
        ctx=w.metadata()
        self.assertTrue(w.notify_trial_saved(ctx["true_label"],ctx["trial_id"]+".npz"))
        self.assertEqual("已完成 1 / 84 组",w.progress_text)
        self.assertFalse(w.notify_trial_saved(ctx["true_label"],ctx["trial_id"]+".npz"))
        w.protocol.index=1
        w._transition(w.protocol.enter(P.MI,time.monotonic()))
        w.abort_immediately("fixture_end")
        self.assertEqual("已完成 1 / 84 组",w.progress_text)
        w.deleteLater()

    def test_capture_waits_for_presentation_and_mi_images_identical(self):
        w=HybridExperimentWindow(Config(mode="mi",repetitions=1,include_dedicated_rest=False))
        captures=[];w.capture_requested.connect(captures.append)
        w.resize(900,600);w.start_experiment();w._timer.stop()
        self.assertEqual([],captures);self.assertIsNone(w.protocol.deadline)
        w.show();self.present(w);self.assertEqual(1,len(captures))
        self.assertEqual("headless_paint_test",captures[0]["onset_estimate"])
        self.assertFalse(captures[0]["presentation_is_physical_measurement"])
        images=[]
        for index in (0,1):
            w.protocol.index=index
            w._transition(w.protocol.enter(P.MI,time.monotonic()));self.present(w)
            image=w.grab().toImage();images.append(bytes(image.constBits()))
        self.assertEqual(images[0],images[1])
        self.assertNotEqual(captures[-1]["mi_true_label"],captures[-2]["mi_true_label"])
        self.assertEqual(HYBRID.names,tuple(captures[-1]["channel_names"]))
        w.abort_immediately("fixture_end");w.deleteLater()

    def test_rest_metadata_never_claims_short_baseline_is_dedicated_rest(self):
        w=HybridExperimentWindow(Config(mode="rest"));w.resize(800,600);w.show()
        w.start_experiment();w._timer.stop();self.present(w)
        self.assertEqual("dedicated_rest",w.metadata("rest_open")["record_kind"])
        self.assertEqual("closed",w.metadata("rest_closed")["eye_state"])
        self.assertEqual("rest_baseline",w.metadata("baseline")["record_kind"])
        self.assertNotEqual(w.metadata("rest_open")["trial_id"],w.metadata("baseline")["trial_id"])
        w.abort_immediately("fixture_end");w.deleteLater()

    def test_mi_cue_does_not_start_recording_and_default_capture_is_five_seconds(self):
        for mode in ("serial","mi"):
            with self.subTest(mode=mode):
                w=HybridExperimentWindow(Config(mode=mode,repetitions=1,include_dedicated_rest=False))
                captures=[];w.capture_requested.connect(captures.append)
                w.protocol.index=next(i for i,t in enumerate(w.protocol.plan) if t.mi_label)
                w.resize(900,600)
                w._transition(w.protocol.enter(P.MI_CUE if mode=="serial" else P.CUE,time.monotonic()))
                w.show();w._timer.stop();self.present(w)
                self.assertEqual([],captures)
                self.assertAlmostEqual(3.,w.protocol.deadline-w.phase_started)
                self.assertIsNone(w.protocol.tick(w.protocol.deadline-.001))
                w._transition(w.protocol.tick(w.protocol.deadline));self.present(w)
                self.assertEqual(1,len(captures))
                self.assertEqual("mi",captures[0]["stage"])
                self.assertAlmostEqual(5.,captures[0]["duration_seconds"])
                self.assertEqual(3.,captures[0]["config"]["mi_cue_seconds"])
                w.abort_immediately("fixture_end");w.deleteLater()

    def test_full_present_record_save_and_audit_cycle_with_synthetic_eeg(self):
        from src.services import ExperimentRecorder, ExperimentClock
        from src.workers import EEGWorker
        with tempfile.TemporaryDirectory() as tmp:
            rec=ExperimentRecorder(tmp)
            clock=ExperimentClock(local_clock=time.monotonic)
            worker=EEGWorker(SimpleNamespace(source_name="synthetic_test_fixture",flush=lambda:0),
                SimpleNamespace(reset=lambda:None),rec,experiment_clock=clock,
                marker_outlet=SimpleNamespace(publish=lambda *a,**k:False,stop=lambda:None))
            worker.set_experiment_context("synthetic_fixture_not_participant","test")
            worker._channel_names=HYBRID.names;worker._channel_units=("uV",)*8
            worker.set_paradigm_type("SSVEP+MI混合")
            c=Config(purpose="pilot",include_dedicated_rest=False,idle_trials=1,
                baseline_seconds=.5,cue_seconds=.5,mi_cue_seconds=.5,ssvep_seconds=2.,gap_seconds=.5,mi_seconds=2.,rest_seconds=.5)
            w=HybridExperimentWindow(c)
            w.resize(800,600)
            loop=QEventLoop();summaries=[];errors=[];recognitions=[]
            host=SimpleNamespace(experiment_recorder=rec,_pipeline_worker=worker,_notify=lambda message:None,
                                 actions=SimpleNamespace(montage_id=HYBRID.id))
            controller=ExperimentController(host)
            controller._label_completed_run=lambda values:""
            worker.recognition_received.connect(recognitions.append)
            def capture(ctx):
                worker.capture_experiment_trial(ctx["true_label"],ctx["frequency_hz"],ctx["marker_timestamp"],
                    duration_seconds=ctx["duration_seconds"],metadata=ctx)
            def saved(label,path):
                f=Path(path);f=f if f.is_absolute() else rec.root/f
                with np.load(f,allow_pickle=False) as z:q=json.loads(str(z["signal_quality"].item()))
                w.notify_trial_saved(label,path,q.get("status"),{"p_ssvep":q.get("p_ssvep"),"p_ssvep_valid":q.get("p_ssvep_valid",False)})
            w.capture_requested.connect(capture)
            w.phase_marker.connect(lambda name,t,ctx:worker.record_experiment_marker(name,t,ctx))
            worker.labeled_trial_saved.connect(saved)
            w.experiment_finished.connect(controller._finish)
            w.experiment_finished.connect(lambda status,summary:(summaries.append(summary),loop.quit()))
            start=time.monotonic();sent=0;rng=np.random.default_rng(4)
            def feed():
                nonlocal sent
                try:
                    n=int((time.monotonic()-start)*250)
                    if n<=sent:return
                    t=start+np.arange(sent,n)/250.;x=rng.normal(0,8,(8,n-sent));sent=n
                    worker._apply_pending_stimulus(None,np.array([]),0)
                    window=round((worker._labeled_trial_recorder.capture_seconds or 2.)*250)
                    worker._collect_labeled_trial(x,t,250.,window)
                except Exception as exc:
                    errors.append(str(exc));loop.quit()
            timer=QTimer();timer.setInterval(10);timer.timeout.connect(feed)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
            w.show();w.start_experiment();timer.start();timeout.start(30000);loop.exec()
            timer.stop();timeout.stop()
            try:
                self.assertEqual([],errors)
                self.assertTrue(summaries,"synthetic integration timed out")
                summary=summaries[-1]
                self.assertEqual("completed",summary["status"])
                self.assertEqual(2,summary["completed_pairs"])
                self.assertEqual(6,summary["saved_trials"])
                audit=audit_hybrid_run(rec.root,summary)
                self.assertTrue(audit["software_timing_checks_passed"],audit)
                self.assertTrue((rec.root/"hybrid_runs"/(summary["run_id"]+"-timing-audit.json")).exists())
                self.assertEqual([],recognitions)
                for path in summary["saved_paths"]:
                    f=Path(path);f=f if f.is_absolute() else rec.root/f
                    with np.load(f,allow_pickle=False) as z:ctx=json.loads(str(z["trial_context"].item()))
                    self.assertEqual("",ctx["preflight_id"])
                    self.assertEqual("pilot",ctx["acquisition_purpose"])
                    if ctx["stage"]=="mi":self.assertEqual("pilot_pair",ctx["record_kind"])
            finally:
                w.abort_immediately("fixture_end");w.deleteLater()


class TimingAuditTest(unittest.TestCase):
    def test_standalone_rest_audit_requires_only_the_selected_eye_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for eye in ("open", "closed"):
                stage="rest_"+eye
                path=root/(stage+".npz")
                context=dict(run_id="rest-fixture",trial_id=stage,stage=stage,montage_id=HYBRID.id,
                             duration_seconds=5.,stimulus_lsl_time=100.)
                np.savez_compressed(path,eeg=np.ones((8,1250)),sample_timestamps=100.004+np.arange(1250)/250,
                    fs=250.,channel_names=HYBRID.names,trial_context=json.dumps(context),signal_quality='{"status":"good"}')
                summary=dict(run_id="rest-fixture",status="completed",saved_paths=[str(path)],
                    config={"mode":"rest","rest_eye_state":eye},
                    presentation_events=[dict(trial_id=stage,presentation_duration_seconds=5.01,
                        render_frame_monotonic_seconds=np.arange(0,5,.016).tolist())])
                audit=audit_hybrid_run(root,summary)
                self.assertTrue(audit["software_timing_checks_passed"])
                self.assertTrue(audit["dedicated_rest_complete"])
                self.assertEqual([stage],audit["expected_rest_stages"])
                summary["config"]["rest_eye_state"]="closed" if eye=="open" else "open"
                self.assertFalse(audit_hybrid_run(root,summary)["software_timing_checks_passed"])

    def test_missing_presentation_and_delayed_eeg_fail_audit_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);f=root/"trial.npz";n=750
            context=dict(run_id="hybrid-fixture",trial_id="trial",stage="mi",montage_id=HYBRID.id,
                duration_seconds=3.,stimulus_lsl_time=100.,preflight_id="fixture-probe")
            def save(delay):
                np.savez_compressed(f,eeg=np.ones((8,n)),sample_timestamps=100+delay+np.arange(n)/250,
                    fs=250.,channel_names=HYBRID.names,trial_context=json.dumps(context),signal_quality=json.dumps({"status":"good"}))
            event=dict(trial_id="trial",presentation_duration_seconds=3.01,render_frame_monotonic_seconds=np.arange(0,3,.016).tolist())
            summary=dict(run_id="hybrid-fixture",status="completed",saved_paths=[str(f)],config={},presentation_events=[event])
            save(.004);self.assertTrue(audit_hybrid_run(root,summary)["software_timing_checks_passed"])
            save(.25);self.assertFalse(audit_hybrid_run(root,summary)["software_timing_checks_passed"])
            save(.004);summary["presentation_events"]=[]
            self.assertFalse(audit_hybrid_run(root,summary)["software_timing_checks_passed"])
            self.assertTrue(f.exists())


if __name__=="__main__":unittest.main()
