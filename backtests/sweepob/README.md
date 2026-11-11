# SweepOB EA – backtests, bug fixes and RR / trailing-stop tests

`SweepOB_EA.mq5` (now v1.20) is ported bar-for-bar to Python in `sweepob_bt.py`, and every result here comes from that port.
MetaTrader 5 can't run in this environment, so none of this is a Strategy Tester run, and the v1.10/v1.20 EA has **not been compiled**.
Compile it in MetaEditor before you use it.

## Data
| Set | Pairs | Period | Spread |
|---|---|---|---|
| Majors | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD | 2026-03-26 → 2026-09-25 (M1, getdata-finance free samples, MIT) | fixed 1.0–1.6 pips |
| GBPCAD | GBPCAD | 2018-01-01 → 2018-06-04 (Dukascopy ticks from `FX-Data/FX-Data-GBPCAD-DS`, converted with `ticks2m1.py`) | real per-minute spread, median 3.1 pips |

No 2026 GBPCAD data was reachable, so GBPCAD is tested on a different year and doubles as an out-of-sample market.
The first 21 days of each set are warm-up.

**In-sample (IS)** = majors April–June 2026. **Out-of-sample (OOS)** = majors July–September 2026.

## Simulation
* Broker clock: "New York close" (GMT+2 in winter, GMT+3 during US DST). Killzones are evaluated in true IST.
* Order handling runs on M1 like MT5's "1 minute OHLC" mode (O→L→H→C for up bars, O→H→L→C for down bars). Bars are BID and ASK = BID + spread.
* Results are in R, the multiple of the risk on each trade (scaled by the risk multiplier during a loss streak).

## Bugs fixed in v1.10
1. **Tester killzones were an hour early during US DST.** `InpTesterGMTOffsetMin` was one fixed offset for the whole year. The new `InpTesterTZ = TZ_NY_CLOSE` switches GMT+2/GMT+3 on the US DST dates. `TZ_FIXED` keeps the old behaviour. Live trading was never affected.
2. **The armed H1 order block was stored as an array index.** The OB array shifts once it holds 200 entries, so the index could point at the wrong block and the mitigation-cancel check would test the wrong zone. It's now stored by candle time (`g_zOBT`) and looked up with `FindOBByTime`.
3. **`DailyStats` counted open positions on other symbols** (same magic number) in this chart's daily P&L, which could trigger the daily-loss stop. It now filters by symbol.
4. **The lot cap overrode the risk % without telling you.** `InpMaxLot = 0.02` is still the default, so your safety cap isn't loosened. The EA now prints a warning when the cap makes the real risk smaller than `InpRiskPercent`.
5. **Minor:** the out-of-window order-block scan in `FindOB` broke ties toward the older candle, while the in-window scan picks the newer one. Both now pick the newer candle.

## New options in v1.10
| Input | Default | Purpose |
|---|---|---|
| `InpSLBufferPoints` | **50** (was 20) | Wider SL buffer. It was the most consistent single improvement. |
| `InpMinSLPoints` | 0 | Push the SL out to a minimum size. |
| `InpEntryMode` | limit | `ENTRY_MARKET` enters on the CHoCH bar instead of the OB limit (tested worse). |
| `InpReduceAfterLosses` / `InpReducedRiskMult` | 2 / 0.5 | Halve risk after 2 losses in a row; full risk returns after a win. |
| `InpPauseAfterLosses` / `InpPauseSetups` | 3 / 5 | After 3 losses in a row, skip the next 5 valid setups. |
| `InpTesterTZ` | NY close | See bug 1. |

There is deliberately no martingale or "recover the drawdown" sizing (see below).

## Results

### Before vs after (all pairs)
| Config | IS R | OOS R | GBPCAD R | Total R | Trades | PF | Max DD (R) |
|---|---|---|---|---|---|---|---|
| v1.00 defaults | -12.0 | -8.0 | -13.0 | **-33.0** | 105 | 0.48 | 41.0 |
| v1.00 with the tester clock bug | -13.0 | -1.0 | -9.0 | -23.0 | 107 | 0.61 | 34.0 |
| v1.10, SL buffer 50 only | -3.0 | -3.0 | -10.0 | -16.0 | 104 | 0.72 | 25.0 |
| **v1.10 defaults** (buffer 50 + loss-streak protection) | -2.5 | -4.0 | -5.5 | **-12.0** | 85 | 0.69 | 18.5 |
| v1.10 + PDH/PDL zones only (`InpUseHTFOB=false`) | +4.0 | +4.5 | -2.5 | **+6.0** | 36 | 1.40 | 5.5 |

Fixing the clock bug puts the killzones where you meant them, but it doesn't make the strategy better. The hour-early version was just as negative.

### GBPCAD (2018)
24 trades with v1.00 defaults: 1 TP, 16 SL, 7 break-even, **-13R**. With v1.10 defaults: 19 trades, -5.5R.
A ~3-pip spread against 5–8-pip M1 stops is too expensive. This setup isn't suited to GBPCAD at M1 unless your broker's spread is well under 2 pips.

### Other rule changes tested (`grid1`/`grid2`)
None was profitable in every segment. Clearly worse: market entry, an M1 structure length of 3, trading both directions, no break-even, the London+NY killzones only.
M5/M15 as the entry timeframe produced too few trades (7–30) to judge. Full tables: `grid1_results.csv`, `grid2_results.csv`, `grid3_results.csv`.

### Money-management "recovery" (applied to the trade list, 0.5% base risk)
| Trades | Scheme | Return % | Max DD % | Largest risk on one trade |
|---|---|---|---|---|
| v1.00 defaults | fixed 0.5% | -15.4 | 18.7 | 0.5% |
| v1.00 defaults | martingale (x2 after a loss, 4 steps) | **-90.1** | 92.9 | 8% |
| v1.00 defaults | size to win back the drawdown in one TP | **-91.8** | 95.4 | 8% |
| v1.00 defaults | halve risk after 2 losses | -8.5 | 11.4 | 0.5% |
| v1.00 defaults | pause 5 setups after 3 losses | -7.3 | 10.9 | 0.5% |

Martingale and drawdown-recovery sizing turn a slow loss into a near wipe-out, because the strategy's expectancy is negative and it has long losing streaks (an 11% win rate at RR 3).
They only "work" on a rule set that is already about break-even, and then only by luck: one run made +40% while risking 8% on a single trade.
The only recovery that actually helps is the defensive kind (cut risk or pause during a streak), which is what v1.10 adds.


## v1.20 – lower reward:risk and trailing stop (`grid4*.json`)
Every row uses the v1.10 base: SL buffer 50 points plus loss-streak protection. R totals: IS = majors Apr–Jun 2026, OOS = majors Jul–Sep 2026.

| Exit | Win % | IS R | OOS R | GBPCAD R | Total R | PF | Max DD (R) |
|---|---|---|---|---|---|---|---|
| 1:3, BE at 1R (v1.10) | 12.9 | -2.5 | -4.0 | -5.5 | -12.0 | 0.69 | 18.5 |
| 1:2 | – | -6.0 | -7.0 | -2.5 | -15.5 | 0.70 | 22.5 |
| 1:1.5 | 33.8 | -10.0 | -4.5 | +4.5 | -10.0 | 0.79 | 19.8 |
| **1:1** | **48.9** | -2.0 | **+2.0** | -1.5 | **-1.5** | **0.97** | **11.5** |
| 1:1, BE at 0.5R | – | -4.5 | +0.5 | -5.5 | -9.5 | 0.74 | 12.0 |
| 1:0.75 | 50.5 | -3.4 | -2.9 | -3.9 | -10.1 | 0.78 | 13.9 |
| 1:0.5 | 59.4 | -2.0 | -4.2 | -5.0 | -11.3 | 0.71 | 14.3 |
| Trail: start 1R, distance 0.5R, no TP | 48.9 | -3.8 | -1.8 | -2.8 | -8.4 | 0.81 | 14.8 |
| Trail: start 1R, distance 1R, no TP | – | -3.3 | -4.4 | -6.0 | -13.6 | 0.68 | 18.0 |
| Trail: start 0.5R, distance 0.5R, no TP | – | -6.0 | -1.9 | -6.2 | -14.2 | 0.63 | 18.5 |
| Trail: start 0.5R, distance 0.25R, no TP | – | -5.2 | -4.6 | -6.1 | -15.8 | 0.60 | 17.8 |
| Trail: start 1.5R, distance 1R, no TP | – | -3.2 | -8.8 | -0.8 | -12.8 | 0.72 | 18.7 |
| 1:1 + trail 0.5R / 0.25R | – | -4.4 | -4.4 | -5.9 | -14.6 | 0.63 | 16.6 |
| 1:2 + trail 1R / 0.5R | 48.9 | -2.6 | -1.5 | -2.6 | -6.7 | 0.85 | 13.6 |
| 1:3 + trail 1R / 1R | – | 0.0 | -4.7 | -6.0 | -10.7 | 0.76 | 14.0 |

Win rate needed to break even: 66.7% at 1:0.5, 57.1% at 1:0.75, 50% at 1:1.
The low ratios win more often (59% at 1:0.5) but stay short of break-even, so the spread decides the result.
**1:1 is the best exit tested** and is close to break-even (49% wins). Trailing stops cut the losers no faster than a fixed SL. They mostly trim winners, and none beat a fixed 1:1 target.
With PDH/PDL zones only, every exit lands between -9R and +6R on 35–40 trades (`grid4_pd_results.csv`), which is too few trades to tell apart.

**v1.20 defaults:** `InpRR = 1.0`, `InpBreakEvenR = 0` (break-even at +1R is pointless with a 1R target).
Trailing is available but off: `InpTrailStartR` (0 = off), `InpTrailDistR`, `InpTrailStepPoints`. `InpRR = 0` means no fixed TP, leaving the trailing stop to close the trade.
The trail is measured from the original SL distance, which the EA reads from the opening order in history, so it still works after the SL has moved.

## Bottom line
* v1.00 → v1.10 cut the loss from -33R to -12R. v1.20 (1:1) brings it to about break-even (-1.5R over 92 trades, max DD 11.5R).
  That is an improvement, not an edge: the strategy is still not profitable on these pairs.
* Using only PDH/PDL zones plus loss-streak protection is the only version that came out positive in-sample and out-of-sample on the majors (+6R over 36 trades). It still lost on GBPCAD, and 36 trades is far too few to trust.
  Forward-test it on a demo before risking money.
* Keep the risk per trade small. Don't add martingale.

## Reproduce
```
# majors: git clone --depth 1 https://github.com/getdata-finance/<pair>-1m-ohlcv-forex-historical-data data/<pair>
# GBPCAD: download hourly tick CSVs from github.com/FX-Data/FX-Data-GBPCAD-DS (branch GBPCAD-2018), then
python3 ticks2m1.py <tick_dir> data/gbpcad/GBPCAD_1m.csv
python3 sweepob_bt.py data mytag '{"sl_buffer_pts":50,"use_htf_ob":false}'   # single run
python3 experiments.py grid3.json                                              # IS / OOS / GBPCAD table
python3 recovery.py runs/trades_v100_defaults.csv                              # money-management schemes
```
