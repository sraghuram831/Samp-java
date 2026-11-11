# SweepOB EA – backtest on major FX pairs

The EA (`SweepOB_EA.mq5`) was ported bar-for-bar to Python (`sweepob_bt.py`) and run on M1 data.
MetaTrader 5 cannot run in this environment, so this is a re-implementation, not a Strategy Tester run.

## Data
* Source: getdata-finance free M1 samples on GitHub (MIT), e.g. `github.com/getdata-finance/eurusd-1m-ohlcv-forex-historical-data`
* Pairs: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD (NZDUSD not available)
* Period: 2026-03-26 → 2026-09-25 (≈6 months). The first 21 days are warm-up (structure/history only), so trading starts mid-April.
* Bars treated as BID; ASK = BID + spread. Spreads (pips): EURUSD 1.0, GBPUSD 1.4, USDJPY 1.2, USDCHF 1.5, AUDUSD 1.2, USDCAD 1.6

## Simulation assumptions
* Server time UTC+3 (summer, the whole window is US-DST), so H1/D1 bars align like a typical MT5 broker; killzones are evaluated in true IST.
* Intrabar fills follow the MT5 "1 minute OHLC" path (O→L→H→C for up bars, O→H→L→C for down bars).
* All EA defaults: H1 len 10, M1 len 5, sweep + CHoCH required, OB near-edge limit, 30-bar expiry, SL buffer 20 pts, RR 3, BE at +1R, max 3 trades/day, one setup per killzone, one exposure at a time.
* Results are in R (multiples of initial risk). Money is not modelled because the default `InpMaxLot = 0.02` caps the size well below 0.5% risk on most accounts.

## Results – EA defaults
| Pair | Trades | TP | SL | BE | Total R | Pips | Median SL (pips) |
|---|---|---|---|---|---|---|---|
| AUDUSD | 13 | 1 | 9 | 3 | -6.0 | -23.2 | 4.3 |
| EURUSD | 13 | 1 | 5 | 7 | -2.0 | +5.9 | 5.3 |
| GBPUSD | 15 | 1 | 8 | 6 | -5.0 | -36.6 | 5.5 |
| USDCAD | 11 | 1 | 8 | 2 | -5.0 | -35.8 | 4.2 |
| USDCHF | 14 | 0 | 11 | 3 | -11.0 | -77.4 | 7.5 |
| USDJPY | 15 | 5 | 6 | 4 | +9.0 | +152.8 | 6.8 |
| **All** | **81** | **9** | **47** | **25** | **-20.0** | | |

Win rate 11%, profit factor 0.57, average -0.25R per trade, max drawdown 32R (≈16% at 0.5% risk).

## Sensitivity (all six pairs)
| Config | Trades | Win % | Total R | Avg R | Max DD (R) | PF |
|---|---|---|---|---|---|---|
| EA defaults | 81 | 11.1 | -20.0 | -0.25 | 32.0 | 0.57 |
| Defaults, zero spread | 89 | 12.4 | -12.0 | -0.14 | 23.0 | 0.73 |
| No break-even | 81 | 14.8 | -33.0 | -0.41 | 45.0 | 0.52 |
| RR 2 | 82 | 15.9 | -21.0 | -0.26 | 28.0 | 0.55 |
| RR 2, no BE | 82 | 22.0 | -28.0 | -0.34 | 35.0 | 0.56 |
| Entry at OB midpoint | 62 | 12.9 | -15.0 | -0.24 | 20.9 | 0.62 |
| Bias = both sides | 213 | 8.9 | -81.0 | -0.38 | 95.0 | 0.41 |
| SL buffer 50 pts | 80 | 16.2 | -6.0 | -0.08 | 17.0 | 0.87 |

## Takeaways
* No configuration tested is profitable; the edge is negative even with zero spread.
* M1 order-block stops are tiny (median 4–7 pips), so a 1–1.5 pip spread is ~20–30% of the risk on every trade, and stops get tagged by noise. A wider SL buffer was the single most helpful change.
* Only ~13 trades per pair in 5 months: the sample is small, so treat USDJPY's positive result as noise, not an edge.
* About 60% of limit orders expire unfilled within 30 bars.

## Reproduce
```
git clone --depth 1 https://github.com/getdata-finance/eurusd-1m-ohlcv-forex-historical-data data/eurusd   # same for gbpusd, usdjpy, usdchf, audusd, usdcad
python3 sweepob_bt.py data 1.0 base                 # args: data dir, spread multiplier, tag, optional JSON param overrides
python3 sweepob_bt.py data 1.0 buf50 '{"sl_buffer_pts":50}'
```
