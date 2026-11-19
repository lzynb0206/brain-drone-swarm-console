import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from scripts.benchmark_subject24_audited import (
    COMMANDS, RUNS, active, emit_fold, metrics, outer_folds, split_groups,
    trca_fit, trca_scores,
)


class AuditedBenchmarkTest(unittest.TestCase):
    def test_group_splits_do_not_leak_and_cover_every_trial(self):
        records=[]
        for run,count in zip(RUNS,(9,15)):
            for block in range(count):
                for label in range(7):
                    records.append(dict(run_id=run,group=f"{run}:{block}",label_index=label-1))
        folds=outer_folds(records)
        covered=[]
        for name,train,test in folds:
            groups=lambda ix:{records[i]["group"] for i in ix}
            self.assertFalse(groups(train)&groups(test))
            fit,val=split_groups(records,train,.25,23)
            self.assertFalse(groups(fit)&groups(val))
            self.assertEqual(set(fit)|set(val),set(train))
            if name.startswith("grouped"):covered.extend(test)
        self.assertEqual(sorted(covered),list(range(len(records))))
        name,train,test=folds[-1]
        self.assertTrue(all(records[i]["run_id"]==RUNS[0] for i in train))
        self.assertTrue(all(records[i]["run_id"]==RUNS[1] for i in test))

    def test_rejection_counts_as_failed_active_command(self):
        truth=np.array([-1,-1,0,1,-1])
        scores=np.array([[.6,.1,.1,.1,.05,.05],[.7,.1,.05,.05,.05,.05],
                         [.65,.1,.1,.05,.05,.05],[.8,.04,.04,.04,.04,.04],
                         [.75,.05,.05,.05,.05,.05]])
        records=[dict(source_file=str(i),run_id=RUNS[0],group=str(i),
            true_label=COMMANDS[y] if y>=0 else "空闲",usable_channels=8) for i,y in enumerate(truth)]
        with tempfile.TemporaryDirectory() as tmp:
            rows=emit_fold("grouped_fold_1","dummy",scores,SimpleNamespace(truth=truth),records,
                           np.array([0,1]),np.array([2,3,4]),Path(tmp))
        m=metrics_without_empty_classes(rows)
        self.assertEqual(m,(1,0,1,1))  # one top-1 correct, rejected; accepted wrong; idle false alarm
        self.assertAlmostEqual(rows[0]["threshold"],.7)
        # Held-out idle scores cannot affect the threshold.
        self.assertEqual(rows[0]["final_label"],"空闲")
        self.assertEqual(rows[1]["final_label"],COMMANDS[0])

    def test_metrics_class_mapping_and_idle_denominator(self):
        rows=[]
        for k,label in enumerate(COMMANDS):
            rows.append(dict(true_label=label,candidate_label=label,active_correct=1,
                accepted=k!=0,emitted_correct=int(k!=0),idle_false_accept=0,seven_state_correct=int(k!=0)))
        rows.append(dict(true_label="空闲",candidate_label=COMMANDS[0],active_correct=0,accepted=False,
                         emitted_correct=0,idle_false_accept=0,seven_state_correct=1))
        m=metrics(rows)
        self.assertEqual(m["active_total"],6)
        self.assertEqual(m["forced_six_accuracy"],1)
        self.assertAlmostEqual(m["command_success_rate"],5/6)
        self.assertAlmostEqual(m["seven_state_accuracy"],6/7)
        self.assertEqual(m["accepted_precision"],1)
        self.assertEqual(m["confusion_matrix"],np.column_stack((np.eye(6,dtype=int),np.zeros(6,int))).tolist())

    def test_trca_inputs_remain_unchanged_and_scores_are_independent(self):
        rng=np.random.default_rng(8)
        banks=rng.normal(size=(18,3,4,100)).astype(np.float32)
        before=banks.copy();truth=np.tile(np.arange(6),3)
        model=trca_fit(banks,truth,np.arange(12))
        vectorized=trca_scores(banks,np.arange(12,18),model)
        manual=np.zeros((6,6))
        for i,idx in enumerate(range(12,18)):
            for band in range(3):
                for cls in range(6):
                    a=(model[1][band].T@banks[idx,band]).ravel()
                    b=(model[1][band].T@model[0][band,cls]).ravel()
                    r=np.corrcoef(a,b)[0,1]
                    manual[i,cls]+=((band+1)**-1.25+.25)*np.sign(r)*r*r
        np.testing.assert_allclose(manual,vectorized,atol=1e-12)
        np.testing.assert_array_equal(before,banks)


def metrics_without_empty_classes(rows):
    return (sum(r["active_correct"] for r in rows),sum(r["emitted_correct"] for r in rows),
            sum(r["accepted"] for r in rows if r["true_label"]!="空闲"),sum(r["idle_false_accept"] for r in rows))


if __name__=="__main__":unittest.main()
