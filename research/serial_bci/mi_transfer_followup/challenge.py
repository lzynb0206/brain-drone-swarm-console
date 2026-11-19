"""Secondary zero-calibration transfer across two archived participant IDs.

Train once on the Nov 3 session and evaluate the sixteen completed formal
pairs of the Nov 7 session. Both runs stopped before completion. Their raw
acquisition metadata describe identical hardware, montage and LSL clock.
Independent physical timing and participant compliance were not recorded.
"""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np

from src.bci.montage import HYBRID


TRAIN = "hybrid-20261103-122156-da2fba"
TEST = "hybrid-20261107-175736-34bffc"
RAW_ROOT = Path(__file__).resolve().parents[3] / "data" / "research_experiments" / "raw_trials"
EXPERIMENT = "cross_person_archived_challenge"


def _read_ledger(raw_root, run, timing=False):
    suffix = "-timing-audit.json" if timing else ".json"
    return json.loads((raw_root.parent / "hybrid_runs" / (run + suffix)).read_text())


def _audit(records, raw_root, run, subject):
    directories = list(raw_root.glob(f"*/*/*/{run}"))
    if len(directories) != 1:
        raise ValueError(f"Need one original recording directory for {run}")
    ledger = _read_ledger(raw_root, run)
    timing = _read_ledger(raw_root, run, timing=True)
    if (ledger["acquisition_subject_id"] != subject or ledger["run_id"] != run
            or timing["run_id"] != run or ledger["collection_scheme"] != "formal"
            or ledger["config"]["mode"] != "serial"):
        raise ValueError(f"Unexpected participant, run or task in the ledger for {run}")
    pairs = {r["pair"] for r in records}
    if len(records) != len(pairs) or not pairs:
        raise ValueError(f"Empty or duplicate trial IDs for {run}")

    stage_quality = {"ssvep": Counter(), "mi": Counter()}
    redundant = Counter()
    stage_hashes = set()
    contract = set()
    trial_timing = {t["trial_id"]: t for t in timing["trials"]}
    accepted_timing_errors = Counter()
    for r in records:
        if (r["subject"] != subject or r["run"] != run or r["montage"] != HYBRID.id
                or r["clock"] != "pylsl.local_clock" or not r["pair"].startswith(run+"-pair-")):
            raise ValueError(f"Trial and manifest disagree for {r['pair']}")
        ends = {}
        for stage, key in (("ssvep", "ss_sha256"), ("mi", "mi_sha256")):
            trial = r["pair"] + "-" + stage
            path = directories[0] / (trial + ".npz")
            if hashlib.sha256(path.read_bytes()).hexdigest() != r[key]:
                raise ValueError(f"File hash changed for {trial}")
            with np.load(path, allow_pickle=False) as f:
                ctx = json.loads(str(f["trial_context"].item()))
                meta = json.loads(str(f["metadata"].item()))
                quality = json.loads(str(f["signal_quality"].item()))
                names = tuple(f["channel_names"].tolist())
                source = meta["source"]
                if (meta["trial_id"] != trial or ctx["stage"] != stage or ctx["pair_id"] != r["pair"]
                        or ctx["run_id"] != run or ctx["record_kind"] != "serial_pair"
                        or str(f["subject_id"].item()) != ctx["acquisition_subject_id"] or ctx["acquisition_subject_id"] != subject
                        or ctx["acquisition_date"] != ledger["acquisition_date"]
                        or ctx["acquisition_purpose"] != "formal" or str(f["collection_scheme"].item()) != "formal"
                        or source != "ExpandableEEG Native · /dev/cu.ExpandableEEGBT"
                        or ctx["montage_id"] != HYBRID.id or names != HYBRID.names
                        or tuple(f["original_channel_names"].tolist()) != names or tuple(ctx["channel_names"]) != names
                        or tuple(f["channel_units"].tolist()) != ("uV",)*len(names)
                        or float(f["fs"]) != 250 or str(f["clock_source"].item()) != r["clock"]
                        or ctx["clock_source"] != r["clock"] or ctx["config"]["random_seed"] != ledger["config"]["random_seed"]
                        or (stage == "mi" and ctx["preceding_ssvep_trial_id"] != r["pair"]+"-ssvep")):
                    raise ValueError(f"Raw acquisition contract mismatch for {trial}")
                times = f["sample_timestamps"]
                if not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
                    raise ValueError(f"Invalid marker times in {trial}")
                ends[stage] = (times[0], times[-1])
                contract.add((source, names, r["clock"], r["montage"]))
                signal_hash = hashlib.sha256(f["eeg"].tobytes()).hexdigest()
                if signal_hash in stage_hashes:
                    raise ValueError(f"Identical full EEG epoch occurs twice in {run}")
                stage_hashes.add(signal_hash)
                stage_quality[stage][quality["status"]] += 1
                redundant[stage] += bool(quality.get("redundant_channels"))
                accepted_timing_errors[stage] += bool(trial_timing[trial]["errors"])
        if ends["ssvep"][1] >= ends["mi"][0]:
            raise ValueError(f"Archived timestamps disagree with stage order for {r['pair']}")
    if len(contract) != 1:
        raise ValueError(f"Mixed hardware or recording settings in {run}")
    details = dict(subject=subject, run=run, date=ledger["acquisition_date"], status=ledger["status"],
                   planned_pairs=ledger["planned_pairs"], completed_pairs=ledger["completed_pairs"],
                   admissible_pairs=len(records), blocks=dict(Counter(str(r["block"]) for r in records)),
                   stage_quality=stage_quality, redundant_channel_flags=dict(redundant),
                   included_stages_with_timing_audit_errors=dict(accepted_timing_errors),
                   quality_pass=timing["signal_quality_checks_passed"],
                   software_timing_pass=timing["software_timing_checks_passed"],
                   physical_display_timing_verified=timing["physical_display_timing_verified"],
                   schedule_seed=ledger["config"]["random_seed"])
    return contract.pop(), stage_hashes, details


def challenge(records_by_run, score_by_pair, evaluate, *, raw_root=None):
    """Run only the named historical train/test split; a new recording is never added.

    records_by_run maps a run ID to stage-validated trial dictionaries from
    serial.run.load. score_by_pair maps a unique pair ID to its six FBCCA
    scores at 1.5 s. The callback fits on training records only.
    """
    if TRAIN not in records_by_run or TEST not in records_by_run:
        return [], []
    root = Path(raw_root) if raw_root is not None else RAW_ROOT
    train, test = records_by_run[TRAIN], records_by_run[TEST]
    tr_contract, tr_signals, tr = _audit(train, root, TRAIN, "subject-0001")
    te_contract, te_signals, te = _audit(test, root, TEST, "subject-0003")
    if (tr_contract != te_contract or tr_signals & te_signals
            or date.fromisoformat(tr["date"]) >= date.fromisoformat(te["date"])
            or te["completed_pairs"] != len(test)):
        raise ValueError("Cross-person contract, unique raw epochs, chronology or test completeness failed")
    matching_scores = {r["pair"]: score_by_pair[r["pair"]] for r in [*train, *test]}
    if any(np.asarray(s).shape != (6,) or not np.isfinite(s).all() for s in matching_scores.values()):
        raise ValueError("Missing finite 1.5-s FBCCA scores for an included trial")
    rows, fold = evaluate(train, test, matching_scores, EXPERIMENT,
                          "nov03_subject0001_to_nov07_subject0003", seed=20261107)
    if len(rows) != len(test) or {row["pair"] for row in rows} != {r["pair"] for r in test}:
        raise ValueError("The challenge must predict every test trial exactly once")
    # The shared seed can confound a trial-level alignment analysis through task order.
    def schedule(records):
        return {r["pair"].rsplit("-", 1)[-1]: (r["true_hz"], r["hand"]) for r in records}
    train_order, test_order = schedule(train), schedule(test)
    common = train_order.keys() & test_order.keys()
    fold.update(train_participants=[tr["subject"]], test_participants=[te["subject"]],
                raw_train_audit=tr, raw_test_audit=te, same_seed=tr["schedule_seed"] == te["schedule_seed"],
                shared_trial_positions=len(common),
                positions_with_identical_cues=sum(train_order[k] == test_order[k] for k in common),
                matched_contract=dict(source=tr_contract[0], channels=list(tr_contract[1]),
                                      clock=tr_contract[2], montage=tr_contract[3], fs=250, units="uV"),
                evidence_scope="Secondary held-participant archival challenge, one test person, incomplete run.")
    return rows, [fold]
