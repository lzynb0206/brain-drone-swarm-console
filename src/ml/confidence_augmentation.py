"""Training-only SSVEP confidence recomputation with prefix windows and past idle.

This never pairs unrelated public SSVEP and MI trials. The MI identity and
whole-run train/test partition are established before any augmentation.
"""
from pathlib import Path
import numpy as np
from src.bci.montage import research_decoder
from src.ml.hybrid_pairs import read_stage


def training_confidence_windows(directory,training_runs,seconds=(1.,1.5,2.)):
    stages=[];runs=set(training_runs)
    for path in Path(directory).rglob("hybrid-*.npz"):
        stage=read_stage(path);context=stage["context"]
        if context.get("run_id") in runs and context.get("stage")=="ssvep":stages.append(stage)
    stages.sort(key=lambda s:(s["context"]["run_id"],s["timestamps"][0]))
    decoders={};result={}
    for row in stages:
        ctx=row["context"];run=ctx["run_id"];values=[];validity=[]
        for sec in seconds:
            decoder=decoders.setdefault((run,sec),research_decoder(ctx["montage_id"]))
            n=round(sec*row["fs"])
            if row["eeg"].shape[-1]<n:
                values.append(0.);validity.append(False);continue
            dt=np.diff(row["timestamps"][:n]);timing=bool(np.isfinite(dt).all() and (dt>0).all() and dt.max()<=.02)
            try:decoded=decoder.decode(row["eeg"][:,:n],row["fs"])
            except ValueError as exc:
                if "没有可用 EEG 通道" not in str(exc):raise
                values.append(0.);validity.append(False);continue
            signal_ok=timing and len(decoded.used_channels_1based)>=2
            ready=decoded.signal_quality["bn_idle_windows"]>=decoder.min_idle_windows
            values.append(float(decoded.signal_quality["p_ssvep"]));validity.append(signal_ok and ready)
            if ctx.get("true_label")=="空闲" and signal_ok:decoder.observe_idle_result(decoded)
        if ctx.get("record_kind")=="serial_pair":result[ctx["pair_id"]]=(values,validity)
    return result


def expand_training_conditions(data):
    """Copy only training epochs, retaining each original pair ID and run group."""
    if str(data["condition_source"].item())!="actual_preceding_ssvep":
        raise ValueError("Confidence augmentation needs actual paired training SSVEP")
    p=np.asarray(data["train_p_aug"],float);valid=np.asarray(data["train_p_aug_valid"],bool)
    if p.ndim!=2 or p.shape!=valid.shape or len(p)!=len(data["train_y"]):raise ValueError("Malformed P augmentation")
    pair,window=np.nonzero(valid)
    if not len(pair):raise ValueError("No valid training confidence windows")
    result=dict(data)
    for key,value in data.items():
        if key.startswith("train_") and key not in ("train_p_aug","train_p_aug_valid") and np.asarray(value).ndim and len(value)==len(p):
            result[key]=np.asarray(value)[pair]
    result["train_p"]=p[pair,window].astype(np.float32);result["train_p_valid"]=np.ones(len(pair),bool)
    result["train_original_pair_index"]=pair;result["train_confidence_window_index"]=window
    result["train_p_aug"]=p[pair];result["train_p_aug_valid"]=valid[pair]
    # All test arrays remain the exact original objects and values.
    return result
