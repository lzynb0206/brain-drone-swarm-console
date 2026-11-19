"""Six-frequency public EEG validation of trial-level FBCCA decision probabilities."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from functools import lru_cache
from pathlib import Path

import h5py
import numpy as np
from scipy.special import expit
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.bci.fbcca_decoder import FBCCADecoder, _orthonormal_basis
from src.bci.preprocessing import filter_bank, notch_mains, remove_linear_drift

FREQ = np.arange(8, 14)
WINDOWS = (1.5, 1.0, 2.0)
BASELINES = ('raw', 'other_mean', 'train_nontarget')
METHODS = ('margin', 'prior', 'platt', 'isotonic')


@lru_cache(maxsize=32)
def references(fs, n, frequencies):
    decoder = FBCCADecoder(frequencies, [str(f) for f in frequencies])
    return [_orthonormal_basis(ref.T) for ref in decoder._reference_signals(fs, n)]


def fbcca(epoch, fs=250., frequencies=tuple(FREQ)):
    """Score a single finite posterior EEG window, without a raw-amplitude censor."""
    epoch = np.asarray(epoch, dtype=float)
    if epoch.ndim != 2 or not np.isfinite(epoch).all() or fs <= 100 or epoch.shape[1] < fs * .4:
        raise ValueError('Expected finite, channels × samples EEG and fs > 100 Hz.')
    clean = notch_mains(remove_linear_drift(epoch), fs)
    scale = clean.std(axis=1, keepdims=True)
    keep = scale[:, 0] > 1e-12
    if not np.any(keep):
        raise ValueError('Every EEG channel is flat')
    data = clean[keep] / scale[keep]
    bases = references(fs, epoch.shape[1], tuple(frequencies))
    scores = np.zeros(len(bases))
    for bank, weight in filter_bank(data, fs):
        q = _orthonormal_basis(bank.T)
        if q.shape[1]:
            for k, ref in enumerate(bases):
                rho = np.linalg.svd(q.T @ ref, compute_uv=False)[0]
                scores[k] += weight * min(1., rho) ** 2
    return scores


def baseline(scores, label, variant):
    if variant == 'train_nontarget':
        if len(np.unique(label)) < 2:
            raise ValueError('Non-target background fitting needs at least two distinct true classes.')
        return np.array([scores[label != k, k].mean() for k in range(scores.shape[1])])
    return np.zeros(scores.shape[1])


def decisions(scores, offset, variant):
    if variant == 'other_mean':
        adjusted = scores - (scores.sum(axis=1, keepdims=True) - scores) / (scores.shape[1] - 1)
        if not np.array_equal(adjusted.argmax(axis=1), scores.argmax(axis=1)):
            raise ArithmeticError('Other-class mean unexpectedly changed the prediction')
    else:
        adjusted = scores - offset
    pred = adjusted.argmax(axis=1)
    top = np.sort(adjusted, axis=1)[:, -2:]
    margin = np.clip((top[:, 1] - top[:, 0]) / np.maximum(np.abs(top[:, 1]), 1e-12), 0, 1)
    return pred, margin


def fit(scores, label, subjects, variant):
    # For the learned background, fit the calibration examples' offsets without
    # that subject, matching the scoring condition of the held-out evaluation.
    if variant not in BASELINES:
        raise ValueError(f'Unknown background rule: {variant}')
    scores, label, subjects = np.asarray(scores), np.asarray(label), np.asarray(subjects)
    if scores.ndim != 2 or scores.shape[0] != len(label) or len(label) != len(subjects) or not len(label):
        raise ValueError('Expected one score vector, true class and group per training trial.')
    if not np.isfinite(scores).all() or not np.all(np.isin(label, np.arange(scores.shape[1]))):
        raise ValueError('Scores must be finite and class indices zero-based.')
    if variant == 'train_nontarget' and len(np.unique(subjects)) < 2:
        raise ValueError('Cross-fitting the background requires at least two training groups.')
    offset = baseline(scores, label, variant)
    pred, margin = decisions(scores, offset, variant)
    if variant == 'train_nontarget':
        for subj in np.unique(subjects):
            val = subjects == subj
            inner = baseline(scores[~val], label[~val], variant)
            pred[val], margin[val] = decisions(scores[val], inner, variant)
    y = (pred == label).astype(int)
    if np.unique(y).size == 1:
        # Retain finite probabilities when small calibration folds have no errors.
        rate = (y.sum() + 0.5) / (len(y) + 1)
        intercept, slope = float(np.log(rate / (1-rate))), 0.
    else:
        model = LogisticRegression(C=1, solver='lbfgs', tol=1e-10, max_iter=250).fit(margin[:, None], y)
        intercept, slope = float(model.intercept_[0]), float(model.coef_[0, 0])
    iso = IsotonicRegression(out_of_bounds='clip').fit(margin, y)
    return dict(offset=offset.tolist(), intercept=intercept, slope=slope,
                iso_x=iso.X_thresholds_.tolist(), iso_y=iso.y_thresholds_.tolist(), prior=float(y.mean()))


def apply(scores, variant, model):
    pred, margin = decisions(scores, np.array(model['offset']), variant)
    return pred, {'margin':margin, 'prior':np.full(len(scores), model['prior']),
                  'platt':expit(model['intercept'] + model['slope'] * margin),
                  'isotonic':np.interp(margin, model['iso_x'], model['iso_y'])}


def metrics(y, p):
    bins = np.minimum((p*10).astype(int), 9)
    ece = sum(abs(np.sum(p[bins == b] - y[bins == b])) for b in range(10)) / len(y)
    q = p.clip(1e-6, 1-1e-6)
    return {'brier':float(np.mean((p-y)**2)), 'ece10':float(ece),
            'logloss':float(-np.mean(y*np.log(q)+(1-y)*np.log1p(-q))),
            'auroc':float(roc_auc_score(y,p)) if np.unique(y).size == 2 else None}


def write_csv(path, rows):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def validate(root, out):
    start = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    sources, predictions, summary, reliability, models = {}, [], [], [], {}
    for corpus, n in (('benchmark',1260),('beta',1680)):
        source = root / f'{corpus}_common6_2s.h5'
        with h5py.File(source) as f:
            eeg, label, subject, block = (f[key][:] for key in ('eeg','label','subject','block'))
            assert len(eeg)==n and np.array_equal(FREQ[label], f['frequency_hz'][:])
            assert len(set(zip(subject, block, label)))==n
            assert np.all(np.bincount(label)==n//6) and f.attrs['sample_rate_hz']==250
        scores = {s:np.stack([fbcca(trial[:, :round(250*s)]) for trial in eeg]) for s in WINDOWS}
        sources[corpus] = (scores,label,subject,block)
        print(f'{corpus} scored {len(eeg)} trials in {time.monotonic()-start:.1f} s',flush=True)
    for target_corpus,(scores_by_s,label,subject,block) in sources.items():
        for split in (['loso','benchmark_to_beta'] if target_corpus=='beta' else ['loso']):
            for window, scores in scores_by_s.items():
                for variant in BASELINES:
                    preds = np.empty_like(label)
                    probs = {method:np.full(len(label),np.nan) for method in METHODS}
                    if split=='benchmark_to_beta':
                        source_scores,source_label,source_subject,_ = sources['benchmark']
                        model = fit(source_scores[window],source_label,source_subject,variant)
                        models[f'{window}:{variant}'] = model
                        preds,probs = apply(scores,variant,model)
                    else:
                        for subj in np.unique(subject):
                            test = subject == subj
                            model = fit(scores[~test],label[~test],subject[~test],variant)
                            preds[test],partial = apply(scores[test],variant,model)
                            for method,p in partial.items():
                                probs[method][test] = p
                    correct = (preds==label).astype(int)
                    tag = dict(dataset=target_corpus, split=split, seconds=window, variant=variant)
                    for i in range(len(subject)):
                        predictions.append(dict(**tag,subject=int(subject[i]),block=int(block[i]),
                            trial_id=f'{target_corpus}-s{subject[i]}-b{block[i]}-f{FREQ[label[i]]}',
                            true_hz=FREQ[label[i]],pred_hz=FREQ[preds[i]], correct=int(correct[i]),
                            margin=probs['margin'][i], **{'p_'+k:p[i] for k,p in probs.items() if k!='margin'}))
                    for method,p in probs.items():
                        assert np.isfinite(p).all()
                        row = dict(**tag,method=method, n=len(label),subjects=len(np.unique(subject)),
                                   accuracy=float(correct.mean()),fp8=int(np.sum((label!=0)&(preds==0))),
                                   non8=int(np.sum(label!=0)),fn8=int(np.sum((label==0)&(preds!=0))),
                                   true8=int(np.sum(label==0)), **metrics(correct,p))
                        summary.append(row)
                        if window==1.5:
                            bins=np.minimum((p*10).astype(int),9)
                            for b in np.unique(bins):
                                w=bins==b
                                reliability.append(dict(**tag,method=method,bin_lo=b/10,bin_hi=(b+1)/10,
                                                        n=int(w.sum()),mean_p=float(p[w].mean()),acc=float(correct[w].mean())))
    write_csv(out/'trials.csv',predictions)
    write_csv(out/'metrics.csv',summary)
    write_csv(out/'reliability.csv',reliability)
    (out/'benchmark_models.json').write_text(json.dumps(models,indent=2)+'\n')
    hashes={c:hashlib.sha256((root/f'{c}_common6_2s.h5').read_bytes()).hexdigest() for c in sources}
    (out/'run.json').write_text(json.dumps(dict(input_sha256=hashes,seconds=time.monotonic()-start,
        windows=WINDOWS,frequencies=FREQ.tolist(),primary_seconds=1.5),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--processed',type=Path,default=ROOT/'data/public_ssvep/processed')
    parser.add_argument('--out',type=Path,default=HERE/'results')
    args=parser.parse_args()
    validate(args.processed,args.out)
