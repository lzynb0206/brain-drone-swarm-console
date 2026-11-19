"""Trial-wise SSVEP confidence as a moderator of mu and beta MI evidence.

Per participant and per session, fit CSP + shrinkage LDA separately to 8–13 Hz
and 13–30 Hz motor imagery. The gate predicts RIGHT HAND, not joint correctness.
It only changes a convex mixture of the two experts. A flip of the final hand
class is possible only when their 0.5 decisions differ.

Fit gates on out-of-block predictions from the calibration period. The caller
must derive p_train_oof from the actual preceding SSVEP of the same trial,
excluding that trial's entire block from its fitted offsets and calibrator.
p_test comes from a frozen SSVEP model fitted to all calibration blocks.

All candidates optimize exactly the same Brier loss for the true hand, plus
a fixed L2 penalty of one on each coefficient, without using test labels.
no_p fits a constant weight. mi_only and with_p add one equally penalized
coefficient for relative MI certainty or preceding SSVEP correctness P.
mi_plus_p adds P conditional on the MI-only moderator. The correction is
strongly regularized when the two experts agree or the moderator has little
variation. The procedure is a research hypothesis, not a claim of a new
family of mixture models or a causal effect of SSVEP on MI.
"""
from itertools import product

import numpy as np
from scipy import signal
from scipy.optimize import minimize
from scipy.special import expit

from src.ml.mi_baseline import CSPClassifier, MI_CHANNELS

BANDS = ((8.0, 13.0), (13.0, 30.0))
RIDGE = 1.0
SD_FLOOR = 0.05


def _binary(y):
    y = np.asarray(y)
    if y.ndim != 1 or not np.all(np.isin(y, (0, 1))):
        raise ValueError("Hand labels must be a one-dimensional array: 0 left, 1 right")
    return y.astype(int)


def _epochs(x, fs):
    x = np.asarray(x, dtype=float)
    if (x.ndim != 3 or x.shape[1] != len(MI_CHANNELS) or not len(x)
            or x.shape[-1] < fs or not np.isfinite(x).all()):
        raise ValueError("Expected finite, nonempty trial x (C3,Cz,C4) x time, >=1 s")
    return signal.detrend(x, axis=-1, type="linear")


def _predict_csp(prepared_train, y, prepared_test, fs):
    if np.min(np.bincount(y, minlength=2)) < 2:
        raise ValueError("Each inner CSP training fold needs at least two trials per hand")
    model = CSPClassifier(fs).fit(prepared_train, y, prepared=True)
    log_odds = model.features(prepared_test) @ model.coef.T + model.intercept
    return expit(log_odds.ravel())


def expert_evidence(x_train, y_train, block_train, x_test, fs=250):
    """Return two n×2 arrays of right-hand probabilities (mu, beta).

    The first is grouped out-of-fold for x_train; the second is fitted with
    all x_train and evaluated on x_test. No model component sees test labels.
    Filter each epoch locally so neither task-phase nor block edges share data.
    """
    if not np.isfinite(fs) or fs <= 2 * BANDS[-1][1]:
        raise ValueError("Sampling rate must exceed 60 Hz")
    train = _epochs(x_train, fs)
    test = _epochs(x_test, fs)
    y = _binary(y_train)
    blocks = np.asarray(block_train)
    if (train.shape[1:] != test.shape[1:] or y.shape != blocks.shape
            or len(y) != len(train) or len(np.unique(blocks)) < 2):
        raise ValueError("Training trials, blocks, labels or test window do not align")
    oof = np.full((len(train), len(BANDS)), np.nan)
    held = np.empty((len(test), len(BANDS)))
    for band, limits in enumerate(BANDS):
        sos = signal.butter(4, limits, fs=fs, btype="bandpass", output="sos")
        pad = min(train.shape[-1]-1, round(0.5*fs))
        train_f = signal.sosfiltfilt(sos, train, axis=-1, padlen=pad)
        test_f = signal.sosfiltfilt(sos, test, axis=-1, padlen=pad)
        for block in np.unique(blocks):
            val = blocks == block
            oof[val, band] = _predict_csp(train_f[~val], y[~val], train_f[val], fs)
        held[:, band] = _predict_csp(train_f, y, test_f, fs)
    if not np.isfinite(oof).all() or not np.isfinite(held).all():
        raise ArithmeticError("Expert predictions are incomplete or nonfinite")
    return oof, held


def _probabilities(x, name, ndim):
    x = np.asarray(x, dtype=float)
    if x.ndim != ndim or not len(x) or not np.isfinite(x).all() or np.any((x < 0) | (x > 1)):
        raise ValueError(f"{name} must contain finite probabilities in [0,1]")
    return x


def _moderator(train, held):
    center = float(np.mean(train))
    scale = max(float(np.std(train)), SD_FLOOR)
    def transform(x):
        return np.clip((x-center)/(2*scale), -1., 1.)
    return transform(train), transform(held), dict(center=center, scale=scale)


def _gate(expert_train, expert_test, y, train_columns, test_columns):
    train_design = np.column_stack([np.ones(len(y)), *train_columns])
    test_design = np.column_stack([np.ones(len(expert_test)), *test_columns])
    diff = expert_train[:, 0] - expert_train[:, 1]
    design = train_design * diff[:, None]
    residual = y - expert_train.mean(axis=1)
    dim = design.shape[1]
    gram = design.T @ design + RIDGE*np.eye(dim)
    cross = design.T @ residual
    coef = np.linalg.solve(gram, cross)
    # |a|+|b|(+|c|) <= 0.5 guarantees a convex mixture for all future
    # bounded moderators, including values unseen in the calibration blocks.
    if np.linalg.norm(coef, ord=1) > 0.5:
        faces = np.array(list(product((-1., 1.), repeat=dim)))
        opt = minimize(lambda t: 0.5*t@gram@t - t@cross, np.zeros(dim),
                       jac=lambda t: gram@t-cross, method="SLSQP",
                       constraints=[dict(type="ineq", fun=lambda t: 0.5-faces@t,
                                         jac=lambda t: -faces)],
                       options=dict(ftol=1e-13, maxiter=100))
        if not opt.success or np.linalg.norm(opt.x, ord=1) > 0.50000001:
            raise ArithmeticError(f"Could not fit a convex MI gate: {opt.message}")
        coef = opt.x
    weight = np.clip(0.5 + test_design @ coef, 0., 1.)
    q = weight*expert_test[:, 0] + (1-weight)*expert_test[:, 1]
    return q, dict(coefficients=coef.tolist(), weight_mu_min=float(weight.min()),
                   weight_mu_mean=float(weight.mean()), weight_mu_max=float(weight.max()))


def fit_gate(oof, y_train, p_train_oof, held, p_test):
    """Fit matched gates with no P, P, MI certainty, and MI certainty plus P.

    Returned vectors no_p, with_p, mi_only, mi_plus_p contain P(right hand).
    With constant P the P coefficient contributes no trial information.
    For mismatch controls permute P inside its original blocks and rerun
    this function on the SAME oof and held arrays, keeping all EEG and hand
    labels fixed. Test-only shifts diagnose reliance of the already fitted
    gate on trial alignment. Joint training/test shifts answer another question.
    """
    y = _binary(y_train)
    train = _probabilities(oof, "OOF experts", 2)
    test = _probabilities(held, "held experts", 2)
    p_train = _probabilities(p_train_oof, "OOF P", 1)
    p_held = _probabilities(p_test, "held P", 1)
    if (train.shape != (len(y), 2) or len(p_train) != len(y) or test.shape != (len(p_held), 2)
            or len(np.unique(y)) != 2):
        raise ValueError("Require two expert columns, both hands and one P per trial")
    # Stronger right or left preference is higher certainty of the respective
    # expert. This contrast uses only MI outputs and does not encode hand identity.
    def relative_certainty(x):
        return np.abs(2*x[:, 0]-1) - np.abs(2*x[:, 1]-1)
    mtr, mte, m_info = _moderator(relative_certainty(train), relative_certainty(test))
    ptr, pte, p_info = _moderator(p_train, p_held)
    pred = {}
    arms = {}
    for name, tr, te in (("no_p", [], []), ("with_p", [ptr], [pte]),
                         ("mi_only", [mtr], [mte]), ("mi_plus_p", [mtr, ptr], [mte, pte])):
        pred[name], arms[name] = _gate(train, test, y, tr, te)
    pred["info"] = dict(arms=arms, ridge=RIDGE, p_moderator=p_info, mi_moderator=m_info,
                        n_experts_disagree_train=int(np.sum((train[:, 0]>.5) != (train[:, 1]>.5))),
                        n_experts_disagree_held=int(np.sum((test[:, 0]>.5) != (test[:, 1]>.5))))
    return pred
