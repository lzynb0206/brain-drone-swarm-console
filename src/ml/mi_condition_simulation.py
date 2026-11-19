"""Explicitly artificial associations between public MI and historical SSVEP P.

These associations are label-blind negative controls, never measured serial pairs.
Keep this API separate from the measured-pair loader and deployment pipeline.
"""
import numpy as np

SOURCE = "simulated_unpaired_public_MI_historical_SSVEP"


def confidence_pools(rows):
    """Older SSVEP runs supply training P; last run supplies test P."""
    runs = sorted({r["run"] for r in rows})
    if len(runs) < 2:
        raise ValueError("Separate historical SSVEP sessions are required")
    eligible = [r for r in rows if r["p_eligible"]]
    if any(not np.isfinite(r["p"]) or not 0 <= r["p"] <= 1 for r in eligible):
        raise ValueError("Invalid source confidence")
    if len({r["file"] for r in rows}) != len(rows):
        raise ValueError("Duplicate source trials")
    pools = {"T": [r for r in eligible if r["run"] != runs[-1]],
             "E": [r for r in eligible if r["run"] == runs[-1]]}
    if not all(pools.values()):
        raise ValueError("Both source pools need eligible P")
    return pools


def assign_confidence(pool, count, seed):
    """Sampling with replacement; deliberately accepts no MI signal or label."""
    if not pool or count < 1:
        raise ValueError("Nonempty confidence pool and MI trial count required")
    indices = np.random.default_rng(seed).integers(0, len(pool), count)
    return (np.array([pool[i]["p"] for i in indices], np.float32),
            [pool[i]["file"] for i in indices])


def simulation_condition(mode, p, groups, *, constant, seed):
    p = np.asarray(p, np.float32).copy()
    groups = np.asarray(groups)
    if groups.shape != p.shape or p.ndim != 1 or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid simulated condition array")
    if mode == "none":
        return np.zeros_like(p), np.zeros_like(p)
    if mode == "constant":
        if not np.isfinite(constant) or not 0 <= constant <= 1:
            raise ValueError("Training-only mean confidence required")
        p[:] = constant
    elif mode == "shuffled":
        rng = np.random.default_rng(seed)
        for group in np.unique(groups):
            idx = np.flatnonzero(groups == group)
            p[idx] = rng.permutation(p[idx])
    elif mode != "assigned":
        raise ValueError("Use assigned, none, constant or shuffled; no real-pair claim")
    return p, np.ones_like(p)
