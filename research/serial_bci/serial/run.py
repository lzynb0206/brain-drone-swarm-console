"""Offline trial-aligned SSVEP–MI evaluation for the console's raw NPZ format."""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.bci.config import LABEL_TO_FREQUENCY
from src.bci.montage import MONTAGES
from src.ml.hybrid_pairs import read_stage
from src.ml.mi_baseline import CSPClassifier
from research.serial_bci.serial.report import write_report
from research.serial_bci.serial.transfer_weight import decomposition, weight_from_losses
from research.serial_bci.mi.run_mi import csp_right
from research.serial_bci.ssvep.validate import FREQ, apply, baseline, decisions, fbcca, metrics

METHODS = ('prior', 'no_p', 'ss_only', 'margin_product', 'product', 'joint_logistic', 'weighted_posthoc')
HANDS = ('想象左手', '想象右手')


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def binary_fit(x, y, test):
    # The smoothed fallback covers all-correct or all-incorrect calibration blocks.
    if len(np.unique(y)) == 1:
        return np.full(len(test), (y.sum()+1)/(len(y)+2))
    return LogisticRegression(C=1, max_iter=500).fit(x, y).predict_proba(test)[:, 1]


def stage_ok(s):
    t = s['timestamps']
    return (s['fs'] == 250 and np.isfinite(t).all() and len(t) >= 750
            and np.all(np.diff(t) > 0) and np.max(np.diff(t)) <= .1)


def waveform(s, modality, offset=0):
    ctx = s['context']
    montage = MONTAGES[ctx['montage_id']]
    if tuple(ctx['channel_names']) != montage.names:
        raise ValueError('context_channel_order')
    indices = montage.indices(montage.names, modality)
    first, last = (35+offset, 410+offset) if modality == 'SSVEP' else (0, 750)
    # Timestamps are in the capture clock. Align the window to stimulus onset.
    times = s['timestamps'] - float(ctx['stimulus_lsl_time'])
    if times[0] > first/250 + .025 or times[-1] < (last-1)/250 - .025:
        raise ValueError('stage_does_not_cover_window')
    at = max(0, int(np.searchsorted(times, first/250)))
    x = s['eeg'][list(indices), at:at + last-first]
    if x.shape[1] != last-first or not np.isfinite(x).all() or np.any(np.std(x, axis=1)<1e-12):
        raise ValueError('flat_nonfinite_or_short')
    actual = times[at:at+last-first]
    if (not np.isfinite(actual).all() or np.max(np.diff(actual))>.1
            or abs((actual[-1]-actual[0])-(last-first-1)/250)>.035):
        raise ValueError('window_clock')
    return x


def load(folder, max_block=None):
    records, idle, audit = [], [], []
    mi_paths = {p.stem for p in folder.glob('*-mi.npz')}
    seen = set()
    for path in sorted(folder.glob('*-ssvep.npz')):
        try:
            ss = read_stage(path)
        except (ValueError, KeyError, OSError) as e:
            audit.append(dict(run=folder.name,trial=path.stem,kind='unreadable_SS',status=str(e)))
            continue
        sc = ss['context']
        if max_block is not None and sc.get('block_index',0)>max_block:
            continue
        kind = sc['record_kind']
        if kind not in ('ssvep_idle', 'serial_pair') or sc.get('acquisition_purpose') != 'formal':
            continue
        item = dict(run=folder.name, trial=path.stem, kind=kind, status='')
        audit.append(item)
        try:
            if not stage_ok(ss):
                raise ValueError('ss_clock_or_rate')
            s = fbcca(waveform(ss, 'SSVEP'))
            if kind == 'ssvep_idle':
                if sc['block_index'] == 0 and sc.get('idle_targets_flicker') is True:
                    idle.append((ss['timestamps'][-1], s,ss['subject_id'],sc['run_id'],sc['montage_id'],sc['clock_source']))
                item['status'] = 'idle'
                continue
            pair = sc['pair_id']
            if pair in seen:
                raise RuntimeError('Duplicate pair identifier')
            seen.add(pair)
            partner = folder/(pair+'-mi.npz')
            if partner.stem not in mi_paths:
                raise ValueError('no_MI')
            mi = read_stage(partner)
            mc = mi['context']
            if (not ss['subject_id'] or mi['subject_id'] != ss['subject_id']
                or any(mc.get(k) != sc.get(k) for k in ('run_id','pair_id','block_index','montage_id','ssvep_true_label','mi_true_label','record_kind','clock_source','acquisition_purpose','baseline_trial_id'))
                or sc['block_index']<=0 or not sc.get('clock_source') or sc['stage'] != 'ssvep' or mc['stage'] != 'mi' or mc['preceding_ssvep_trial_id'] != path.stem
                or sc['run_id'] != folder.name or sc['pair_id']+'-ssvep' != path.stem):
                raise ValueError('identity_or_stage_mismatch')
            if not stage_ok(mi) or ss['timestamps'][-1] >= mi['timestamps'][0]:
                raise ValueError('mi_clock_rate_or_stage_order')
            if tuple(sc['target_frequencies_hz']) != tuple(FREQ) or LABEL_TO_FREQUENCY.get(sc['ssvep_true_label']) != sc['frequency_hz']:
                raise ValueError('frequency_contract')
            if mc['mi_true_label'] not in HANDS:
                raise ValueError('hand_contract')
            records.append(dict(subject=ss['subject_id'], run=sc['run_id'], pair=pair, block=int(sc['block_index']),
                                true_hz=int(sc['frequency_hz']), hand=HANDS.index(mc['mi_true_label']),
                                mi=waveform(mi,'MI'), scores=s, start=ss['timestamps'][0],
                                end=mi['timestamps'][-1], montage=sc['montage_id'], clock=sc['clock_source'],
                                baseline=sc['baseline_trial_id'],preflight=sc.get('preflight_id',''),
                                gap=mi['timestamps'][0]-ss['timestamps'][-1],
                                good=ss['quality'].get('status')==mi['quality'].get('status')=='good',
                                ss_sha256=ss['sha256'], mi_sha256=mi['sha256']))
            item['status']='ok'
        except (ValueError, KeyError, OSError) as e:
            item['status']=str(e)
    for stem in sorted(mi_paths):
        if not (folder/(stem[:-3]+'-ssvep.npz')).exists():
            audit.append(dict(run=folder.name,trial=stem,kind='orphan_MI',status='no_SS'))
    if records and len({r['subject'] for r in records}) != 1:
        raise ValueError('Mixed subjects in one run')
    backgrounds = {}
    if records:
        first=min(r['start'] for r in records)
        subject=records[0]['subject']
        contracts={(r['montage'],r['clock'],r['baseline']) for r in records}
        if len(contracts)!=1:
            raise ValueError('Mixed acquisition contracts in one run')
        montage,clock,base_id=contracts.pop()
        initial=[v for time,v,person,run,m,c in sorted(idle,key=lambda x:x[0])
                 if time<first and (person,run,m,c)==(subject,folder.name,montage,clock)]
        if len(initial)>=8:
            backgrounds['idle']=np.mean(initial[:8],axis=0)
        expected_path=folder/(folder.name+'-baseline.npz')
        if base_id==expected_path.stem and expected_path.is_file():
            try:
                rest=read_stage(expected_path)
                rc=rest['context']
                if (not stage_ok(rest) or rest['subject_id']!=subject or rc.get('run_id')!=folder.name
                    or rc.get('stage')!='baseline' or rc.get('record_kind')!='rest_baseline'
                    or rc.get('eye_state')!='open' or rest['timestamps'][-1]>=first
                    or rc.get('montage_id')!=montage or rc.get('clock_source')!=clock):
                    raise ValueError('invalid_true_rest')
                backgrounds['rest']=np.mean([fbcca(waveform(rest,'SSVEP',k)) for k in (0,375,750)],axis=0)
                status='rest'
            except (ValueError,KeyError,OSError) as e:
                status=str(e)
            audit.append(dict(run=folder.name,trial=base_id,kind='rest_baseline',status=status))
    return records, backgrounds, audit


def ss_predict(s, y, blocks, train, test, variant, idle=None):
    def decode(tr, te):
        offset = idle[variant] if variant in ('idle','rest') else baseline(s[tr], y[tr], variant)
        return decisions(s[te], offset, variant)
    out_y, out_x = [], []
    for b in np.unique(blocks[train]):
        val=train[blocks[train]==b]
        pt, mt=decode(train[blocks[train]!=b], val)
        out_y.extend(pt==y[val])
        out_x.extend(mt)
    pred, margin = decode(train, test)
    p = binary_fit(np.array(out_x)[:,None], np.array(out_y), margin[:,None])
    return pred, margin, p


def evaluate(records, idle, public_model):
    g = np.array([r['block'] for r in records])
    tr, te = np.flatnonzero(g<=4), np.flatnonzero(g>4)
    if max(records[j]['end'] for j in tr) >= min(records[j]['start'] for j in te):
        raise ValueError('Calibration must finish before the held-out recordings begin')
    s = np.stack([r['scores'] for r in records])
    f = np.array([r['true_hz']-8 for r in records])
    y = np.array([r['hand'] for r in records])
    x = np.stack([r['mi'] for r in records])
    primary = 'train_nontarget'
    for leave in np.unique(g[tr]):
        inner=tr[g[tr]!=leave]
        if set(f[inner])!=set(range(6)) or np.min(np.bincount(y[inner],minlength=2))<2:
            raise ValueError('insufficient_calibration_classes')
    def stages(train,test):
        pred,m,p=ss_predict(s,f,g,train,test,primary)
        q=csp_right(CSPClassifier().fit(x[train],y[train]), x[test])
        return pred,m,p,q
    def evidence(p,q):
        return np.column_stack((p, np.abs(2*q-1)))
    def hand_features(p,q,conditional):
        z=logit(np.clip(q,.00001,.99999))
        return np.column_stack((z,p-.5,z*(p-.5))) if conditional else z[:,None]
    inner_prior = np.zeros(len(g))
    fold_scores = np.zeros((len(g),4))
    for b in np.unique(g[tr]):
        val=tr[g[tr]==b]
        inner_train=tr[g[tr]!=b]
        fold_scores[val]=np.stack(stages(inner_train, val),axis=1)
        hits=[]
        for other in np.unique(g[inner_train]):
            left=inner_train[g[inner_train]!=other]
            check=inner_train[g[inner_train]==other]
            offset=baseline(s[left], f[left], primary)
            guess,_=decisions(s[check], offset, primary)
            hits.extend(guess==f[check])
        inner_prior[val]=np.mean(hits)
    a,m0,p0,q0=fold_scores[tr].T
    source_s=a==f[tr]
    source_m=(q0>.5)==y[tr]
    source_joint=source_s & source_m
    vote0=np.maximum(q0,1-q0)
    base0=inner_prior[tr]*vote0
    weight=weight_from_losses(base0,p0*vote0-base0,source_joint)
    def forecasts(m,p,q):
        v=np.maximum(q,1-q)
        n=len(p)
        return dict(prior=np.full(n,source_joint.mean()),no_p=source_s.mean()*v,
                    weighted_posthoc=(source_s.mean()+weight*(p-source_s.mean()))*v,
                    ss_only=source_m.mean()*p, margin_product=m*v,product=p*v,
                    joint_logistic=binary_fit(evidence(p0,q0),source_joint,evidence(p,q)))
    pred,m,p,q=stages(tr,te)
    forecasts_test=forecasts(m,p,q)
    forecasts_train=forecasts(m0,p0,q0)
    thresholds={k:float(np.quantile(v,.2)) for k,v in forecasts_train.items()}
    # Out-of-fold probabilities and identical MI inputs for the two hand classifiers.
    hand_models=[binary_fit(hand_features(p0,q0,c),y[tr],hand_features(p,q,c)) for c in (False,True)]
    variants = {}
    for v in ('raw','other_mean','idle','rest',primary):
        if v in ('idle','rest') and v not in idle:
            continue
        variants[v]=ss_predict(s,f,g,tr,te,v,idle)
    frozen,ext=apply(s[te],primary,public_model)
    rows=[]
    for i,index in enumerate(te):
        r=records[index]
        row = {k:r[k] for k in ('subject','run','pair','block','true_hz','hand','gap','good','ss_sha256','mi_sha256')}
        row.update(pred_hz=int(pred[i]+8), p=p[i], q_right=q[i], mi_correct=int((q[i]>.5)==y[index]),
                   ss_correct=int(pred[i]==f[index]),joint=int((pred[i]==f[index]) and ((q[i]>.5)==y[index])),
                   hand_mi_only=hand_models[0][i],hand_with_p=hand_models[1][i],frozen_hz=int(frozen[i]+8),frozen_p=ext['platt'][i])
        for v in ('raw','other_mean','idle','rest',primary):
            row[v+'_hz']=int(variants[v][0][i]+8) if v in variants else ''
            row[v+'_p']=float(variants[v][2][i]) if v in variants else ''
        for k in METHODS:
            row[k]=forecasts_test[k][i]
            row[k+'_accept']=int(row[k]>=thresholds[k])
        rows.append(row)
    return rows,dict(run=records[0]['run'],train=len(tr),test=len(te),
                     train_blocks=sorted(set(g[tr].tolist())),test_blocks=sorted(set(g[te].tolist())),preflight_present=sum(bool(r['preflight']) for r in records),
                     weight_posthoc=weight,prior_s=float(source_s.mean()),prior_m=float(source_m.mean()),thresholds=thresholds)


def summarize(rows):
    out={}
    def col(k):
        return np.array([r[k] for r in rows])
    joint=col('joint')
    for name in METHODS:
        score=col(name)
        accept=col(name+'_accept').astype(bool)
        # Use pair ID as a label-independent tie breaker. Per-run matched coverage.
        rank=np.zeros(len(rows),bool)
        for run in np.unique(col('run')):
            ix=np.flatnonzero(col('run')==run)
            ordered=ix[np.lexsort((col('pair')[ix],-score[ix]))]
            rank[ordered[:round(.8*len(ix))]]=True
        def selected(mask):
            return dict(accepted=int(mask.sum()), errors=int(np.sum(1-joint[mask])),
                        coverage=float(mask.mean()),accuracy=float(joint[mask].mean()) if mask.any() else None)
        out[name]={**metrics(joint,score),'frozen_policy':selected(accept),'matched80':selected(rank)}
    ss={}
    for v in ('raw','other_mean','idle','rest','train_nontarget','frozen'):
        avail=[r for r in rows if r[v+'_hz']!='']
        if avail:
            f=np.array([r['true_hz'] for r in avail])
            h=np.array([r[v+'_hz'] for r in avail])
            p=np.array([r[v+'_p'] for r in avail])
            ss[v]=dict(n=len(f),correct=int(sum(f==h)),non8=int(sum(f!=8)),true8=int(sum(f==8)),
                       fp8=int(sum((h==8)&(f!=8))),fn8=int(sum((h!=8)&(f==8))),**metrics(f==h,p))
    hand={}
    for k in ('q_right','hand_mi_only','hand_with_p'):
        q=col(k)
        hand[k]=dict(correct=int(np.sum((q>.5)==col('hand'))),balanced_acc=float(balanced_accuracy_score(col('hand'),q>.5)),
                     **metrics(col('hand'),q))
    return dict(n=len(rows),ss_correct=int(sum(col('ss_correct'))),mi_correct=int(sum(col('mi_correct'))),
                joint_correct=int(joint.sum()),transfer_decomposition=decomposition(joint,col('no_p'),col('product')),command=out,ss=ss,mi=hand)


def rehearsal(raw, out):
    out.mkdir(parents=True,exist_ok=True)
    reports=[]
    for folder in sorted({p.parent for p in raw.rglob('hybrid-*-ssvep.npz')}):
        rec,_,_=load(folder,max_block=4)
        if not rec:
            continue
        block=np.array([r['block'] for r in rec])
        if set(block)!={1,2,3,4}:
            reports.append(dict(run=folder.name,eligible=len(rec),status='four_training_blocks_required'))
            continue
        f=np.array([r['true_hz']-8 for r in rec])
        y=np.array([r['hand'] for r in rec])
        scores=np.array([r['scores'] for r in rec])
        eeg=np.array([r['mi'] for r in rec])
        ss_pred=np.full(len(rec),-1)
        mi_pred=np.full(len(rec),-1)
        for b in np.unique(block):
            train=block!=b
            test=~train
            if set(f[train])!=set(range(6)) or np.min(np.bincount(y[train],minlength=2))<2:
                break
            ss_pred[test],_=decisions(scores[test],baseline(scores[train],f[train],'train_nontarget'),'train_nontarget')
            mi_pred[test]=CSPClassifier().fit(eeg[train],y[train]).predict(eeg[test])
        if np.any(mi_pred<0):
            reports.append(dict(run=folder.name,eligible=len(rec),status='insufficient_classes'))
            continue
        reports.append(dict(run=folder.name,status='calibration_only',eligible=len(rec),
            ss_correct=int(np.sum(f==ss_pred)),mi_correct=int(np.sum(y==mi_pred)),
            false8=int(np.sum((ss_pred==0)&(f!=0))),non8=int(np.sum(f!=0)),
            missed8=int(np.sum((ss_pred!=0)&(f==0))),true8=int(np.sum(f==0)),
            target_hand_counts=[[int(np.sum((f==k)&(y==h))) for h in (0,1)] for k in range(6)],
            ss_confusion_true_by_pred=np.bincount(f*6+ss_pred,minlength=36).reshape(6,6).tolist(),
            mi_confusion_true_by_pred=np.bincount(y*2+mi_pred,minlength=4).reshape(2,2).tolist()))
    if not reports:
        raise ValueError('No formal serial recordings')
    (out/'calibration_check.json').write_text(json.dumps(reports,indent=2))
    print('Calibration report:',out/'calibration_check.json')


def run(raw,out,model):
    out.mkdir(parents=True,exist_ok=True)
    public_model=json.loads(model.read_text())['1.5:train_nontarget']
    rows,audits,splits=[],[],[]
    for folder in sorted({p.parent for p in raw.rglob('hybrid-*-ssvep.npz')} | {p.parent for p in raw.rglob('hybrid-*-mi.npz')}):
        records,idle,audit=load(folder)
        audits.extend(audit)
        if not records:
            continue
        blocks={r['block'] for r in records}
        if len(blocks)<6 or not {1,2,3,4}.issubset(blocks):
            splits.append(dict(run=folder.name,excluded='insufficient_blocks',eligible_pairs=len(records)))
            continue
        try:
            pred,split=evaluate(records,idle,public_model)
        except ValueError as e:
            splits.append(dict(run=folder.name,excluded=str(e),eligible_pairs=len(records)))
            continue
        rows.extend(pred)
        splits.append(split)
    if audits:
        write_csv(out/'epoch_audit.csv',audits)
    if not rows:
        raise ValueError('No eligible serial runs with four calibration blocks and at least two test blocks')
    write_csv(out/'trials.csv',rows)
    people=sorted({r['subject'] for r in rows})
    by_person={p:summarize([r for r in rows if r['subject']==p]) for p in people}
    per=np.array([v['command']['no_p']['brier']-v['command']['product']['brier'] for v in by_person.values()])
    # Summarize uncertainty at the participant level. Pilot N=2 is purely descriptive.
    effect=dict(mean=float(per.mean()),people_better=int(np.sum(per>0)),n=len(per))
    if len(per)>=8:
        rng=np.random.default_rng(20261119)
        effect['participant_bootstrap_ci95']=np.quantile(rng.choice(per,(10000,len(per))).mean(1),[.025,.975]).tolist()
    rng=np.random.default_rng(4611)
    shuffled=[]
    g=np.array([(r['run'],r['block']) for r in rows],dtype=object)
    groups=[[i for i,t in enumerate(g.tolist()) if t==list(pair)] for pair in sorted(set(map(tuple,g)))]
    p=np.array([r['p'] for r in rows])
    q=np.array([max(r['q_right'],1-r['q_right']) for r in rows])
    truth=np.array([r['joint'] for r in rows])
    for _ in range(500):
        alt=p.copy()
        for ix in groups:
            if len(ix)>1:
                alt[ix]=np.roll(p[ix],rng.integers(1,len(ix)))
        shuffled.append(float(np.mean((alt*q-truth)**2)))
    good=[r for r in rows if r['good']]
    summary=dict(pooled=summarize(rows),by_person=by_person,by_run={s['run']:summarize([r for r in rows if r['run']==s['run']]) for s in splits if 'test' in s},
                 good_only=summarize(good) if good else None,primary_brier_gain=effect,splits=splits,
                 within_block_shift_brier_mean=float(np.mean(shuffled)),within_block_shift_range=np.quantile(shuffled,[.025,.975]).tolist(),
                 input_audit=dict(Counter(a['status'] for a in audits)),public_model_sha256=hashlib.sha256(model.read_bytes()).hexdigest())
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_report(rows,summary,out)
    print('People',len(people),'held pairs',len(rows),'joint correct',sum(truth),'macro Brier gain',effect['mean'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,default=ROOT/'data/research_experiments/raw_trials')
    parser.add_argument('--out',type=Path,default=HERE/'results')
    parser.add_argument('--public-model',type=Path,default=HERE.parent/'ssvep/results/benchmark_models.json')
    parser.add_argument('--calibration-only',action='store_true',help='Use only the first four blocks for a technical rehearsal')
    args=parser.parse_args()
    if args.calibration_only:
        rehearsal(args.raw,args.out)
    else:
        run(args.raw,args.out,args.public_model)
        from research.serial_bci.serial.compare_calibrators import evaluate_richer
        evaluate_richer(args.raw,args.out,args.public_model)
