#!/usr/bin/env python3
"""MI-only single-pass diffusion ablations with training-session selection.

All choices (epochs, residual strength, normalization, resting descriptors,
ERD targets) use T-session data. E-session is evaluated after freezing them.
The public dataset has no SSVEP condition, and no pseudo-pairing is created.
"""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time
import numpy as np
import torch
from sklearn.model_selection import GroupKFold
from sklearn.metrics import confusion_matrix

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.ml.public_mi import load_bnci_session
from src.ml.mi_baseline import CSPClassifier,preprocess_mi,BCI2A_CHANNELS,MI_CHANNELS
from src.ml.mi_diffusion import MIDiffusion
from src.ml.mi_research import RestDescriptor,calibrate_erd,calibrated_diffusion_loss

VARIANTS=("noise_only","physics","physics_rest")
SEEDS=(20261101,20261102,20261103)


def ability(x,y,groups):
    pred=np.full(len(y),-1,int)
    for tr,va in GroupKFold(len(np.unique(groups))).split(x,y,groups):
        model=CSPClassifier().fit(x[tr],y[tr],prepared=True);pred[va]=model.predict(x[va],prepared=True)
    return float((pred==y).mean())


def training_setup(raw,rest,y,groups,seed):
    x=preprocess_mi(raw,250.);b=preprocess_mi(rest,250.)
    mu=x.mean((0,2),keepdims=True);sd=x.std((0,2),keepdims=True).clip(1e-6)
    enc=RestDescriptor().fit(rest);sub=enc.transform(rest)
    cal=calibrate_erd(x,b,y,ability(x,y,groups))
    torch.manual_seed(seed)
    model=MIDiffusion(subject_features=9)
    return dict(x=x,mean=mu,scale=sd,encoder=enc,calibration=cal,model=model,
        xt=torch.tensor((x-mu)/sd,dtype=torch.float32),bt=torch.tensor((b-mu)/sd,dtype=torch.float32),
        sub=torch.tensor(sub),y=torch.tensor(y,dtype=torch.long))


def train_epoch(state,optimizer,variant):
    model=state["model"];model.train();losses=[]
    for ix in torch.randperm(len(state["xt"])).split(32):
        sub=state["sub"][ix] if variant=="physics_rest" else torch.zeros(len(ix),9)
        loss,_=calibrated_diffusion_loss(model,state["xt"][ix],torch.zeros(len(ix)),torch.zeros(len(ix)),sub,
            state["bt"][ix],state["y"][ix],state["calibration"],physics=variant!="noise_only")
        if not torch.isfinite(loss):raise RuntimeError("Nonfinite MI objective")
        optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
        losses.append(float(loss.detach()))
    return float(np.mean(losses))


def transform(state,raw,rest,variant):
    model=state["model"];model.eval();x=preprocess_mi(raw,250.)
    tensor=torch.tensor((x-state["mean"])/state["scale"],dtype=torch.float32)
    features=state["encoder"].transform(rest) if variant=="physics_rest" else np.zeros((len(x),9),np.float32)
    parts=[]
    for offset in range(0,len(x),32):
        sl=slice(offset,offset+32);n=len(tensor[sl]);sub=torch.tensor(features[sl])
        parts.append(model.single_pass(tensor[sl],torch.zeros(n),torch.zeros(n),sub).numpy())
    result=np.concatenate(parts)*state["scale"]+state["mean"]
    return x,result


def main():
    p=argparse.ArgumentParser();p.add_argument("--subjects",nargs="+",type=int,default=list(range(1,10)))
    p.add_argument("--seeds",nargs="+",type=int,default=list(SEEDS))
    p.add_argument("--out",type=Path,default=ROOT/"outputs/mi_diffusion_research_20261101");a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(2)
    protocol=dict(variants=VARIANTS,seeds=a.seeds,candidate_epochs=[12,24],residual_strengths=[0.,.25,.5,1.],
        selection="First four T-session runs train; last two T-session runs validate; refit all T; evaluate E",
        inference="single network forward, deterministic residual, no added noise",SSVEP_condition="unavailable_public_MI",
        all_trials_included=True)
    (a.out/"protocol.json").write_text(json.dumps(protocol,indent=2)+"\n")
    indices=[BCI2A_CHANNELS.index(n) for n in MI_CHANNELS]
    for subject in a.subjects:
        # Concurrent invocations must not train/write the same subject's artifacts.
        subject_lock=(a.out/f"A{subject:02d}.lock").open("a")
        fcntl.flock(subject_lock,fcntl.LOCK_EX)
        train_path=ROOT/f"data/public_mi/bnci2014_001/A{subject:02d}T.mat"
        test_path=train_path.with_name(f"A{subject:02d}E.mat")
        train=load_bnci_session(train_path);use=train["y"]<2
        raw=train["eeg"][use][:,indices];rest=train["baseline"][use][:,indices];y=train["y"][use];groups=train["run"][use]
        x=preprocess_mi(raw,250.);oof=ability(x,y,groups)
        val=np.isin(groups,np.unique(groups)[-2:]);fit=~val
        for seed in a.seeds:
            for variant in VARIANTS:
                dest=a.out/f"A{subject:02d}_{variant}_{seed}";dest.mkdir(parents=True,exist_ok=True)
                if (dest/"report.json").exists():continue
                started=time.monotonic();state=training_setup(raw[fit],rest[fit],y[fit],groups[fit],seed)
                optimizer=torch.optim.AdamW(state["model"].parameters(),lr=.001,weight_decay=.0001)
                candidates=[]
                for epoch in range(1,25):
                    train_epoch(state,optimizer,variant)
                    if epoch not in (12,24):continue
                    original,processed=transform(state,raw,rest,variant)
                    for strength in (0.,.25,.5,1.):
                        xx=original+strength*(processed-original)
                        clf=CSPClassifier().fit(xx[fit],y[fit],prepared=True);pred=clf.predict(xx[val],prepared=True)
                        candidates.append(dict(epochs=epoch,strength=strength,validation_correct=int((pred==y[val]).sum()),validation_n=int(val.sum())))
                chosen=max(candidates,key=lambda r:(r["validation_correct"],-r["strength"],-r["epochs"]))
                (dest/"selection.json").write_text(json.dumps(dict(selected=chosen,candidates=candidates,test_read=False),indent=2)+"\n")
                state=training_setup(raw,rest,y,groups,seed)
                optimizer=torch.optim.AdamW(state["model"].parameters(),lr=.001,weight_decay=.0001);history=[]
                for epoch in range(chosen["epochs"]):history.append(train_epoch(state,optimizer,variant))
                original,processed=transform(state,raw,rest,variant);train_x=original+chosen["strength"]*(processed-original)
                clf=CSPClassifier().fit(train_x,y,prepared=True)
                # No E data or E labels were used by selection, fitting, or calibration.
                test=load_bnci_session(test_path);use_test=test["y"]<2;ty=test["y"][use_test]
                test_raw=test["eeg"][use_test][:,indices];test_rest=test["baseline"][use_test][:,indices]
                tx,px=transform(state,test_raw,test_rest,variant);test_x=tx+chosen["strength"]*(px-tx)
                pred=clf.predict(test_x,prepared=True)
                baseline=CSPClassifier().fit(x,y,prepared=True);baseline_pred=baseline.predict(tx,prepared=True)
                row=dict(subject=subject,variant=variant,seed=seed,selected=chosen,train_n=len(y),test_n=len(ty),
                    baseline_correct=int((baseline_pred==ty).sum()),correct=int((pred==ty).sum()),
                    accuracy=float((pred==ty).mean()),baseline_accuracy=float((baseline_pred==ty).mean()),
                    training_oof_ability=oof,calibration=state["calibration"],rest_descriptor=state["encoder"].metadata(),
                    labels=ty.tolist(),predictions=pred.tolist(),baseline_predictions=baseline_pred.tolist(),
                    confusion=confusion_matrix(ty,pred,labels=[0,1]).tolist(),training_losses=history,
                    elapsed_seconds=time.monotonic()-started,condition_source="none_public_MI_unpaired",
                    inference="one deterministic forward",paired_benefit_tested=False)
                clf.save(dest/"classifier.npz",dict(requires_diffusion=True,variant=variant))
                torch.save(dict(state_dict=state["model"].state_dict(),config=state["model"].config,
                    mean=state["mean"].tolist(),scale=state["scale"].tolist(),rest_encoder=state["encoder"].metadata(),
                    variant=variant,seed=seed,strength=chosen["strength"],step=3,fs=250.,channels=MI_CHANNELS,samples=750),dest/"diffusion.pt")
                (dest/"report.json").write_text(json.dumps(row,indent=2)+"\n")
                print(subject,seed,variant,"selected",chosen,"test",row["correct"],"/",len(ty),flush=True)
        fcntl.flock(subject_lock,fcntl.LOCK_UN);subject_lock.close()
    rows=[json.loads(p.read_text()) for p in sorted(a.out.glob("A*/report.json"))]
    (a.out/"report.json").write_text(json.dumps(dict(protocol=protocol,results=rows),indent=2)+"\n")

if __name__=="__main__":main()
