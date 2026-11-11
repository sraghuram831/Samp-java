"""Vectorised single-asset backtest with execution costs."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Costs:
    """All in basis points of notional. The spread is the full bid-ask width."""
    spread_bps: float = 0.8
    commission_bps: float = 0.2
    slippage_bps: float = 0.2

    @property
    def per_side(self) -> float:
        return (self.spread_bps / 2 + self.commission_bps + self.slippage_bps) / 1e4


def bar_returns(close: np.ndarray) -> np.ndarray:
    r = np.zeros(len(close))
    r[1:] = close[1:] / close[:-1] - 1
    return r


def net_returns(ret: np.ndarray, pos: np.ndarray, costs: Costs):
    """Returns (net return per bar, turnover per bar).

    The position chosen at the close of bar t is held over bar t+1, so a signal
    never earns the return of the bar it was computed on. Costs are charged on
    every change in position.
    """
    held = np.zeros(len(pos))
    held[1:] = pos[:-1]
    turnover = np.abs(np.diff(held, prepend=0.0))
    return held * ret - turnover * costs.per_side, turnover


def sharpe(r: np.ndarray, bars_per_year: float) -> float:
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0


def max_drawdown(r: np.ndarray) -> float:
    equity = np.cumprod(1 + r)
    peak = np.maximum.accumulate(equity)
    return float((1 - equity / peak).max()) if len(r) else 0.0


def total_return(r: np.ndarray) -> float:
    return float(np.prod(1 + r) - 1)
