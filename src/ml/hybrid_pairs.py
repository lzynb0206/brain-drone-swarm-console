"""Audit actual paired recordings. Public/unpaired data cannot acquire fake P values."""
from pathlib import Path
import hashlib
import json
import numpy as np
from src.bci.montage import HYBRID, HYBRID_IDS, MONTAGES
from src.services.hybrid_experiment_protocol import MI_TASK_SETS


def read_stage(path):
    path = Path(path)
    with np.load(path, allow_pickle=False) as p:
        context = json.loads(str(p["trial_context"].item()))
        names = tuple(str(n) for n in p["channel_names"])
        eeg = p["eeg"].copy()
        timestamps = p["sample_timestamps"].copy()
        fs = float(p["fs"])
        quality = json.loads(str(p["signal_quality"].item()))
        subject_id = str(p["subject_id"].item()).strip() if "subject_id" in p else ""
    montage = MONTAGES.get(context.get("montage_id"))
    if montage is None or montage.id not in HYBRID_IDS or names != montage.names:
        raise ValueError("Not a recording with the actual hybrid montage")
    if eeg.ndim != 2 or eeg.shape[0] != 8 or len(timestamps) != eeg.shape[1]:
        raise ValueError("Malformed paired recording")
    return dict(path=path, context=context, eeg=eeg, timestamps=timestamps, fs=fs, quality=quality, subject_id=subject_id,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def assemble_pairs(directory, runs, task_set="left_right", seconds=3.):
    rows = {}; audit = []; selected_runs = set(runs)
    for path in sorted(Path(directory).rglob("hybrid-*-mi.npz")):
        mi = read_stage(path); ctx = mi["context"]
        if ctx.get("run_id") not in selected_runs:
            continue
        pair_id = ctx.get("pair_id")
        if pair_id in rows:
            raise ValueError(f"Duplicate pair ID: {pair_id}")
        if ctx.get("record_kind") != "serial_pair":
            audit.append({"file":path.name,"included":False,"reason":"not_serial_pair"}); continue
        source = path.parent / f"{ctx.get('preceding_ssvep_trial_id', '')}.npz"
        if not source.exists():
            raise ValueError(f"Missing preceding SSVEP for {path.name}")
        ss = read_stage(source); sc = ss["context"]
        if not mi["subject_id"] or ss["subject_id"]!=mi["subject_id"]:
            raise ValueError("Paired stages require the same nonempty participant ID")
        if (sc.get("run_id") != ctx.get("run_id") or sc.get("pair_id") != pair_id
                or sc.get("stage") != "ssvep" or ctx.get("stage") != "mi"
                or sc.get("montage_id") != ctx.get("montage_id")
                or sc.get("mi_true_label") != ctx.get("mi_true_label")):
            raise ValueError(f"Mismatched pair: {path.name}")
        if not len(ss["timestamps"]) or not len(mi["timestamps"]) or ss["timestamps"][-1] >= mi["timestamps"][0]:
            raise ValueError("SSVEP must finish before MI starts")
        label = ctx.get("mi_true_label")
        labels = MI_TASK_SETS[task_set]
        if label not in labels:
            raise ValueError(f"MI task mismatch: {label}")
        if mi["fs"] != 250. or mi["eeg"].shape[1] != round(seconds*mi["fs"]):
            raise ValueError("MI epoch sample rate or length differs; do not silently crop trials")
        p = ss["quality"].get("p_ssvep")
        valid = bool(ss["quality"].get("p_ssvep_valid", False))
        if ctx.get("p_ssvep_valid", False) != valid or valid and (p is None or not 0 <= p <= 1 or ctx.get("p_ssvep") != p):
            raise ValueError("P does not match the preceding saved SSVEP evidence")
        x = mi["eeg"][5:8]
        baseline_path = path.parent / f"{ctx.get('baseline_trial_id', '')}.npz"
        if not baseline_path.exists():
            raise ValueError("Missing pre-run resting baseline")
        rest = read_stage(baseline_path)
        if rest["subject_id"]!=mi["subject_id"]:
            raise ValueError("Rest baseline participant differs from MI participant")
        if (rest["context"].get("stage") != "baseline" or rest["context"].get("run_id") != ctx["run_id"]
                or rest["context"].get("montage_id") != ctx.get("montage_id")
                or rest["timestamps"][-1] >= ss["timestamps"][0] or rest["fs"] != mi["fs"]):
            raise ValueError("Rest baseline must precede the pair in the same run")
        dt = np.diff(mi["timestamps"])
        signal_valid = bool(np.isfinite(x).all() and np.isfinite(dt).all() and np.all(dt>0)
                            and len(dt) and dt.max() <= .02 and mi["quality"].get("status") != "bad")
        rdt=np.diff(rest["timestamps"])
        signal_valid = signal_valid and bool(np.isfinite(rest["eeg"][5:8]).all() and len(rdt)
            and np.isfinite(rdt).all() and np.all(rdt>0) and rdt.max()<=.02 and rest["quality"].get("status")!="bad")
        rows[pair_id] = dict(eeg=x, baseline=rest["eeg"][5:8], y=labels.index(label), p=float(p) if valid else 0.,
            p_valid=valid, signal_valid=signal_valid, run=ctx["run_id"], pair_id=pair_id, subject_id=mi["subject_id"])
        audit.append(dict(pair_id=pair_id, included=True, p_valid=valid, signal_valid=signal_valid,
            ssvep_file=source.name, ssvep_sha256=ss["sha256"], mi_file=path.name, mi_sha256=mi["sha256"],
            baseline_file=baseline_path.name, baseline_sha256=rest["sha256"]))
    # Retain a record of incomplete SSVEP→MI trials instead of hiding aborts.
    for path in sorted(Path(directory).rglob("hybrid-*-ssvep.npz")):
        ss = read_stage(path); ctx = ss["context"]
        if ctx.get("run_id") in selected_runs and ctx.get("record_kind") == "serial_pair" and ctx.get("pair_id") not in rows:
            audit.append(dict(file=path.name,included=False,reason="incomplete_pair_no_MI"))
    if not rows:
        raise ValueError("No actual serial SSVEP→MI pairs in the requested runs")
    values = list(rows.values())
    return {k:np.asarray([r[k] for r in values]) for k in values[0]}, audit


def export_split(directory, train_runs, test_runs, output, task_set="left_right", seconds=3., augment_confidence=True):
    if not train_runs or not test_runs or set(train_runs) & set(test_runs):
        raise ValueError("Specify disjoint whole training and test runs")
    payload = {}; audits = {}
    for split,runs in (("train",train_runs),("test",test_runs)):
        rows,audit = assemble_pairs(directory,runs,task_set,seconds)
        missing = set(runs)-set(rows["run"].tolist())
        if missing:
            raise ValueError(f"Runs with no complete pairs: {sorted(missing)}")
        payload.update({f"{split}_{k}":v for k,v in rows.items()}); audits[split]=audit
    if augment_confidence:
        from src.ml.confidence_augmentation import training_confidence_windows
        augmented=training_confidence_windows(directory,train_runs)
        pairs=payload["train_pair_id"].tolist()
        payload["train_p_aug"]=np.asarray([augmented.get(k,([0.]*3,[False]*3))[0] for k in pairs],np.float32)
        payload["train_p_aug_valid"]=np.asarray([augmented.get(k,([0.]*3,[False]*3))[1] for k in pairs],bool)
        payload["confidence_window_seconds"]=np.asarray([1.,1.5,2.])
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(output,**payload,fs=250.,channel_names=np.array(HYBRID.mi),
        task_labels=np.array(MI_TASK_SETS[task_set]),condition_source="actual_preceding_ssvep",
        audit=json.dumps(audits,ensure_ascii=False))
    return audits
