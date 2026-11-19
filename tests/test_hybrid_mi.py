import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from scipy.io import savemat
import torch
from PySide6.QtWidgets import QApplication

from src.bci.config import COMMANDS
from src.bci.montage import LEGACY, HYBRID
from src.services.hybrid_experiment_protocol import HybridExperimentConfig as Config, HybridExperimentProtocol as Protocol, HybridPhase as P, build_hybrid_plan
from src.views.hybrid_experiment_window import HybridExperimentWindow
from src.ml.mi_baseline import CSPClassifier
from src.ml.mi_diffusion import MIDiffusion, condition_values
from src.ml.public_mi import load_bnci_session
from src.ml.hybrid_pairs import export_split
from src.services import ExperimentRecorder, ExperimentClock
from src.workers import EEGWorker
from src.adapters.real.lsl_eeg_stream import LSLEEGStream
from src.adapters.real.selectable_eeg_stream import SelectableEEGStream
from src.views.main_view import MainView


class MontageTest(unittest.TestCase):
    def test_old_and_new_indices_are_not_interchangeable(self):
        self.assertEqual((0,1,2,6,7),HYBRID.indices(LEGACY.names,"SSVEP"))
        self.assertEqual((0,1,2,3,4),HYBRID.indices(HYBRID.names,"SSVEP"))
        with self.assertRaises(ValueError):HYBRID.indices(LEGACY.names,"MI")

    def test_layout_only_maps_generic_live_channels_and_keeps_replay_unchanged(self):
        names=tuple(f"CH{i+1}" for i in range(8))
        self.assertEqual(HYBRID.names,LSLEEGStream._configured_montage_names(names,HYBRID.names))
        self.assertEqual(LEGACY.names,LSLEEGStream._configured_montage_names(LEGACY.names,HYBRID.names))
        csv=SimpleNamespace(connected=False,channel_names=LEGACY.names)
        lsl=LSLEEGStream();hardware=SimpleNamespace(connected=False,channel_names=LEGACY.names)
        stream=SelectableEEGStream(csv,lsl,hardware);stream.configure_montage(HYBRID.names)
        self.assertEqual(LEGACY.names,csv.channel_names)
        self.assertEqual(HYBRID.names,hardware.channel_names)
        csv.connected=True
        with self.assertRaises(RuntimeError):stream.configure_montage(LEGACY.names)


class HybridProtocolTest(unittest.TestCase):
    def test_balanced_randomized_cross_product(self):
        config=Config();plan=build_hybrid_plan(config)
        pairs=[t for t in plan if t.mi_label]
        self.assertEqual(84,len(pairs));self.assertEqual(14,sum(t.ssvep_label=="空闲" for t in plan))
        for s in COMMANDS:
            for m in config.mi_labels:
                self.assertEqual(7,sum(t.ssvep_label==s and t.mi_label==m for t in pairs))
        self.assertEqual(plan,build_hybrid_plan(config))

    def test_cannot_advance_without_save_and_pause_waits_for_complete_pair(self):
        p=Protocol(Config(repetitions=1,idle_trials=1,include_dedicated_rest=False,mi_cue_seconds=1,mi_seconds=3))
        self.assertEqual(P.BASELINE,p.start(0));self.assertEqual(P.SAVE_BASELINE,p.tick(5))
        self.assertIsNone(p.tick(100));p.saved("baseline",100)
        p.tick(101);p.tick(106);p.saved("ssvep",106);p.tick(109)
        self.assertIsNotNone(p.trial.mi_label)
        p.tick(110);p.toggle_pause(111);self.assertFalse(p.paused)
        p.tick(115);p.saved("ssvep",115);self.assertEqual(P.GAP,p.phase)
        p.tick(116);self.assertEqual(P.MI_CUE,p.phase)
        p.tick(117);p.tick(120);p.saved("mi",120)
        self.assertEqual(P.REST,p.phase);self.assertTrue(p.paused)
        p.toggle_pause(121);p.tick(124);self.assertEqual(P.CUE,p.phase)

    def test_mi_only_records_rest_then_no_ssvep_stage(self):
        p=Protocol(Config(mode="mi",repetitions=1,include_dedicated_rest=False,mi_cue_seconds=1,mi_seconds=3))
        p.start(0);p.tick(5);p.saved("baseline",5)
        self.assertEqual(P.MI,p.tick(6));p.tick(9)
        with self.assertRaises(RuntimeError):p.saved("ssvep",9)
        p.saved("mi",9);p.abort(10);self.assertFalse(p.running)

    def test_mixed_default_separates_gap_cue_and_five_second_recording(self):
        config=Config(repetitions=1,idle_trials=1,include_dedicated_rest=False)
        self.assertEqual((1.,5.),(config.cue_seconds,config.ssvep_seconds))
        p=Protocol(config)
        p.index=next(i for i,t in enumerate(p.plan) if t.mi_label)
        p.enter(P.SAVE_SSVEP,100.)
        self.assertEqual(P.GAP,p.saved("ssvep",100.))
        self.assertIsNone(p.tick(100.999))
        self.assertEqual(P.MI_CUE,p.tick(101.))
        self.assertIsNone(p.tick(103.999))
        self.assertEqual(P.MI,p.tick(104.))
        self.assertIsNone(p.tick(108.999))
        self.assertEqual(P.SAVE_MI,p.tick(109.))

    def test_mi_only_has_three_second_cue_before_five_second_recording(self):
        p=Protocol(Config(mode="mi",repetitions=1,include_dedicated_rest=False))
        p.start(0.);p.tick(5.);p.saved("baseline",5.)
        self.assertIsNone(p.tick(7.999))
        self.assertEqual(P.MI,p.tick(8.))
        self.assertIsNone(p.tick(12.999))
        self.assertEqual(P.SAVE_MI,p.tick(13.))


class HybridWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_ui_applies_layout_only_while_stopped_and_routes_serial_mode(self):
        csv=SimpleNamespace(connected=False,channel_names=LEGACY.names)
        stream=SelectableEEGStream(csv,LSLEEGStream())
        view=MainView(eeg_stream=stream)
        view._activate(f"montage:{HYBRID.id}")
        self.assertEqual(HYBRID.id,view.actions.montage_id)
        self.assertEqual(HYBRID.names,view._eeg_channel_names)
        with patch.object(view.experiment,"start_hybrid") as start:
            view._activate("paradigm-start");start.assert_called_once()
        view._pipeline_worker=SimpleNamespace(isRunning=lambda:True)
        view._activate("montage:posterior8_v1")
        self.assertEqual(HYBRID.id,view.actions.montage_id)
        view._pipeline_worker=None
        view._activate("montage:posterior8_v1")
        self.assertEqual(LEGACY.id,view.actions.montage_id)
        self.assertEqual(LEGACY.names,csv.channel_names)
        view.close()

    def test_early_ack_and_wrong_trial_id_and_preceding_condition(self):
        w=HybridExperimentWindow(Config(repetitions=1,idle_trials=1,include_dedicated_rest=False))
        captures=[];w.capture_requested.connect(captures.append)
        w.resize(800,600);w.show();w.start_experiment();w._timer.stop()
        def present():
            w.repaint();self.app.processEvents();self.app.processEvents()
        def advance():
            self.assertIsNotNone(w.protocol.deadline)
            w._transition(w.protocol.tick(w.protocol.deadline));present()
        present()
        ctx=captures[-1];self.assertEqual("baseline",ctx["stage"])
        self.assertFalse(w.notify_trial_saved("睁眼静息","wrong.npz"))
        self.assertTrue(w.notify_trial_saved("睁眼静息",ctx["trial_id"]+".npz"))
        self.assertEqual(P.BASELINE,w.protocol.phase)
        advance();self.assertEqual(P.CUE,w.protocol.phase)
        self.assertEqual(ctx["trial_id"],w.baseline_trial_id)
        advance();advance()
        ctx=w.metadata();w.notify_trial_saved("空闲",ctx["trial_id"]+".npz",evidence={"p_ssvep":.1});present()
        advance();advance()
        ctx=w.metadata();advance()
        w.notify_trial_saved(ctx["true_label"],ctx["trial_id"]+".npz",evidence={"p_ssvep":.02,"p_ssvep_valid":True});present()
        advance();self.assertEqual(P.MI_CUE,w.protocol.phase)
        advance()
        self.assertEqual(P.MI,w.protocol.phase)
        self.assertEqual(.02,captures[-1]["p_ssvep"])
        self.assertEqual(ctx["trial_id"],captures[-1]["preceding_ssvep_trial_id"])
        w.abort_immediately("test_end");w.deleteLater()


class MIAlgorithmsTest(unittest.TestCase):
    def test_public_reader_uses_cue_plus_two_seconds_and_excludes_eog(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"test.mat"
            x=np.tile(np.arange(1500.)[:,None],(1,25));x[:,22:]=99999
            savemat(path,{"data":np.array([dict(X=x,trial=np.array([1]),y=np.array([1]),fs=250,artifacts=np.array([1]))],dtype=object)})
            d=load_bnci_session(path)
            self.assertEqual((1,22,750),d["eeg"].shape)
            self.assertEqual(500,d["eeg"][0,0,0]);self.assertEqual(1249,d["eeg"][0,0,-1])
            self.assertEqual(0,d["baseline"][0,0,0]);self.assertTrue(d["artifact_flag"][0])

    def test_csp_spatial_signal_generalization_and_serialization(self):
        rng=np.random.default_rng(2);x=rng.normal(size=(80,3,500));y=np.arange(80)%2
        x[y==0,0]*=4;x[y==1,2]*=4
        model=CSPClassifier().fit(x[:60],y[:60]);pred=model.predict(x[60:])
        self.assertGreater(np.mean(pred==y[60:]),.9)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"m.npz";model.save(p,{})
            np.testing.assert_array_equal(pred,CSPClassifier.load(p).predict(x[60:]))
        with self.assertRaises(ValueError):model.predict(x[60:,:,:250])

    def test_no_fake_public_confidence_and_grouped_shuffle(self):
        with self.assertRaises(ValueError):condition_values("true","none_public_MI_unpaired",[.1],[True],[1])
        p=[.1,.2,.8,.9];shuffled,_=condition_values("shuffled","actual_preceding_ssvep",p,[True]*4,[0,0,1,1])
        np.testing.assert_allclose(sorted(shuffled[:2]),p[:2]);np.testing.assert_allclose(sorted(shuffled[2:]),p[2:])
        with self.assertRaises(ValueError):condition_values("true","actual_preceding_ssvep",[.1],[False],[1])

    def test_film_gradients_physics_loss_and_reverse_diffusion_are_finite(self):
        torch.set_num_threads(2);torch.manual_seed(1)
        model=MIDiffusion();x=torch.randn(4,3,250);p=torch.tensor([.1,.9,.2,.8]);mask=torch.ones(4);sub=torch.zeros(4,6)
        loss,terms=model.loss(x,p,mask,sub,x);loss.backward()
        self.assertTrue(torch.isfinite(loss));self.assertGreater(model.network.confidence[0].weight.grad.abs().sum(),0)
        a=model.reconstruct(x,p,mask,sub);b=model.reconstruct(x,p,mask,sub)
        torch.testing.assert_close(a,b);self.assertEqual(x.shape,a.shape);self.assertTrue(torch.isfinite(a).all())


class PairedRecordingTest(unittest.TestCase):
    def test_saved_files_carry_causal_background_and_export_complete_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            rec=ExperimentRecorder(directory)
            stream=SimpleNamespace(source_name="LSL test",flush=lambda:0)
            decoder=SimpleNamespace(reset=lambda:None)
            marker=SimpleNamespace(publish=lambda *a,**k:True,stop=lambda:None)
            worker=EEGWorker(stream,decoder,rec,experiment_clock=ExperimentClock(local_clock=lambda:100.,unix_clock=lambda:1000.),marker_outlet=marker)
            worker.set_experiment_context("fixture_participant", "fixture_session")
            worker.set_paradigm_type("SSVEP+MI混合");worker._channel_names=HYBRID.names
            rng=np.random.default_rng(4);x=rng.normal(size=(8,750))*8
            events=[];worker.recognition_received.connect(events.append)
            clock=10.
            for run in ("hybrid-test-train","hybrid-test-test"):
                def capture(stage,label,trial_id,**extra):
                    nonlocal clock
                    meta=dict(run_id=run,trial_id=trial_id,stage=stage,true_label=label,
                        paradigm_type="SSVEP" if stage=="ssvep" else "MI",montage_id=HYBRID.id,**extra)
                    worker.capture_experiment_trial(label,0.,clock,duration_seconds=3.,metadata=meta)
                    worker._apply_pending_stimulus(None,np.array([]),0)
                    worker._collect_labeled_trial(x,clock+np.arange(750)/250.,250.,750);clock+=4
                    return Path(directory)/"raw_trials"/(trial_id+".npz")
                baseline=run+"-baseline";capture("baseline","睁眼静息",baseline)
                for i in range(8):capture("ssvep","空闲",f"{run}-idle-{i}",record_kind="ssvep_idle")
                pair=run+"-pair-0009"
                ss=capture("ssvep",COMMANDS[0],pair+"-ssvep",pair_id=pair,record_kind="serial_pair",mi_true_label="想象左手")
                with np.load(ss,allow_pickle=False) as z:quality=json.loads(str(z["signal_quality"].item()))
                self.assertEqual(8,quality["bn_idle_windows"]);self.assertTrue(quality["p_ssvep_valid"])
                capture("mi","想象左手",pair+"-mi",pair_id=pair,record_kind="serial_pair",mi_true_label="想象左手",
                    baseline_trial_id=baseline,preceding_ssvep_trial_id=pair+"-ssvep",
                    p_ssvep_valid=True,p_ssvep=quality["p_ssvep"])
            self.assertEqual([],events)
            output=Path(directory)/"epochs.npz"
            export_split(Path(directory)/"raw_trials",["hybrid-test-train"],["hybrid-test-test"],output)
            with np.load(output,allow_pickle=False) as z:
                self.assertEqual((1,3,750),z["train_eeg"].shape)
                self.assertEqual("actual_preceding_ssvep",str(z["condition_source"].item()))
            # Subject/date/type/run folders must remain usable by paired training export.
            nested = Path(directory) / "nested-raw"
            for file in rec.raw_trial_dir.glob("*.npz"):
                with np.load(file, allow_pickle=False) as data:
                    run_id = json.loads(str(data["trial_context"].item()))["run_id"]
                target = nested / "受试者1号" / "2026-11-02" / "SSVEP+MI混合" / run_id
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target / file.name)
            nested_output = Path(directory) / "nested-epochs.npz"
            export_split(nested, ["hybrid-test-train"], ["hybrid-test-test"], nested_output)
            with np.load(output) as flat, np.load(nested_output) as grouped:
                for field in ("train_eeg", "test_eeg", "train_p_aug", "train_p_aug_valid"):
                    np.testing.assert_array_equal(flat[field], grouped[field])
            with self.assertRaises(ValueError):export_split(directory,["same"],["same"],output)
            mi_path=Path(directory)/"raw_trials/hybrid-test-test-pair-0009-mi.npz"
            with np.load(mi_path,allow_pickle=False) as z:changed={k:z[k] for k in z.files}
            context=json.loads(str(changed["trial_context"].item()));context["p_ssvep"]=-.1
            changed["trial_context"]=json.dumps(context);np.savez_compressed(mi_path,**changed)
            with self.assertRaisesRegex(ValueError,"P does not match"):
                export_split(Path(directory)/"raw_trials",["hybrid-test-train"],["hybrid-test-test"],output)


if __name__=="__main__":unittest.main()
