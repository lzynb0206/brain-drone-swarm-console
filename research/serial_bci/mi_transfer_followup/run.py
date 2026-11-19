"""Trial-aligned, forward evaluation from the console's raw recordings."""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from sklearn.metrics import roc_auc_score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from research.serial_bci.serial.run import (
    load,
    ss_predict,
    read_stage,
    MONTAGES,
    CSPClassifier,
    csp_right,
)
from research.serial_bci.serial.compare_calibrators import predict as richer_predict
from research.serial_bci.ssvep.validate import fbcca, FREQ
from research.serial_bci.mi_transfer_followup.experts import expert_evidence, fit_gate
from research.serial_bci.mi_transfer_followup.collection_audit import audit_pairs

ARMS = (
    "broadband",
    "mu",
    "beta",
    "no_p",
    "with_p",
    "mi_only",
    "mi_plus_p",
    "shift_refit",
    "oracle_visual",
)
SHIFTS = 499
SEED = 261119


def write_rows(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def metric(y, q, reference):
    hit = (q > 0.5) == y
    control = (reference > 0.5) == y
    return dict(
        n=len(y),
        correct=int(hit.sum()),
        accuracy=float(hit.mean()),
        ba=(
            float(np.mean([hit[y == k].mean() for k in (0, 1)]))
            if len(np.unique(y)) == 2
            else None
        ),
        brier=float(np.mean((q - y) ** 2)),
        rescued=int(np.sum(hit & ~control)),
        harmed=int(np.sum(~hit & control)),
    )


def auc(y, p):
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None


def long_score(path):
    stage = read_stage(path)
    ctx = stage["context"]
    times = stage["timestamps"] - ctx["stimulus_lsl_time"]
    at = int(np.searchsorted(times, 0.14))
    times = times[at : at + 1000]
    if (
        len(times) != 1000
        or abs(times[0] - 0.14) > 0.025
        or np.max(np.diff(times)) > 0.1
        or abs(times[-1] - times[0] - 3.996) > 0.035
    ):
        raise ValueError("4s_window_clock_or_length")
    names = MONTAGES[ctx["montage_id"]]
    indices = list(names.indices(tuple(ctx["channel_names"]), "SSVEP"))
    signal = stage["eeg"][indices, at : at + 1000]
    if not np.isfinite(signal).all() or np.any(signal.std(axis=1) < 1e-12):
        raise ValueError("4s_flat_or_nonfinite")
    return fbcca(signal)


def shift(p, groups, rng=None):
    shifted = p.copy()
    for group in np.unique(groups):
        idx = np.flatnonzero(groups == group)
        if len(idx) > 1:
            step = 1 if rng is None else int(rng.integers(1, len(idx)))
            shifted[idx] = np.roll(p[idx], step)
    return shifted


def evaluate(
    train_records, test_records, scores_by_pair, experiment, fold_name, seed=SEED
):
    train = sorted(train_records, key=lambda r: (r["run"], r["start"]))
    test = sorted(test_records, key=lambda r: (r["run"], r["start"]))
    records = train + test
    if len({r["pair"] for r in records}) != len(records):
        raise ValueError("Duplicate pair across calibration or test")
    if len({r["ss_sha256"] for r in records}) != len(records) or len(
        {r["mi_sha256"] for r in records}
    ) != len(records):
        raise ValueError("Repeated source file across the evaluated pairs")
    if train[-1]["run"] > test[0]["run"]:
        raise ValueError("Training must precede test")
    for shared_run in {r["run"] for r in train} & {r["run"] for r in test}:
        if max(r["end"] for r in train if r["run"] == shared_run) >= min(
            r["start"] for r in test if r["run"] == shared_run
        ):
            raise ValueError(
                "Calibration and test epochs overlap within the same clock"
            )
    g = np.array([f"{r['run']}:{r['block']}" for r in records])
    s = np.stack([scores_by_pair[r["pair"]] for r in records])
    f = np.array([r["true_hz"] - int(FREQ[0]) for r in records])
    y = np.array([r["hand"] for r in records])
    x = np.stack([r["mi"] for r in records])
    tr, te = np.arange(len(train)), np.arange(len(train), len(records))
    if set(g[tr]) & set(g[te]) or len(np.unique(g[tr])) < 3:
        raise ValueError("Need disjoint splits and at least three training blocks")
    multi = experiment == "external_gap_p"
    if multi:
        ss_dir = HERE.parent / "ssvep"
        scalar = json.loads((ss_dir / "results/benchmark_models.json").read_text())[
            "1.5:train_nontarget"
        ]
        vector = json.loads(
            (ss_dir / "comparator_results/benchmark_models.json").read_text()
        )

    def ss(a, b):
        pred, _, p = ss_predict(s, f, g, a, b, "train_nontarget")
        if multi:
            p = richer_predict(s, f, g, a, b, scalar, vector)["source_five_intercept"]
        return pred, p

    p0 = np.empty(len(tr))
    correct0 = np.empty(len(tr), dtype=float)
    for group in np.unique(g[tr]):
        a, b = tr[g[tr] != group], tr[g[tr] == group]
        if (
            len(np.unique(f[a])) != len(FREQ)
            or np.min(np.bincount(y[a], minlength=2)) < 2
        ):
            raise ValueError("Insufficient training frequencies or hands")
        pred, p0[b] = ss(a, b)
        correct0[b] = pred == f[b]
    pred, p = ss(tr, te)
    correct = (pred == f[te]).astype(int)
    oof, held = expert_evidence(x[tr], y[tr], g[tr], x[te])
    model = fit_gate(oof, y[tr], p0, held, p)
    info = model.pop("info")
    model["mu"], model["beta"] = held.T
    model["broadband"] = csp_right(CSPClassifier().fit(x[tr], y[tr]), x[te])
    model["shift_refit"] = fit_gate(
        oof, y[tr], shift(p0, g[tr]), held, shift(p, g[te])
    )["mi_plus_p"]
    # The following arm deliberately consumes SS labels at evaluation, and is never used for deployment.
    model["oracle_visual"] = fit_gate(oof, y[tr], correct0, held, correct)["mi_plus_p"]
    rng = np.random.default_rng(seed)
    shifted = np.concatenate([shift(p, g[te], rng) for _ in range(SHIFTS)])
    surrogate = fit_gate(oof, y[tr], p0, np.tile(held, (SHIFTS, 1)), shifted)[
        "mi_plus_p"
    ].reshape(SHIFTS, len(te))
    # Store individual outcomes for equal-person aggregation and cross-session clustering.
    # Conditional mispairing is an offline control. Only this control sees the true visual target.
    strata = np.asarray([f"{g[i]}:{f[i]}" for i in te])
    indices = [np.flatnonzero(strata == group) for group in sorted(set(strata))]
    shuffled = np.repeat(p[None, :], SHIFTS, axis=0)
    for idx in indices:
        if len(idx) > 1:
            for draw in range(SHIFTS):
                shuffled[draw, idx] = rng.permutation(p[idx])
    conditional = fit_gate(
        oof, y[tr], p0, np.tile(held, (SHIFTS, 1)), shuffled.ravel()
    )["mi_plus_p"].reshape(SHIFTS, len(te))
    rows = []
    for i, r in enumerate(test):
        row = {
            k: r[k]
            for k in (
                "subject",
                "run",
                "pair",
                "block",
                "true_hz",
                "hand",
                "gap",
                "good",
                "ss_sha256",
                "mi_sha256",
            )
        }
        row.update(
            **{
                k: r.get(k)
                for k in ("ssvep_redundant", "mi_redundant", "c3_c4_redundant")
            },
            experiment=experiment,
            fold=fold_name,
            p=float(p[i]),
            ss_hz=int(FREQ[pred[i]]),
            ss_hit=int(correct[i]),
            ss_training_rate=float(correct0.mean()),
            **{key: float(model[key][i]) for key in ARMS},
        )
        rows.append(row)
    return rows, dict(
        experiment=experiment,
        fold=fold_name,
        train_n=len(train),
        test_n=len(test),
        shuffle_seed=seed,
        train_subjects=sorted({r["subject"] for r in train}),
        test_subjects=sorted({r["subject"] for r in test}),
        train_runs=sorted({r["run"] for r in train}),
        test_runs=sorted({r["run"] for r in test}),
        gate=info,
        shift_right=surrogate,
        conditional_right=conditional,
        conditional_movable=sum(len(idx) for idx in indices if len(idx) > 1),
        training_pairs=[r["pair"] for r in train],
    )


def summarize(rows):
    y = np.array([r["hand"] for r in rows])
    ref = np.array([r["mi_only"] for r in rows])
    p = np.array([r["p"] for r in rows])
    c = np.array([r["ss_hit"] for r in rows])
    arms = {key: metric(y, np.array([r[key] for r in rows]), ref) for key in ARMS}
    g = np.array([f"{r['run']}:{r['block']}" for r in rows])
    wins = 0.0
    pairs = 0
    for block in set(g):
        a = p[(g == block) & (c == 1)]
        b = p[(g == block) & (c == 0)]
        wins += np.sum(a[:, None] > b) + 0.5 * np.sum(a[:, None] == b)
        pairs += len(a) * len(b)
    hz = np.array([r["true_hz"] for r in rows])
    guess = np.array([r["ss_hz"] for r in rows])
    return dict(
        arms=arms,
        ss_prior_brier=float(
            np.mean((np.array([r["ss_training_rate"] for r in rows]) - c) ** 2)
        ),
        ss_auc_within_block=wins / pairs if pairs else None,
        ss_withinblock_pairs=pairs,
        false8=int(np.sum((guess == 8) & (hz != 8))),
        non8=int(np.sum(hz != 8)),
        missed8=int(np.sum((guess != 8) & (hz == 8))),
        true8=int(np.sum(hz == 8)),
        redundant_counts={
            key: sum(r.get(key) is True for r in rows)
            for key in ("c3_c4_redundant", "mi_redundant", "ssvep_redundant")
        },
        ss_correct=int(c.sum()),
        ss_brier=float(np.mean((p - c) ** 2)),
        ss_auc=auc(c, p),
        expert_disagreement=sum((r["mu"] > 0.5) != (r["beta"] > 0.5) for r in rows),
        n=len(rows),
    )


def aggregate(rows, folds):
    people = sorted({r["subject"] for r in rows})
    by_person = {s: summarize([r for r in rows if r["subject"] == s]) for s in people}
    pooled = summarize(rows)

    def delta(s):
        arms = by_person[s]["arms"]
        if arms["mi_plus_p"]["ba"] is None:
            return None
        return arms["mi_plus_p"]["ba"] - arms["mi_only"]["ba"]

    observed = np.array([delta(s) for s in people if delta(s) is not None])
    person_ci = None
    interval_method = None
    if len(observed) >= 8:
        if np.all(observed == 0):
            # No observed positive or negative person effect. Bound each rate with a one-sided
            # exact binomial upper bound and use the [-1,1] range of balanced-accuracy differences.
            upper = 1 - 0.025 ** (1 / len(observed))
            person_ci = [-upper, upper]
            interval_method = "exact_sign_rate_bound_all_zero"
        elif np.ptp(observed) > 0:
            rng = np.random.default_rng(SEED)
            boot = rng.choice(observed, (10000, len(observed)), replace=True).mean(
                axis=1
            )
            person_ci = np.quantile(boot, [0.025, 0.975]).tolist()
            interval_method = "participant_percentile_bootstrap"

    def null_comparison(column):
        data = {
            r["pair"]: f[column][:, j]
            for f in folds
            for j, r in enumerate([r for r in rows if r["fold"] == f["fold"]])
        }
        null = []
        for person in people:
            chosen = [r for r in rows if r["subject"] == person]
            if len({r["hand"] for r in chosen}) != 2:
                continue
            rights = np.stack([data[r["pair"]] for r in chosen], axis=1)
            y = np.array([r["hand"] for r in chosen])
            hit = (rights > 0.5) == y
            ba = (hit[:, y == 0].mean(1) + hit[:, y == 1].mean(1)) / 2
            null.append(ba - by_person[person]["arms"]["mi_only"]["ba"])
        dist = np.mean(null, axis=0) if null else np.array([])
        return (
            (
                np.quantile(dist, [0.025, 0.5, 0.975]).tolist(),
                float(observed.mean() - dist.mean()),
            )
            if len(dist)
            else ([], None)
        )

    shifts, shift_gain = null_comparison("shift_right")
    conditional, conditional_gain = null_comparison("conditional_right")
    return dict(
        pooled=pooled,
        by_person=by_person,
        subject_count=len(people),
        estimable_subjects=len(observed),
        mean_person_delta_ba=float(observed.mean()) if len(observed) else None,
        subject_bootstrap_95ci=person_ci,
        interval_method=interval_method,
        shift_delta_ba_quantiles=shifts,
        shift_draws=SHIFTS,
        paired_minus_shift_mean_ba=shift_gain,
        conditional_shift_delta_ba_quantiles=conditional,
        paired_minus_conditional_mean_ba=conditional_gain,
        conditional_movable=sum(f["conditional_movable"] for f in folds),
    )


def run(raw, out, historic_challenge=False, strict_new=False, roster=None):
    raw, out = Path(raw), Path(out)
    primary_experiment = "within_4s" if strict_new else "within_1p5"
    out.mkdir(parents=True, exist_ok=True)
    folders = sorted(
        {
            p.parent
            for suffix in ("ssvep", "mi")
            for p in raw.rglob(f"hybrid-*-{suffix}.npz")
        }
    )
    records_by_run = {}
    short = {}
    long = {}
    audits = []
    long_issues = []
    omissions = []
    acquisition = []
    for folder in folders:
        pairs, _, audit = load(folder)
        if roster is not None and (not pairs or pairs[0]["subject"] not in roster):
            continue
        audits.extend(audit)
        if not pairs:
            continue
        if folder.name in records_by_run:
            raise ValueError(f"Duplicate run directory {folder.name}")
        if strict_new and pairs[0]["subject"] in {
            "subject-0001",
            "subject-0003",
            "subject-0004",
        }:
            raise ValueError(
                f"{folder.name} belongs to a development ID; supply only the new cohort"
            )
        acquisition.append(audit_pairs(folder, pairs))
        records_by_run[folder.name] = pairs
        for r in pairs:
            if r["pair"] in short:
                raise ValueError("Duplicate pair ID across runs")
            short[r["pair"]] = r["scores"]
            try:
                long[r["pair"]] = long_score(folder / (r["pair"] + "-ssvep.npz"))
            except (ValueError, KeyError, OSError) as ex:
                long_issues.append(dict(pair=r["pair"], issue=str(ex)))
    issues = []
    owners = defaultdict(list)
    for a in acquisition:
        for seed in a["seeds"]:
            owners[seed].append(a["run"])
        if a["plan_mismatch_pairs"] or a["seed_missing_pairs"] or len(a["seeds"]) != 1:
            issues.append(f"{a['run']} has unverified assignment or seed")
        if strict_new and not all(
            v["planned"] == 84 and v["idle"] == 8 and v["preset_seed"] in a["seeds"]
            for v in a["planned_settings"]
        ):
            issues.append(f"{a['run']} does not match the prospective 84-pair preset")
        if strict_new and (
            not a["signal_sources"]
            or any(
                any(
                    k in source.lower()
                    for k in ("mock", "synthetic", "simulate", "模拟")
                )
                for source in a["signal_sources"]
            )
        ):
            issues.append(f"{a['run']} needs source verification")
    issues.extend(
        f"Repeated seed {seed} in {', '.join(names)}"
        for seed, names in owners.items()
        if len(names) > 1
    )
    rows = []
    folds = []
    complete_by_person = defaultdict(list)

    def append(train, test, scores, experiment, name):
        # Stable per-fold random streams; never select a shift from its outcome.
        seed = SEED + int.from_bytes(
            hashlib.sha256(name.encode()).digest()[:4], "little"
        )
        pred, meta = evaluate(train, test, scores, experiment, name, seed)
        rows.extend(pred)
        folds.append(meta)

    for name, rs in sorted(records_by_run.items()):
        blocks = {r["block"] for r in rs}
        if max(blocks) > 7:
            raise ValueError(f"{name}: expected at most seven numbered blocks")
        if not {1, 2, 3, 4}.issubset(blocks) or len(blocks & {5, 6, 7}) < 2:
            omissions.append(
                dict(
                    run=name, eligible=len(rs), reason="insufficient sequential blocks"
                )
            )
            continue
        if strict_new and blocks != set(range(1, 8)):
            omissions.append(
                dict(
                    run=name,
                    eligible=len(rs),
                    reason="new cohort requires all seven planned blocks",
                )
            )
            continue
        train = [r for r in rs if r["block"] <= 4]
        test = [r for r in rs if r["block"] > 4]
        for exp, score in [
            ("within_1p5", short),
            ("within_4s", long),
            ("external_gap_p", short),
        ]:
            tr = [r for r in train if r["pair"] in score]
            te = [r for r in test if r["pair"] in score]
            if (
                strict_new
                and exp == primary_experiment
                and (
                    {r["block"] for r in tr + te} != set(range(1, 8))
                    or not tr
                    or not te
                )
            ):
                omissions.append(
                    dict(
                        run=name,
                        eligible=len(te),
                        reason="4s primary missing a planned block",
                    )
                )
                continue
            try:
                append(tr, te, score, exp, name + "_" + exp)
            except (ValueError, IndexError) as ex:
                if exp == "within_4s" and (
                    len(tr) != len(train) or len(te) != len(test)
                ):
                    omissions.append(
                        dict(
                            run=name,
                            eligible=len(te),
                            reason="4s unavailable: " + str(ex),
                        )
                    )
                    continue
                raise
        complete_by_person[rs[0]["subject"]].append(name)
    for names in complete_by_person.values():
        for a, b in zip(names, names[1:]):
            if a.split("-")[1] == b.split("-")[1]:
                continue
            old, new = records_by_run[a], records_by_run[b]
            if old[0]["montage"] == new[0]["montage"]:
                append(old, new, short, "across_days", a + "__" + b)
                if all(r["pair"] in long for r in old + new):
                    append(old, new, long, "across_days_4s", a + "__" + b + "_4s")
    if historic_challenge:
        from research.serial_bci.mi_transfer_followup.challenge import challenge

        new, meta = challenge(records_by_run, short, evaluate, raw_root=raw)
        rows.extend(new)
        folds.extend(meta)
        if meta:
            old = records_by_run[meta[0]["train_runs"][0]]
            held = records_by_run[meta[0]["test_runs"][0]]
            if all(r["pair"] in long for r in old + held):
                append(
                    old, held, long, "cross_person_archived_4s", meta[0]["fold"] + "_4s"
                )
    if strict_new:
        enrolled = sorted(
            roster if roster is not None else {a["subject"] for a in acquisition}
        )
        flow = []
        for person in enrolled:
            recorded = [a for a in acquisition if a["subject"] == person]
            primary = [
                r
                for r in rows
                if r["subject"] == person and r["experiment"] == primary_experiment
            ]
            flow.append(
                dict(
                    subject=person,
                    formal_runs_with_valid_pairs=len(recorded),
                    valid_pairs_all_runs=sum(a["valid_pairs"] for a in recorded),
                    primary_held_pairs=len(primary),
                    planned_test_per_scheduled_session=36,
                    primary_evaluable=int(len({r["hand"] for r in primary}) == 2),
                )
            )
        write_rows(out / "cohort_flow.csv", flow)
    write_rows(out / "epoch_audit.csv", audits)
    write_rows(out / "predictions.csv", rows)
    (out / "acquisition_audit.json").write_text(
        json.dumps(
            dict(runs=acquisition, issues=issues, omissions=omissions),
            ensure_ascii=False,
            indent=2,
        )
    )
    if not rows:
        raise ValueError(
            "No evaluable forward split; check raw path, dates, classes and formal block counts"
        )
    experiments = {
        e: aggregate(
            [r for r in rows if r["experiment"] == e],
            [f for f in folds if f["experiment"] == e],
        )
        for e in sorted({r["experiment"] for r in rows})
    }
    for f in folds:
        del f["shift_right"], f["conditional_right"]
    from importlib.metadata import version
    from research.serial_bci.mi_transfer_followup import experts, collection_audit
    from research.serial_bci.serial import run as serial_baseline
    from research.serial_bci.ssvep import validate, literature_comparator
    from src.ml import mi_baseline, hybrid_pairs
    from src.bci import preprocessing, fbcca_decoder
    from src.services import hybrid_experiment_protocol
    from research.serial_bci.mi import run_mi
    import research.serial_bci.serial.compare_calibrators as compare_calibrators

    artifacts = [
        Path(__file__),
        HERE / "design.md",
        HERE / "reporting.py",
        HERE / "challenge.py",
        HERE.parent / "ssvep/results/benchmark_models.json",
        HERE.parent / "ssvep/comparator_results/benchmark_models.json",
    ]
    artifacts.extend(
        Path(m.__file__)
        for m in (
            experts,
            collection_audit,
            serial_baseline,
            validate,
            literature_comparator,
            mi_baseline,
            hybrid_pairs,
            hybrid_experiment_protocol,
            preprocessing,
            fbcca_decoder,
            run_mi,
            compare_calibrators,
        )
    )
    provenance = dict(
        python=sys.version.split()[0],
        packages={k: version(k) for k in ("numpy", "scipy", "scikit-learn")},
        sha256={
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        },
    )
    summary = dict(
        primary_experiment=primary_experiment,
        provenance=provenance,
        experiments=experiments,
        folds=folds,
        omissions=omissions,
        long_window_exclusions=long_issues,
        acquisition_issues=issues,
        audit_counts=dict(Counter(r["status"] for r in audits)),
        strict_new=strict_new,
        development_challenge=historic_challenge,
    )
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    from research.serial_bci.mi_transfer_followup.reporting import write_report

    write_report(summary, rows, out)
    print(out / "结果.md")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw", type=Path, default=ROOT / "data/research_experiments/raw_trials"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output directory (new-cohort runs default to a separate directory)",
    )
    parser.add_argument(
        "--roster",
        type=Path,
        help="Prospectively registered new anonymous subject IDs, one per line; enables running against the original acquisition tree",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--historical-challenge",
        action="store_true",
        help="Also evaluate the fixed 16-trial third development ID",
    )
    mode.add_argument(
        "--new-cohort",
        action="store_true",
        help="Require seven blocks and keep historical development IDs out",
    )
    mode.add_argument(
        "--calibration-only",
        action="store_true",
        help="Engineering rehearsal: only first four blocks, 4 s visual window",
    )
    args = parser.parse_args()
    if args.calibration_only:
        if args.roster:
            parser.error("--roster is for the prospective cohort")
        from research.serial_bci.mi_transfer_followup.technical_check import rehearsal

        rehearsal(args.raw, args.out or HERE / "engineering_check", long_score)
        raise SystemExit(0)
    output = args.out or HERE / ("new_cohort_results" if args.new_cohort else "results")
    if args.new_cohort and output.resolve() == (HERE / "results").resolve():
        parser.error("Select a new-cohort output separate from development results")
    roster = None
    if args.roster:
        if not args.new_cohort:
            parser.error("--roster requires --new-cohort")
        listed = [s.strip() for s in args.roster.read_text().splitlines() if s.strip()]
        if (
            not listed
            or len(set(listed)) != len(listed)
            or set(listed) & {"subject-0001", "subject-0003", "subject-0004"}
        ):
            parser.error(
                "Supply a nonempty prospective list of unique new IDs; historical development IDs are ineligible"
            )
        roster = set(listed)
    run(args.raw, output, args.historical_challenge, args.new_cohort, roster)
