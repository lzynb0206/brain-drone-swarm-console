import tempfile
import unittest
from pathlib import Path
import numpy as np
import torch
import json
import subprocess
import sys
from src.ml.mi_filterbank import FilterBankCSP,filter_mi_banks
from src.ml.mi_diffusion import MIDiffusion
from src.ml.mi_research import RestDescriptor,calibrate_erd,calibrated_diffusion_loss
from src.ml.confidence_augmentation import expand_training_conditions
from src.ml.research_statistics import paired_effect,mcnemar_exact,ability_interaction
from src.services.research_feedback_policy import ResearchFeedbackPolicy
from src.ml.mi_research_pipeline import MIResearchPipeline
from scripts.research_paired_mi import audit_data


class ResearchExtensionsTest(unittest.TestCase):
    def test_filterbank_spatial_generalization_serialization_and_contract(self):
        rng=np.random.default_rng(41);x=rng.normal(size=(60,3,500));y=np.arange(60)%2
        x[y==0,0]*=4;x[y==1,2]*=4
        model=FilterBankCSP().fit(x[:40],y[:40]);pred=model.predict(x[40:])
        self.assertGreater((pred==y[40:]).mean(),.9)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"model.npz";model.save(path,{"train_only":True})
            np.testing.assert_array_equal(pred,FilterBankCSP.load(path).predict(x[40:]))
        with self.assertRaises(ValueError):model.predict(x[40:,:,:250])

    def test_single_pass_is_deterministic_and_calls_network_exactly_once(self):
        torch.set_num_threads(2);model=MIDiffusion(subject_features=9);x=torch.randn(2,3,500)
        calls=[];handle=model.network.register_forward_hook(lambda *args:calls.append(1))
        args=(x,torch.zeros(2),torch.zeros(2),torch.zeros(2,9))
        a=model.single_pass(*args);self.assertEqual(1,len(calls))
        b=model.single_pass(*args);torch.testing.assert_close(a,b);handle.remove()

    def test_rest_normalization_does_not_fit_test_and_exposes_missing_fields(self):
        rng=np.random.default_rng(2);x=rng.normal(size=(10,3,500));enc=RestDescriptor().fit(x)
        center=enc.center.copy();features=enc.transform(x[:2]*5)
        np.testing.assert_array_equal(center,enc.center);self.assertEqual((2,9),features.shape)
        self.assertTrue(np.isfinite(features).all());self.assertFalse(enc.metadata()["EOG_template_available"])

    def test_erd_strength_comes_from_training_ability_and_phys_gradients_finite(self):
        rng=np.random.default_rng(3);x=rng.normal(size=(8,3,500));rest=rng.normal(size=(8,3,500));y=np.arange(8)%2
        weak=calibrate_erd(x,rest,y,.5);strong=calibrate_erd(x,rest,y,.8)
        self.assertEqual(0,weak["strength"]);self.assertAlmostEqual(.6,strong["strength"])
        model=MIDiffusion(subject_features=9)
        loss,_=calibrated_diffusion_loss(model,torch.tensor(x,dtype=torch.float32),torch.zeros(8),torch.zeros(8),
            torch.zeros(8,9),torch.tensor(rest,dtype=torch.float32),torch.tensor(y),strong)
        loss.backward();self.assertTrue(torch.isfinite(loss))

    def test_confidence_augmentation_preserves_test_and_pair_groups(self):
        test=np.arange(12).reshape(2,2,3)
        d=dict(condition_source=np.asarray("actual_preceding_ssvep"),train_y=np.array([0,1]),
            train_eeg=test.copy(),test_eeg=test,train_pair_id=np.array(["a","b"]),train_run=np.array(["r","r"]),
            train_p_aug=np.array([[.1,.2,.3],[.4,.5,.6]]),train_p_aug_valid=np.array([[1,1,1],[1,0,1]],bool))
        result=expand_training_conditions(d);self.assertIs(test,result["test_eeg"])
        self.assertEqual([0,0,0,1,1],result["train_y"].tolist())
        self.assertEqual(["a","a","a","b","b"],result["train_pair_id"].tolist())
        d["condition_source"]=np.asarray("public_unpaired")
        with self.assertRaises(ValueError):expand_training_conditions(d)

    def test_effects_resample_people_and_small_subgroup_is_not_sufficient(self):
        result=paired_effect([.5,.6,.7],[.6,.7,.8]);self.assertAlmostEqual(.1,result["mean_gain"])
        result=ability_interaction([.5,.55,.7,.8],[.5,.6,.7,.8],[.6,.65,.72,.81])
        self.assertEqual(2,result["low_ability_count"]);self.assertFalse(result["low_ability_subset_sufficient"])
        self.assertEqual({"fixed":1,"harmed":0,"p":1.},mcnemar_exact([0,1],[1,1],[0,1]))

    def test_passive_feedback_requires_repeated_low_mi_and_handles_stale_input(self):
        p=ResearchFeedbackPolicy()
        self.assertEqual("visual_refocus",p.update(.3,.4,0).display_state)
        self.assertEqual("observe",p.update(.9,.4,1).display_state)
        result=p.update(.9,.4,2);self.assertEqual("mi_guidance_candidate",result.display_state)
        self.assertTrue(result.simulation_only)
        self.assertEqual("check_signal",p.update(None,.4,3).display_state)
        with self.assertRaises(ValueError):p.update(.9,.4,3)

    def test_paired_runner_refuses_unpaired_and_roundtrips_research_inference(self):
        # Synthetic contract fixture only; never retained as scientific evidence.
        with self.assertRaises(ValueError):audit_data({})
        rng=np.random.default_rng(55);d=dict(fs=250.,channel_names=np.array(["C3","Cz","C4"]),
            condition_source=np.asarray("actual_preceding_ssvep"),task_labels=np.array(["想象左手","想象右手"]))
        for split,n in (("train",24),("test",8)):
            y=np.arange(n)%2;x=rng.normal(size=(n,3,250));x[y==0,0]*=3;x[y==1,2]*=3
            d.update({f"{split}_eeg":x,f"{split}_baseline":rng.normal(size=x.shape),f"{split}_y":y,
                f"{split}_p":rng.uniform(.1,.9,n),f"{split}_p_valid":np.ones(n,bool),
                f"{split}_signal_valid":np.ones(n,bool),f"{split}_pair_id":np.array([f"{split}{i}" for i in range(n)]),
                f"{split}_subject_id":np.array(["fixture_participant"]*n),
                f"{split}_run":np.array([f"{split}{i//8}" for i in range(n)])})
        d["test_signal_valid"][-1]=False
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);data=folder/"fixture.npz";np.savez_compressed(data,**d);out=folder/"output"
            completed=subprocess.run([sys.executable,"scripts/research_paired_mi.py","--data",str(data),"--out",str(out),
                "--epochs","1","--seeds","55","--variants","none","true","shuffled"],capture_output=True,text=True)
            self.assertEqual(0,completed.returncode,completed.stderr)
            report=json.loads((out/"report.json").read_text())
            self.assertTrue(all(r["total"]==8 and r["predictions"][-1]==-1 for r in report["results"]))
            pipe=MIResearchPipeline(out/"true_55")
            raw=d["test_eeg"][:-1];features=pipe.prepare_rest(d["test_baseline"][:-1])
            with self.assertRaises(ValueError):pipe.predict(raw,rest_features=features)
            pred=pipe.predict(raw,rest_features=features,preceding_p=d["test_p"][:-1],p_source="actual_preceding_ssvep")
            expected=next(r["predictions"][:-1] for r in report["results"] if r["method"]=="true")
            np.testing.assert_array_equal(pred,expected)


if __name__=="__main__":unittest.main()
