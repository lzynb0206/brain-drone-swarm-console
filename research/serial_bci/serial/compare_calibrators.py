"""Development-only test of richer public confidence features on real pairs."""
import csv
import json
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from scipy.special import expit, logit

from research.serial_bci.serial.run import binary_fit, load, metrics
from research.serial_bci.mi.run_mi import CSPClassifier, csp_right
from research.serial_bci.ssvep.literature_comparator import features, fit_gap_ridge, frozen_gap_logit
from research.serial_bci.ssvep.validate import baseline, decisions, write_csv

HERE=Path(__file__).resolve().parent
METHODS=('source_margin_intercept','source_five_intercept','personal_five_ridge')


def predict(scores, f, block, tr, te, scalar_model, vector_model):
    def adjusted(a,b):
        return scores[b]-baseline(scores[a],f[a],'train_nontarget')
    corrected=np.empty_like(scores[tr])
    for b in np.unique(block[tr]):
        hold=block[tr]==b
        corrected[hold]=adjusted(tr[~hold],tr[hold])
    evaluation=adjusted(tr,te)
    event=corrected.argmax(1)==f[tr]
    def logits(z):
        _,margin=decisions(z,np.zeros(6),'raw')
        return (scalar_model['intercept']+scalar_model['slope']*margin,
                frozen_gap_logit(z,vector_model))
    outputs={}
    for name,raw,ev in zip(METHODS[:2],logits(corrected),logits(evaluation)):
        shift=brentq(lambda d:float((expit(raw+d)-event).sum()+d),-len(event)-1,len(event)+1)
        outputs[name]=expit(ev+shift)
    if np.unique(event).size==1:
        outputs[METHODS[2]]=np.full(len(te),(event.sum()+1)/(len(event)+2))
    else:
        ridge=fit_gap_ridge(corrected,event)
        outputs[METHODS[2]]=ridge.predict_proba(features(evaluation))[:,1]
    return outputs


def evaluate_richer(raw, out, scalar_path):
    vector_path=HERE.parent/'ssvep/comparator_results/benchmark_models.json'
    scalar=json.loads(scalar_path.read_text())['1.5:train_nontarget']
    vector=json.loads(vector_path.read_text())
    summary=json.loads((out/'summary.json').read_text())
    with (out/'trials.csv').open() as f:
        saved={r['pair']:r for r in csv.DictReader(f)}
    trial_rows=[]
    for split in summary['splits']:
        if 'test' not in split:
            continue
        matches=[p for p in raw.rglob('hybrid-*-ssvep.npz') if p.parent.name==split['run']]
        if not matches:
            raise ValueError('Raw trial sources missing')
        records,_,_=load(matches[0].parent)
        s=np.array([r['scores'] for r in records])
        f=np.array([r['true_hz']-8 for r in records])
        b=np.array([r['block'] for r in records])
        tr,te=np.flatnonzero(b<=4),np.flatnonzero(b>4)
        estimates=predict(s,f,b,tr,te,scalar,vector)
        x=np.stack([r['mi'] for r in records])
        y=np.array([r['hand'] for r in records])
        q0=np.zeros(len(tr))
        p0={k:np.zeros(len(tr)) for k in METHODS}
        for block in np.unique(b[tr]):
            hold=b[tr]==block
            train,test=tr[~hold],tr[hold]
            q0[hold]=csp_right(CSPClassifier().fit(x[train],y[train]),x[test])
            po=predict(s,f,b,train,test,scalar,vector)
            for k in METHODS:
                p0[k][hold]=po[k]
        q=np.array([float(saved[records[i]['pair']]['q_right']) for i in te])
        ztrain=logit(np.clip(q0,1e-5,1-1e-5))
        ztest=logit(np.clip(q,1e-5,1-1e-5))
        hand={}
        for k in METHODS:
            t=p0[k]-.5
            v=estimates[k]-.5
            hand[k]=binary_fit(np.column_stack((ztrain,t,ztrain*t)),y[tr],np.column_stack((ztest,v,ztest*v)))
        for j,index in enumerate(te):
            r=saved[records[index]['pair']]
            row={k:r[k] for k in ('subject','run','pair','ss_correct','hand','joint','q_right','hand_mi_only','product','no_p','p')}
            for k in METHODS:
                row[k]=estimates[k][j]
                row[k+'_hand']=hand[k][j]
            trial_rows.append(row)
    write_csv(out/'richer_trials.csv',trial_rows)
    def aggregate(rows):
        def col(k):
            return np.array([float(r[k]) for r in rows])
        q=col('q_right')
        truth=col('joint')
        result={}
        for k in METHODS:
            p=col(k)
            result[k]={'ss':metrics(col('ss_correct'),p),
                       'joint':metrics(truth,p*np.maximum(q,1-q)),
                       'hand_correct':int(sum((col(k+'_hand')>.5)==col('hand'))),
                       'hand':metrics(col('hand'),col(k+'_hand'))}
        return result
    report={'pooled':aggregate(trial_rows),'by_person':{k:aggregate([r for r in trial_rows if r['subject']==k]) for k in sorted({r['subject'] for r in trial_rows})}}
    (out/'richer_summary.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    with (out/'结果表.md').open('a') as file:
        print('\n## 五维置信度补充分析\n',file=file)
        print('| 校准器 | 视觉正确性 Brier | 联合 Brier | 加 P 后手别判对数 |\n|---|---:|---:|---:|',file=file)
        for k,v in report['pooled'].items():
            print(f"| {k} | {v['ss']['brier']:.5f} | {v['joint']['brier']:.5f} | {v['hand_correct']}/{len(trial_rows)} |",file=file)
