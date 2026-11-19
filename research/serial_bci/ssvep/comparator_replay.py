"""Hold MI predictions and every cross-corpus pair fixed while changing the SSVEP calibrator."""
import json
import sys
from pathlib import Path

import numpy as np

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'paired'))
from independent_replay import csv_rows, summarize, independent_bootstrap


def run():
    out=HERE/'comparator_results'
    inputs=csv_rows(out/'trials.csv')
    main=csv_rows(HERE/'results/trials.csv')
    reference={(int(r['subject']),int(r['block']),int(r['true_hz'])):r for r in main if r['dataset']=='beta'
               and r['split']=='benchmark_to_beta' and r['seconds']=='1.5' and r['variant']=='train_nontarget'}
    assert len(reference)==len(inputs)==1680
    observed=[]
    for row in inputs:
        key=tuple(int(row[k]) for k in ['subject','block','true_hz'])
        ref=reference.pop(key)
        assert ref['pred_hz']==row['pred_hz'] and ref['correct']==row['correct']
        assert np.isclose(float(ref['p_platt']),float(row['platt']))
        observed.append(float(ref['p_prior']))
    assert not reference and np.unique(observed).size==1
    p0=observed[0]
    mi=csv_rows(HERE.parent/'mi/results/predictions.csv')
    assert len({(r['subject'],r['session'],r['trial']) for r in mi})==len(mi)==1296
    right=np.array([float(r['p_right_csp']) for r in mi])
    mi_ok=(right>.5)==np.array([int(r['y_true']) for r in mi])
    ss_ok=np.array([bool(int(r['correct'])) for r in inputs])
    ss_ids=np.array([r['subject'] for r in inputs])
    mi_ids=np.array([r['subject'] for r in mi])
    q=np.maximum(right,1-right)
    actual=ss_ok[:,None]&mi_ok
    methods=['no_p','platt','isotonic','ridge_five_differences','gmm_bayes']
    metrics,losses={},{}
    for method in methods:
        p=np.full(len(inputs),p0) if method=='no_p' else np.array([float(r[method]) for r in inputs])
        metrics[method],brier,nll=summarize(p[:,None]*q,actual,ss_ids,mi_ids)
        losses[method]=(brier,nll)
    baseline=json.loads((HERE.parent/'paired/independent_replay.json').read_text())['variants']['train_nontarget']
    for arm,original in [('platt','p_times_q'),('no_p','prior_s_times_q')]:
        for k,val in metrics[arm].items():
            assert np.isclose(val,baseline['metrics'][original][k])
    deltas={}
    for arm in methods[1:]:
        for control in ('platt','no_p'):
            if arm==control:
                continue
            delta={}
            rng=np.random.default_rng(1192026)
            for idx,key in enumerate(['brier','binary_nll']):
                d=losses[arm][idx]-losses[control][idx]
                delta[key]=dict(mean=float(d.mean()),ci95=independent_bootstrap(d,rng))
            delta['accuracy_at_coverage_change']=metrics[arm]['accuracy_at_coverage']-metrics[control]['accuracy_at_coverage']
            deltas[f'{arm}_minus_{control}']=delta
    result=dict(design='Independent Cartesian product. No measured serial MI or label transfer.',
                n_visual=len(inputs),n_mi=len(mi),product_cells=int(actual.size),correct_pairs=int(actual.sum()),
                coverage=.8,source_s_correct_prior=p0,metrics=metrics,comparisons=deltas)
    (out/'independent_replay.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    run()
