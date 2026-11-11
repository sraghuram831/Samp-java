# SweepOB EA – backtests, bug fixes (v1.10) and loss-recovery tests

`SweepOB_EA.mq5` (now v1.10) is ported bar-for-bar to Python in `sweepob_bt.py`, and every result here comes from that port.
MetaTrader 5 can't run in this environment, so none of this is a Strategy Tester run, and the v1.10 EA has **not been compiled**.
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

## Bottom line
* The v1.10 defaults cut the loss by about 64% (-33R → -12R) and halve the drawdown, but the strategy is still negative on these pairs.
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
