"""Three-channel, run-calibrated motor imagery decoding on BCI IV-2a."""
import argparse
import csv
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import scipy
import sklearn
from scipy import signal
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.ml.mi_baseline import BCI2A_CHANNELS, MI_CHANNELS, CSPClassifier, preprocess_mi
from src.ml.public_mi import load_bnci_session

BANDS = ((8, 12), (12, 16), (16, 22), (22, 30))


def covariance_features(x, fs):
    """Log of trace-normalized shrinkage covariance in each sensorimotor band."""
    x = np.asarray(x, float)
    if x.ndim != 3 or x.shape[1] != 3 or x.shape[-1] < fs or not np.isfinite(x).all() or fs <= 60:
        raise ValueError("Expected finite trials x (C3,Cz,C4) x samples, at least one second")
    x = signal.detrend(x, axis=-1)
    upper = np.triu_indices(3)
    weight = np.where(upper[0] == upper[1], 1., np.sqrt(2))
    features = []
    for lo, hi in BANDS:
        sos = signal.butter(4, [lo, hi], fs=fs, btype="bandpass", output="sos")
        eeg = signal.sosfiltfilt(sos, x, axis=-1, padlen=min(round(fs * .5), x.shape[-1] - 1))
        eeg -= eeg.mean(axis=-1, keepdims=True)
        cov = eeg @ eeg.transpose(0, 2, 1)
        cov = .95 * cov / np.maximum(np.trace(cov, axis1=1, axis2=2)[:, None, None], 1e-30) + .05 * np.eye(3) / 3
        vals, vectors = np.linalg.eigh(cov)
        matrix_log = (vectors * np.log(vals)[:, None, :]) @ vectors.transpose(0, 2, 1)
        features.append(matrix_log[:, upper[0], upper[1]] * weight)
    return np.column_stack(features)


def fit_decoder(train, labels):
    return make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)).fit(train, labels)


def fit_mi(train_x, labels, groups, fs):
    """Fit a subject's decoder plus Platt map on out-of-run training scores."""
    features = covariance_features(train_x, fs)
    labels, groups = np.asarray(labels), np.asarray(groups)
    if set(np.unique(labels)) != {0, 1} or len(np.unique(groups)) < 2:
        raise ValueError("Need two hands and at least two training groups")
    held_score = np.full(len(labels), np.nan)
    for group in np.unique(groups):
        test = groups == group
        fit = fit_decoder(features[~test], labels[~test])
        held_score[test] = fit.decision_function(features[test])
    # Both slope and intercept fitted using only T-session leave-one-run-out scores.
    platt = LogisticRegression(C=1., max_iter=500).fit(held_score[:, None], labels)
    return fit_decoder(features, labels), platt


def predict_mi(model, test_x, fs):
    decoder, platt = model
    score = decoder.decision_function(covariance_features(test_x, fs))
    return platt.predict_proba(score[:, None])[:, 1], expit(score)


def csp_right(model, test_x):
    """Probability of right hand for a fitted binary CSPClassifier (0 left, 1 right)."""
    if not np.array_equal(model.classes, [0, 1]) or test_x.shape[-1] != model.sample_count:
        raise ValueError("CSP classes or input duration do not match the trained binary MI decoder")
    eeg = preprocess_mi(model._validate(test_x), model.fs)
    return expit((model.features(eeg) @ model.coef.T + model.intercept).ravel())


def read_session(source):
    session = load_bnci_session(source)
    keep = np.flatnonzero(session['y'] < 2)
    ch = [BCI2A_CHANNELS.index(c) for c in MI_CHANNELS]
    return dict(x=session['eeg'][keep][:, ch], y=session['y'][keep], run=session['run'][keep],
                trial=keep + 1, artifact=session['artifact_flag'][keep], fs=session['fs'])


def run_subject(train, test_file):
    fs = train['fs']
    model = fit_mi(train['x'], train['y'], train['run'], fs)
    csp = CSPClassifier(fs).fit(train['x'], train['y'])
    test = read_session(test_file)
    p, raw_p = predict_mi(model, test['x'], fs)
    baseline_p = csp_right(csp, test['x'])
    assert np.array_equal(csp.predict(test['x']), (baseline_p > .5))
    return test, p, raw_p, baseline_p


def metrics(rows, column):
    y = np.array([r['y_true'] for r in rows])
    p = np.array([r[column] for r in rows])
    correct = y == (p > .5)
    return dict(n=len(y), correct=int(correct.sum()), accuracy=correct.mean(),
                balanced_accuracy=balanced_accuracy_score(y, p > .5),
                brier=np.mean((p-y)**2), nll=log_loss(y,p,labels=[0,1]))


def write_table(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT/'data/public_mi/bnci2014_001')
    parser.add_argument('--out', type=Path, default=HERE/'results')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows, hashes = [], {}
    for number in range(1, 10):
        subject = f'A{number:02}'
        train_path = args.data / f'{subject}T.mat'
        test_path = args.data / f'{subject}E.mat'
        train = read_session(train_path)
        test, p, raw_p, base = run_subject(train, test_path)
        for path in (train_path, test_path):
            with path.open('rb') as f:
                hashes[path.name] = hashlib.file_digest(f, 'sha256').hexdigest()
        for i, prob in enumerate(p):
            truth = int(test['y'][i])
            pred = int(prob > .5)
            rows.append(dict(subject=subject,session='E',run=int(test['run'][i]),
                             trial=int(test['trial'][i]),y_true=truth,p_right=prob,y_pred=pred,
                             p_correct=max(prob,1-prob),correct=int(truth==pred),
                             p_right_uncalibrated=raw_p[i],p_right_csp=base[i],
                             official_artifact=int(test['artifact'][i])))
        print(f'{subject} complete', flush=True)
    assert len({(r['subject'],r['trial']) for r in rows}) == len(rows) == 1296
    write_table(args.out/'predictions.csv', rows)
    models = dict(bandcov_platt='p_right',bandcov_raw='p_right_uncalibrated',csp_lda='p_right_csp')
    subjects = sorted({r['subject'] for r in rows})
    detail = [dict(subject=s, model=k, **metrics([r for r in rows if r['subject']==s], v))
              for s in subjects for k,v in models.items()]
    write_table(args.out/'per_subject.csv', detail)
    rng = np.random.default_rng(261119)
    draws = rng.integers(len(subjects),size=(10000,len(subjects)))
    def interval(values):
        return np.quantile(np.mean(values[draws],axis=1),[.025,.975]).tolist()
    stats = {}
    for key in ('accuracy','brier','nll'):
        primary = np.array([r[key] for r in detail if r['model']=='bandcov_platt'])
        baseline = np.array([r[key] for r in detail if r['model']=='csp_lda'])
        stats[key] = dict(mean=float(primary.mean()), percentile_ci95=interval(primary),
                          delta_vs_csp=float((primary-baseline).mean()), delta_ci95=interval(primary-baseline))
    summary = dict(dataset='BNCI2014-001', subjects=len(subjects), train_session='T', test_session='E',
                   protocol='Within subject, prior-day training. All left/right trials including official artifact flags.',
                   public_eeg_reused_in_workspace=True, source='https://bnci-horizon-2020.eu/database/data-sets',
                   tested_models={k:metrics(rows,v) for k,v in models.items()}, subject_bootstrap=stats,
                   versions=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__,sklearn=sklearn.__version__),
                   sha256=hashes)
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary['tested_models'],indent=2))


if __name__ == '__main__':
    main()
