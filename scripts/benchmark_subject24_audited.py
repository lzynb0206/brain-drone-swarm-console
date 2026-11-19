#!/usr/bin/env python3
"""Audited six-method, trial-level evaluation of the user's confirmed subject A.

All settings are selected inside training folds. Raw recordings, production
models and earlier benchmark artifacts are never modified. Neural results refer
to the project's adaptations, not certified reproductions of published papers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

# This environment contains two OpenMP libraries (sklearn and torch). Both must
# remain single-threaded here; mixed multithreaded kernels crash in libomp.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import numpy as np
from scipy import linalg
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_recent_ssvep_methods import Trial, prepare_data
from src.bci.config import COMMANDS, SSVEP_CHANNEL_NAMES, TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.preprocessing import assess_signal_quality
from src.ml.fb_eegnet import FBEEGNetFusion
from src.ml.recent_ssvep_models import SSVEPTFFNet, BrainWaveDecoderAdaptation

RUNS = ("auto-20261024-145545-3730b3", "auto-20261027-204129-9c221a")
METHODS = ("FBCCA", "Dual-FBCCA+LR", "FB-eTRCA", "FB-EEGNet", "SSVEP-TFFNet adapted", "CNN-BiGRU adapted")
SEEDS = (17, 29, 43)
SPLIT_SEED = 20261028


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def load_data():
    trials, records, hashes = [], [], set()
    for run, expected in zip(RUNS, (60, 105)):
        paths = sorted((ROOT / "data/research_experiments/raw_trials").glob(run + "-trial-*.npz"))
        assert len(paths) == expected, (run, len(paths))
        for path in paths:
            with np.load(path, allow_pickle=False) as p:
                x = p["eeg"].astype(np.float64)
                label = str(p["true_label"].item())
                context = json.loads(str(p["trial_context"].item()))
                ts = p["sample_timestamps"].astype(np.float64)
                assert float(p["fs"]) == 250 and x.shape == (8, 1250)
                assert tuple(p["channel_names"].tolist()) == SSVEP_CHANNEL_NAMES
                assert np.isfinite(x).all() and np.isfinite(ts).all()
                assert context["target_frequencies_hz"] == list(TARGET_FREQUENCIES_HZ)
                assert context["true_label"] == label
                assert label in (*COMMANDS, "空闲")
                assert context.get("analysis_start_offset_seconds", 0) == 0
                digest = hashlib.sha256(x.tobytes()).hexdigest()
                assert digest not in hashes, "Duplicated raw trial"
                hashes.add(digest)
                block = int(context["block_index"])
                y = COMMANDS.index(label) if label in COMMANDS else -1
                quality = assess_signal_quality(x, 250)
                records.append(dict(source_file=path.name, run_id=run, subject_id="subject_A_user_confirmed",
                    block=block, group=f"{run}:{block}", true_label=label, label_index=y,
                    eeg_sha256=digest, file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    usable_channels=int(quality["usable_channel_count"]),
                    timestamp_gap_seconds=float(np.diff(ts).max()),
                    timestamp_span_seconds=float(ts[-1] - ts[0]),
                    capture_onset_delay_seconds=float(ts[0] - float(p["stimulus_lsl_time"]))))
                trials.append(Trial(path, run, block, y, x))
    assert Counter(r["true_label"] for r in records) == Counter(dict(zip((*COMMANDS,"空闲"),(23,23,24,24,23,24,24))))
    prepared = prepare_data(trials)
    baseline = FBCCADecoder(min_score=0, min_margin=0)
    baseline_rows=[]
    for t,r in zip(trials,records):
        try:
            result=baseline.decode(t.eeg,250)
            baseline_rows.append([result.scores[c] for c in COMMANDS])
        except ValueError as exc:
            if "没有可用 EEG 通道" not in str(exc):raise
            assert r["usable_channels"]==0
            baseline_rows.append([0.]*6)
    scores = np.array(baseline_rows)
    return prepared, records, scores


def split_groups(records, indices, validation_fraction, seed):
    rng = np.random.default_rng(seed)
    validation_groups = set()
    for run in sorted({records[i]["run_id"] for i in indices}):
        groups = sorted({records[i]["group"] for i in indices if records[i]["run_id"] == run})
        shuffled = rng.permutation(groups)
        count = max(1, round(len(groups) * validation_fraction))
        validation_groups.update(shuffled[:count])
    val = np.array([i for i in indices if records[i]["group"] in validation_groups], int)
    fit = np.array([i for i in indices if records[i]["group"] not in validation_groups], int)
    return fit, val


def outer_folds(records):
    rng = np.random.default_rng(SPLIT_SEED)
    fold_by_group = {}
    for run in RUNS:
        groups = sorted({r["group"] for r in records if r["run_id"] == run})
        for fold, chunk in enumerate(np.array_split(rng.permutation(groups), 5)):
            fold_by_group.update({str(g): fold for g in chunk})
    result = []
    for fold in range(5):
        test = np.array([i for i,r in enumerate(records) if fold_by_group[r["group"]] == fold])
        train = np.array([i for i,r in enumerate(records) if fold_by_group[r["group"]] != fold])
        result.append((f"grouped_fold_{fold+1}", train, test))
    result.append(("cross_day_oct24_to_oct27",
        np.array([i for i,r in enumerate(records) if r["run_id"] == RUNS[0]]),
        np.array([i for i,r in enumerate(records) if r["run_id"] == RUNS[1]])))
    return result


def active(indices, truth):
    return np.array([i for i in indices if truth[i] >= 0], int)


def balanced_subset(indices, records, fraction, seed):
    """Take whole training blocks; never choose trials using their predictions."""
    if fraction == 1:
        return indices.copy()
    rng = np.random.default_rng(seed)
    groups = sorted({records[i]["group"] for i in indices})
    take = set(rng.permutation(groups)[:max(3, round(len(groups)*fraction))])
    out = np.array([i for i in indices if records[i]["group"] in take], int)
    assert min(Counter(records[i]["label_index"] for i in out).values()) >= 2
    assert set(records[i]["label_index"] for i in out) == set(range(6))
    return out


def trca_fit(banks, truth, train, ridge=1e-3):
    templates, filters = [], []
    for band in range(banks.shape[1]):
        tm, ws = [], []
        for cls in range(6):
            x = banks[train[truth[train] == cls], band].astype(np.float64).copy()
            assert x.shape[0] >= 2
            x -= x.mean(axis=-1, keepdims=True)
            q = np.einsum("nct,ndt->cd", x, x)
            summed = x.sum(axis=0)
            s = summed @ summed.T - q
            qreg = q + ridge*max(np.trace(q)/q.shape[0], 1e-12)*np.eye(q.shape[0])
            vals, vecs = linalg.eigh(s, qreg)
            w = vecs[:,np.argmax(vals)]
            w /= np.linalg.norm(w)
            ws.append(w); tm.append(x.mean(axis=0))
        filters.append(np.column_stack(ws)); templates.append(tm)
    return np.array(templates), np.array(filters)


def trca_scores(banks, indices, model):
    templates, filters = model
    out = np.zeros((len(indices), 6))
    for band in range(banks.shape[1]):
        w = filters[band].T
        x = np.einsum("dc,nct->ndt", w, banks[indices,band]).reshape(len(indices),-1)
        tm = np.einsum("dc,kct->kdt", w, templates[band]).reshape(6,-1)
        x = x-x.mean(axis=1,keepdims=True); tm = tm-tm.mean(axis=1,keepdims=True)
        corr = (x @ tm.T) / np.maximum(np.linalg.norm(x,axis=1)[:,None]*np.linalg.norm(tm,axis=1)[None,:],1e-12)
        out += ((band+1)**-1.25+.25)*np.sign(corr)*corr**2
    assert np.isfinite(out).all()
    return out


def classical_tune(method, data, records, fit, val, outer_train, folder):
    trials = []
    for fraction in (.5, 1.):
        selected = balanced_subset(fit, records, fraction, SPLIT_SEED)
        for parameter in ((.01,.1,1.) if method == METHODS[1] else (.001,.01,.1)):
            if method == METHODS[1]:
                scaler = StandardScaler().fit(data.dual_features[selected])
                model = LogisticRegression(C=parameter, max_iter=5000).fit(scaler.transform(data.dual_features[selected]), data.truth[selected])
                assert model.n_iter_.max() < 5000
                score = model.predict_proba(scaler.transform(data.dual_features[val]))
            else:
                model = trca_fit(data.trca_banks,data.truth,selected,parameter)
                score = trca_scores(data.trca_banks,val,model)
            trials.append(dict(fraction=fraction,parameter=parameter,fit_active=len(selected),
                validation_correct=int((score.argmax(1)==data.truth[val]).sum()),validation_count=len(val)))
    # Equal validation accuracy: prefer all data, then less model flexibility.
    best = max(trials,key=lambda v:(v["validation_correct"],v["fraction"],-v["parameter"] if method==METHODS[1] else v["parameter"]))
    train = balanced_subset(outer_train, records,best["fraction"],SPLIT_SEED)
    if method == METHODS[1]:
        scaler=StandardScaler().fit(data.dual_features[train])
        model=LogisticRegression(C=best["parameter"],max_iter=5000).fit(scaler.transform(data.dual_features[train]),data.truth[train])
        scores=model.predict_proba(scaler.transform(data.dual_features))
        np.savez_compressed(folder/"model.npz",mean=scaler.mean_,scale=scaler.scale_,weights=model.coef_,bias=model.intercept_,classes=model.classes_)
        with np.load(folder/"model.npz") as p:
            z=(data.dual_features-p["mean"])/p["scale"]
            logits=z@p["weights"].T+p["bias"]; logits-=logits.max(axis=1,keepdims=True)
            check=np.exp(logits); check/=check.sum(axis=1,keepdims=True)
            assert np.allclose(check,scores,atol=1e-10)
    else:
        model=trca_fit(data.trca_banks,data.truth,train,best["parameter"])
        scores=trca_scores(data.trca_banks,np.arange(len(records)),model)
        np.savez_compressed(folder/"model.npz",templates=model[0],filters=model[1])
        with np.load(folder/"model.npz") as p:
            check=trca_scores(data.trca_banks,np.arange(len(records)),(p["templates"],p["filters"]))
            assert np.allclose(check,scores,atol=1e-10)
    audit=dict(candidates=trials,selected=best,final_training_active=len(train),source_files=[records[i]["source_file"] for i in train],reload_prediction_verified=True)
    save_json(folder/"training.json",audit)
    return scores, audit


def network(method):
    if method == METHODS[3]: return FBEEGNetFusion(channels=8,bands=4,classes=6)
    if method == METHODS[4]: return SSVEPTFFNet(channels=8,classes=6)
    if method == METHODS[5]: return BrainWaveDecoderAdaptation(channels=8,classes=6)
    raise ValueError(method)


def arrays_for(method,data):
    if method == METHODS[3]: return data.eegnet_clean,data.eegnet_banks
    if method == METHODS[4]: return data.tff_temporal,data.tff_spectrum
    return (data.brainwave_raw,)


def seed_torch(seed):
    import torch
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def infer(model, arrays, indices):
    import torch
    model.eval()
    scores=[]
    with torch.no_grad():
        for ix in np.array_split(np.array(indices,int), max(1,(len(indices)+31)//32)):
            if len(ix): scores.append(torch.softmax(model(*(torch.from_numpy(a[ix]) for a in arrays)),dim=1).numpy())
    result=np.concatenate(scores)
    assert np.isfinite(result).all() and np.allclose(result.sum(axis=1),1,atol=1e-5)
    return result


def neural_fit(method, arrays, truth, train, seed, lr, epochs, val=None):
    import torch
    seed_torch(seed); model=network(method)
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=1e-3)
    criterion=torch.nn.CrossEntropyLoss(label_smoothing=.05)
    rng=np.random.default_rng(seed); history=[]; best_key=None; best_epoch=0
    start=time.perf_counter(); initial=None; best=None
    for epoch in range(1,epochs+1):
        model.train(); shuffled=rng.permutation(train); losses=[]
        for ix in np.array_split(shuffled,max(1,int(np.ceil(len(train)/32)))):
            opt.zero_grad(set_to_none=True)
            logits=model(*(torch.from_numpy(a[ix]) for a in arrays))
            loss=criterion(logits,torch.from_numpy(truth[ix]))
            assert torch.isfinite(loss), (method,epoch)
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            assert torch.isfinite(norm) and norm > 0
            opt.step(); losses.append(float(loss.detach()))
        value=float(np.mean(losses))
        if initial is None: initial=value
        record=dict(epoch=epoch,training_loss=value)
        if val is not None and (epoch%5==0 or epoch==epochs):
            p=infer(model,arrays,val); n=int((p.argmax(1)==truth[val]).sum())
            nll=float(-np.log(np.maximum(p[np.arange(len(val)),truth[val]],1e-12)).mean())
            record.update(validation_correct=n,validation_count=len(val),validation_nll=nll)
            key=(n,-nll)
            if best_key is None or key>best_key:
                best_key=key; best_epoch=epoch
                best={k:v.detach().clone() for k,v in model.state_dict().items()}
            if epoch>=30 and epoch-best_epoch>=25:
                history.append(record); break
        history.append(record)
    if val is not None:
        model.load_state_dict(best)
    train_scores=infer(model,arrays,train)
    info=dict(seed=seed,learning_rate=lr,epochs_run=epoch,selected_epoch=best_epoch or epoch,
        first_training_loss=initial,last_training_loss=value,training_accuracy=float((train_scores.argmax(1)==truth[train]).mean()),
        parameter_count=sum(p.numel() for p in model.parameters()),seconds=time.perf_counter()-start,history=history)
    if best_key is not None: info.update(validation_correct=best_key[0],validation_nll=-best_key[1],validation_count=len(val))
    return model,info


def neural_tune(method,data,records,fit,val,outer_train,folder,max_epochs):
    import torch
    arrays=arrays_for(method,data); candidates=[]
    for lr in (.001,.003):
        print(f"    {method}: inner validation lr={lr}",flush=True)
        model,info=neural_fit(method,arrays,data.truth,fit,101,lr,max_epochs,val)
        candidates.append(info)
        save_json(folder/f"validation_lr_{lr}.json",info)
    selected=max(candidates,key=lambda d:(d["validation_correct"],-d["validation_nll"]))
    all_scores=[]; runs=[]
    for seed in SEEDS:
        print(f"    {method}: refit seed={seed}, epochs={selected['selected_epoch']}",flush=True)
        model,info=neural_fit(method,arrays,data.truth,outer_train,seed,selected["learning_rate"],selected["selected_epoch"])
        scores=infer(model,arrays,np.arange(len(records)))
        checkpoint=folder/f"seed_{seed}.pt"
        torch.save(model.state_dict(),checkpoint)
        restored=network(method); restored.load_state_dict(torch.load(checkpoint,map_location="cpu",weights_only=True))
        check=infer(restored,arrays,np.arange(len(records)))
        assert np.allclose(scores,check,atol=1e-7),method
        all_scores.append(scores);runs.append(info)
        save_json(folder/f"refit_seed_{seed}.json",info)
    scores=np.mean(all_scores,axis=0)
    audit=dict(candidates=candidates,selected={k:v for k,v in selected.items() if k!="history"},
        final_training_active=len(outer_train),source_files=[records[i]["source_file"] for i in outer_train],
        seeds=list(SEEDS),seed_predictions_combination="arithmetic mean of 3 softmax vectors, then argmax; never best test seed",
        refits=runs,reload_prediction_verified=True)
    save_json(folder/"training.json",audit)
    np.savez_compressed(folder/"all_seed_scores.npz",scores=np.stack(all_scores))
    return scores,audit


def sanity(out):
    """Known-frequency decoding plus independent TRCA reference and gradient checks."""
    import torch
    from scripts.benchmark_recent_ssvep_methods import fit_predict_fb_etrca
    rng=np.random.default_rng(98); trials=[]
    t=np.arange(1250)/250
    for rep in range(5):
        for k,f in enumerate(TARGET_FREQUENCIES_HZ):
            phase=np.arange(8)[:,None]*.12
            x=10*np.sin(2*np.pi*f*t+phase)+4*np.sin(4*np.pi*f*t+phase)+rng.normal(size=(8,1250))
            trials.append(Trial(Path(f"synthetic_{rep}_{k}"),"synthetic",rep,k,x))
    data=prepare_data(trials); train=np.arange(24); test=np.arange(24,30)
    model=trca_fit(data.trca_banks,data.truth,train)
    a=trca_scores(data.trca_banks,test,model).argmax(1)
    b=fit_predict_fb_etrca(data.trca_banks,data.truth,train,test)
    assert np.array_equal(a,b) and np.array_equal(a,data.truth[test])
    dec=FBCCADecoder(min_score=0,min_margin=0)
    fb=[COMMANDS.index(dec.decode(trials[i].eeg,250).label) for i in test]
    assert np.array_equal(fb,data.truth[test])
    scaler=StandardScaler().fit(data.dual_features[train]); lr=LogisticRegression(C=.1,max_iter=5000).fit(scaler.transform(data.dual_features[train]),data.truth[train])
    assert np.array_equal(lr.predict(scaler.transform(data.dual_features[test])),data.truth[test])
    results=dict(known_frequencies_fbcca_correct="6/6",known_frequencies_lr_correct="6/6",known_frequencies_trca_correct="6/6",trca_independent_implementation_agreement=True,neural={})
    # A complete fixed minibatch must be learnable, not only shape-compatible.
    for method in METHODS[3:]:
        print(f"Sanity: fitting known-frequency signals for {method}",flush=True)
        arrays=arrays_for(method,data)
        fitted,info=neural_fit(method,arrays,data.truth,train,79,.003,60)
        p=infer(fitted,arrays,test)
        acc=float((p.argmax(1)==data.truth[test]).mean())
        assert info["training_accuracy"]>=.95 and acc>=5/6,(method,info["training_accuracy"],acc)
        assert info["last_training_loss"] < info["first_training_loss"]
        results["neural"][method]=dict(training_accuracy=info["training_accuracy"],known_frequency_test_accuracy=acc,first_loss=info["first_training_loss"],last_loss=info["last_training_loss"],gradients_finite=True)
    save_json(out/"sanity_checks.json",results)
    return results


def emit_fold(name,method,scores,data,records,train,test,folder):
    idle=train[data.truth[train]<0]
    # Idle is never trained as one of six commands. Only training-fold idle is
    # used for this explicitly exploratory rejection operating point.
    threshold=float(np.quantile(scores[idle].max(axis=1),.95,method="higher"))
    rows=[]
    for i in test:
        y=int(data.truth[i]); pred=int(np.argmax(scores[i])); top=float(scores[i,pred])
        candidate=COMMANDS[pred]
        if method==METHODS[0] and records[i]["usable_channels"]==0:
            pred=-2; candidate="无法解码"
        accepted=bool(top>threshold and records[i]["usable_channels"]>=2)
        row=dict(protocol="cross_day" if name.startswith("cross_day") else "grouped_5fold",fold=name,method=method,
            source_file=records[i]["source_file"],run_id=records[i]["run_id"],group=records[i]["group"],true_label=records[i]["true_label"],
            candidate_label=candidate,active_correct=int(y>=0 and y==pred),confidence=top,
            threshold=threshold,accepted=accepted,final_label=COMMANDS[pred] if accepted else "空闲",
            emitted_correct=int(y>=0 and y==pred and accepted),idle_false_accept=int(y<0 and accepted),
            seven_state_correct=int((y<0 and not accepted) or (y>=0 and y==pred and accepted)))
        for cls in range(6): row[f"score_{COMMANDS[cls]}"]=float(scores[i,cls])
        rows.append(row)
    save_json(folder/"decision_policy.json",dict(rule="score_top > training-idle 95th percentile (higher order statistic) AND usable_channels >= 2",
        training_idle_count=len(idle),threshold=threshold,warning="Only 24 idle recordings total; exploratory gate, no guaranteed online false-alarm rate."))
    return rows


def metrics(rows):
    act=[r for r in rows if r["true_label"]!="空闲"];idle=[r for r in rows if r["true_label"]=="空闲"]
    n=len(act);ok=sum(r["active_correct"] for r in act);acc=sum(r["accepted"] for r in act);emit=sum(r["emitted_correct"] for r in act)
    cm=np.zeros((6,7),int)
    for r in act:
        pred=COMMANDS.index(r["candidate_label"]) if r["candidate_label"] in COMMANDS else 6
        cm[COMMANDS.index(r["true_label"]),pred]+=1
    return dict(active_correct=ok,active_total=n,forced_six_accuracy=ok/n,confusion_matrix=cm.tolist(),
        confusion_columns=[*COMMANDS,"无法解码"],undecodable_active=int(cm[:,6].sum()),
        per_class={c:dict(correct=int(cm[k,k]),total=int(cm[k].sum()),accuracy=float(cm[k,k]/cm[k].sum())) for k,c in enumerate(COMMANDS)},
        emitted_correct=emit,emitted_active=acc,command_success_rate=emit/n,accepted_precision=emit/acc if acc else None,
        coverage=acc/n,idle_total=len(idle),idle_false_accept=sum(r["idle_false_accept"] for r in idle),
        seven_state_correct=sum(r["seven_state_correct"] for r in rows),seven_state_total=len(rows),
        seven_state_accuracy=sum(r["seven_state_correct"] for r in rows)/len(rows))


def render_report(out,records,rows,config):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    results={}
    for protocol in ("grouped_5fold","cross_day"):
        results[protocol]={m:metrics([r for r in rows if r["protocol"]==protocol and r["method"]==m]) for m in METHODS}
    report=dict(configuration=config,results=results,notes=[
        "All 165 trials retained: 141 active and 24 idle. Every primary test trial predicted once out of fold.",
        "Same-subject mixed-session grouped validation is not a future-session performance guarantee.",
        "Cross-day evaluation holds out all Oct27 data, but the dataset has been inspected in earlier research; not a prospective blind study.",
        "Neural numbers are three-seed probability ensembles of project adaptations, not exact official paper reproductions.",
        "Idle gate is selected using training-fold idle only. Rejected active trials count as failures for command_success_rate.",
        "No test-based exclusion, label shifting, channel search, best-seed reporting or test-based early stopping.",
        "A completed correctness audit does not imply zero classification errors or certify every implementation detail."])
    save_json(out/"report.json",report)
    with (out/"predictions.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    flat=[]
    for protocol,res in results.items():
        for method,m in res.items():flat.append(dict(protocol=protocol,method=method,**{k:v for k,v in m.items() if k not in ("confusion_matrix","confusion_columns","per_class")}))
    with (out/"summary.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    fig,ax=plt.subplots(figsize=(11,5.2));x=np.arange(6)
    for shift,protocol,label,color in [(-.19,"grouped_5fold","Grouped 5-fold (141 active)","#2563eb"),(.19,"cross_day","Oct24 train -> Oct27 test (90 active)","#0d9488")]:
        ys=[results[protocol][m]["forced_six_accuracy"]*100 for m in METHODS]
        bars=ax.bar(x+shift,ys,width=.36,label=label,color=color)
        ax.bar_label(bars,labels=[f"{y:.1f}%" for y in ys],padding=3,fontsize=9)
    ax.axhline(100/6,color="#777",ls="--",lw=1,label="Chance 16.7%")
    ax.set_xticks(x,["FBCCA","Dual-FBCCA\n+ LR","FB-eTRCA","FB-EEGNet\n(3 seeds)","TFFNet adapted\n(3 seeds)","CNN-BiGRU adapted\n(3 seeds)"])
    ax.set_ylim(0,105);ax.set_ylabel("Forced six-command accuracy (%)")
    ax.set_title("Subject A | 60 + 105 recordings | Complete 5 s | No subject B data")
    ax.legend(fontsize=9);ax.grid(axis="y",alpha=.18);fig.tight_layout();fig.savefig(out/"comparison.png",dpi=180);plt.close(fig)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=ROOT/"outputs/subject24_audited_six_methods_20261028")
    parser.add_argument("--max-epochs",type=int,default=100);parser.add_argument("--sanity-only",action="store_true");parser.add_argument("--skip-sanity",action="store_true")
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    if not args.skip_sanity:
        if not (out/"sanity_checks.json").exists():sanity(out)
    else: assert (out/"sanity_checks.json").exists(),"Run sanity checks first"
    if args.sanity_only:return
    data,records,baseline=load_data();folds=outer_folds(records)
    config=dict(runs=list(RUNS),subjects="User-confirmed Oct24 participant only",total=165,active=141,idle=24,
        labels=list(COMMANDS),seconds=5,fs=250,channels=list(SSVEP_CHANNEL_NAMES),seed=SPLIT_SEED,
        max_neural_epochs=args.max_epochs,neural_seeds=list(SEEDS),neural_learning_rates=[.001,.003],
        neural_early_stopping="inner validation accuracy then NLL, every 5 epochs; patience 25, minimum 30 epochs",
        classical_training_fractions=[.5,1],lr_C=[.01,.1,1],trca_ridge=[.001,.01,.1],
        method_definitions={METHODS[0]:"actual runtime FBCCA channel QC, 6/14/22--50 Hz, default harmonics",METHODS[1]:"runtime 12-dimensional dual-FBCCA, training-only StandardScaler and LR",METHODS[2]:"ensemble TRCA with Butterworth subbands and signed squared correlations",METHODS[3]:"project filter-bank EEGNet fusion",METHODS[4]:"project time/complex-spectrum TFFNet adaptation",METHODS[5]:"project CNN+BiGRU adaptation"})
    import torch, scipy, sklearn
    config["versions"] = dict(python=sys.version,numpy=np.__version__,scipy=scipy.__version__,sklearn=sklearn.__version__,torch=torch.__version__)
    config["source_hashes"] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__),ROOT/"scripts/benchmark_recent_ssvep_methods.py",ROOT/"src/ml/dual_fbcca_features.py",
        ROOT/"src/ml/fb_eegnet.py",ROOT/"src/ml/recent_ssvep_models.py",ROOT/"src/bci/fbcca_decoder.py",ROOT/"src/bci/preprocessing.py")}
    if (out/"configuration.json").exists():
        assert json.loads((out/"configuration.json").read_text())==config,"Configuration changed: use a new output directory"
        assert json.loads((out/"data_manifest.json").read_text())==records,"Source recordings changed"
    save_json(out/"configuration.json",config);save_json(out/"data_manifest.json",records)
    rows=[];seen=set()
    for fold_index,(name,train,test) in enumerate(folds):
        folder=out/name;folder.mkdir(exist_ok=True)
        fit,val=split_groups(records,train,.25,SPLIT_SEED+fold_index)
        fit=active(fit,data.truth);val=active(val,data.truth);train_active=active(train,data.truth)
        groups=lambda ix:{records[i]["group"] for i in ix}
        assert not groups(train)&groups(test) and not groups(fit)&groups(val)
        assert not set(train)&set(test)
        assert len(set(data.truth[fit]))==6 and len(set(data.truth[val]))==6
        if name.startswith("grouped"):
            assert not set(test)&seen;seen.update(test)
        manifest=dict(name=name,train=[records[i]["source_file"] for i in train],test=[records[i]["source_file"] for i in test],
            inner_fit=[records[i]["source_file"] for i in fit],inner_validation=[records[i]["source_file"] for i in val],
            counts=dict(train_total=len(train),train_active=len(train_active),inner_fit=len(fit),inner_validation=len(val),test_total=len(test),test_active=len(active(test,data.truth))))
        save_json(folder/"split.json",manifest)
        print(f"FOLD {name}: {manifest['counts']}",flush=True)
        for method in METHODS:
            sub=folder/method.replace(" ","_").replace("+","_");sub.mkdir(exist_ok=True)
            cache=sub/"completed_predictions.json"
            if cache.exists():
                rows.extend(json.loads(cache.read_text()));print(f"  resumed {method}",flush=True);continue
            start=time.perf_counter()
            if method==METHODS[0]:scores=baseline
            elif method in METHODS[1:3]:scores,_=classical_tune(method,data,records,fit,val,train_active,sub)
            else:scores,_=neural_tune(method,data,records,fit,val,train_active,sub,args.max_epochs)
            chunk=emit_fold(name,method,scores,data,records,train,test,sub)
            save_json(cache,chunk);rows.extend(chunk)
            # Intermediate logs intentionally do not reveal held-out accuracy.
            print(f"  COMPLETE {method}, {time.perf_counter()-start:.1f}s; predictions saved",flush=True)
    assert seen==set(range(len(records)))
    for method in METHODS:
        selected=[r for r in rows if r["method"]==method and r["protocol"]=="grouped_5fold"]
        assert len(selected)==165 and len({r["source_file"] for r in selected})==165
    report=render_report(out,records,rows,config)
    print(json.dumps(report["results"],ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__":main()
