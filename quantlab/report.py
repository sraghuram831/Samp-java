"""Plain-markdown research report."""
from __future__ import annotations

import pandas as pd

from .pipeline import Result

GATES = [("gate_multiple_testing", "Multiple-testing (deflated Sharpe)"),
         ("gate_oos", "Out-of-sample"),
         ("gate_stability", "Fold stability"),
         ("gate_regimes", "Volatility regimes"),
         ("gate_monte_carlo", "Monte Carlo")]


def _row(vals) -> str:
    return "| " + " | ".join(str(x) for x in vals) + " |"


def _table(header, rows) -> list[str]:
    return [_row(header), _row(["---"] * len(header)), *[_row(r) for r in rows], ""]


def render(res: Result, source: str) -> str:
    cfg, t = res.config, res.table
    c = cfg.costs
    out = ["# Strategy research report", "", "**Status: RESEARCH ONLY.** Nothing here is cleared for live capital.", "",
           f"- Data: {source}, {res.n_bars:,} bars (~{res.bars_per_year:,.0f} bars/year)",
           f"- In-sample: first {cfg.is_folds}/{cfg.folds} of the data; held out: last {cfg.folds - cfg.is_folds}/{cfg.folds}",
           f"- Costs per side: {c.per_side * 1e4:.2f} bps (spread {c.spread_bps}, commission {c.commission_bps}, slippage {c.slippage_bps})",
           f"- Data issues: {'; '.join(res.data_issues) if res.data_issues else 'none found'}", ""]

    out += ["## Funnel", ""]
    f = res.funnel
    out += _table(["Stage", "Strategies"],
                  [["Generated", f"{f['generated']:,}"],
                   [f"Backtested (>= {cfg.min_trades} in-sample trades)", f"{f['backtested']:,}"],
                   [f"Passed initial filter (in-sample Sharpe >= {cfg.min_is_sharpe})", f"{f['passed_initial_filter']:,}"],
                   ["Passed validation", f"{f['passed_validation']:,}"]])
    out += [f"The {f['backtested']:,} backtests are heavily correlated and amount to roughly "
            f"{res.effective_trials:.0f} independent attempts. The luckiest of that many strategies with no skill "
            f"at all is expected to show an in-sample Sharpe of about **{res.luck_sharpe:.2f}**. "
            "In-sample numbers near or below that are not evidence.", ""]

    surv = t[t["passed_filter"]]
    if len(surv):
        out += ["## Where survivors of the initial filter failed", ""]
        rows = [[label, int(surv[g].sum()), int((~surv[g]).sum())] for g, label in GATES[:-1]]
        ran = surv["mc_sharpe_p05"].notna()
        rows.append(["Monte Carlo (only run if all of the above pass)",
                     int(surv["gate_monte_carlo"].sum()), int((ran & ~surv["gate_monte_carlo"]).sum())])
        out += _table(["Gate", "Passed", "Failed"], rows)

    out += ["## Walk-forward (re-select on trailing window, trade next slice)", ""]
    out += _table(["Universe", "Stitched out-of-sample Sharpe"],
                  [[k, f"{s:.2f}"] for k, s in res.walk_forward.items()])

    val = res.validated.sort_values("oos_sharpe", ascending=False)
    out += [f"## Validated candidates ({len(val)})", ""]
    if len(val):
        out += _table(["Strategy", "IS Sharpe", "OOS Sharpe", "OOS return", "OOS max DD", "Deflated SR", "MC Sharpe p05"],
                      [[r.id, f"{r.is_sharpe:.2f}", f"{r.oos_sharpe:.2f}", f"{r.oos_return:.1%}",
                        f"{r.oos_max_dd:.1%}", f"{r.deflated_sharpe:.2f}", f"{r.mc_sharpe_p05:.2f}"]
                       for r in val.head(20).itertuples()])
    else:
        out += ["None. No strategy earned the right to go further on this data.", ""]

    top = t[t["backtested"]].sort_values("is_sharpe", ascending=False).head(10)
    out += ["## Top 10 by in-sample Sharpe, and what happened out of sample", ""]
    out += _table(["Strategy", "IS Sharpe", "OOS Sharpe", "Validated"],
                  [[r.id, f"{r.is_sharpe:.2f}", f"{r.oos_sharpe:.2f}", "yes" if r.validated else "no"]
                   for r in top.itertuples()])

    any_val = len(val) > 0
    out += ["## Validation gate", ""]
    out += _table(["Stage", "Status"],
                  [["Backtest with costs", "PASSED" if f["passed_initial_filter"] else "FAILED"],
                   ["Out-of-sample, regimes, Monte Carlo, multiple-testing", "PASSED" if any_val else "FAILED"],
                   ["Paper trading", "REQUIRED" if any_val else "NOT REACHED"],
                   ["Live deployment", "BLOCKED"]])
    return "\n".join(out)


def save(res: Result, source: str, out_dir: str) -> str:
    import os
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "report.md")
    with open(path, "w") as fh:
        fh.write(render(res, source))
    res.table.sort_values("is_sharpe", ascending=False).to_csv(os.path.join(out_dir, "strategies.csv"), index=False)
    return path
