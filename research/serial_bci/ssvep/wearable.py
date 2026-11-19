"""External study of FBCCA on paired dry and wet electrode recordings."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import time
from pathlib import Path

import h5py
import numpy as np
from scipy.optimize import brentq
from scipy.special import expit
from sklearn.metrics import roc_auc_score

from validate import ROOT, HERE, fbcca, fit, apply, decisions, baseline, metrics, write_csv

FREQUENCIES = (9.25,11.25,13.25,9.75,11.75,13.75,10.25,12.25,14.25,10.75,12.75,14.75)
ARMS = ('source_raw', 'source_background', 'personal_background', 'personal_background_intercept', 'personal_raw_intercept')


def load_scores(path, corpus):
    with h5py.File(path) as f:
        assert tuple(json.loads(f.attrs['frequencies_hz_json'])) == FREQUENCIES
        assert json.loads(f.attrs['common_channels_json']) == ['O1','OZ','O2','PO3','POZ','PO4']
        assert f.attrs['sample_rate_hz']==250
        labels, person, device, block = (f[key][:] for key in ('label','subject','electrode_type','block'))
        assert np.array_equal(np.asarray(FREQUENCIES)[labels], f['frequency_hz'][:])
        assert len(set(zip(person,device,block,labels)))==len(labels)
        assert len(labels)==(1800 if corpus=='nakanishi' else 24480)
        scores = np.empty((len(labels),len(FREQUENCIES)))
        for start in range(0,len(labels),128):
            windows = f['eeg'][start:start+128,:,:375]
            scores[start:start+len(windows)] = [fbcca(x,250.,FREQUENCIES) for x in windows]
    return scores, labels, person, device, block


def intercept_update(model, logits, correct):
    # MAP shift with fixed source slope and N(0,1) prior.
    delta=brentq(lambda d: (expit(logits+d)-correct).sum()+d,-30.,30.)
    return dict(model,intercept=model['intercept']+delta)


def personal_models(x,y,block,source,raw):
    # Two labeled blocks, 12 targets each. For each calibration trial, use
    # a background learned only from the other block to predict correctness.
    if not np.array_equal(np.unique(block),[1,2]) or len(x)!=24:
        raise ValueError('Expected exactly the first two complete labeled blocks')
    variant = 'train_nontarget'
    full = dict(source, offset=baseline(x,y,variant).tolist())
    logits = np.empty(len(y))
    correct = np.zeros(len(y),dtype=bool)
    for b in (1,2):
        val = block==b
        pred,margin = decisions(x[val],baseline(x[~val],y[~val],variant),variant)
        correct[val] = pred==y[val]
        logits[val] = source['intercept'] + source['slope']*margin
    raw_pred, raw_margin = decisions(x,np.zeros(x.shape[1]),'raw')
    raw_logits = raw['intercept'] + raw['slope']*raw_margin
    return full,intercept_update(full,logits,correct),intercept_update(raw,raw_logits,raw_pred==y)


def summarize(pred, y, margin, prob, person):
    z=(pred==y).astype(float)
    summary=dict(n=len(y),correct=int(z.sum()),accuracy=z.mean(),mean_p=prob.mean(),
                 **metrics(z,prob),margin_brier=np.mean((margin-z)**2))
    aucs=[]
    for s in np.unique(person):
        take=person==s
        if np.unique(z[take]).size==2:
            aucs.append(roc_auc_score(z[take],prob[take]))
    summary.update(n_with_both_outcomes=len(aucs),mean_within_person_auroc=np.mean(aucs))
    for tag,mask in [('low',prob<.7),('high',prob>=.9)]:
        summary[tag+'_n']=int(mask.sum())
        summary[tag+'_correct']=int(z[mask].sum())
        summary[tag+'_acc']=z[mask].mean() if np.any(mask) else None
    return summary


def bootstrap(contrasts):
    rng=np.random.default_rng(20261119)
    rows=[]
    for (device,change,metric),diff in contrasts.items():
        idx=rng.integers(0,len(diff),size=(10000,len(diff)))
        lower,upper=np.quantile(diff[idx].mean(1),[.025,.975])
        rows.append(dict(device=device,contrast=change,metric=metric,
                         mean=np.mean(diff),lo95=lower,hi95=upper,subjects=len(diff)))
    return rows


def run(root,out):
    start=time.monotonic()
    out.mkdir(parents=True,exist_ok=True)
    paths={name:root/f'{name}_common6_2s.h5' for name in ('nakanishi','wearable')}
    src,ys,ss,_,_=load_scores(paths['nakanishi'],'nakanishi')
    raw=fit(src,ys,ss,'raw')
    bg=fit(src,ys,ss,'train_nontarget')
    x,y,person,device,block=load_scores(paths['wearable'],'wearable')
    # Identical target block split for all four conditions.
    test=block>=3
    groups,summary,subject_summary,contrasts={},[],[],{}
    with gzip.open(out/'trials.csv.gz','wt',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['subject','device','block','target_hz','arm','pred_hz','correct','margin','p'])
        writer.writeheader()
        for code,device_name in [(1,'dry'),(2,'wet')]:
            mask=(device==code)&test
            ids=np.unique(person[mask])
            assert len(ids)==102 and mask.sum()==9792
            pred=np.empty((len(ARMS),len(y)),dtype=int)
            prob=np.empty((len(ARMS),len(y)))
            margin=np.empty_like(prob)
            for s in ids:
                unit=(person==s)&(device==code)
                early=unit&~test
                late=unit&test
                assert set(y[early])==set(range(12)) and late.sum()==96
                personal,updated,raw_updated=personal_models(x[early],y[early],block[early],bg,raw)
                for a,m in enumerate([raw,bg,personal,updated,raw_updated]):
                    variant='raw' if a in (0,4) else 'train_nontarget'
                    pred[a,late],ps=apply(x[late],variant,m)
                    margin[a,late],prob[a,late]=ps['margin'],ps['platt']
            for a,arm in enumerate(ARMS):
                row=summarize(pred[a,mask],y[mask],margin[a,mask],prob[a,mask],person[mask])
                summary.append(dict(device=device_name,arm=arm,**row))
                cond=[]
                for s in ids:
                    w=mask&(person==s)
                    z=pred[a,w]==y[w]
                    acc,brier=float(z.mean()),float(np.mean((z-prob[a,w])**2))
                    cond.append((acc,brier))
                    subject_summary.append(dict(subject=int(s),device=device_name,arm=arm,accuracy=acc,brier=brier))
                groups[(device_name,arm)]=np.array(cond)
                for i in np.flatnonzero(mask):
                    writer.writerow(dict(subject=int(person[i]),device=device_name,block=int(block[i]),target_hz=FREQUENCIES[y[i]],
                        arm=arm,pred_hz=FREQUENCIES[pred[a,i]],correct=int(pred[a,i]==y[i]),margin=margin[a,i],p=prob[a,i]))
            for control,arm in [(ARMS[0],ARMS[1]),(ARMS[1],ARMS[2]),(ARMS[2],ARMS[3]),(ARMS[1],ARMS[3]),(ARMS[0],ARMS[4]),(ARMS[4],ARMS[3])]:
                change=f'{arm}_minus_{control}'
                for j,metric in enumerate(['accuracy','brier']):
                    contrasts[device_name,change,metric]=(groups[device_name,arm]-groups[device_name,control])[:,j]
    for arm in ARMS:
        for j,metric in enumerate(['accuracy','brier']):
            contrasts['dry_minus_wet',arm,metric]=(groups['dry',arm]-groups['wet',arm])[:,j]
    write_csv(out/'metrics.csv',summary)
    write_csv(out/'participant_effects.csv',bootstrap(contrasts))
    write_csv(out/'subject_metrics.csv',subject_summary)
    out.joinpath('source_models.json').write_text(json.dumps(dict(source_raw=raw,source_background=bg),indent=2)+'\n')
    out.joinpath('run.json').write_text(json.dumps(dict(seconds=time.monotonic()-start,source='Nakanishi',
        target='Wearable',seconds_per_epoch=1.5,source_trials=len(src),target_trials=len(x),test_trials=int(test.sum()),
        calibration_blocks=[1,2],test_blocks=list(range(3,11)),frequencies=FREQUENCIES,
        sha256={k:hashlib.sha256(path.read_bytes()).hexdigest() for k,path in paths.items()}),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--processed',type=Path,default=ROOT/'data/public_ssvep/processed')
    parser.add_argument('--out',type=Path,default=HERE/'wearable_results')
    args=parser.parse_args()
    run(args.processed,args.out)
