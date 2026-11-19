#!/usr/bin/env python3
"""Fixed-setting MI diffusion pilot; E/test data never train or choose parameters."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
import torch
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.metrics import confusion_matrix

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.ml.mi_baseline import CSPClassifier,preprocess_mi,MI_CHANNELS
from src.ml.mi_diffusion import MIDiffusion,baseline_features,condition_values
from src.ml.confidence_augmentation import expand_training_conditions


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--epochs",type=int,default=12)
    parser.add_argument("--data",type=Path,default=ROOT/"outputs/public_mi_baseline_20261101/A01_central3_epochs.npz")
    parser.add_argument("--out",type=Path,default=ROOT/"outputs/mi_diffusion_pilot_20261101")
    parser.add_argument("--conditions",nargs="+",choices=("none","true","constant","shuffled"),default=["none"])
    parser.add_argument("--subject-conditioning",action="store_true")
    parser.add_argument("--augment-training-confidence",action="store_true")
    parser.add_argument("--erd-weight",type=float,default=.05);parser.add_argument("--protect-weight",type=float,default=.02)
    a=parser.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if a.epochs<1:raise ValueError("epochs must be positive")
    torch.set_num_threads(4);seed=20261101
    with np.load(a.data,allow_pickle=False) as z:
        data={k:z[k].copy() for k in z.files}
    if a.augment_training_confidence:data=expand_training_conditions(data)
    source=str(data["condition_source"].item())
    if float(data["fs"])!=250. or tuple(str(x) for x in data["channel_names"])!=MI_CHANNELS:
        raise ValueError("This MI diffusion pipeline requires 250 Hz C3, Cz, C4 in that order")
    if any(c!="none" for c in a.conditions) and source!="actual_preceding_ssvep":
        raise ValueError("Public MI data has no preceding SSVEP; use --conditions none")
    sets={};reports=[]
    for split in ("train","test"):
        labels=data[f"{split}_y"];selected=np.isin(labels,[0,1]);n=len(labels)
        # Same analysis cohort for every paired-data ablation, including no-P.
        available=data.get(f"{split}_signal_valid",np.ones(n,bool))
        available=available & data.get(f"{split}_p_valid",np.ones(n,bool))
        good=selected & available
        if good.sum()<2:
            raise ValueError(f"Not enough usable {split} MI pairs; check signal and preceding P validity")
        sets[split]=dict(x=preprocess_mi(data[f"{split}_eeg"][good],250.),y=labels[good],
            baseline=preprocess_mi(data[f"{split}_baseline"][good],250.),
            p=data.get(f"{split}_p",np.zeros(n))[good],valid=data.get(f"{split}_p_valid",np.zeros(n,bool))[good],
            groups=data[f"{split}_run"][good],denominator=int(selected.sum()),
            omitted=int((selected&~available).sum()), original_labels=labels[selected],
            good_in_selected=available[selected])
        if len(sets[split]["x"])<2:raise ValueError("Not enough usable MI pairs")
    tr,te=sets["train"],sets["test"]
    channel_names=tuple(str(x) for x in data["channel_names"])
    labels=tuple(str(x) for x in data.get("task_labels",np.array(["想象左手","想象右手"])))
    classifier=CSPClassifier(channel_names=channel_names,task_labels=labels).fit(tr["x"],tr["y"],prepared=True)
    def report(method,pred,**extra):
        all_pred=np.full(te["denominator"],-1,int);all_pred[te["good_in_selected"]]=pred
        y=te["original_labels"];correct=int((all_pred==y).sum())
        row=dict(method=method,correct=correct,total=len(y),accuracy=correct/len(y),
                 invalid_counted_wrong=te["omitted"],confusion=confusion_matrix(y,all_pred,labels=[0,1,-1]).tolist(),
                 predictions=all_pred.tolist(),labels=y.tolist(),**extra)
        reports.append(row);print(method,f"{correct}/{len(y)} = {correct/len(y):.2%}",flush=True)
    report("CSP_LDA",classifier.predict(te["x"],prepared=True))
    mean=tr["x"].mean((0,2),keepdims=True);scale=tr["x"].std((0,2),keepdims=True).clip(1e-6)
    features={s:baseline_features(d["baseline"]) for s,d in sets.items()}
    fm=features["train"].mean(0);fs=features["train"].std(0).clip(.1)
    for s,d in sets.items():
        d["xt"]=torch.tensor((d["x"]-mean)/scale,dtype=torch.float32)
        d["bt"]=torch.tensor((d["baseline"]-mean)/scale,dtype=torch.float32)
        d["subject"]=torch.tensor((features[s]-fm)/fs if a.subject_conditioning else np.zeros_like(features[s]),dtype=torch.float32)
    if source=="actual_preceding_ssvep":
        # Tests whether a plain classifier benefits from P without diffusion.
        simple=LinearDiscriminantAnalysis(solver="lsqr",shrinkage="auto")
        simple.fit(np.column_stack((classifier.features(tr["x"]),tr["p"])),tr["y"])
        report("CSP_features_plus_P_LDA",simple.predict(np.column_stack((classifier.features(te["x"]),te["p"]))))
    for mode in a.conditions:
        torch.manual_seed(seed);np.random.seed(seed);started=time.monotonic()
        model=MIDiffusion(erd_weight=a.erd_weight,protect_weight=a.protect_weight)
        optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
        cond={s:tuple(torch.tensor(v) for v in condition_values(mode,source,d["p"],d["valid"],d["groups"],seed))
              for s,d in sets.items()}
        history=[];model.train()
        for epoch in range(a.epochs):
            order=torch.randperm(len(tr["xt"]));losses=[]
            for idx in order.split(32):
                loss,terms=model.loss(tr["xt"][idx],cond["train"][0][idx],cond["train"][1][idx],tr["subject"][idx],tr["bt"][idx])
                if not torch.isfinite(loss):raise RuntimeError("Nonfinite diffusion loss")
                optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
                losses.append(float(loss.detach()))
            history.append(float(np.mean(losses)))
            print(f"{mode} epoch {epoch+1}/{a.epochs}, loss={history[-1]:.5f}",flush=True)
        model.eval();processed={}
        for s,d in sets.items():
            chunks=[]
            for offset in range(0,len(d["xt"]),32):
                sl=slice(offset,offset+32)
                chunks.append(model.reconstruct(d["xt"][sl],cond[s][0][sl],cond[s][1][sl],d["subject"][sl],seed=seed+offset).numpy())
            processed[s]=np.concatenate(chunks)*scale+mean
        downstream=CSPClassifier(channel_names=channel_names,task_labels=labels).fit(processed["train"],tr["y"],prepared=True)
        report(f"diffusion_{mode}_CSP_LDA",downstream.predict(processed["test"],prepared=True),
               seconds=time.monotonic()-started,training_loss=history)
        downstream.save(a.out/f"{mode}_downstream.npz",dict(condition_source=source,preprocessing="MI_diffusion",requires_diffusion=True))
        torch.save(dict(state_dict=model.state_dict(),config=model.config,condition=mode,
            subject_conditioning=a.subject_conditioning,normalization_mean=mean.tolist(),normalization_scale=scale.tolist(),
            subject_mean=fm.tolist(),subject_scale=fs.tolist(),source=source,seed=seed,
            fs=250.,channel_names=channel_names,samples=tr["x"].shape[-1],start_step=3),a.out/f"{mode}_diffusion.pt")
    result=dict(source=source,data=str(a.data),sha256=hashlib.sha256(a.data.read_bytes()).hexdigest(),
        seed=seed,epochs=a.epochs,train_used=len(tr["y"]),train_invalid_excluded=tr["omitted"],
        training_confidence_augmentation=a.augment_training_confidence,
        test_tuned=False,paired_efficacy_demonstrated=False,subject_conditioning=a.subject_conditioning,
        erd_weight=a.erd_weight,protect_weight=a.protect_weight,
        limitation="Observed MI is the target, no clean EEG ground truth. One-subject pilot is not evidence of cross-stage benefit.",
        results=reports)
    (a.out/"report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")

if __name__=="__main__":main()
