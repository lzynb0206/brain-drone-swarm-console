#!/usr/bin/env python3
"""Future REAL paired-data research, with whole-run validation and explicit negative controls.

This command refuses public unpaired data. It never emits equipment commands.
Training labels calibrate the objective only; inference never receives an MI label.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import torch
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.metrics import confusion_matrix
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.ml.mi_baseline import CSPClassifier,preprocess_mi,MI_CHANNELS
from src.ml.mi_diffusion import condition_values
from src.ml.mi_research import calibrated_diffusion_loss
from scripts.research_mi_diffusion_suite import training_setup

VARIANTS={
    "none":dict(condition="none",physics=True,rest=True,augment=True),
    "true":dict(condition="true",physics=True,rest=True,augment=True),
    "constant":dict(condition="constant",physics=True,rest=True,augment=True),
    "shuffled":dict(condition="shuffled",physics=True,rest=True,augment=True),
    "no_physics":dict(condition="true",physics=False,rest=True,augment=True),
    "no_rest":dict(condition="true",physics=True,rest=False,augment=True),
    "no_protection":dict(condition="true",physics=True,rest=True,augment=True,protect=0.),
    "fixed_erd":dict(condition="true",physics=True,rest=True,augment=True,fixed_erd=True),
    "no_confidence_augmentation":dict(condition="true",physics=True,rest=True,augment=False),
}

def audit_data(data):
    if "condition_source" not in data or str(data["condition_source"].item())!="actual_preceding_ssvep":
        raise ValueError("Requires actual paired recordings; public MI cannot supply SSVEP confidence")
    if float(data["fs"])!=250. or tuple(data["channel_names"])!=MI_CHANNELS:
        raise ValueError("Requires measured 250 Hz C3/Cz/C4")
    if set(data["train_run"]) & set(data["test_run"]):raise ValueError("Training and test runs overlap")
    if set(data["train_pair_id"]) & set(data["test_pair_id"]):raise ValueError("Training and test pairs overlap")
    subjects=set(data.get("train_subject_id",[]))|set(data.get("test_subject_id",[]))
    if len(subjects)!=1 or not next(iter(subjects),"") or "train_subject_id" not in data or "test_subject_id" not in data:
        raise ValueError("Run one identified participant at a time; aggregate participants afterwards")
    if len(np.unique(data["train_run"]))<3:raise ValueError("At least three whole training runs required for train/validation separation")
    for split in ("train","test"):
        n=len(data[f"{split}_y"])
        if len(set(data[f"{split}_pair_id"]))!=n:raise ValueError("Duplicate original pairs")
        if set(np.unique(data[f"{split}_y"]))!={0,1}:raise ValueError("Requires two MI classes")
        for suffix in ("eeg","baseline","p","p_valid","signal_valid","run","pair_id","subject_id"):
            if len(data[f"{split}_{suffix}"])!=n:raise ValueError("Paired-data length mismatch")
        valid=data[f"{split}_p_valid"].astype(bool);p=data[f"{split}_p"]
        if not np.isfinite(p[valid]).all() or ((p[valid]<0)|(p[valid]>1)).any():raise ValueError("Invalid P values")
    return True

def subset(data,split):
    good=data[f"{split}_p_valid"].astype(bool)&data[f"{split}_signal_valid"].astype(bool)
    result={k:data[f"{split}_{k}"][good] for k in ("eeg","baseline","y","p","run","pair_id")}
    for key in ("p_aug","p_aug_valid"):
        if f"{split}_{key}" in data:result[key]=data[f"{split}_{key}"][good]
    if not len(result["y"]):raise ValueError("No usable paired trials")
    result["good"]=good;result["all_y"]=data[f"{split}_y"]
    return result

def take(d,idx):
    return {k:v[idx] for k,v in d.items() if k not in ("good","all_y")}

def setup(d,seed,options,lateralized):
    s=training_setup(d["eeg"],d["baseline"],d["y"],d["run"],seed)
    if options.get("fixed_erd"):s["calibration"]["strength"]=1.
    if not lateralized:
        s["calibration"]["strength"]=0.
        s["calibration"]["reason_disabled"]="Wave/grasp tasks have no justified left/right contralateral mapping"
        s["calibration"]["target_contralateral_erd"]=np.zeros((2,2)).tolist()
    s["model"].config["protect_weight"]=options.get("protect",.02)
    return s

def conditions(d,mode,seed,training=False,augment=False):
    p=d["p"].astype(np.float32).copy();rng=np.random.default_rng(seed)
    if training and augment and "p_aug" in d:
        # One condition per original pair per epoch: no pseudoreplication of MI epochs.
        for i in range(len(p)):
            choices=np.r_[p[i],d["p_aug"][i][d["p_aug_valid"][i].astype(bool)]]
            p[i]=rng.choice(choices)
    effective=mode if training or mode!="shuffled" else "true"
    return condition_values(effective,"actual_preceding_ssvep",p,np.ones(len(p),bool),d["run"],seed)

def train(s,d,options,epochs,seed):
    optimizer=torch.optim.AdamW(s["model"].parameters(),lr=.001,weight_decay=.0001);history=[]
    for epoch in range(epochs):
        s["model"].train();p,m=map(torch.tensor,conditions(d,options["condition"],seed+epoch,True,options["augment"]));losses=[]
        for ix in torch.randperm(len(d["y"])).split(32):
            sub=s["sub"][ix] if options["rest"] else torch.zeros(len(ix),9)
            loss,_=calibrated_diffusion_loss(s["model"],s["xt"][ix],p[ix],m[ix],sub,s["bt"][ix],s["y"][ix],s["calibration"],options["physics"])
            if not torch.isfinite(loss):raise RuntimeError("Nonfinite paired MI objective")
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(s["model"].parameters(),1);optimizer.step();losses.append(float(loss.detach()))
        history.append(float(np.mean(losses)))
    return history

def transform(s,d,options,seed):
    s["model"].eval();x=preprocess_mi(d["eeg"],250.)
    z=torch.tensor((x-s["mean"])/s["scale"],dtype=torch.float32)
    features=s["encoder"].transform(d["baseline"]) if options["rest"] else np.zeros((len(x),9),np.float32)
    p,m=map(torch.tensor,conditions(d,options["condition"],seed));parts=[]
    for start in range(0,len(x),32):
        sl=slice(start,start+32);parts.append(s["model"].single_pass(z[sl],p[sl],m[sl],torch.tensor(features[sl])).numpy())
    return x,np.concatenate(parts)*s["scale"]+s["mean"]

def main():
    p=argparse.ArgumentParser();p.add_argument("--data",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--seeds",nargs="+",type=int,default=[20261101,20261102,20261103]);p.add_argument("--epochs",nargs="+",type=int,default=[12,24])
    p.add_argument("--variants",nargs="+",choices=VARIANTS,default=list(VARIANTS));a=p.parse_args()
    if any(e<1 for e in a.epochs):p.error("Positive epochs required")
    with np.load(a.data,allow_pickle=False) as z:data={k:z[k].copy() for k in z.files}
    audit_data(data);a.out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(2)
    tr,te=subset(data,"train"),subset(data,"test");runs=np.unique(tr["run"])
    if len(runs)<3:raise ValueError("At least three usable training runs required")
    va=tr["run"]==runs[-1];fit=~va;fitting=take(tr,fit);validation=take(tr,va)
    labels=tuple(str(v) for v in data["task_labels"]);lateralized=labels==("想象左手","想象右手")
    protocol=dict(subject_id=str(data["train_subject_id"][0]),data_sha256=hashlib.sha256(a.data.read_bytes()).hexdigest(),train_runs=runs.tolist(),
        validation_run=str(runs[-1]),test_runs=np.unique(te["run"]).tolist(),variants=a.variants,seeds=a.seeds,epochs=a.epochs,
        train_only_condition_augmentation=True,shuffled_control="Training P shuffled within training run; inference uses real preceding P",
        test_invalid_counted_wrong=True,contralateral_erd_enabled=lateralized,research_only=True)
    (a.out/"protocol.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n")
    reports=[]
    def result(method,pred,**extra):
        full=np.full(len(te["all_y"]),-1,int);full[te["good"]]=pred;y=te["all_y"]
        return dict(method=method,total=len(y),correct=int((full==y).sum()),accuracy=float((full==y).mean()),
            invalid_counted_wrong=int((~te["good"]).sum()),labels=y.tolist(),predictions=full.tolist(),
            confusion=confusion_matrix(y,full,labels=[0,1,-1]).tolist(),**extra)
    tx=preprocess_mi(tr["eeg"],250.);ex=preprocess_mi(te["eeg"],250.)
    base=CSPClassifier(task_labels=labels).fit(tx,tr["y"],prepared=True)
    reports.append(result("CSP_LDA",base.predict(ex,prepared=True)))
    simple=LinearDiscriminantAnalysis(solver="lsqr",shrinkage="auto").fit(np.c_[base.features(tx),tr["p"]],tr["y"])
    reports.append(result("CSP_features_P_LDA",simple.predict(np.c_[base.features(ex),te["p"]])))
    for seed in a.seeds:
        for name in a.variants:
            options=VARIANTS[name];dest=a.out/f"{name}_{seed}";dest.mkdir(exist_ok=True);candidates=[]
            for epochs in a.epochs:
                s=setup(fitting,seed,options,lateralized);train(s,fitting,options,epochs,seed)
                x,px=transform(s,fitting,options,seed);vx,pvx=transform(s,validation,options,seed)
                for strength in (0.,.25,.5,1.):
                    clf=CSPClassifier(task_labels=labels).fit(x+strength*(px-x),fitting["y"],prepared=True)
                    pred=clf.predict(vx+strength*(pvx-vx),prepared=True)
                    candidates.append(dict(epochs=epochs,strength=strength,correct=int((pred==validation["y"]).sum()),n=len(pred)))
            selected=max(candidates,key=lambda r:(r["correct"],-r["strength"],-r["epochs"]))
            (dest/"selection.json").write_text(json.dumps(dict(selected=selected,candidates=candidates),indent=2)+"\n")
            s=setup(tr,seed,options,lateralized);history=train(s,tr,options,selected["epochs"],seed)
            x,px=transform(s,tr,options,seed);vx,pvx=transform(s,te,options,seed);strength=selected["strength"]
            clf=CSPClassifier(task_labels=labels).fit(x+strength*(px-x),tr["y"],prepared=True)
            pred=clf.predict(vx+strength*(pvx-vx),prepared=True)
            full=CSPClassifier(task_labels=labels).fit(px,tr["y"],prepared=True).predict(pvx,prepared=True)
            row=result(name,pred,seed=seed,selected=selected,full_strength=result("full_strength",full),losses=history,calibration=s["calibration"])
            reports.append(row);clf.save(dest/"classifier.npz",dict(requires_diffusion=True,variant=name))
            torch.save(dict(state_dict=s["model"].state_dict(),config=s["model"].config,mean=s["mean"].tolist(),scale=s["scale"].tolist(),
                rest_encoder=s["encoder"].metadata(),variant=name,uses_rest=options["rest"],condition=options["condition"],
                strength=strength,step=3,fs=250.,channels=MI_CHANNELS,samples=tx.shape[-1]),dest/"diffusion.pt")
            (dest/"report.json").write_text(json.dumps(row,ensure_ascii=False,indent=2)+"\n")
            print(name,seed,row["correct"],"/",row["total"],flush=True)
    (a.out/"report.json").write_text(json.dumps(dict(protocol=protocol,results=reports),ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__":main()
