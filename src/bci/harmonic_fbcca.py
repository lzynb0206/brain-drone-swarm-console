"""FBCCA score ablation: explicitly average individual harmonic evidence.

Unlike scaling reference sin/cos amplitudes (cancelled by CCA whitening), this
changes how the correlations contribute to the final bank scores.
"""
from __future__ import annotations

from functools import lru_cache
import numpy as np

from src.bci.config import TARGET_FREQUENCIES_HZ
from src.bci.fbcca_decoder import _orthonormal_basis
from src.bci.preprocessing import filter_bank, FILTER_BANK_LOW_EDGES_HZ


@lru_cache(maxsize=8)
def harmonic_references(fs, n):
    t = np.arange(n) / float(fs)
    references = []
    for frequency in TARGET_FREQUENCIES_HZ:
        row = []
        for h in range(1, 5):
            phase = 2*np.pi*frequency*h*t
            row.append(_orthonormal_basis(np.column_stack((np.sin(phase), np.cos(phase)))))
        references.append(row)
    return references


def harmonic_mask():
    frequency = np.asarray(TARGET_FREQUENCIES_HZ)[:, None] * np.arange(1, 5)
    return (frequency[None, :, :] >= np.asarray(FILTER_BANK_LOW_EDGES_HZ)[:, None, None]) & (frequency[None, :, :] <= 50.)


def individual_harmonic_components(prepared, fs):
    references = harmonic_references(float(fs), prepared.shape[1])
    mask = harmonic_mask()
    result = np.zeros((3, 6, 4))
    for b, (filtered, _) in enumerate(filter_bank(prepared, fs)):
        qx = _orthonormal_basis(filtered.T)
        if not qx.shape[1]:
            continue
        for k in range(6):
            for h in range(4):
                if not mask[b, k, h]:
                    continue
                cross = np.einsum("ni,nj->ij", qx, references[k][h], optimize=False)
                result[b, k, h] = np.clip(np.linalg.svd(cross, compute_uv=False)[0], 0., 1.)**2
    return result


def harmonic_average(components, exponent):
    if not np.isfinite(exponent) or exponent < 0:
        raise ValueError("Harmonic decay exponent must be finite and nonnegative")
    weights = harmonic_mask() * np.arange(1., 5.)**(-exponent)
    weights /= weights.sum(axis=-1, keepdims=True)
    return np.sum(np.asarray(components)*weights, axis=-1)


def combine_harmonic_evidence(joint, separate, *, mix, exponent, bank_weights):
    if not np.isfinite(mix) or not 0 <= mix <= 1:
        raise ValueError("Harmonic mixture must be in [0,1]")
    weights = np.asarray(bank_weights, dtype=float)
    if weights.shape != (3,) or not np.isfinite(weights).all() or np.any(weights < 0) or weights.sum() <= 0:
        raise ValueError("Three nonnegative bank weights with positive sum required")
    values = (1-mix)*np.asarray(joint) + mix*harmonic_average(separate, exponent)
    return np.einsum("...bk,b->...k", values, weights)
