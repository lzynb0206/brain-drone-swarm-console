#!/usr/bin/env python3
"""Four anatomical channel scopes, fixed full-window QC and causal idle BN."""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.audit_subject1_bn_fbcca import RUNS,metrics
from src.bci.config import COMMANDS
from src.bci.fbcca_components import causal_idle_background
from src.bci.regularized_fbcca import regularized_components
from src.bci.preprocessing import prepare_ssvep_channels,FILTER_BANK_WEIGHTS

OUT=ROOT/"outputs/subject1_channel_scope_20261101"
AUDIT=ROOT/"outputs/subject1_bn_audit_20261101"
SCOPES={"all8":(1,2,3,4,5,6,7,8),"posterior6":(1,2,3,4,5,6),"occipital3":(1,2,3),"midline2":(2,5)}


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def select(corrected,valid,records):
    old=np.array([i for i,r in enumerate(records) if r["run"] in RUNS[:2]])
    old_records=[records[i] for i in old];candidates=[]
    for scope in SCOPES:
        for weights in (list(FILTER_BANK_WEIGHTS),[1.,1.,.5]):
            pred=np.einsum("nbk,b->nk",corrected[scope][old],weights).argmax(1)
            result=metrics(old_records,pred,valid[scope][old],np.arange(len(old)))
            days=[metrics(old_records,pred,valid[scope][old],[i for i,r in enumerate(old_records) if r["run"]==run]) for run in RUNS[:2]]
            candidates.append({"config":{"scope":scope,"bank_weights":weights},"old":result,"old_sessions":days})
    candidates.sort(key=lambda c:(-c["old"]["correct"],-min(d["accuracy"] for d in c["old_sessions"]),c["config"]["scope"]!="all8"))
    save(OUT/"old_batch_search.json",{"candidate_count":len(candidates),"candidates":candidates})
    selected=candidates[0]["config"]
    save(OUT/"selected_config.json",{"selection_runs":RUNS[:2],"evaluation_run":RUNS[2],**selected})
    print('Frozen from OLD only',selected,'old correct',candidates[0]["old"]["correct"],flush=True)
    return selected,candidates


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    records=json.loads((AUDIT/"report.json").read_text())["records"]
    with np.load(AUDIT/"scores.npz") as p:original_valid=p["valid_5s"]
    values={k:np.zeros((len(records),3,6)) for k in SCOPES};valid={k:np.zeros(len(records),bool) for k in SCOPES}
    diagnostics=[]
    for i,r in enumerate(records):
        path=ROOT/"data/research_experiments/raw_trials"/r["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==r["sha256"]
        with np.load(path) as p:eeg,fs=p["eeg"],float(p["fs"])
        info={"file":r["file"],"scopes":{}}
        for scope,channels in SCOPES.items():
            try:data,used,_=prepare_ssvep_channels(eeg,channels,fs)
            except ValueError:continue
            values[scope][i]=regularized_components(data,fs,(0.,))[0][0]
            valid[scope][i]=original_valid[i] and len(used)>=2
            info["scopes"][scope]={"used_channels":used,"valid":bool(valid[scope][i])}
        diagnostics.append(info)
        if (i+1)%30==0:print(f'Channel scopes {i+1}/270',flush=True)
    save(OUT/"channel_diagnostics.json",diagnostics)
    corrected={}
    for k in SCOPES:
        bg,_=causal_idle_background(values[k],[r["run"] for r in records],[r["label"]=="空闲" for r in records],valid[k])
        corrected[k]=values[k]-bg
    np.savez_compressed(OUT/"components.npz",**{f'raw_{k}':v for k,v in values.items()},**{f'corrected_{k}':v for k,v in corrected.items()},**{f'valid_{k}':v for k,v in valid.items()})
    selected,candidates=select(corrected,valid,records)
    configs={"previous_BN":{"scope":"all8","bank_weights":list(FILTER_BANK_WEIGHTS)},"previous_reweighted":{"scope":"all8","bank_weights":[1.,1.,.5]},"selected":selected}
    for candidate in candidates:configs.setdefault('best_old_'+candidate["config"]["scope"],candidate["config"])
    groups={"all":np.arange(len(records)),"old":[i for i,r in enumerate(records) if r["run"] in RUNS[:2]],"new":[i for i,r in enumerate(records) if r["run"]==RUNS[2]]}
    methods={};scores={};preds={}
    for k,c in configs.items():
        score=np.einsum('nbk,b->nk',corrected[c["scope"]],c["bank_weights"]);pred=np.where(valid[c["scope"]],score.argmax(1),-1)
        scores[k]=score;preds[k]=pred;methods[k]={"config":c,**{g:metrics(records,pred,valid[c["scope"]],ix) for g,ix in groups.items()}}
        print(k,{g:f'{methods[k][g]["correct"]}/{methods[k][g]["total"]}' for g in groups},flush=True)
    save(OUT/"report.json",{"methods":methods,"selected":selected})
    np.savez_compressed(OUT/"scores.npz",**scores)
    with np.load(ROOT/"outputs/subject1_bn_refinement_20261101/scores.npz") as p:
        np.testing.assert_allclose(scores["previous_BN"][original_valid],p["previous_BN"][original_valid],atol=1e-12,rtol=1e-12)
        np.testing.assert_allclose(scores["previous_reweighted"][original_valid],p["selected"][original_valid],atol=1e-12,rtol=1e-12)


if __name__=='__main__':main()
