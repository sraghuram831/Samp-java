"""The discovery engine: generate -> batch backtest -> filter -> validate."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import validation as v
from .backtest import Costs, bar_returns, max_drawdown, net_returns, sharpe, total_return
from .data import bars_per_year
from .strategies import Indicators, Spec, generate_specs, positions


@dataclass(frozen=True)
class Config:
    costs: Costs = Costs()
    folds: int = 10            # equal time slices of the data
    is_folds: int = 6          # first slices are in-sample; the rest are held out
    wf_train_folds: int = 4
    min_trades: int = 30       # in-sample round trips needed to be worth measuring
    min_is_sharpe: float = 1.0
    min_oos_sharpe: float = 0.5
    min_deflated_sharpe: float = 0.95
    min_positive_folds: float = 0.6
    mc_draws: int = 500


@dataclass
class Result:
    config: Config
    n_bars: int
    bars_per_year: float
    data_issues: list[str]
    table: pd.DataFrame                 # one row per backtested strategy
    funnel: dict[str, int]
    luck_sharpe: float                  # annualised Sharpe expected from the luckiest no-skill trial
    effective_trials: float             # trials after discounting near-duplicate strategies
    walk_forward: dict[str, float] = field(default_factory=dict)

    @property
    def validated(self) -> pd.DataFrame:
        return self.table[self.table["validated"]]


BLOCKS_PER_FOLD = 20


def effective_trials(block_returns: np.ndarray) -> float:
    """How many independent bets a set of correlated strategies amounts to.

    Entropy-based effective rank of the correlation spectrum: 1 if all rows
    move together, the row count if they are uncorrelated. Parameter neighbours are
    near-duplicates, so counting every grid point as a trial would overstate
    how much luck the search had access to.
    """
    z = block_returns - block_returns.mean(axis=1, keepdims=True)
    sd = z.std(axis=1, keepdims=True)
    z = z[sd[:, 0] > 0] / sd[sd[:, 0] > 0]
    if len(z) < 2:
        return float(len(z))
    gram = z.T @ z / z.shape[1]          # same non-zero spectrum as the row correlation matrix
    lam = np.clip(np.linalg.eigvalsh(gram), 0, None)
    p = lam[lam > 0] / lam.sum()
    return float(np.exp(-(p * np.log(p)).sum()))


def run(df: pd.DataFrame, config: Config = Config(), specs: list[Spec] | None = None) -> Result:
    specs = generate_specs() if specs is None else specs
    cfg = config
    close = df["close"].to_numpy(float)
    n = len(close)
    bpy = bars_per_year(df.index)
    ret = bar_returns(close)
    ind = Indicators(close)
    blocks = np.linspace(0, n, cfg.folds * BLOCKS_PER_FOLD + 1).astype(int)
    edges = blocks[::BLOCKS_PER_FOLD]
    fold_n = np.diff(edges).astype(float)
    split = edges[cfg.is_folds]
    is_blocks = cfg.is_folds * BLOCKS_PER_FOLD

    # ---- batch backtest: keep only per-fold sufficient statistics ----
    m = len(specs)
    fsum, fsq = np.zeros((m, cfg.folds)), np.zeros((m, cfg.folds))
    bsum = np.zeros((m, is_blocks))
    is_trades, exposure = np.zeros(m), np.zeros(m)
    for i, spec in enumerate(specs):
        r, turn = net_returns(ret, positions(spec, ind), cfg.costs)
        by_block = np.add.reduceat(r, blocks[:-1])
        bsum[i] = by_block[:is_blocks]
        fsum[i] = by_block.reshape(cfg.folds, BLOCKS_PER_FOLD).sum(axis=1)
        fsq[i] = np.add.reduceat(r * r, edges[:-1])
        is_trades[i] = turn[:split].sum() / 2
        exposure[i] = np.count_nonzero(r) / n

    def sr(cols):
        cnt = fold_n[cols].sum()
        mean = fsum[:, cols].sum(axis=1) / cnt
        var = fsq[:, cols].sum(axis=1) / cnt - mean ** 2
        return np.where(var > 0, mean / np.sqrt(np.where(var > 0, var, 1)), 0.0)

    is_cols, oos_cols = slice(0, cfg.is_folds), slice(cfg.is_folds, cfg.folds)
    is_sr_bar = sr(is_cols)
    table = pd.DataFrame({
        "id": [s.id for s in specs],
        "family": [s.family for s in specs],
        "is_sharpe": is_sr_bar * np.sqrt(bpy),
        "oos_sharpe": sr(oos_cols) * np.sqrt(bpy),
        "is_trades": is_trades,
        "exposure": exposure,
        "positive_folds": (fsum > 0).mean(axis=1),
    })
    tradable = is_trades >= cfg.min_trades
    table["backtested"] = tradable
    # Selection uses in-sample numbers only. The held-out slices are not consulted.
    table["passed_filter"] = tradable & (table["is_sharpe"] >= cfg.min_is_sharpe)

    # Luck benchmark: sampling variance of a no-skill Sharpe is 1/T per bar, and
    # the search made n_eff roughly independent attempts at beating it.
    n_backtested = int(tradable.sum())
    n_eff = effective_trials(bsum[tradable])
    n_trials = max(2, int(round(n_eff)))
    trial_var = 1.0 / split
    luck = v.expected_max_sharpe(n_trials, trial_var) * np.sqrt(bpy)

    # ---- validation gate for the survivors ----
    with np.errstate(invalid="ignore"):
        high_vol = np.nan_to_num(ind.vol_ratio(), nan=1.0) > 1
    checks = {k: np.full(m, np.nan) for k in
              ["deflated_sharpe", "oos_return", "oos_max_dd", "regime_high_vol",
               "regime_low_vol", "mc_sharpe_p05", "mc_drawdown_p95"]}
    gates = {k: np.zeros(m, bool) for k in
             ["gate_multiple_testing", "gate_oos", "gate_stability", "gate_regimes", "gate_monte_carlo"]}
    for i in np.flatnonzero(table["passed_filter"].to_numpy()):
        r, _ = net_returns(ret, positions(specs[i], ind), cfg.costs)
        r_is, r_oos = r[:split], r[split:]
        dsr = v.deflated_sharpe(r_is, n_trials, trial_var)
        reg = v.regime_returns(r_oos, high_vol[split:])
        checks["deflated_sharpe"][i] = dsr
        checks["oos_return"][i] = total_return(r_oos)
        checks["oos_max_dd"][i] = max_drawdown(r_oos)
        checks["regime_high_vol"][i] = reg["high_vol"]
        checks["regime_low_vol"][i] = reg["low_vol"]
        gates["gate_multiple_testing"][i] = dsr >= cfg.min_deflated_sharpe
        gates["gate_oos"][i] = sharpe(r_oos, bpy) >= cfg.min_oos_sharpe and total_return(r_oos) > 0
        gates["gate_stability"][i] = table["positive_folds"].iat[i] >= cfg.min_positive_folds
        gates["gate_regimes"][i] = reg["high_vol"] >= 0 and reg["low_vol"] >= 0
        # Monte Carlo is the expensive check, so it only runs once the others pass.
        if all(gates[g][i] for g in gates if g != "gate_monte_carlo"):
            mc = v.monte_carlo(r_oos, bpy, draws=cfg.mc_draws, seed=i)
            checks["mc_sharpe_p05"][i] = mc["sharpe_p05"]
            checks["mc_drawdown_p95"][i] = mc["drawdown_p95"]
            gates["gate_monte_carlo"][i] = mc["sharpe_p05"] > 0
    for k, col in {**checks, **gates}.items():
        table[k] = col
    table["validated"] = table["passed_filter"] & np.logical_and.reduce(list(gates.values()))

    # ---- walk-forward: does "pick the recent best" survive on the next slice? ----
    wf = {}
    fam = table["family"].to_numpy()
    for name in ["all", *sorted(set(fam))]:
        elig = tradable if name == "all" else tradable & (fam == name)
        wf[name], _ = v.walk_forward(fsum, fsq, fold_n, bpy, cfg.wf_train_folds, elig)

    funnel = {"generated": m, "backtested": n_backtested,
              "passed_initial_filter": int(table["passed_filter"].sum()),
              "passed_validation": int(table["validated"].sum())}
    return Result(cfg, n, bpy, list(df.attrs.get("issues", [])), table, funnel, float(luck), n_eff,
                  wf)
