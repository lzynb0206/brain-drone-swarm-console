"""Independent checks of frozen serial forecasts, source timing, and scored outcomes."""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from research.serial_bci.serial import run as serial


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(condition, detail):
    if not condition:
        raise ValueError(detail)


def eq(a, b, detail):
    if a == "" or b == "":
        check(a == b, detail)
    else:
        check(np.isclose(float(a), float(b), atol=2e-11, rtol=1e-10), detail)


def table(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def sequence(folder):
    order = []
    seeds = set()
    for path in sorted(folder.glob("*-ssvep.npz")):
        c, _, _, _ = source(path)
        if c.get("acquisition_purpose") == "formal" and c.get("record_kind") == "serial_pair":
            order.append((c["block_index"], c["frequency_hz"], c["mi_true_label"]))
            seeds.add(str(c.get("config",{}).get("random_seed","missing")))
    return order, sorted(seeds)


def source(path):
    with np.load(path, allow_pickle=False) as z:
        context = json.loads(z["trial_context"].item())
        clock = z["sample_timestamps"]
        return context, clock, str(z["true_label"].item()), str(z["subject_id"].item())


def independent_background(path, offsets):
    context, clock, _, _ = source(path)
    with np.load(path, allow_pickle=False) as z:
        posterior = serial.MONTAGES[context["montage_id"]].ssvep
        indices = [z["channel_names"].tolist().index(name) for name in posterior]
        eeg = z["eeg"][indices]
    values = []
    for offset in offsets:
        start = np.searchsorted(clock - context["stimulus_lsl_time"], (35+offset)/250)
        segment = eeg[:, start:start+375]
        check(segment.shape[1] == 375, f"short baseline {path.name}")
        values.append(serial.fbcca(segment))
    return np.mean(values, axis=0)


def audit_run(folder, records, backgrounds, saved, split, model):
    train = [r for r in records if r["block"] <= 4]
    held = [r for r in records if r["block"] > 4]
    check({r["block"] for r in train} == {1,2,3,4}, "training block IDs")
    check({r["block"] for r in held} == set(split["test_blocks"]), "held block IDs")
    check({r["pair"] for r in held} == set(saved), "held trial IDs")
    check(len(train) == split["train"] and len(held) == split["test"], "split lengths")
    rest_candidates, idle_candidates, source_hashes, starts, tr_end, te_start = [], [], {}, [], [], []
    for r in records:
        ss_path, mi_path = (folder/(r["pair"]+suffix) for suffix in ("-ssvep.npz", "-mi.npz"))
        sc, st, s_label, person = source(ss_path)
        mc, mt, m_label, mi_person = source(mi_path)
        check(sc["pair_id"] == mc["pair_id"] == r["pair"] and person == mi_person == r["subject"], "identity")
        check(sc["block_index"] == mc["block_index"] == r["block"] and st[-1] < mt[0], "pair timing")
        check(sc["ssvep_true_label"] == s_label and mc["mi_true_label"] == m_label, "top-level raw labels")
        check(int(serial.LABEL_TO_FREQUENCY[s_label]) == r["true_hz"] and serial.HANDS.index(m_label) == r["hand"],
              "raw truth to admitted row")
        starts.append(st[0])
        if r["block"] <= 4:
            tr_end.append(mt[-1])
        else:
            te_start.append(st[0])
            row = saved[r["pair"]]
            check(int(row["true_hz"]) == r["true_hz"] and int(row["hand"]) == r["hand"], "CSV labels")
        for path, key in ((ss_path,"ss_sha256"), (mi_path,"mi_sha256")):
            h = digest(path)
            check(h == r[key], "raw-file hash")
            if r["block"] > 4:
                check(h == row[key], "held manifest hash")
            source_hashes[path.name] = h
    check(max(tr_end) < min(te_start), "later blocks cross calibration end")
    first = min(starts)
    for tag, path in (("rest", folder/(folder.name+"-baseline.npz")),):
        if tag in backgrounds:
            c, t, _, person = source(path)
            check(t[-1] < first and c["eye_state"] == "open" and person == records[0]["subject"]
                  and all(r["baseline"] == path.stem and c["clock_source"] == r["clock"]
                          and c["montage_id"] == r["montage"] for r in records), "rest predecessor")
            np.testing.assert_allclose(backgrounds[tag], independent_background(path, [0,375,750]), rtol=1e-10)
            rest_candidates.append(dict(file=path.name, seconds_before_first_pair=float(first - t[-1])))
            source_hashes[path.name] = digest(path)
    if "idle" in backgrounds:
        for path in sorted(folder.glob("*-ssvep.npz")):
            c,t,_,person = source(path)
            if (c.get("record_kind") == "ssvep_idle" and c.get("idle_targets_flicker") is True
                and c["block_index"] == 0 and t[-1] < first and c["run_id"] == folder.name
                and c["clock_source"] == records[0]["clock"] and c["montage_id"] == records[0]["montage"]
                and person == records[0]["subject"] and len(t) >= 750 and np.all((np.diff(t)>0)&(np.diff(t)<=.1))):
                idle_candidates.append((t[-1], path))
        idle_candidates = sorted(idle_candidates)[:8]
        check(len(idle_candidates)==8, "initial idle count")
        np.testing.assert_allclose(backgrounds["idle"],np.mean(
            [independent_background(p,[0]) for _,p in idle_candidates],axis=0),rtol=1e-10)
        for _, p in idle_candidates:
            source_hashes[p.name] = digest(p)
    original, original_info = serial.evaluate(records, backgrounds, model)
    metadata = {"subject","run","pair","block","true_hz","hand","gap","good","ss_sha256","mi_sha256",
                "mi_correct","ss_correct","joint"}
    checked = sorted(set(original[0]) - metadata)
    for r in original:
        for col in checked:
            eq(r[col], saved[r["pair"]][col], f"frozen output {col}")
    for mod in ("frequency","hand","both"):
        changed = []
        for r in records:
            copy = r.copy()
            if r["block"] > 4:
                if mod in ("frequency","both"):
                    copy["true_hz"] = 8 + (r["true_hz"]-8+1) % 6
                if mod in ("hand","both"):
                    copy["hand"] = 1-r["hand"]
            changed.append(copy)
        alt, alt_info = serial.evaluate(changed, backgrounds, model)
        check(alt_info == original_info, f"test-label mutation changed fitted metadata: {mod}")
        for base, other in zip(original,alt,strict=True):
            check(base["pair"] == other["pair"], "row ID changed")
            for key in checked:
                eq(base[key], other[key], f"test-label mutation {mod}: {key}")
    return dict(train=len(train),test=len(held),train_end_to_first_test_s=float(min(te_start)-max(tr_end)),
                all_stage_input_SHA256=source_hashes,rest=rest_candidates,
                initial_idle_files=[p.name for _,p in idle_candidates], audited_prediction_columns=checked,
                held_label_mutations=["cycle all six frequency labels", "flip every hand label", "both"])


def audit_metrics(rows, report, splits):
    actual, p_product, p_no = [],[],[]
    for r in rows:
        p,q = float(r["p"]),float(r["q_right"])
        correct_s = int(r["pred_hz"]) == int(r["true_hz"])
        correct_m = (q > .5) == int(r["hand"])
        c = int(correct_s and correct_m)
        check((int(r["ss_correct"]),int(r["mi_correct"]),int(r["joint"])) == (correct_s, correct_m, c),
              "per-row outcomes")
        v = max(q,1.-q)
        split = splits[r["run"]]
        eq(p*v, r["product"], "product formula")
        eq(split["prior_s"]*v,r["no_p"],"no-P formula")
        eq(split["prior_m"]*p,r["ss_only"],"SS-only formula")
        for key in serial.METHODS:
            check(int(r[key+"_accept"])==int(float(r[key])>=split["thresholds"][key]),"accept flag")
        actual.append(c)
        p_product.append(p*v)
        p_no.append(float(r["no_p"]))
    pp,pn,y=map(np.array,(p_product,p_no,actual))
    gain=float(np.mean((pn-y)**2)-np.mean((pp-y)**2))
    eq(np.mean((pn-y)**2),report["command"]["no_p"]["brier"],"no-P Brier")
    eq(np.mean((pp-y)**2),report["command"]["product"]["brier"],"product Brier")
    eq(y.sum(),report["joint_correct"],"joint accuracy numerator")
    check(report["n"]==len(rows) and report["mi_correct"]==sum(int(r["mi_correct"]) for r in rows)
          and report["ss_correct"]==sum(int(r["ss_correct"]) for r in rows),"marginal accuracies")
    for key in serial.METHODS:
        matched = []
        for run in {r["run"] for r in rows}:
            rr=[r for r in rows if r["run"] == run]
            ordered=sorted(rr,key=lambda r: (-float(r[key]),r["pair"]))
            matched.extend(ordered[:round(.8*len(ordered))])
            if key == "no_p":
                by_mi=sorted(rr,key=lambda r: (-abs(2*float(r["q_right"])-1),r["pair"]))
                if splits[run]["prior_s"] > 0:
                    check([r["pair"] for r in ordered]==[r["pair"] for r in by_mi],"no-P rank vs MI rank")
        target=report["command"][key]["matched80"]
        check(len(matched)==target["accepted"] and sum(1-int(r["joint"]) for r in matched)==target["errors"],
              f"matched coverage errors for {key}")
    return gain


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw",type=Path,default=ROOT/"data/research_experiments/raw_trials")
    parser.add_argument("--results",type=Path,default=HERE/"results")
    parser.add_argument("--model",type=Path,default=HERE.parent/"ssvep/results/benchmark_models.json")
    parser.add_argument("--out",type=Path,default=HERE/"results/independent_audit.json")
    args=parser.parse_args()
    rows=table(args.results/"trials.csv")
    summary=json.loads((args.results/"summary.json").read_text())
    model=json.loads(args.model.read_text())["1.5:train_nontarget"]
    check(digest(args.model)==summary["public_model_sha256"],"public FBCCA model hash")
    check(len({(r["run"],r["pair"]) for r in rows})==len(rows),"duplicate held ID")
    splits = {s["run"]:s for s in summary["splits"] if "test" in s}
    audited, schedules, schedule_overlap = {}, {}, []
    for run, split in splits.items():
        matches=([args.raw] if args.raw.name==run else [])+[p for p in args.raw.rglob(run) if p.is_dir()]
        check(len(matches)==1,"ambiguous raw run")
        records,bg,_=serial.load(matches[0])
        saved={r["pair"]:r for r in rows if r["run"]==run}
        audited[run]=audit_run(matches[0],records,bg,saved,split,model)
        seq, seeds = sequence(matches[0])
        audited[run]["raw_config_random_seeds"] = seeds
        for earlier, previous in schedules.items():
            schedule_overlap.append(dict(runs=[earlier,run],overlap=min(len(seq),len(previous)),
                identical_block_frequency_hand_in_raw_order=sum(a==b for a,b in zip(seq,previous))))
        schedules[run] = seq
    pooled=audit_metrics(rows,summary["pooled"],splits)
    gains={s:audit_metrics([r for r in rows if r["subject"]==s],summary["by_person"][s],splits)
           for s in sorted({r["subject"] for r in rows})}
    macro=float(np.mean(list(gains.values())))
    eq(macro,summary["primary_brier_gain"]["mean"],"subject mean gain")
    result=dict(passed=True, test_rows=len(rows), Brier_gain_no_P_minus_product_by_person=gains,
                participant_mean_gain=macro, pooled_gain=pooled,source_runs=audited,
                raw_serial_target_order_comparison=schedule_overlap,
                matched_coverage_ranks_verified=True, raw_pipeline_scope="Label mutations after raw eligibility and "
                "alignment, before fitting or scoring. Hashes and source target fields independently checked from NPZ.",
                file_sha256={str(p.relative_to(HERE.parent)):digest(p) for p in
                    (HERE/"run.py",args.model,args.results/"trials.csv",args.results/"summary.json")
                    if p.is_relative_to(HERE.parent)})
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(f"Verified {len(rows)} later trials in {len(splits)} runs, mean Brier gain {macro:.7f}.")


if __name__=="__main__":
    main()
