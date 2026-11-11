"""Strategy families, parameter grids, and causal position rules.

Every rule decides its position at bar t using data up to and including the
close of bar t. The backtester applies that position to bar t+1.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Spec:
    family: str
    params: tuple  # sorted (key, value) pairs, so specs are hashable

    @property
    def p(self) -> dict:
        return dict(self.params)

    @property
    def id(self) -> str:
        return self.family + "(" + ",".join(f"{k}={v}" for k, v in self.params) + ")"


def _spec(family: str, **params) -> Spec:
    return Spec(family, tuple(sorted(params.items())))


class Indicators:
    """Cached indicators on one close series, so thousands of variants stay cheap."""

    VOL_SHORT, VOL_LONG = 96, 960

    def __init__(self, close):
        self.c = pd.Series(np.asarray(close, dtype=float))
        self.ret = self.c.pct_change().fillna(0.0)
        self._cache: dict = {}

    def _get(self, key, fn):
        if key not in self._cache:
            self._cache[key] = np.asarray(fn(), dtype=float)
        return self._cache[key]

    def close(self):
        return self._get("c", lambda: self.c)

    def ema(self, n):
        return self._get(("ema", n), lambda: self.c.ewm(span=n, adjust=False).mean())

    def prior_max(self, n):
        return self._get(("max", n), lambda: self.c.rolling(n).max().shift(1))

    def prior_min(self, n):
        return self._get(("min", n), lambda: self.c.rolling(n).min().shift(1))

    def zscore(self, n):
        return self._get(("z", n), lambda: (self.c - self.c.rolling(n).mean()) / self.c.rolling(n).std())

    def rsi(self, n):
        def f():
            d = self.c.diff()
            up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
            dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
            return 100 - 100 / (1 + up / dn)
        return self._get(("rsi", n), f)

    def vol(self, n):
        return self._get(("vol", n), lambda: self.ret.rolling(n).std())

    def roc_score(self, n):
        """n-bar return in units of its expected standard deviation."""
        return self._get(("roc", n), lambda: (self.c / self.c.shift(n) - 1)
                         / (self.ret.rolling(self.VOL_SHORT).std() * np.sqrt(n)))

    def eff_ratio(self, n):
        """Kaufman efficiency ratio: net move / path length. A trend-strength filter."""
        return self._get(("er", n), lambda: (self.c - self.c.shift(n)).abs()
                         / self.c.diff().abs().rolling(n).sum())

    def vol_ratio(self):
        """Short-run over long-run volatility: >1 means a high-volatility regime."""
        return self._get("vr", lambda: self.vol(self.VOL_SHORT) / self.vol(self.VOL_LONG))


def _hold(entry: np.ndarray, exit_: np.ndarray) -> np.ndarray:
    """1 from each entry until the next exit, else 0 (exit wins ties)."""
    n = len(entry)
    ev = np.full(n, np.nan)
    ev[entry] = 1.0
    ev[exit_] = 0.0
    idx = np.where(np.isnan(ev), 0, np.arange(n))
    np.maximum.accumulate(idx, out=idx)
    out = ev[idx]
    out[np.isnan(out)] = 0.0
    return out


def _trend(ind, p):
    raw = np.sign(ind.ema(p["fast"]) - ind.ema(p["slow"]))
    if p["er_min"] > 0:
        raw = np.where(ind.eff_ratio(p["slow"]) >= p["er_min"], raw, 0.0)
    return raw


def _breakout(ind, p):
    c = ind.close()
    with np.errstate(invalid="ignore"):
        long_ = _hold(c > ind.prior_max(p["lookback"]), c < ind.prior_min(p["exit"]))
        short = _hold(c < ind.prior_min(p["lookback"]), c > ind.prior_max(p["exit"]))
    return long_ - short


def _meanrev(ind, p):
    z = ind.zscore(p["lookback"])
    with np.errstate(invalid="ignore"):
        long_ = _hold(z < -p["entry_z"], z > -p["exit_z"])
        short = _hold(z > p["entry_z"], z < p["exit_z"])
    return long_ - short


def _rsi(ind, p):
    r = ind.rsi(p["period"])
    with np.errstate(invalid="ignore"):
        long_ = _hold(r < p["low"], r > 50)
        short = _hold(r > 100 - p["low"], r < 50)
    return long_ - short


def _momentum(ind, p):
    s = ind.roc_score(p["lookback"])
    with np.errstate(invalid="ignore"):
        return np.where(np.abs(s) > p["threshold"], np.sign(s), 0.0)


RULES = {"trend": _trend, "breakout": _breakout, "meanrev": _meanrev,
         "rsi": _rsi, "momentum": _momentum}

DIRECTIONS = ("both", "long", "short")
VOL_FILTERS = ("none", "high", "low")


def positions(spec: Spec, ind: Indicators) -> np.ndarray:
    """Target position in {-1, 0, +1} decided at each bar's close."""
    p = spec.p
    pos = np.nan_to_num(RULES[spec.family](ind, p))
    if p["direction"] == "long":
        pos = np.clip(pos, 0, 1)
    elif p["direction"] == "short":
        pos = np.clip(pos, -1, 0)
    if p["vol_filter"] != "none":
        vr = ind.vol_ratio()
        with np.errstate(invalid="ignore"):
            ok = vr > 1 if p["vol_filter"] == "high" else vr <= 1
        pos = np.where(ok, pos, 0.0)
    return pos


def generate_specs() -> list[Spec]:
    """The hypothesis space: every family x parameter x direction x regime filter."""
    base: list[tuple[str, dict]] = []
    for fast, slow, er in product([5, 10, 20, 30, 50], [50, 100, 200, 400], [0, 0.05, 0.1, 0.15]):
        if fast < slow:
            base.append(("trend", dict(fast=fast, slow=slow, er_min=er)))
    for lb, frac in product([20, 40, 60, 96, 192, 384], [0.25, 0.5, 1.0]):
        base.append(("breakout", dict(lookback=lb, exit=max(2, int(lb * frac)))))
    for lb, ez, xz in product([20, 48, 96, 192], [1.0, 1.5, 2.0, 2.5, 3.0], [0.0, 0.5]):
        base.append(("meanrev", dict(lookback=lb, entry_z=ez, exit_z=xz)))
    for period, low in product([7, 14, 28], [20, 25, 30]):
        base.append(("rsi", dict(period=period, low=low)))
    for lb, thr in product([12, 24, 48, 96, 192, 384], [0.0, 0.5, 1.0, 1.5]):
        base.append(("momentum", dict(lookback=lb, threshold=thr)))
    return [_spec(fam, direction=d, vol_filter=v, **params)
            for (fam, params), d, v in product(base, DIRECTIONS, VOL_FILTERS)]
