"""CLI: python -m quantlab [--csv FILE | --synthetic] [options]"""
from __future__ import annotations

import argparse
from dataclasses import replace

from . import data, report
from .backtest import Costs
from .pipeline import Config, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quantlab", description="Research-only strategy discovery and validation.")
    ap.add_argument("--csv", help="OHLC bars: timestamp,open,high,low,close")
    ap.add_argument("--bars", type=int, default=60_000, help="synthetic: number of 15-minute bars")
    ap.add_argument("--seed", type=int, default=7, help="synthetic: random seed")
    ap.add_argument("--autocorr", type=float, default=0.0,
                    help="synthetic: plant an edge (>0 momentum, <0 mean reversion); 0 = no edge")
    ap.add_argument("--spread-bps", type=float, default=Costs.spread_bps)
    ap.add_argument("--commission-bps", type=float, default=Costs.commission_bps)
    ap.add_argument("--slippage-bps", type=float, default=Costs.slippage_bps)
    ap.add_argument("--out", default="reports", help="output directory")
    a = ap.parse_args(argv)

    if a.csv:
        df, source = data.load_csv(a.csv), a.csv
    else:
        df = data.synthetic(a.bars, a.seed, a.autocorr)
        source = f"synthetic (seed={a.seed}, autocorr={a.autocorr})"
    cfg = replace(Config(), costs=Costs(a.spread_bps, a.commission_bps, a.slippage_bps))
    res = run(df, cfg)
    path = report.save(res, source, a.out)
    for stage, count in res.funnel.items():
        print(f"{stage.replace('_', ' '):>24}: {count:,}")
    print(f"{'luck benchmark Sharpe':>24}: {res.luck_sharpe:.2f} (~{res.effective_trials:.0f} independent trials)")
    print(f"{'walk-forward Sharpe':>24}: {res.walk_forward['all']:.2f}")
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
