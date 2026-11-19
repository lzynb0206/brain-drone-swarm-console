"""Secondary analysis of how much trial-specific SSVEP evidence to propagate."""
import numpy as np


def weight_from_losses(base, change, event):
    """Least-squares mixing weight with disjoint calibration predictions."""
    denom = np.mean(change**2)
    if denom < 1e-12:
        return 0.
    return float(np.clip(np.mean(change * (event-base))/denom, 0., 1.))


def decomposition(event, base, score):
    change = score-base
    signal = float(2*np.mean(change*(event-base)))
    cost = float(np.mean(change**2))
    gain = float(np.mean((event-base)**2 - (event-score)**2))
    if not np.isclose(signal-cost,gain):
        raise ArithmeticError('Brier difference identity failed')
    return dict(residual_alignment=signal, squared_change=cost, brier_gain=gain)
