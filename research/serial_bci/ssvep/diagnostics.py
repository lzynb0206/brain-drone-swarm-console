"""Paired participant bootstrap and fixed-threshold audit for the 1.5 s window."""
from pathlib import Path
import argparse
import csv
import numpy as np

from validate import write_csv


def diagnostics(out):
    with (out/'trials.csv').open() as handle:
        rows = [r for r in csv.DictReader(handle) if float(r['seconds'])==1.5]
    effects, gates = [], []
    for dataset,split in sorted(set((r['dataset'],r['split']) for r in rows)):
        data = [r for r in rows if r['dataset']==dataset and r['split']==split]
        by_variant = {v:sorted((r for r in data if r['variant']==v),key=lambda r:r['trial_id'])
                      for v in ('raw','train_nontarget')}
        raw, corrected = by_variant.values()
        assert [r['trial_id'] for r in raw]==[r['trial_id'] for r in corrected]
        ids = np.array([int(r['subject']) for r in raw])
        subjects = np.unique(ids)
        target = np.array([int(r['true_hz']) for r in raw])
        v = {}
        for name,group in by_variant.items():
            y = np.array([int(r['correct']) for r in group])
            pred = np.array([int(r['pred_hz']) for r in group])
            p = np.array([float(r['p_platt']) for r in group])
            iso = np.array([float(r['p_isotonic']) for r in group])
            m = np.array([float(r['margin']) for r in group])
            prior = np.array([float(r['p_prior']) for r in group])
            v[name]=dict(accuracy=y,fpr8=(pred[target!=8]==8).astype(float),
                         fnr8=(pred[target==8]!=8).astype(float),
                         platt_brier=(p-y)**2,iso_brier=(iso-y)**2,
                         margin_brier=(m-y)**2,prior_brier=(prior-y)**2)
            for threshold in (0.,.7,.8,.9,.95):
                accept=p>=threshold
                n=int(np.sum(accept))
                gates.append(dict(dataset=dataset,split=split,variant=name,threshold=threshold,
                    total=len(y),accepted=n,coverage=n/len(y),acc_accepted=float(y[accept].mean()) if n else '',
                    fp8_accepted=int(np.sum(accept&(pred==8)&(target!=8))),non8_total=int(np.sum(target!=8)),
                    incorrect_accepted=int(np.sum((y==0)&accept)),correct_rejected=int(np.sum((y==1)&~accept))))
        a,b = v.values()
        comparisons = {'corrected_minus_raw_accuracy':(b['accuracy']-a['accuracy'],ids),
            'corrected_minus_raw_8hz_fpr':(b['fpr8']-a['fpr8'],ids[target!=8]),
            'corrected_minus_raw_8hz_fnr':(b['fnr8']-a['fnr8'],ids[target==8]),
            'corrected_minus_raw_platt_brier':(b['platt_brier']-a['platt_brier'],ids),
            'corrected_platt_minus_same_prediction_margin_brier':(b['platt_brier']-b['margin_brier'],ids),
            'corrected_platt_minus_same_prediction_prior_brier':(b['platt_brier']-b['prior_brier'],ids),
            'corrected_platt_minus_same_prediction_isotonic_brier':(b['platt_brier']-b['iso_brier'],ids)}
        generator=np.random.default_rng(20261119)
        boot=generator.integers(0,len(subjects),(10000,len(subjects)))
        for k,(diff,person) in comparisons.items():
            paired=np.array([diff[person==s].mean() for s in subjects])
            low,high=np.quantile(paired[boot].mean(axis=1),[.025,.975])
            effects.append(dict(dataset=dataset,split=split,contrast=k,subjects=len(paired),
                                mean=float(paired.mean()),lo95=float(low),hi95=float(high)))
    write_csv(out/'participant_effects.csv',effects)
    write_csv(out/'selective.csv',gates)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path(__file__).parent/'results')
    diagnostics(parser.parse_args().out)
