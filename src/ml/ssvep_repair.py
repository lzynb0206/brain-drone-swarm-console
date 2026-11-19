"""Research-only, explicitly named SSVEP preprocessing and TRCA corrections.

No production decoder is changed. Channels keep their physical positions when
masked. Transformations never receive labels. The crop/phase augmentation is a
project adaptation, NOT a feature claimed to come from the original papers.
"""
from __future__ import annotations

import numpy as np
from scipy import linalg, signal

from src.bci.preprocessing import assess_signal_quality

FS = 250.0


def quality_preprocess(eeg, *, fs=FS, check_quality=True, band=(6, 80), normalize=True):
    """Mask invalid channels before normalization, retain electrode ordering."""
    x = np.asarray(eeg, dtype=np.float64).copy()
    if x.ndim != 2 or x.shape[-1] < 100:
        raise ValueError("Expected C x T EEG with at least 100 samples")
    if check_quality:
        quality = assess_signal_quality(x, fs)
        mask = np.asarray(quality["usable_mask"], bool)
    else:
        mask = np.isfinite(x).all(axis=-1) & (np.nanstd(x, axis=-1) > 1e-12)
        quality = {"usable_mask": mask.tolist(), "usable_channel_count": int(mask.sum())}
    # A zero channel is a missing-input indicator, not a fabricated EEG channel.
    x[~mask] = 0
    x = signal.detrend(x, axis=-1)
    b, a = signal.iirnotch(50, 30, fs=fs)
    x = signal.filtfilt(b, a, x, axis=-1)
    if band is not None:
        sos = signal.butter(4, band, fs=fs, btype="bandpass", output="sos")
        x = signal.sosfiltfilt(sos, x, axis=-1)
    x -= x.mean(axis=-1, keepdims=True)
    if normalize:
        scale = np.maximum(np.std(x, axis=-1, keepdims=True), 1e-8)
        x /= scale
    x[~mask] = 0
    assert np.isfinite(x).all()
    return x.astype(np.float32), quality


def eegnet_streams(clean, fs=FS):
    """Preserve relative band power instead of unit-normalizing every band."""
    def bp(low):
        sos = signal.butter(4, (low, 45), fs=fs, btype="bandpass", output="sos")
        return signal.sosfiltfilt(sos, clean, axis=-1).astype(np.float32)
    return bp(5), np.stack([bp(low) for low in (6, 10, 14, 18)], axis=-3)


def complex_spectrum(x, fs=FS):
    """Paper's 0.2-Hz FFT grid for ANY <=5-s epoch, using zero padding.

    Zero padding interpolates the spectrum; it does not add observations or
    improve the physical frequency resolution of a short epoch.
    """
    x = np.asarray(x)
    nfft = round(fs / .2)
    if x.shape[-1] > nfft:
        raise ValueError("A single spectral epoch cannot exceed five seconds")
    spectrum = np.fft.rfft(x, n=nfft, axis=-1) / x.shape[-1]
    f = np.fft.rfftfreq(nfft, 1/fs)
    z = spectrum[..., (f >= 7) & (f < 64)]
    out = np.concatenate((z.real, z.imag), axis=-1)
    assert out.shape[-1] == 570
    return out.astype(np.float32)


def crop_batch(streams, indices, starts, *, length, shifts=None):
    """Identical trial-local crop and common-channel shift for all streams."""
    indices, starts = np.asarray(indices), np.asarray(starts)
    if len(indices) != len(starts):
        raise ValueError("One crop start is required per trial")
    if np.any(starts < 0) or any(np.any(starts + length > x.shape[-1]) for x in streams):
        raise ValueError("Crop extends beyond its source trial")
    result = []
    for x in streams:
        z = np.stack([x[i, ..., s:s+length] for i, s in zip(indices, starts)])
        if shifts is not None:
            z = np.stack([np.roll(row, int(shift), axis=-1) for row, shift in zip(z, shifts)])
        result.append(np.ascontiguousarray(z, dtype=np.float32))
    return tuple(result)


def reference_filterbank(x, fs=FS, bands=3):
    """Chebyshev-I edges from mnakanishi/TRCA-SSVEP/src/filterbank.m.

    Each bank filters the same input. No post-band standardization is applied.
    SOS realization improves numerical stability relative to polynomial form.
    """
    lows = (6, 14, 22, 30, 38)
    stops = (4, 10, 16, 24, 32)
    out = []
    for low, stop in zip(lows[:bands], stops[:bands]):
        order, wn = signal.cheb1ord((low, 90), (stop, 100), 3, 40, fs=fs)
        sos = signal.cheby1(order, .5, wn, btype="bandpass", fs=fs, output="sos")
        out.append(signal.sosfiltfilt(sos, x, axis=-1))
    return np.stack(out, axis=1)


def fit_etrca(banks, labels, *, ridge=1e-6):
    """TRCA between-trial covariance; ensemble of class-specific filters."""
    banks = np.asarray(banks, np.float64)
    labels = np.asarray(labels)
    classes = np.unique(labels)
    if not np.array_equal(classes, np.arange(len(classes))):
        raise ValueError("Labels must be contiguous, starting at zero")
    templates, filters = [], []
    for band in range(banks.shape[1]):
        tm, ws = [], []
        for label in classes:
            x = banks[labels == label, band].copy()
            if len(x) < 2:
                raise ValueError("TRCA needs at least two trials of every class")
            x -= x.mean(axis=-1, keepdims=True)
            q = np.einsum('nct,ndt->cd', x, x)
            s = x.sum(0) @ x.sum(0).T - q
            q += max(np.trace(q)/q.shape[0], 1e-12)*ridge*np.eye(q.shape[0])
            _, w = linalg.eigh(s, q)
            w = w[:, -1]
            w /= np.linalg.norm(w)
            tm.append(x.mean(0)); ws.append(w)
        templates.append(tm); filters.append(np.stack(ws, axis=1))
    return np.asarray(templates), np.asarray(filters)


def score_etrca(banks, model):
    """Author test_model.m's linear weighted correlations, NOT signed r^2."""
    templates, filters = model
    out = np.zeros((len(banks), templates.shape[1]))
    for band in range(banks.shape[1]):
        w = filters[band].T
        x = np.einsum('dc,nct->ndt', w, banks[:, band]).reshape(len(banks), -1)
        t = np.einsum('dc,kct->kdt', w, templates[band]).reshape(len(templates[band]), -1)
        x -= x.mean(axis=-1, keepdims=True); t -= t.mean(axis=-1, keepdims=True)
        r = x @ t.T / np.maximum(np.linalg.norm(x, axis=1)[:, None]*np.linalg.norm(t, axis=1)[None], 1e-15)
        out += ((band+1)**-1.25+.25)*r
    return out
