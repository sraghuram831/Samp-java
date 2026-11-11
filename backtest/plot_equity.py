#!/usr/bin/env python3
"""Plot cumulative net P&L (USD per oz) per setup and timeframe from trades.csv.gz."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
res = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results")
session = sys.argv[2] if len(sys.argv) > 2 else "NewYork"

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
COLORS = {"1m": "#2a78d6", "3m": "#eb6834", "5m": "#1baf7a"}   # fixed per timeframe
TITLES = {"cross": "Trading the cross", "hold": "Trading the hold",
          "snapback": "Trading the distance (snapback)"}

t = pd.read_csv(os.path.join(res, "trades.csv.gz"), parse_dates=["entry_time", "exit_time"])
t = t[t["session"] == session]

fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharey=True, facecolor=SURFACE)
for ax, setup in zip(axes, TITLES):
    ax.set_facecolor(SURFACE)
    for tf, color in COLORS.items():
        s = t[(t["setup"] == setup) & (t["tf"] == tf)].sort_values("exit_time")
        if s.empty:
            continue
        eq = s["pnl"].cumsum()
        x = range(1, len(eq) + 1)          # trade number: the cached days need not be contiguous
        ax.plot(x, eq, color=color, lw=2, label=tf, solid_capstyle="round")
        ax.annotate(f"{tf}  {eq.iloc[-1]:+.0f}", (len(eq), eq.iloc[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=9, color=INK)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_title(TITLES[setup], loc="left", fontsize=11, color=INK, fontweight="bold")
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.set_xlabel("Trade number", color=MUTED, fontsize=9)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.margins(x=0.02)
    ax.set_xlim(right=ax.get_xlim()[1] + 0.22 * (ax.get_xlim()[1] - ax.get_xlim()[0]))
axes[0].set_ylabel("Cumulative net P&L, USD per oz", color=MUTED, fontsize=9)
axes[0].legend(title="Chart timeframe", frameon=False, fontsize=9, title_fontsize=9, loc="upper left")
fig.suptitle(f"XAUUSD 9 EMA + VWAP: cumulative net P&L after spread, {session} session",
             x=0.01, ha="left", fontsize=13, color=INK, fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.94))
out = os.path.join(res, f"equity_{session}.png")
fig.savefig(out, dpi=200, facecolor=SURFACE)
print(out)
