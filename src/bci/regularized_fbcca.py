"""EEG-side ridge whitening inside FBCCA; no classifier or template is fitted.

For X=U S V', shrink each retained whitened direction by
s/sqrt(s^2 + ridge*mean(s^2)). Ridge=0 is the existing truncated CCA exactly.
"""
from __future__ import annotations

import numpy as np

from src.bci.fbcca_components import _reference_bases
from src.bci.preprocessing import filter_bank


def regularized_components(prepared, fs, ridge_values=(0., .01, .05, .1, .25, .5, 1.)):
    data = np.asarray(prepared, dtype=float)
    ridges = np.asarray(ridge_values, dtype=float)
    if data.ndim != 2 or min(data.shape) < 1 or not np.isfinite(data).all():
        raise ValueError("Expected finite channels by samples")
    if ridges.ndim != 1 or not ridges.size or not np.isfinite(ridges).all() or np.any(ridges < 0):
        raise ValueError("Ridge values must be finite and nonnegative")
    references = _reference_bases(float(fs), data.shape[1])[3]
    result = np.zeros((len(ridges), 3, len(references)))
    retained_ranks, condition_ratios = [], []
    for bank, (filtered, _) in enumerate(filter_bank(data, fs)):
        centered = filtered.T - filtered.T.mean(axis=0, keepdims=True)
        u, s, _ = np.linalg.svd(centered, full_matrices=False)
        tolerance = max(max(centered.shape)*np.finfo(float).eps, .01)*s[0]
        keep = s > tolerance
        u, s = u[:, keep], s[keep]
        retained_ranks.append(int(keep.sum()))
        condition_ratios.append(float(s[0]/s[-1]) if len(s) else 0.)
        if not len(s):
            continue
        squared = s*s
        attenuation = np.sqrt(squared[None, :] / (squared[None, :] + ridges[:, None]*squared.mean()))
        for k, reference in enumerate(references):
            cross = np.einsum("ni,nj->ij", u, reference, optimize=False)
            roots = np.linalg.svd(cross[None, :, :]*attenuation[:, :, None], compute_uv=False)
            result[:, bank, k] = np.clip(roots[:, 0], 0., 1.)**2
    return result, {"retained_ranks": retained_ranks, "condition_ratios": condition_ratios}
