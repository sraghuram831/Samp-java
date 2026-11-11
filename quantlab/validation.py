"""Robustness checks: deflated Sharpe, regimes, Monte Carlo, walk-forward."""
from __future__ import annotations

from statistics import NormalDist

import numpy as np

_N = NormalDist()
_EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, trial_sharpe_var: float) -> float:
    """Sharpe the best of n_trials skill-less strategies is expected to show by luck.

    Per-bar (not annualised) units. Bailey & Lopez de Prado (2014).
    """
    if n_trials < 2:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * np.e))
    return float(np.sqrt(trial_sharpe_var) * ((1 - _EULER) * a + _EULER * b))


def deflated_sharpe(r: np.ndarray, n_trials: int, trial_sharpe_var: float) -> float:
    """Probability the true Sharpe is above the luck benchmark for n_trials tries."""
    sd = r.std()
    if sd == 0 or len(r) < 3:
        return 0.0
    sr = r.mean() / sd
    z = (r - r.mean()) / sd
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    sr0 = expected_max_sharpe(n_trials, trial_sharpe_var)
    return float(_N.cdf((sr - sr0) * np.sqrt(len(r) - 1) / np.sqrt(denom)))


def regime_returns(r: np.ndarray, high_vol: np.ndarray) -> dict[str, float]:
    """Net return earned in high- and low-volatility bars."""
    return {"high_vol": float(r[high_vol].sum()), "low_vol": float(r[~high_vol].sum())}


def monte_carlo(r: np.ndarray, bars_per_year: float, draws: int = 500,
                block: int = 96, seed: int = 0) -> dict[str, float]:
    """Block bootstrap of the return path: how bad could this have plausibly looked?"""
    rng = np.random.default_rng(seed)
    n = len(r)
    n_blocks = -(-n // block)
    starts = rng.integers(0, n - block + 1, size=(draws, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(draws, -1)[:, :n]
    paths = r[idx]
    sd = paths.std(axis=1)
    sr = np.where(sd > 0, paths.mean(axis=1) / np.where(sd > 0, sd, 1) * np.sqrt(bars_per_year), 0.0)
    cum = np.cumsum(paths, axis=1)
    dd = (np.maximum.accumulate(cum, axis=1) - cum).max(axis=1)
    return {"sharpe_p05": float(np.percentile(sr, 5)),
            "sharpe_p50": float(np.percentile(sr, 50)),
            "prob_loss": float((cum[:, -1] < 0).mean()),
            "drawdown_p95": float(np.percentile(dd, 95))}


def walk_forward(fold_sum: np.ndarray, fold_sumsq: np.ndarray, fold_n: np.ndarray,
                 bars_per_year: float, train_folds: int, eligible: np.ndarray | None = None):
    """Re-select the best strategy on each trailing window, trade it on the next fold.

    fold_sum / fold_sumsq are (strategies x folds) sums of net returns and their
    squares. Returns the stitched out-of-sample Sharpe and the per-fold picks.
    This tests the selection process itself, not any single parameter set.
    """
    n_strats, n_folds = fold_sum.shape
    mask = np.ones(n_strats, bool) if eligible is None else eligible
    if not mask.any() or n_folds <= train_folds:
        return 0.0, []
    s = ss = cnt = 0.0
    picks = []
    for k in range(train_folds, n_folds):
        tr = slice(k - train_folds, k)
        n_tr = fold_n[tr].sum()
        mean = fold_sum[:, tr].sum(axis=1) / n_tr
        var = fold_sumsq[:, tr].sum(axis=1) / n_tr - mean ** 2
        score = np.where((var > 0) & mask, mean / np.sqrt(np.where(var > 0, var, 1)), -np.inf)
        best = int(np.argmax(score))
        picks.append((k, best, float(fold_sum[best, k])))
        s += fold_sum[best, k]
        ss += fold_sumsq[best, k]
        cnt += fold_n[k]
    mean = s / cnt
    var = ss / cnt - mean ** 2
    return (float(mean / np.sqrt(var) * np.sqrt(bars_per_year)) if var > 0 else 0.0), picks
