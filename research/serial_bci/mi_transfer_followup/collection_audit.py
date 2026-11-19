"""Read-only comparison of recorded pairs with the console's seeded collection plan."""

from collections import Counter
from functools import lru_cache
import json

import numpy as np

from src.bci.config import LABEL_TO_FREQUENCY
from src.services.hybrid_experiment_protocol import (
    HybridExperimentConfig,
    build_hybrid_plan,
)


@lru_cache(maxsize=128)
def by_index(config_text):
    cfg = HybridExperimentConfig(**json.loads(config_text))
    return {t.index: t for t in build_hybrid_plan(cfg)}


def audit_pairs(folder, records):
    seeds = set()
    sources = set()
    preflights = set()
    settings = set()
    missing_seed = []
    mismatch = []
    baseline_ids = set()
    for r in records:
        flags = {}
        for stage in ("ssvep", "mi"):
            with np.load(
                folder / (r["pair"] + "-" + stage + ".npz"), allow_pickle=False
            ) as z:
                ctx = json.loads(str(z["trial_context"].item()))
                quality = json.loads(str(z["signal_quality"].item()))
                meta = json.loads(str(z["metadata"].item()))
                if (
                    ctx.get("acquisition_subject_id") != r["subject"]
                    or meta.get("trial_id") != r["pair"] + "-" + stage
                ):
                    raise ValueError(
                        f"Archival participant or trial identity mismatch: {r['pair']} {stage}"
                    )
                sources.add(str(meta.get("source", "")).split(" · ")[0])
                preflights.add(str(ctx.get("preflight_id", "")))
                baseline_ids.add(str(ctx.get("baseline_trial_id", "")))
                redundant = quality.get("redundant_channels", {})
                names = quality.get("selected_channel_names", [])
                channel_pairs = []
                for channel, ref in redundant.items():
                    i, j = int(channel) - 1, int(ref) - 1
                    if min(i, j) >= 0 and max(i, j) < len(names):
                        channel_pairs.append({names[i], names[j]})
                flags[stage + "_redundant"] = bool(redundant)
                if stage == "mi":
                    flags["c3_c4_redundant"] = any(
                        pair == {"C3", "C4"} for pair in channel_pairs
                    )
                config = ctx.get("config", {})
                if config.get("random_seed") is None:
                    missing_seed.append(r["pair"])
                    continue
                seed = config["random_seed"]
                seeds.add(seed)
                settings.add(
                    (
                        config.get("serial_pair_count"),
                        config.get("idle_trials"),
                        config.get("serial_study_seed"),
                    )
                )
                plan = by_index(json.dumps(config, sort_keys=True))
                t = plan.get(ctx.get("trial_index"))
                if (
                    ctx.get("random_seed") != seed
                    or t is None
                    or t.block != r["block"]
                    or t.mi_label != ctx["mi_true_label"]
                    or LABEL_TO_FREQUENCY.get(t.ssvep_label) != r["true_hz"]
                ):
                    mismatch.append(r["pair"])
        r.update(flags)
    return dict(
        subject=records[0]["subject"],
        run=folder.name,
        valid_pairs=len(records),
        seeds=sorted(seeds),
        planned_settings=[
            dict(planned=n, idle=m, preset_seed=k)
            for n, m, k in sorted(settings, key=str)
        ],
        signal_sources=sorted(sources),
        seed_missing_pairs=sorted(set(missing_seed)),
        plan_mismatch_pairs=sorted(set(mismatch)),
        preflight_ids=sorted(preflights),
        baseline_ids=sorted(baseline_ids),
        blocks=dict(Counter(int(r["block"]) for r in records)),
        accepted_cells={
            str(b): dict(
                Counter(
                    f"{r['true_hz']}Hz-{'R' if r['hand'] else 'L'}"
                    for r in records
                    if r["block"] == b
                )
            )
            for b in sorted({r["block"] for r in records})
        },
    )
