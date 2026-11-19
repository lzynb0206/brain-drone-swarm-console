"""Exploratory test of paired central EEG as a visual-task reference for MI.

Run from the project root:
  .venv312/bin/python -m research.serial_bci.mi_transfer_followup.state_reference --historical-challenge
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy import signal
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.bci.montage import MONTAGES
from research.serial_bci.mi_transfer_followup.challenge import challenge, RAW_ROOT
from research.serial_bci.mi_transfer_followup.experts import BANDS
from research.serial_bci.serial.run import load, read_stage, ss_predict

HERE = Path(__file__).resolve().parent
ARMS = ("post", "reference", "mean_p", "paired_p")
CONTROLS = ("wrong_reference", "wrong_front", "wrong_p")
SHIFTS = 499
SEED = 261119


def lateralization(stage):
    ctx = stage["context"]
    times = stage["timestamps"] - ctx["stimulus_lsl_time"]
    at = int(np.searchsorted(times, 0.25))
    window = times[at:at+625]
    if (stage["fs"] != 250 or len(window) != 625 or abs(window[0]-.25) > .025
            or np.max(np.diff(window)) > .1 or np.min(np.diff(window)) <= 0
            or abs(window[-1]-window[0]-624/250) > .035):
        raise ValueError("visual/MI reference window clock or length")
    # read_stage validates the NPZ channel_names against the named montage.
    names = tuple(ctx["channel_names"])
    if names != MONTAGES[ctx["montage_id"]].names or not {"C3","Cz","C4"}.issubset(names):
        raise ValueError("undeclared reference electrodes")
    left, center, right = stage["eeg"][[names.index(n) for n in ("C3", "Cz", "C4")], at:at+625]
    bipolar = np.stack([left-center, right-center])
    if not np.isfinite(bipolar).all() or np.any(bipolar.std(axis=1) < 1e-12):
        raise ValueError("nonfinite or flat central bipolar")
    bipolar = signal.detrend(bipolar, axis=-1)
    out = []
    for band in BANDS:
        sos = signal.butter(4, band, fs=250, btype="bandpass", output="sos")
        filtered = signal.sosfiltfilt(sos, bipolar, axis=-1, padlen=125)[:, 62:-62]
        power = np.maximum(np.mean(filtered**2, axis=1), 1e-12)
        out.append(np.log(power[0]) - np.log(power[1]))
    return np.array(out)


def visual_probability(records, n_train, scores):
    s = np.stack([scores[r["pair"]] for r in records])
    frequency = np.array([r["true_hz"]-8 for r in records])
    group = np.array([f"{r['run']}:{r['block']}" for r in records])
    tr, te = np.arange(n_train), np.arange(n_train, len(records))
    if set(group[tr]) & set(group[te]) or len(set(group[tr])) < 3:
        raise ValueError("Need disjoint blocks and three or more calibration blocks")
    p0 = np.full(n_train, np.nan)
    for block in np.unique(group[tr]):
        a, b = tr[group[tr] != block], tr[group[tr] == block]
        _, _, p0[b] = ss_predict(s, frequency, group, a, b, "train_nontarget")
    _, _, p = ss_predict(s, frequency, group, tr, te, "train_nontarget")
    if not np.isfinite(p0).all() or not np.isfinite(p).all():
        raise ValueError("Missing grouped visual correctness probabilities")
    return p0, p


def designs(motor, front, p, p_mean):
    return dict(post=motor, reference=motor-front, mean_p=motor-p_mean*front,
                paired_p=motor-p[:, None]*front)


def shifts(blocks, rng=None):
    """Return a whole-front reassignment within chronological blocks."""
    order = np.arange(len(blocks))
    for b in np.unique(blocks):
        idx = np.flatnonzero(blocks == b)
        if len(idx) > 1:
            step = 1 if rng is None else int(rng.integers(1, len(idx)))
            order[idx] = np.roll(idx, step)
    return order


def evaluate(train, test, scores, experiment, fold, features, seed=SEED):
    train = sorted(train, key=lambda r: (r["run"], r["start"]))
    test = sorted(test, key=lambda r: (r["run"], r["start"]))
    records = train+test
    if (len({r["pair"] for r in records}) != len(records) or train[-1]["run"] > test[0]["run"]
            or (train[-1]["run"] == test[0]["run"] and train[-1]["end"] >= test[0]["start"])):
        raise ValueError("Duplicate or nonchronological calibration and test")
    n = len(train)
    y = np.array([r["hand"] for r in records])
    front, motor = np.stack([features[r["pair"]] for r in records], axis=1)
    p0, p = visual_probability(records, n, scores)
    p_mean = float(p0.mean())
    tr, te = designs(motor[:n], front[:n], p0, p_mean), designs(motor[n:], front[n:], p, p_mean)
    models = {arm: make_pipeline(StandardScaler(), LogisticRegression(C=1., max_iter=1000))
              .fit(tr[arm], y[:n]) for arm in ARMS}
    outputs = {arm: model.predict_proba(te[arm])[:, 1] for arm, model in models.items()}
    blocks = np.array([f"{r['run']}:{r['block']}" for r in test])
    rng = np.random.default_rng(seed)
    orders = np.stack([shifts(blocks)] + [shifts(blocks, rng) for _ in range(SHIFTS)])
    m, r = motor[n:], front[n:]
    null = {}
    shifted = {"wrong_reference": ("reference", m[None]-r[orders]),
               "wrong_front": ("paired_p", m[None]-p[orders, None]*r[orders]),
               "wrong_p": ("paired_p", m[None]-p[orders, None]*r[None])}
    for name, (arm, data) in shifted.items():
        probabilities = models[arm].predict_proba(data.reshape(-1, 2))[:, 1].reshape(orders.shape)
        outputs[name], null[name] = probabilities[0], probabilities[1:]
    rows = []
    for i, item in enumerate(test):
        row = {k: item[k] for k in ("subject", "run", "block", "pair", "true_hz", "hand")}
        row.update(experiment=experiment, fold=fold, p=float(p[i]), p_train_oof_mean=p_mean,
                   visual_mu=float(r[i, 0]), visual_beta=float(r[i, 1]),
                   motor_mu=float(m[i, 0]), motor_beta=float(m[i, 1]))
        row.update({key:float(a[i]) for key, a in outputs.items()})
        rows.append(row)
    return rows, dict(experiment=experiment, fold=fold, train_n=n, test_n=len(test),
                      p_train_oof_mean=p_mean, shift_predictions=null)


def metrics(y, probs):
    hit = (probs > .5) == y
    return dict(n=len(y), correct=int(hit.sum()), ba=float(np.mean([hit[y == k].mean() for k in (0, 1)]))
                if len(set(y)) == 2 else None, brier=float(np.mean((probs-y)**2)))


def summarize(rows):
    y = np.array([r["hand"] for r in rows])
    q = {key:np.array([r[key] for r in rows]) for key in ARMS+CONTROLS}
    def gain(candidate, control):
        a, b = (q[candidate] > .5) == y, (q[control] > .5) == y
        return dict(rescued=int(sum(a & ~b)), harmed=int(sum(b & ~a)))
    return dict(arms={key:metrics(y, val) for key,val in q.items()},
                reference_vs_post=gain("reference","post"), p_vs_mean=gain("paired_p","mean_p"))


def shift_quantiles(rows, folds, name):
    by_pair = {}
    for fold in folds:
        test = [r for r in rows if r["fold"] == fold["fold"]]
        by_pair.update(zip([r["pair"] for r in test], fold["shift_predictions"][name].T))
    accuracy = []
    for subject in sorted({r["subject"] for r in rows}):
        sr = [r for r in rows if r["subject"] == subject]
        y = np.array([r["hand"] for r in sr])
        if len(set(y)) < 2:
            continue
        hit = (np.stack([by_pair[r["pair"]] for r in sr], axis=1) > .5) == y
        accuracy.append((hit[:, y == 0].mean(1)+hit[:, y == 1].mean(1))/2)
    return np.quantile(np.mean(accuracy,axis=0), [.025,.5,.975]).tolist() if accuracy else []


def run(raw_root, out, historical=False):
    raw_root, out = Path(raw_root), Path(out)
    folders = sorted({p.parent for p in raw_root.rglob("hybrid-*-mi.npz")})
    runs, scores, features, issues = {}, {}, {}, []
    rows, folds = [], []
    for folder in folders:
        original, _, _ = load(folder)
        if not original:
            continue
        if folder.name in runs:
            raise ValueError("Repeated run directory")
        records = []
        for rec in original:
            pair = rec["pair"]
            if pair in features:
                raise ValueError("Repeated pair identifier")
            try:
                epochs = []
                for phase, key in (("ssvep","ss_sha256"),("mi","mi_sha256")):
                    st = read_stage(folder / f"{pair}-{phase}.npz")
                    if st["sha256"] != rec[key]:
                        raise RuntimeError("Source EEG hash changed during analysis")
                    epochs.append(lateralization(st))
            except ValueError as e:
                issues.append(dict(run=folder.name, pair=pair, reason=str(e)))
                continue
            records.append(rec)
            features[pair], scores[pair] = epochs, rec["scores"]
        runs[folder.name] = records
        blocks = {r["block"] for r in records}
        if not {1,2,3,4}.issubset(blocks) or len(blocks & {5,6,7}) < 2:
            continue
        train, test = [r for r in records if r["block"] <= 4], [r for r in records if r["block"] > 4]
        a, f = evaluate(train,test,scores,"within_session",folder.name,features,seed=SEED+len(folds))
        rows.extend(a)
        folds.append(f)
    if historical:
        def archived(train, test, score, experiment, fold, seed):
            return evaluate(train,test,score,experiment,fold,features,seed)
        a, fs = challenge(runs, scores, archived, raw_root=raw_root)
        rows.extend(a)
        folds.extend(fs)
    if not rows:
        raise ValueError("No valid forward split in the supplied EEG")
    report = {}
    for ex in sorted({r["experiment"] for r in rows}):
        sample = [r for r in rows if r["experiment"] == ex]
        fs = [f for f in folds if f["experiment"] == ex]
        by_subject = {s:summarize([r for r in sample if r["subject"] == s]) for s in sorted({r["subject"] for r in sample})}
        mean_person_ba = {}
        for arm in ARMS+CONTROLS:
            bas = [v["arms"][arm]["ba"] for v in by_subject.values() if v["arms"][arm]["ba"] is not None]
            mean_person_ba[arm] = float(np.mean(bas)) if bas else None
        report[ex] = dict(pooled=summarize(sample), by_subject=by_subject,
                          by_run={name:summarize([r for r in sample if r["run"] == name])
                                  for name in sorted({r["run"] for r in sample})},
                          mean_person_ba=mean_person_ba,
                          shift_mean_person_ba_quantiles={k:shift_quantiles(sample, fs, k) for k in CONTROLS})
    for fold in folds:
        del fold["shift_predictions"]
    out.mkdir(exist_ok=True,parents=True)
    with (out/"predictions.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out/"summary.json").write_text(json.dumps(dict(experiments=report, folds=folds, exclusions=issues),
                                    ensure_ascii=False, allow_nan=False, indent=2)+"\n")
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW_ROOT)
    parser.add_argument("--out", type=Path, default=HERE/"reference_results")
    parser.add_argument("--historical-challenge", action="store_true")
    args=parser.parse_args()
    results=run(args.raw,args.out,args.historical_challenge)
    print(json.dumps({e:result["pooled"] for e,result in results.items()},ensure_ascii=False,indent=2))
