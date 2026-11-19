#!/usr/bin/env python3
"""Compare internal harmonic score aggregation on the same subject-1 split."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_subject1_bn_fbcca import RUNS, metrics
from src.bci.config import COMMANDS
from src.bci.preprocessing import prepare_ssvep_channels, FILTER_BANK_WEIGHTS
from src.bci.fbcca_components import causal_idle_background
from src.bci.harmonic_fbcca import individual_harmonic_components, combine_harmonic_evidence

OUT = ROOT / "outputs/subject1_harmonic_evidence_20261101"
PRIOR = ROOT / "outputs/subject1_bn_refinement_20261101"
AUDIT = ROOT / "outputs/subject1_bn_audit_20261101"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def extract(records):
    hashes = [hashlib.sha256((ROOT/"data/research_experiments/raw_trials"/r["file"]).read_bytes()).hexdigest() for r in records]
    assert hashes == [r["sha256"] for r in records]
    cache = OUT / "individual_harmonics.npz"
    if cache.exists():
        with np.load(cache, allow_pickle=False) as p:
            assert p["hashes"].tolist() == hashes
            return p["values"]
    values = np.zeros((len(records), 3, 6, 4))
    for i, row in enumerate(records):
        with np.load(ROOT/"data/research_experiments/raw_trials"/row["file"]) as p:
            try:
                data, _, _ = prepare_ssvep_channels(p["eeg"], range(1, 9), float(p["fs"]))
            except ValueError:
                continue
            values[i] = individual_harmonic_components(data, float(p["fs"]))
        if (i+1)%30 == 0:
            print(f"Individual harmonics {i+1}/270", flush=True)
    np.savez_compressed(cache, values=values, hashes=hashes)
    return values


def select(joint, separate, records, valid):
    old = np.array([i for i,r in enumerate(records) if r["run"] in RUNS[:2]])
    old_records = [records[i] for i in old]
    candidates = []
    for exponent in (0., 1., 2.):
        for mix in (0., .25, .5, .75, 1.):
            for weights in (FILTER_BANK_WEIGHTS, (1., 1., .5)):
                config = {"mix": mix, "exponent": exponent, "bank_weights": list(weights)}
                prediction = combine_harmonic_evidence(joint[old], separate[old], **config).argmax(1)
                result = metrics(old_records, prediction, valid[old], np.arange(len(old)))
                days = [metrics(old_records, prediction, valid[old], [i for i,r in enumerate(old_records) if r["run"] == run]) for run in RUNS[:2]]
                candidates.append({"config": config, "old": result, "old_sessions": days})
    candidates.sort(key=lambda c:(-c["old"]["correct"],-min(d["accuracy"] for d in c["old_sessions"]),c["config"]["mix"],c["config"]["exponent"]))
    selected = candidates[0]["config"]
    save(OUT / "old_batch_search.json", {"candidate_count":len(candidates),"candidates":candidates})
    save(OUT / "selected_config.json", {"selection_runs":RUNS[:2],"evaluation_run":RUNS[2],**selected})
    print("Frozen from OLD only",selected,'old correct',candidates[0]["old"]["correct"],flush=True)
    return selected,candidates


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = json.loads((AUDIT/"report.json").read_text())["records"]
    with np.load(AUDIT/"scores.npz") as p:
        valid = p["valid_5s"]
    with np.load(PRIOR/"components.npz") as p:
        joint = p["components"][:, 3]
    separate = extract(records)
    runs, idle = [r["run"] for r in records], [r["label"]=="空闲" for r in records]
    joint_bg,_ = causal_idle_background(joint,runs,idle,valid)
    separate_bg,_ = causal_idle_background(separate,runs,idle,valid)
    joint, separate = joint-joint_bg, separate-separate_bg
    selected,candidates=select(joint,separate,records,valid)
    configs={"previous_BN":{"mix":0.,"exponent":0.,"bank_weights":list(FILTER_BANK_WEIGHTS)},
             "previous_reweighted":{"mix":0.,"exponent":0.,"bank_weights":[1.,1.,.5]},"selected":selected}
    for candidate in candidates:
        c=candidate["config"]
        configs.setdefault(f'best_old_mix_{c["mix"]}',c)
        configs.setdefault(f'best_old_exponent_{c["exponent"]}',c)
    groups={"all":np.arange(len(records)),"old":[i for i,r in enumerate(records) if r["run"] in RUNS[:2]],"new":[i for i,r in enumerate(records) if r["run"]==RUNS[2]]}
    scores={k:combine_harmonic_evidence(joint,separate,**c) for k,c in configs.items()}
    preds={k:np.where(valid,s.argmax(1),-1) for k,s in scores.items()}
    methods={k:{"config":configs[k],**{g:metrics(records,p,valid,ix) for g,ix in groups.items()}} for k,p in preds.items()}
    truth=np.array([COMMANDS.index(r["label"]) if r["label"] in COMMANDS else -2 for r in records])
    transitions={}
    for name in ("previous_BN","previous_reweighted"):
        transitions[name]={}
        for g,ix in groups.items():
            ix=np.array([i for i in ix if truth[i]>=0]);a,b,y=preds[name][ix],preds["selected"][ix],truth[ix]
            transitions[name][g]={"fixed":int(np.sum((a!=y)&(b==y))),"harmed":int(np.sum((a==y)&(b!=y)))}
    ledger=[]
    for i,r in enumerate(records):
        if truth[i]<0:continue
        item={**r,"batch":"new" if r["run"]==RUNS[2] else "old","predictions":{},"correct":{},"scores":{}}
        for k in ("previous_BN","previous_reweighted","selected"):
            p=preds[k][i];item["predictions"][k]=COMMANDS[p] if p>=0 else "数据无效";item["correct"][k]=bool(p==truth[i]);item["scores"][k]=scores[k][i].tolist()
        ledger.append(item)
    save(OUT/"report.json",{"methods":methods,"transitions":transitions,"selected":selected})
    save(OUT/"all_231_commands.json",ledger)
    np.savez_compressed(OUT/"scores.npz",**scores)
    for name,m in methods.items():print(name,{g:f'{m[g]["correct"]}/{m[g]["total"]}' for g in groups},flush=True)
    print('Changes',transitions,flush=True)
    with np.load(PRIOR/"scores.npz") as p:
        np.testing.assert_allclose(scores["previous_BN"][valid],p["previous_BN"][valid],atol=1e-12,rtol=1e-12)
        np.testing.assert_allclose(scores["previous_reweighted"][valid],p["selected"][valid],atol=1e-12,rtol=1e-12)


if __name__=="__main__":
    main()
