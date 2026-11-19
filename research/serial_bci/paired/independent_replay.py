"""Command-correctness scores under an explicit product of two empirical populations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "mi"))
from run_mi import CSPClassifier, csp_right, read_session


def csv_rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def mi_accuracy_prior(subjects, data):
    """Estimate each MI decoder's correctness prevalence entirely on the training day."""
    priors = {}
    for name in np.unique(subjects):
        train = read_session(data / f"{name}T.mat")
        runs = train["run"]
        right = np.full(len(runs), np.nan)
        for run in np.unique(runs):
            hold = runs == run
            fit = CSPClassifier(train["fs"]).fit(train["x"][~hold], train["y"][~hold])
            right[hold] = csp_right(fit, train["x"][hold])
        assert np.isfinite(right).all()
        priors[name] = float(((right > 0.5) == train["y"]).mean())
    return priors


def group_average(loss, group_s, group_m):
    # Each block pair below contains original trials of two different public datasets.
    return np.array([[loss[np.ix_(group_s == s, group_m == m)].mean()
                      for m in np.unique(group_m)] for s in np.unique(group_s)])


def summarize(prob, actual, subj_s, subj_m):
    prob = np.broadcast_to(prob, actual.shape)
    brier = (prob - actual) ** 2
    nll = np.where(actual, -np.log(prob.clip(1e-12, 1.)), -np.log1p(-prob.clip(0., 1. - 1e-12)))
    flat = prob.ravel()
    accepted_mass = 0.80 * flat.size
    boundary = np.partition(flat, flat.size - int(np.ceil(accepted_mass)))[flat.size - int(np.ceil(accepted_mass))]
    above, tied = prob > boundary, prob == boundary
    fraction = (accepted_mass - above.sum()) / tied.sum()
    # Expected accuracy from label-blind random acceptance at an equal-score boundary.
    acc = (actual[above].sum() + fraction * actual[tied].sum()) / accepted_mass
    metrics = dict(brier=float(brier.mean()), binary_nll=float(nll.mean()),
                   full_accuracy=float(actual.mean()), coverage=.80, accuracy_at_coverage=float(acc),
                   boundary_score=float(boundary), fraction_of_boundary_ties_accepted=float(fraction))
    return metrics, group_average(brier, subj_s, subj_m), group_average(nll, subj_s, subj_m)


def independent_bootstrap(delta, rng):
    """Resample the 70 visual participants and nine MI participants independently."""
    n_s, n_m = delta.shape
    w_s = rng.multinomial(n_s, np.full(n_s, 1 / n_s), 5000) / n_s
    w_m = rng.multinomial(n_m, np.full(n_m, 1 / n_m), 5000) / n_m
    sampled = np.einsum("bi,ij,bj->b", w_s, delta, w_m)
    return np.quantile(sampled, [.025, .975]).tolist()


def run(ss_file, mi_file, data):
    ss_rows = csv_rows(ss_file)
    mi_rows = csv_rows(mi_file)
    s_m = np.array([r["subject"] for r in mi_rows])
    assert len({(r["subject"], r["session"], r["trial"]) for r in mi_rows}) == len(mi_rows)
    right = np.array([float(r["p_right_csp"]) for r in mi_rows])
    y_right = np.array([int(r["y_true"]) for r in mi_rows])
    q = np.maximum(right, 1 - right)
    c_m = (right > 0.5) == y_right
    priors = mi_accuracy_prior(s_m, data)
    prior_m = np.array([priors[s] for s in s_m])
    result = dict(
        estimand="Binary event: both predicted components correct, under independent uniform trial draws "
                 "from external BETA SSVEP and BNCI2014-001 E-session hand MI. Same pair weights for all methods.",
        design="Cartesian product, no recorded pairing or timing; engineering replay on previously examined "
               "data. Coverage is a retrospective score quantile. Intervals resample component participants "
               "separately with frozen predictions, and do not estimate a serial cohort's variability.",
        primary_variant="train_nontarget", mi_prior_T_day_out_of_run_accuracy=priors,
        mi_correct=int(c_m.sum()), n_mi=len(c_m), mi_subjects=len(set(s_m)), windows_ss_seconds=1.5,
        source_prediction_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ss_file, mi_file)},
        variants={})
    for variant in ("train_nontarget", "raw"):
        ss = [r for r in ss_rows if r["dataset"] == "beta" and r["split"] == "benchmark_to_beta"
              and float(r["seconds"]) == 1.5 and r["variant"] == variant]
        assert len({r["trial_id"] for r in ss}) == len(ss) == 1680
        s_s = np.array([r["subject"] for r in ss])
        assert len(set(np.unique(s_s,return_counts=True)[1])) == len(set(np.unique(s_m,return_counts=True)[1])) == 1
        p = np.array([float(r["p_platt"]) for r in ss])[:, None]
        prior_s = np.array([float(r["p_prior"]) for r in ss])[:, None]
        c_s = np.array([bool(int(r["correct"])) for r in ss])
        actual = c_s[:, None] & c_m
        scores = {"p_times_q": p * q, "prior_s_times_q": prior_s * q, "q_alone": q,
                  "p_times_prior_m": p * prior_m, "two_priors": prior_s * prior_m}
        metrics = {}
        blocks = {}
        for name, val in scores.items():
            metrics[name], brier, nll = summarize(val, actual, s_s, s_m)
            blocks[name] = (brier, nll)
        b_base, n_base = blocks["prior_s_times_q"]
        b_full, n_full = blocks["p_times_q"]
        rng = np.random.default_rng(1192026)
        bounds = [max(0., float(c_s.mean()+c_m.mean()-1)), min(float(c_s.mean()), float(c_m.mean()))]
        out = dict(ss_correct=int(c_s.sum()), n_ss=len(ss), ss_subjects=len(set(s_s)),
                   benchmark_training_prior=float(prior_s[0,0]), product_size=int(actual.size),
                   doubly_correct_product_cells=int(actual.sum()), metrics=metrics,
                   possible_joint_correctness_if_only_empirical_marginal_accuracies_are_fixed=bounds,
                   lower_is_better_full_minus_primary_no_p={
                       "brier": float((b_full-b_base).mean()),
                       "brier_two_population_bootstrap_ci95": independent_bootstrap(b_full-b_base, rng),
                       "binary_nll": float((n_full-n_base).mean()),
                       "binary_nll_two_population_bootstrap_ci95": independent_bootstrap(n_full-n_base, rng)})
        assert np.isclose(actual.mean(), c_s.mean()*c_m.mean())
        brier_identity = (p*p).mean() * (q*q).mean() - 2 * (p[:,0]*c_s).mean() * (q*c_m).mean() + actual.mean()
        assert np.isclose(metrics["p_times_q"]["brier"], brier_identity)
        result["variants"][variant] = out
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ss", type=Path, default=HERE.parent/"ssvep/results/trials.csv")
    parser.add_argument("--mi", type=Path, default=HERE.parent/"mi/results/predictions.csv")
    parser.add_argument("--mi-data", type=Path, default=PROJECT/"data/public_mi/bnci2014_001")
    parser.add_argument("--out", type=Path, default=HERE/"independent_replay.json")
    args = parser.parse_args()
    args.out.write_text(json.dumps(run(args.ss, args.mi, args.mi_data), indent=2) + "\n")
