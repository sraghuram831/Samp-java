# 9 EMA + VWAP strategy backtest on XAUUSD

A mechanical backtest of the three setups in the "9 EMA + VWAP Strategy" guide
(trading the cross, the hold, and the snapback), applied to spot gold.

## Status: limited sample

The results cover **57 trading days** in two blocks: 11–22 Nov 2024 (10 days)
and 7 Sep–10 Nov 2026 (47 days). The Dukascopy data feed throttled the
download hard, so the planned one-to-two-year test has not been run.
Re-running the script continues the download from the local cache.

## Results (all figures in USD per oz, i.e. per 0.01 lot)

Net is after the real bid/ask spread (median 0.56). Gross is at mid prices.

| Setup | Trades | Net total | Gross total | Net per trade |
|---|---|---|---|---|
| Cross | 1,392 | −804.52 | −41.12 | −0.58 |
| Hold | 729 | −396.53 | +9.06 | −0.54 |
| Snapback | 1,289 | −428.15 | −74.91 | −0.33 |

These totals pool three timeframes (1m, 3m, 5m) and three sessions.

New York session only, which is the session the guide is written for (net):

| Setup | 1m | 3m | 5m |
|---|---|---|---|
| Cross | −168.25 (183 trades) | −102.44 (136) | −172.84 (111) |
| Hold | −103.97 (176) | −114.87 (53) | −62.36 (26) |
| Snapback | +118.00 (203) | +9.64 (129) | −45.45 (94) |

![Equity curves, New York session](results/equity_NewYork.png)

What the sample shows:

- **The bare mechanical rules have no edge on gold.** Before costs all three
  setups are close to zero per trade; after the spread all three lose.
- **The spread is the loss.** Net per trade (−0.3 to −0.6) is roughly one
  spread, which is what a zero-edge entry costs.
- **Only 4 of 27 setup/timeframe/session cells are net positive**: the
  New York snapback on 1m and 3m, and the Tokyo hold on 3m and 5m. With 27
  cells tested, a few winners are expected by chance.
- **Both directions lose** for every setup, so the result is not a
  long/short bias effect.
- Win rates are low for the trend setups (20–32%), with winners about twice
  the size of losers, which is not enough to cover them.
- An earlier run on only 22 days looked better (cross positive in New York);
  that did not survive the larger sample.

Full per-cell statistics are in `results/summary.csv`, the long/short split in
`results/by_side.csv`, and every trade in `results/trades.csv.gz`.

## How the guide's rules were made mechanical

Signals use mid prices; fills use the next bar's open at the real ask (buys)
or bid (sells). No parameter was tuned.

| Rule | Implementation |
|---|---|
| 9 EMA | EMA(9) of closes, continuous across sessions |
| VWAP | Typical price weighted by Dukascopy volume, reset at each session open |
| Sessions | New York 09:30–16:00 ET (as in the guide), plus London 08:00–16:30 and Tokyo 09:00–15:00 local, since gold trades around the clock |
| Cross entry | EMA crosses VWAP; within 5 bars a candle closes beyond the EMA and the cross price; only 30–240 minutes after the open (the guide's 10:00–13:30 ET) |
| Hold entry | First 30 minutes; price beyond the open and both lines; a candle wicks into the EMA and closes back on the right side |
| Cross/hold exit | First candle that closes back through the EMA, or the session close |
| Snapback entry | Price was beyond the 2-sigma VWAP band in the last 5 bars, then a candle closes back over the EMA while still short of VWAP |
| Snapback exit | Target is VWAP; stop is the 10-bar swing extreme; if both are hit in one bar the stop is assumed first |

## Limits

- The guide's discretionary filters are **not** modelled: key levels, "volume
  showed up", SPY/QQQ agreement, news, and skipping chop days. The guide itself
  calls an unfiltered cross "a low-quality signal".
- Spot gold has no consolidated volume. VWAP here uses one broker's volume, so
  it will differ from a futures-based or TradingView VWAP.
- Spread is Dukascopy's; commission and slippage are not included.
- Each setup is tested as an independent book with one position at a time.

## Running it

```
pip install pandas numpy matplotlib
python3 backtest/xauusd_9ema_vwap.py --start 2025-11-11 --end 2026-11-10
python3 backtest/plot_equity.py                # New York equity chart
python3 backtest/xauusd_9ema_vwap.py --offline # use only days already cached
```

Not financial advice. A backtest is not a forecast.
