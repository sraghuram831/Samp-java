# Samp-java

## quantlab: a research-only strategy discovery pipeline

Generates a large space of rule-based trading strategies, backtests all of them
with execution costs, and then tries hard to reject them. It contains no broker
connection and no order code: the furthest a strategy can get is "paper trading
required".

```
market data -> hypotheses -> parameter grid -> batch backtest -> ranking -> validation gate -> report
```

### Run it

```bash
pip install numpy pandas            # pytest as well, to run the tests
python -m quantlab                  # synthetic 15-minute bars with no edge in them
python -m quantlab --autocorr 0.25  # synthetic bars with a planted momentum edge
python -m quantlab --csv bars.csv   # your own data: timestamp,open,high,low,close
python -m pytest
```

Each run writes `reports/report.md` (funnel, gate results, validated candidates)
and `reports/strategies.csv` (every strategy with all its metrics).

### What it does

| Stage | Module | Notes |
| --- | --- | --- |
| Data | `quantlab/data.py` | CSV loader that reports and removes duplicate, missing, non-positive and inverted bars. Synthetic generator with volatility regimes and fat tails. |
| Hypotheses | `quantlab/strategies.py` | Trend (EMA crossover + trend-strength filter), breakout (channel), mean reversion (z-score, RSI), momentum (rate of change). Each crossed with long/short/both and a volatility-regime filter: 1,503 variants. |
| Backtest | `quantlab/backtest.py` | A position decided at a bar's close is held over the next bar. Spread, commission and slippage are charged on every position change. |
| Selection | `quantlab/pipeline.py` | Uses the first 60% of the data only. The last 40% is held out and never consulted when filtering. |
| Validation | `quantlab/validation.py` | See below. |
| Report | `quantlab/report.py` | Markdown report and CSV. |

A strategy that passes the in-sample filter must then pass every one of these:

1. **Multiple testing**: deflated Sharpe ratio (Bailey and Lopez de Prado) of at
   least 0.95, against the Sharpe the luckiest no-skill strategy would be
   expected to show given how many effectively independent variants were tried.
2. **Out-of-sample**: Sharpe of at least 0.5 and a positive return on the held-out 40%.
3. **Fold stability**: profitable in at least 60% of ten equal time slices.
4. **Regimes**: not losing money in either high- or low-volatility periods of the held-out data.
5. **Monte Carlo**: 5th-percentile Sharpe above zero across 500 block-bootstrap resamples.

Separately, a **walk-forward** test re-selects the best strategy on each trailing
window and trades it on the next slice, which measures whether the selection
process itself carries forward.

### What to expect

On the default no-edge data, about 40 strategies look good in-sample and none
survive validation. That is the correct answer, and a test asserts it. With a
planted edge (`--autocorr 0.25` or `-0.25`), the momentum/breakout or
mean-reversion families respectively are validated, which shows the gate can
tell the difference.

### Limits

- Synthetic data proves the machinery, not a strategy. Real conclusions need real
  bars with realistic costs for the instrument and broker.
- Indicators use closing prices only; fills are assumed at the close plus the
  configured costs. There is no modelling of swaps/financing, gaps, partial
  fills or latency.
- One instrument at a time, fixed unit position size, no portfolio or risk layer.
- The count of effectively independent trials is an estimate. Every extra run
  you make on the same held-out data spends some of its value.
- Passing validation is a reason to paper trade, not to deploy.
