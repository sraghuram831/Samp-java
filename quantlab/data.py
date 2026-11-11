"""Market data: CSV loading with sanity checks, and a synthetic generator."""
from __future__ import annotations

import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close"]


def load_csv(path: str) -> pd.DataFrame:
    """Load OHLC bars. Needs a timestamp column (first column) plus open/high/low/close."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts = df.columns[0]
    df[ts] = pd.to_datetime(df[ts])
    df = df.set_index(ts)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    return clean(df[COLUMNS].astype(float))


def quality_issues(df: pd.DataFrame) -> list[str]:
    """Bad data gives false results; list the problems rather than silently trading on them."""
    issues = []
    if not df.index.is_monotonic_increasing:
        issues.append("timestamps not sorted")
    dupes = int(df.index.duplicated().sum())
    if dupes:
        issues.append(f"{dupes} duplicate timestamps")
    nans = int(df[COLUMNS].isna().any(axis=1).sum())
    if nans:
        issues.append(f"{nans} rows with missing prices")
    bad = int((df[COLUMNS] <= 0).any(axis=1).sum())
    if bad:
        issues.append(f"{bad} rows with non-positive prices")
    inverted = int((df["high"] < df["low"]).sum())
    if inverted:
        issues.append(f"{inverted} rows with high < low")
    jumps = int((df["close"].pct_change().abs() > 0.2).sum())
    if jumps:
        issues.append(f"{jumps} bars with a >20% close-to-close move")
    return issues


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Sort, drop duplicate timestamps and unusable rows. Attaches the issues found."""
    issues = quality_issues(df)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.dropna(subset=COLUMNS)
    df = df[(df[COLUMNS] > 0).all(axis=1) & (df["high"] >= df["low"])]
    df.attrs["issues"] = issues
    return df


def synthetic(n_bars: int = 60_000, seed: int = 7, autocorr: float = 0.0,
              bar_minutes: int = 15, start_price: float = 1.10) -> pd.DataFrame:
    """Weekday intraday bars with volatility regimes and fat tails.

    With autocorr=0 there is no exploitable edge, so an honest pipeline should
    validate (almost) nothing. autocorr>0 plants a momentum edge, autocorr<0 a
    mean-reversion edge, which is useful for checking the pipeline can find one.
    """
    rng = np.random.default_rng(seed)
    # Two-state Markov volatility regime: calm / volatile.
    stay = 0.998
    switches = rng.random(n_bars) > stay
    state = np.cumsum(switches) % 2
    vol = np.where(state == 0, 0.0004, 0.0009)
    eps = rng.standard_t(df=5, size=n_bars) / np.sqrt(5 / 3) * vol
    ret = np.empty(n_bars)
    prev = 0.0
    for i in range(n_bars):
        prev = autocorr * prev + eps[i]
        ret[i] = prev
    close = start_price * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.normal(0, 0.5, size=(2, n_bars))) * vol
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])

    idx = pd.date_range("2022-01-03", periods=int(n_bars * 1.5) + 10, freq=f"{bar_minutes}min")
    idx = idx[idx.dayofweek < 5][:n_bars]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
    df.attrs["issues"] = []
    return df


def bars_per_year(index: pd.DatetimeIndex) -> float:
    years = (index[-1] - index[0]).total_seconds() / (365.25 * 86400)
    return len(index) / years
