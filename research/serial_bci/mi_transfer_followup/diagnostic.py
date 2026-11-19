"""Chronological same-trial P diagnostic. Run with --help for input/output paths."""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.special import expit, logit
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from research.serial_bci.mi.run_mi import csp_right
from research.serial_bci.serial.run import CSPClassifier, load, ss_predict, write_csv


def odds(p):
    return logit(np.clip(p, 1e-5, 1 - 1e-5))


def fit_risk_head(x, y, xt):
    """Forecast correctness of the fixed MI decoder, using calibration blocks only."""
    if np.unique(y).size == 1:
        return np.full(len(xt), (sum(y) + 1) / (len(y) + 2)), np.zeros(x.shape[1])
    scaler = StandardScaler().fit(x)
    model = LogisticRegression(C=1, max_iter=1000).fit(scaler.transform(x), y)
    return model.predict_proba(scaler.transform(xt))[:, 1], model.coef_[0] / scaler.scale_


def predict(records):
    g = np.array([r['block'] for r in records])
    tr = np.flatnonzero(g <= 4)
    te = np.flatnonzero(g > 4)
    if max(records[i]['end'] for i in tr) >= min(records[i]['start'] for i in te):
        raise ValueError('split chronology')
    s = np.stack([r['scores'] for r in records])
    h = np.array([r['true_hz'] - 8 for r in records])
    x = np.stack([r['mi'] for r in records])
    y = np.array([r['hand'] for r in records])

    def stages(train, test):
        if set(h[train]) != set(range(6)) or np.min(np.bincount(y[train], minlength=2)) < 2:
            raise ValueError('insufficient training classes')
        pred, _, p = ss_predict(s, h, g, train, test, 'train_nontarget')
        q = csp_right(CSPClassifier().fit(x[train], y[train]), x[test])
        return np.column_stack([pred, p, q])

    oof = np.zeros((len(g), 3))
    for group in np.unique(g[tr]):
        val = tr[g[tr] == group]
        oof[val] = stages(tr[g[tr] != group], val)
    a0, p0, q0 = oof[tr].T
    a, p, q = stages(tr, te).T
    correct0 = (q0 > 0.5) == y[tr]
    source_rate = np.mean(a0 == h[tr])
    features0 = np.column_stack([np.abs(odds(q0)), odds(p0)])
    features = np.column_stack([np.abs(odds(q)), odds(p)])
    without, _ = fit_risk_head(features0[:, :1], correct0, features[:, :1])
    with_p, weights = fit_risk_head(features0, correct0, features)
    rows = []
    for j, i in enumerate(te):
        r = records[i]
        row = {k: r[k] for k in ('subject', 'run', 'pair', 'block', 'start', 'true_hz', 'hand')}
        row.update(pred_hz=int(a[j] + 8), ss_hit=int(a[j] == h[i]), p=float(p[j]),
                   ss_train_rate=float(source_rate), q_right=float(q[j]),
                   mi_hit=int((q[j] > .5) == y[i]), mi_forecast_only=float(without[j]),
                   mi_forecast_p=float(with_p[j]), p_logit_coef=float(weights[1]))
        rows.append(row)
    return rows, len(tr)


def groups(rows, keys):
    out = defaultdict(list)
    for i, row in enumerate(rows):
        out[tuple(row[k] for k in keys)].append(i)
    return [np.array(idx) for idx in out.values()]


def auc(labels, scores):
    return None if len(set(labels)) < 2 else float(roc_auc_score(labels, scores))


def loss(y, p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(np.where(y, np.log(p), np.log1p(-p))))


def describe(rows):
    def col(k):
        return np.array([row[k] for row in rows])
    p, q, ss, mi, hand = (col(k) for k in ('p', 'q_right', 'ss_hit', 'mi_hit', 'hand'))
    base, rich = col('mi_forecast_only'), col('mi_forecast_p')
    signed = (hand * 2 - 1) * odds(q)
    def rho(z):
        if np.ptp(p) == 0 or np.ptp(z) == 0:
            return None
        return float(spearmanr(p, z).statistic)
    return dict(n=len(rows), ss_hit=int(ss.sum()), mi_hit=int(mi.sum()), joint_hit=int(sum(ss * mi)),
                true8=int(sum(col('true_hz') == 8)), false_8=int(sum((col('true_hz') != 8) & (col('pred_hz') == 8))),
                missed_8=int(sum((col('true_hz') == 8) & (col('pred_hz') != 8))),
                p_min_median_max=np.quantile(p, [0, .5, 1]).tolist(), p_mean=float(p.mean()),
                visual_auc=auc(ss, p), mi_correct_auc_from_p=auc(mi, p), hand_auc_from_p=auc(hand, p),
                visual_brier=float(np.mean((ss - p) ** 2)),
                visual_brier_train_rate=float(np.mean((ss - col('ss_train_rate')) ** 2)),
                rho_p_abs_mi_log_odds=rho(np.abs(odds(q))), rho_p_true_hand_evidence=rho(signed),
                mi_forecast_brier_only=float(np.mean((mi - base) ** 2)),
                mi_forecast_brier_p=float(np.mean((mi - rich) ** 2)),
                mi_forecast_loss_only=loss(mi, base), mi_forecast_loss_p=loss(mi, rich),
                mi_forecast_auc_only=auc(mi, base), mi_forecast_auc_p=auc(mi, rich))


def concordance_pairs(labels, strata):
    plus, minus = [], []
    for ids in strata:
        pos, neg = ids[labels[ids] == 1], ids[labels[ids] == 0]
        if len(pos) and len(neg):
            plus.extend(np.repeat(pos, len(neg)))
            minus.extend(np.tile(neg, len(pos)))
    return np.asarray(plus, dtype=int), np.asarray(minus, dtype=int)


def concordance(p, index):
    a, b = index
    return float(np.mean(.5 + .5 * np.sign(p[a] - p[b]))) if len(a) else None


def controls(rows, draws, seed):
    """Retain run boundaries and margins of each stratum in all null references."""
    rng = np.random.default_rng(seed)
    p = np.array([r['p'] for r in rows])
    mi = np.array([r['mi_hit'] for r in rows])
    visual = np.array([r['ss_hit'] for r in rows])
    hand = np.array([r['hand'] for r in rows])
    with_p = np.array([r['mi_forecast_p'] for r in rows])
    only = np.array([r['mi_forecast_only'] for r in rows])
    coeff = np.array([r['p_logit_coef'] for r in rows])
    fixed_eta = logit(np.clip(with_p, 1e-15, 1 - 1e-15)) - coeff * odds(p)
    out = {}
    for name, keys in [('block_roll', ('run', 'block')), ('within_hz_shuffle', ('run', 'block', 'true_hz'))]:
        strata = groups(rows, keys)
        ix = [concordance_pairs(label, strata) for label in (visual, mi, hand)]
        def stats(swapped, swapped_ss_hit):
            predictions = expit(fixed_eta + coeff * odds(swapped))
            return [concordance(swapped, idx) for idx in ix] + [
                np.mean((only - mi)**2 - (predictions - mi)**2),
                np.sum(swapped_ss_hit * mi)]
        observed = stats(p, visual)
        samples = []
        for _ in range(draws):
            permutation = np.arange(len(p))
            for ids in strata:
                if len(ids) > 1:
                    if name == 'block_roll':
                        permutation[ids] = np.roll(ids, rng.integers(len(ids)))
                    else:
                        permutation[ids] = rng.permutation(ids)
            samples.append(stats(p[permutation], visual[permutation]))
        null = np.asarray(samples, dtype=float)
        controls_out = {}
        for i, key in enumerate(('ss_auc', 'mi_correct_auc', 'right_hand_auc', 'mi_risk_brier_gain',
                                 'oracle_joint_hits')):
            quantiles = np.quantile(null[:, i], [.025, .5, .975]).tolist() if observed[i] is not None else None
            controls_out[key] = dict(observed=float(observed[i]) if observed[i] is not None else None,
                                     neutral_reference_q025_q50_q975=quantiles)
            if i < 3:
                controls_out[key]['compared_positive_negative_pairs'] = len(ix[i][0])
        out[name] = dict(strata=len(strata), eligible_to_move=sum(len(ids) for ids in strata if len(ids) > 1),
                         statistics=controls_out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, default=ROOT / 'data/research_experiments/raw_trials')
    parser.add_argument('--out', type=Path, default=HERE / 'diagnostic_results')
    parser.add_argument('--reference', type=Path, help='check held-out pair identities, classes and stage scores against existing trial CSV')
    parser.add_argument('--draws', type=int, default=4000)
    args = parser.parse_args()
    if args.draws < 1:
        parser.error('--draws must be positive')
    rows, train_counts, excluded = [], {}, {}
    for folder in sorted({p.parent for p in args.raw.rglob('hybrid-*-ssvep.npz')}):
        rec, _, _ = load(folder)
        blocks = {r['block'] for r in rec}
        if len(blocks) < 6 or not {1, 2, 3, 4}.issubset(blocks):
            excluded[folder.name] = 'too few valid recording blocks'
            continue
        try:
            pred, n = predict(rec)
        except ValueError as exc:
            excluded[folder.name] = str(exc)
            continue
        train_counts[folder.name] = n
        rows.extend(pred)
    if not rows:
        raise ValueError('No eligible chronological paired recordings')
    rows.sort(key=lambda r: (r['run'], r['start']))
    if args.reference:
        with args.reference.open(encoding='utf-8') as f:
            ref = {r['pair']: r for r in csv.DictReader(f)}
        if set(ref) != {r['pair'] for r in rows}:
            raise ValueError('Reference pair identities differ')
        for row in rows:
            r = ref[row['pair']]
            for key in ('p', 'q_right', 'true_hz', 'hand', 'pred_hz'):
                np.testing.assert_allclose(row[key], float(r[key]), atol=1e-9, rtol=1e-7)
    summary = dict(training_counts=train_counts, excluded=excluded, overall=describe(rows))
    for key in ('subject', 'run'):
        summary['by_' + key] = {
            str(rows[g[0]][key]): describe([rows[i] for i in g]) for g in groups(rows, (key,))}
    summary['participant_macro'] = {
        key: float(np.mean([r[key] for r in summary['by_subject'].values()]))
        for key in ('visual_brier', 'visual_brier_train_rate', 'mi_forecast_brier_only',
                    'mi_forecast_brier_p', 'mi_forecast_loss_only', 'mi_forecast_loss_p')}
    control_seed = 20261119
    summary['negative_controls'] = dict(draws=args.draws, seed=control_seed,
                                         **controls(rows, args.draws, control_seed))
    summary['stimulus_hand_counts'] = {str(hz): [
        sum(r['true_hz'] == hz and r['hand'] == hand for r in rows) for hand in (0, 1)]
        for hz in range(8, 14)}
    summary['strata_descriptives'] = []
    for ids in groups(rows, ('run', 'block')):
        batch = [rows[i] for i in ids]
        summary['strata_descriptives'].append(dict(run=batch[0]['run'], block=batch[0]['block'],
                                                   **describe(batch)))
    args.out.mkdir(exist_ok=True, parents=True)
    write_csv(args.out / 'trials.csv', rows)
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k in ('overall', 'negative_controls')}, indent=2))


if __name__ == '__main__':
    main()
