"""Inspect FBCCA's existing harmonic references and three filter banks.

These are CCA correlations, not features for an additional learned model.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import _orthonormal_basis
from src.bci.preprocessing import filter_bank, FILTER_BANK_HIGH_EDGE_HZ


@lru_cache(maxsize=16)
def _reference_bases(fs: float, n: int):
    t = np.arange(n, dtype=float) / fs
    bases = []
    for hmax in range(1, 5):
        row = []
        for frequency in TARGET_FREQUENCIES_HZ:
            components = []
            for harmonic in range(1, hmax + 1):
                if harmonic * frequency <= FILTER_BANK_HIGH_EDGE_HZ:
                    phase = 2 * np.pi * harmonic * frequency * t
                    components.extend((np.sin(phase), np.cos(phase)))
            row.append(_orthonormal_basis(np.asarray(components).T))
        bases.append(row)
    return bases


def fbcca_components(prepared: np.ndarray, fs: float) -> np.ndarray:
    """Squared CCA: [maximum harmonic 1..4, bank 6/14/22–50 Hz, class]."""
    references = _reference_bases(float(fs), prepared.shape[1])
    result = np.zeros((4, 3, len(TARGET_FREQUENCIES_HZ)))
    for bank, (filtered, _) in enumerate(filter_bank(prepared, fs)):
        qx = _orthonormal_basis(filtered.T)
        if not qx.shape[1]:
            continue
        for h, row in enumerate(references):
            for k, qy in enumerate(row):
                cross = np.einsum("ni,nj->ij", qx, qy, optimize=False)
                rho = np.clip(np.linalg.svd(cross, compute_uv=False)[0], 0, 1)
                result[h, bank, k] = rho * rho
    return result


def causal_idle_background(values, runs, idle, valid, *, reducer="mean"):
    """Use only preceding explicitly labelled idle windows within each session.

    Command identities are not accepted. Invalid idle windows never update the
    background. Interleaved sessions have independent state.
    """
    values = np.asarray(values, dtype=float)
    if len(values) != len(runs) or len(values) != len(idle) or len(values) != len(valid):
        raise ValueError("Background inputs must have matching trial counts")
    if not np.isfinite(values).all():
        raise ValueError("Background components must be finite")
    if reducer not in ("mean", "median", "ewma"):
        raise ValueError("Background reducer must be mean, median or ewma")
    means = np.zeros_like(values)
    counts = np.zeros(len(values), dtype=int)
    state = {}
    for i, run in enumerate(runs):
        seen = state.setdefault(run, [])
        count = len(seen)
        counts[i] = count
        if count:
            if reducer == "mean":
                means[i] = np.mean(seen, axis=0)
            elif reducer == "median":
                means[i] = np.median(seen, axis=0)
            else:
                estimate = seen[0].copy()
                for past in seen[1:]:
                    estimate = .75 * estimate + .25 * past
                means[i] = estimate
        if idle[i] and valid[i]:
            seen.append(values[i].copy())
    return means, counts


def combine_components(components, config):
    weights = np.asarray(config["bank_weights"], dtype=float)
    harmonic = config["harmonics"]
    if harmonic not in (1, 2, 3, 4):
        raise ValueError("Maximum harmonic must be 1, 2, 3 or 4")
    if weights.shape != (3,) or not np.isfinite(weights).all() or np.any(weights < 0) or weights.sum() <= 0:
        raise ValueError("Three finite nonnegative bank weights with positive sum required")
    harmonics = config.get("harmonics_by_bank", (harmonic,) * 3)
    if len(harmonics) != 3 or any(h not in (1, 2, 3, 4) for h in harmonics):
        raise ValueError("Each bank must use 1..4 harmonics")
    values = np.asarray(components)
    return sum(weights[b] * values[..., h-1, b, :] for b, h in enumerate(harmonics))
