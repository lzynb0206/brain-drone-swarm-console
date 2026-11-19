#!/usr/bin/env python3
"""Frozen-split correction/ablation study, never connected to flight control.

Outputs remain exploratory: the same recordings have been inspected before.
Public data are implementation controls, NOT added to local training data.
The old audited scripts, weights, raw files and reports are left untouched.
"""
from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
import numpy as np
from scipy import signal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.ml.ssvep_repair import (quality_preprocess, eegnet_streams, complex_spectrum,
    crop_batch, reference_filterbank, fit_etrca, score_etrca)
from src.ml.recent_ssvep_models import SSVEPTFFNet, BrainWaveDecoderAdaptation
from src.ml.fb_eegnet import FBEEGNetFusion

OLD=ROOT/'outputs/subject24_audited_six_methods_20261028'
OUT=ROOT/'outputs/subject24_repair_20261028'
METHODS=('FB-EEGNet','SSVEP-TFFNet','CNN-BiGRU')
SEEDS=(17,29,43)
RECIPES=('qc_full','qc_crop_phase_aug')
MAX_EPOCHS=100


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(seed):
    import torch
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)


def freeze(out):
    files=[Path(__file__),ROOT/'src/ml/ssvep_repair.py',ROOT/'src/ml/recent_ssvep_models.py',ROOT/'src/ml/fb_eegnet.py']
    config=dict(purpose='exploratory correction, not prospective blinded evaluation',
        local_data='same subject A: Oct24 60 + Oct27 20:41 105; all other subjects excluded',
        splits='exact saved outer and inner splits from subject24_audited_six_methods_20261028',
        neural_recipes=list(RECIPES),maximum_epochs=MAX_EPOCHS,seeds=list(SEEDS),
        selection='inner validation correct count, then cross entropy; no outer test selection',
        local_neural_training='scratch only, no public data or pretrained weights',
        quality='zero unusable electrodes in fixed slots; <2 usable or >20ms timestamp gap excluded from training; test retained as failure',
        augmented_recipe='2-second within-trial crops, 2 independently augmented views/trial/epoch; common-channel circular shift; five fixed test crops averaged',
        plain_recipe='one full five-second window, no shift or crop augmentation',
        optimizer='Adam lr=0.001, weight_decay=0.001; CE, dropout as architecture; clipping norm 1',
        validation='every 5 epochs; minimum40; patience30; restore best checkpoint',
        etrca='Chebyshev-I 6/14/22 to90Hz; linear weighted correlations; ridge chosen from 1e-6,1e-3,0.1; .2s transient discarded',
        public_control='first10 Benchmark subjects, blocks1-4 fit,5 validation,6 test; six frequency subset, six electrodes padded to8; not full paper reproduction',
        code_hashes={str(p.relative_to(ROOT)):sha(p) for p in files},
        source_manifest_sha256=sha(OLD/'data_manifest.json'))
    path=out/'configuration.json'
    if path.exists():
        assert json.loads(path.read_text())==config,'Configuration/source changed: use a new output directory'
    else: save(path,config)
    return config


def load_local(out):
    records=json.loads((OLD/'data_manifest.json').read_text())
    clean=[];raw=[];valid=[]; masks=[]
    for r in records:
        p=ROOT/'data/research_experiments/raw_trials'/r['source_file']
        assert sha(p)==r['file_sha256']
        with np.load(p,allow_pickle=False) as h: x=h['eeg'].astype(float)
        c,q=quality_preprocess(x)
        z,_=quality_preprocess(x,band=None,normalize=False)
        clean.append(c);raw.append(z);masks.append(q['usable_mask'])
        valid.append(q['usable_channel_count']>=2 and r['timestamp_gap_seconds']<=.02)
    clean=np.array(clean);raw=np.array(raw);y=np.array([r['label_index'] for r in records])
    clean45,banks45=eegnet_streams(clean)
    banks=reference_filterbank(raw)[...,50:]
    data=dict(clean=clean,eegnet=(clean45,banks45),trca=banks,y=y,valid=np.array(valid),records=records)
    save(out/'quality.json',[dict(source_file=r['source_file'],true_label=r['true_label'],usable_mask=m,
        eligible=bool(v),timestamp_gap_seconds=r['timestamp_gap_seconds']) for r,m,v in zip(records,masks,valid)])
    return data


def get_splits(data):
    indices={r['source_file']:i for i,r in enumerate(data['records'])}
    folders=[OLD/f'grouped_fold_{k}' for k in range(1,6)]+[OLD/'cross_day_oct24_to_oct27']
    for folder in folders:
        d=json.loads((folder/'split.json').read_text())
        split={k:np.array([indices[f] for f in d[k]],int) for k in ('train','test','inner_fit','inner_validation')}
        assert not set(split['train'])&set(split['test'])
        assert set(split['inner_fit'])|set(split['inner_validation']) <= set(split['train'])
        yield folder.name,split


def eligible(data,indices):
    return indices[(data['y'][indices]>=0)&data['valid'][indices]]


def net(method):
    if method=='FB-EEGNet': return FBEEGNetFusion(channels=8,bands=4,classes=6)
    if method=='SSVEP-TFFNet': return SSVEPTFFNet(channels=8,classes=6)
    if method=='CNN-BiGRU': return BrainWaveDecoderAdaptation(channels=8,classes=6)
    raise ValueError(method)


def streams(data,method):
    return data['eegnet'] if method=='FB-EEGNet' else (data['clean'],)


def inputs(method,values):
    import torch
    if method=='SSVEP-TFFNet': values=(values[0],complex_spectrum(values[0]))
    return tuple(torch.from_numpy(x) for x in values)


def predict(model,method,data,indices,recipe):
    import torch
    source=streams(data,method); total=source[0].shape[-1]
    length=500 if recipe=='qc_crop_phase_aug' else total
    # Public control has an already extracted 2s epoch, no fabricated windows.
    starts=np.linspace(50,total-length,5).round().astype(int) if total>length else np.array([0])
    if recipe=='qc_full': starts=np.array([0])
    model.eval(); results=[]
    with torch.no_grad():
        for ix in np.array_split(np.asarray(indices,int),max(1,(len(indices)+31)//32)):
            if not len(ix): continue
            probs=[]
            for start in starts:
                values=crop_batch(source,ix,np.full(len(ix),start),length=length)
                probs.append(torch.softmax(model(*inputs(method,values)),dim=1).numpy())
            results.append(np.mean(probs,axis=0))
    return np.concatenate(results) if results else np.zeros((0,6))


def fit_neural(method,data,train,recipe,seed,epochs,validation=None):
    import torch
    setup(seed);model=net(method)
    optimizer=torch.optim.Adam(model.parameters(),lr=.001,weight_decay=.001)
    rng=np.random.default_rng(seed);source=streams(data,method);n=source[0].shape[-1]
    length=500 if recipe=='qc_crop_phase_aug' else n
    history=[];best=None;best_key=None;best_epoch=0;started=time.monotonic()
    for epoch in range(1,epochs+1):
        model.train();losses=[]
        # Repeated crops remain training observations of the SAME trial.
        order=rng.permutation(np.tile(train,2 if recipe=='qc_crop_phase_aug' else 1))
        for ix in np.array_split(order,max(1,int(np.ceil(len(order)/32)))):
            if recipe=='qc_crop_phase_aug':
                start=rng.integers(50,n-length+1,len(ix)) if n>length else np.zeros(len(ix),int)
                shifts=rng.integers(0,250,len(ix))
            else: start=np.zeros(len(ix),int);shifts=None
            values=crop_batch(source,ix,start,length=length,shifts=shifts)
            optimizer.zero_grad(set_to_none=True)
            logits=model(*inputs(method,values))
            loss=torch.nn.functional.cross_entropy(logits,torch.from_numpy(data['y'][ix]))
            assert torch.isfinite(loss)
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
            losses.append(float(loss.detach()))
        row=dict(epoch=epoch,loss=float(np.mean(losses)))
        if validation is not None and (epoch%5==0 or epoch==epochs):
            p=predict(model,method,data,validation,recipe)
            # Every active validation trial counts, including invalid inputs.
            pred=p.argmax(1);pred[~data['valid'][validation]]=-1
            correct=int((pred==data['y'][validation]).sum())
            nll=float(-np.log(np.maximum(p[np.arange(len(p)),data['y'][validation]],1e-12)).mean())
            row.update(validation_correct=correct,validation_count=len(validation),nll=nll)
            key=(correct,-nll)
            if best_key is None or key>best_key:
                best_key=key;best_epoch=epoch
                best={k:v.detach().clone() for k,v in model.state_dict().items()}
            if epoch>=40 and epoch-best_epoch>=30:
                history.append(row);break
        history.append(row)
    if validation is not None: model.load_state_dict(best)
    p=predict(model,method,data,train,recipe)
    info=dict(method=method,recipe=recipe,seed=seed,epochs_run=epoch,selected_epoch=best_epoch or epoch,
        training_count=len(train),training_correct=int((p.argmax(1)==data['y'][train]).sum()),
        parameters=sum(v.numel() for v in model.parameters()),seconds=time.monotonic()-started,history=history)
    if best_key: info.update(validation_correct=best_key[0],validation_nll=-best_key[1],validation_count=len(validation))
    return model,info


def final_rows(data,test,scores,method,fold):
    rows=[]
    for i,p in zip(test,scores):
        pred=int(p.argmax()) if data['valid'][i] else -1
        rows.append(dict(fold=fold,method=method,source_file=data['records'][i]['source_file'],
            true_label=int(data['y'][i]),prediction=pred,quality_valid=bool(data['valid'][i]),
            correct=bool(pred==data['y'][i] and data['y'][i]>=0),scores=p.tolist()))
    return rows


def run_neural(data,name,split,method,out):
    import torch
    folder=out/name/method
    done=folder/'result.json'
    if done.exists(): return json.loads(done.read_text())
    train=eligible(data,split['train']);fit=eligible(data,split['inner_fit'])
    val=split['inner_validation'][data['y'][split['inner_validation']]>=0]
    candidates=[]
    for recipe in RECIPES:
        cached=folder/f'validation_{recipe}.json'
        if cached.exists(): info=json.loads(cached.read_text())
        else:
            print(name,method,'validation',recipe,flush=True)
            _,info=fit_neural(method,data,fit,recipe,101,MAX_EPOCHS,val);save(cached,info)
        candidates.append(info)
    chosen=max(candidates,key=lambda c:(c['validation_correct'],-c['validation_nll']))
    print(name,method,'selected',chosen['recipe'],chosen['selected_epoch'],'epochs',flush=True)
    all_scores=[];refits=[]
    for seed in SEEDS:
        ckpt=folder/f'seed_{seed}.pt';infofile=folder/f'refit_{seed}.json'
        if ckpt.exists() and infofile.exists():
            model=net(method);model.load_state_dict(torch.load(ckpt,map_location='cpu',weights_only=True));info=json.loads(infofile.read_text())
        else:
            model,info=fit_neural(method,data,train,chosen['recipe'],seed,chosen['selected_epoch'])
            folder.mkdir(parents=True,exist_ok=True);torch.save(model.state_dict(),ckpt);save(infofile,info)
        p=predict(model,method,data,split['test'],chosen['recipe'])
        check=net(method);check.load_state_dict(torch.load(ckpt,map_location='cpu',weights_only=True))
        np.testing.assert_allclose(p,predict(check,method,data,split['test'],chosen['recipe']),atol=1e-7)
        all_scores.append(p);refits.append(info)
    result=dict(method=method+' QC/adapted',fold=name,candidates=candidates,
        selected={k:v for k,v in chosen.items() if k!='history'},refits=refits,
        train_files=[data['records'][i]['source_file'] for i in train],
        validation_files=[data['records'][i]['source_file'] for i in val],
        combination='mean of 3 seed softmax vectors, then argmax; one result per saved trial',
        rows=final_rows(data,split['test'],np.mean(all_scores,axis=0),method+' QC/adapted',name))
    np.savez_compressed(folder/'seed_test_scores.npz',scores=np.array(all_scores))
    save(done,result);return result


def run_trca(data,name,split,out):
    folder=out/name/'FB-eTRCA_reference';done=folder/'result.json'
    if done.exists(): return json.loads(done.read_text())
    fit=eligible(data,split['inner_fit']);train=eligible(data,split['train'])
    val=split['inner_validation'][data['y'][split['inner_validation']]>=0]
    choices=[]
    for ridge in (1e-6,.001,.1):
        m=fit_etrca(data['trca'][fit],data['y'][fit],ridge=ridge)
        p=score_etrca(data['trca'][val],m).argmax(1);p[~data['valid'][val]]=-1
        choices.append(dict(ridge=ridge,validation_correct=int((p==data['y'][val]).sum()),validation_count=len(val)))
    best=max(choices,key=lambda c:(c['validation_correct'],c['ridge']))
    m=fit_etrca(data['trca'][train],data['y'][train],ridge=best['ridge'])
    scores=score_etrca(data['trca'][split['test']],m)
    folder.mkdir(parents=True,exist_ok=True);np.savez_compressed(folder/'model.npz',templates=m[0],filters=m[1])
    with np.load(folder/'model.npz') as h:
        np.testing.assert_allclose(scores,score_etrca(data['trca'][split['test']],(h['templates'],h['filters'])),atol=1e-10)
    result=dict(method='FB-eTRCA reference/QC',fold=name,candidates=choices,selected=best,
        train_files=[data['records'][i]['source_file'] for i in train],
        rows=final_rows(data,split['test'],scores,'FB-eTRCA reference/QC',name))
    save(done,result);return result


def public_control(out):
    import h5py
    file=ROOT/'data/public_ssvep/processed/benchmark_common6_2s.h5'
    with h5py.File(file) as h:
        chosen=h['subject'][:]<=10
        raw=h['eeg'][:][chosen];y=h['label'][:][chosen].astype(np.int64);blocks=h['block'][:][chosen];subjects=h['subject'][:][chosen]
    clean=[];broad=[]
    for x in raw:
        x=np.pad(x,((0,2),(0,0)))
        c,_=quality_preprocess(x,check_quality=False);z,_=quality_preprocess(x,check_quality=False,band=None,normalize=False)
        clean.append(c);broad.append(z)
    clean=np.array(clean);banks=reference_filterbank(np.array(broad));streams45=eegnet_streams(clean)
    data=dict(clean=clean,eegnet=streams45,y=y,valid=np.ones(len(y),bool))
    fit=np.flatnonzero(blocks<=4);val=np.flatnonzero(blocks==5);train=np.flatnonzero(blocks<=5);test=np.flatnonzero(blocks==6)
    report=dict(protocol='first10 subjects; block6 held out, blocks1-4 fit and5 validation; six-class subset; NOT original paper protocol',
        source_file=str(file),source_sha256=sha(file),train_count=len(train),test_count=len(test),results={})
    # TRCA is calibrated separately for each public subject, as intended.
    truth=[];pred=[]
    for subject in range(1,11):
        tr=np.flatnonzero((subjects==subject)&(blocks<=5));te=np.flatnonzero((subjects==subject)&(blocks==6))
        m=fit_etrca(banks[tr],y[tr]);p=score_etrca(banks[te],m).argmax(1)
        truth.extend(y[te].tolist());pred.extend(p.tolist())
    report['results']['FB-eTRCA reference']=dict(correct=int(np.sum(np.array(truth)==pred)),total=len(truth),truth=truth,predictions=pred)
    save(out/'public_control.json',report)
    for method in METHODS:
        result_path=out/'public'/method/'result.json'
        if result_path.exists(): result=json.loads(result_path.read_text())
        else:
            print('PUBLIC',method,'validation',flush=True)
            _,selection=fit_neural(method,data,fit,'qc_full',101,MAX_EPOCHS,val)
            model,info=fit_neural(method,data,train,'qc_full',17,selection['selected_epoch'])
            p=predict(model,method,data,test,'qc_full').argmax(1)
            result=dict(correct=int((p==y[test]).sum()),total=len(test),selection=selection,refit=info,
                truth=y[test].tolist(),predictions=p.tolist())
            save(result_path,result)
        report['results'][method]=result;save(out/'public_control.json',report)
    return report


def summarize(out):
    result={}
    for p in out.glob('*/**/result.json'):
        d=json.loads(p.read_text())
        if 'rows' not in d:continue
        protocol='cross_day' if d['fold'].startswith('cross_day') else 'grouped_5fold'
        k=protocol+' / '+d['method'];entry=result.setdefault(k,dict(correct=0,total=0,invalid=0,folds=[]))
        rows=[r for r in d['rows'] if r['true_label']>=0]
        entry['correct']+=sum(r['correct'] for r in rows);entry['total']+=len(rows)
        entry['invalid']+=sum(not r['quality_valid'] for r in rows);entry['folds'].append(d['fold'])
    for entry in result.values():entry['accuracy']=entry['correct']/entry['total']
    save(out/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT)
    p.add_argument('--mode',choices=('public','local','trca','summary'),default='local')
    p.add_argument('--fold',default='all');p.add_argument('--method',choices=METHODS)
    args=p.parse_args();freeze(args.out)
    if args.mode=='summary':summarize(args.out);return
    if args.mode=='public':public_control(args.out);return
    data=load_local(args.out)
    for name,split in get_splits(data):
        if args.fold!='all' and args.fold!=name:continue
        save(args.out/name/'split.json',{k:[data['records'][i]['source_file'] for i in v] for k,v in split.items()})
        print('FOLD',name,flush=True)
        run_trca(data,name,split,args.out)
        if args.mode=='local':
            for method in METHODS:
                if args.method and args.method!=method:continue
                run_neural(data,name,split,method,args.out)
    summarize(args.out)


if __name__=='__main__':main()
