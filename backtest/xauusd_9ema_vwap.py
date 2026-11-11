#!/usr/bin/env python3
"""Backtest of the "9 EMA + VWAP" strategy guide on XAUUSD (spot gold).

Data: Dukascopy 1-minute BID and ASK candles (free, no key). Signals are
computed on mid prices; fills use the real bid/ask, so the spread is paid on
every trade. Nothing is optimised: every parameter below is fixed up front
and the same rules are applied to each timeframe and session.

Usage:
    python3 backtest/xauusd_9ema_vwap.py --start 2024-11-11 --end 2026-11-10
"""
import argparse
import datetime as dt
import lzma
import os
import struct
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
PAUSE = 2.0             # seconds between downloads; the feed throttles fast clients
PRICE_SCALE = 1000.0  # Dukascopy stores XAUUSD prices in 1/1000 USD

# ---- fixed strategy parameters (not tuned) ---------------------------------
EMA_LEN = 9
CONFIRM_BARS = 5        # cross: bars allowed for "a candle closes above the cross"
STRETCH_SIGMA = 2.0     # snapback: "too far from VWAP" = beyond the 2-sigma VWAP band
STRETCH_LOOKBACK = 5    # snapback: stretch must have happened within this many bars
SWING_LOOKBACK = 10     # snapback: stop goes beyond the swing extreme of this many bars
HOLD_MIN_BARS = 2       # hold: bars after the open before a retest counts

# The guide is written for US stocks: VWAP resets at 9:30 ET, crosses are only
# marked 9:30-16:00, cross/snapback are best 10:00-13:30, the hold is a
# first-30-minutes trade. Gold trades ~23h a day, so the same offsets are also
# applied to the London and Tokyo opens.
SESSIONS = {
    #  name     (timezone,           open,  close)
    "NewYork": ("America/New_York", "09:30", "16:00"),
    "London": ("Europe/London", "08:00", "16:30"),
    "Tokyo": ("Asia/Tokyo", "09:00", "15:00"),
}
HOLD_WINDOW_MIN = (0, 30)       # minutes after the session open
TREND_WINDOW_MIN = (30, 240)    # 10:00-13:30 ET equivalent
TIMEFRAMES = [1, 3, 5]
SETUPS = ["cross", "hold", "snapback"]


# ---- data -------------------------------------------------------------------
def _fetch(day, side, offline=False):
    """Return the raw .bi5 bytes for one day (b"" if none), or None if unavailable offline."""
    path = os.path.join(CACHE, f"{day:%Y%m%d}_{side}.bi5")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if offline:
        return None
    url = URL.format(y=day.year, m=day.month - 1, d=day.day, side=side)  # month is 0-based
    for attempt in range(40):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "xauusd-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raw = b""
                break
            print(f"  {day} {side}: HTTP {e.code}, backing off", flush=True)
            time.sleep(30)          # 503: the feed rate-limits; back off and retry
        except Exception as e:
            print(f"  {day} {side}: {type(e).__name__}, backing off", flush=True)
            time.sleep(30)
    else:
        raise RuntimeError(f"download failed: {url}")
    with open(path, "wb") as f:
        f.write(raw)
    print(f"  {day} {side}: {len(raw)} bytes", flush=True)
    time.sleep(PAUSE)
    return raw


def _decode(day, raw):
    if not raw:
        return None
    data = lzma.decompress(raw)
    rec = np.frombuffer(data, dtype=np.dtype(
        [("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")]))
    base = pd.Timestamp(day, tz="UTC")
    idx = base + pd.to_timedelta(rec["t"].astype(np.int64), unit="s")
    return pd.DataFrame({
        "o": rec["o"] / PRICE_SCALE, "h": rec["h"] / PRICE_SCALE,
        "l": rec["l"] / PRICE_SCALE, "c": rec["c"] / PRICE_SCALE,
        "v": rec["v"].astype(float)}, index=idx)


def load_minutes(start, end, offline=False):
    os.makedirs(CACHE, exist_ok=True)
    days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() != 5]      # gold is closed all of Saturday (UTC)
    frames = {"BID": [], "ASK": []}
    # Sequential and newest-first: the feed throttles parallel clients hard.
    for d in reversed(days):
        pair = {s: _fetch(d, s, offline) for s in ("BID", "ASK")}
        if any(v is None for v in pair.values()):
            continue
        for s, raw in pair.items():
            df = _decode(d, raw)
            if df is not None:
                frames[s].append(df)
    bid = pd.concat(frames["BID"]).sort_index().add_prefix("b")
    ask = pd.concat(frames["ASK"]).sort_index().add_prefix("a")
    m = bid.join(ask, how="inner")
    # Dukascopy pads closed-market minutes with flat zero-volume candles.
    m = m[(m["bv"] > 0) | (m["av"] > 0)]
    m["vol"] = m["bv"] + m["av"]
    return m.drop(columns=["bv", "av"])


def resample(m, minutes):
    if minutes == 1:
        return m.copy()
    agg = {"bo": "first", "bh": "max", "bl": "min", "bc": "last",
           "ao": "first", "ah": "max", "al": "min", "ac": "last", "vol": "sum"}
    return m.resample(f"{minutes}min").agg(agg).dropna(subset=["bo"])


def add_indicators(df):
    for k in "ohlc":
        df[k] = (df["b" + k] + df["a" + k]) / 2.0      # mid prices drive the signals
    df["ema"] = df["c"].ewm(span=EMA_LEN, adjust=False).mean()  # continuous, like TradingView
    return df


def session_slices(df, tz, open_s, close_s):
    """Yield (start, stop) integer positions of each session's bars."""
    local = df.index.tz_convert(tz)
    tod = local.hour * 60 + local.minute
    oh, om = map(int, open_s.split(":"))
    ch, cm = map(int, close_s.split(":"))
    o_min, c_min = oh * 60 + om, ch * 60 + cm
    inside = (tod >= o_min) & (tod < c_min)
    key = np.where(inside, local.year * 10000 + local.month * 100 + local.day, 0)
    change = np.flatnonzero(np.diff(key, prepend=0, append=0))
    for a, b in zip(change[:-1], change[1:]):
        if key[a] != 0 and b - a >= 20:
            yield a, b, (tod[a:b] - o_min)


# ---- strategy ---------------------------------------------------------------
def run_session(d, a, b, mins, tf, trades, meta):
    """Simulate all three setups (independent books) over one session."""
    o, h, l, c = d["o"][a:b], d["h"][a:b], d["l"][a:b], d["c"][a:b]
    bo, bh, bl, bc = d["bo"][a:b], d["bh"][a:b], d["bl"][a:b], d["bc"][a:b]
    ao, ah, al, ac = d["ao"][a:b], d["ah"][a:b], d["al"][a:b], d["ac"][a:b]
    ema, vol, ts = d["ema"][a:b], d["vol"][a:b], d["ts"][a:b]
    n = b - a

    # Session-anchored VWAP on typical price, plus its volume-weighted sigma.
    tp = (h + l + c) / 3.0
    cv = np.cumsum(vol)
    vwap = np.cumsum(tp * vol) / cv
    var = np.maximum(np.cumsum(tp * tp * vol) / cv - vwap * vwap, 0.0)
    sig = np.sqrt(var)
    diff = ema - vwap
    sess_open = o[0]

    pos = {s: None for s in SETUPS}       # open position per setup
    pending = {s: None for s in SETUPS}   # entry order to fill at this bar's open
    exit_next = {s: False for s in SETUPS}
    armed = 0          # +1 / -1 after a cross, waiting for the confirming close
    armed_until = -1
    cross_level = 0.0

    def close_pos(s, i, px_bid, px_ask, px_mid, why):
        p = pos[s]
        side = p["side"]
        exit_px = px_bid if side > 0 else px_ask
        trades.append({**meta, "setup": s, "side": "long" if side > 0 else "short",
                       "entry_time": p["time"], "exit_time": ts[i],
                       "entry": p["px"], "exit": exit_px,
                       "pnl": side * (exit_px - p["px"]),
                       "pnl_gross": side * (px_mid - p["mid"]),
                       "bars": i - p["i"], "reason": why})
        pos[s] = None

    for i in range(n):
        last = i == n - 1
        # 1) fills at this bar's open (signals came from the previous close)
        for s in SETUPS:
            if exit_next[s] and pos[s] is not None:
                close_pos(s, i, bo[i], ao[i], o[i], "ema_close")
            exit_next[s] = False
            if pending[s] is not None and pos[s] is None and not last:
                side, stop = pending[s]
                pos[s] = {"side": side, "px": ao[i] if side > 0 else bo[i], "mid": o[i],
                          "i": i, "time": ts[i], "stop": stop}
            pending[s] = None

        # 2) snapback intrabar exits: stop first (conservative), then VWAP target
        p = pos["snapback"]
        if p is not None and i >= 1:
            tgt = vwap[i - 1]
            if p["side"] > 0:
                if bl[i] <= p["stop"]:
                    px = min(bo[i], p["stop"]); close_pos("snapback", i, px, px, px, "stop")
                elif bh[i] >= tgt:
                    px = max(bo[i], tgt); close_pos("snapback", i, px, px, px, "target")
            else:
                if ah[i] >= p["stop"]:
                    px = max(ao[i], p["stop"]); close_pos("snapback", i, px, px, px, "stop")
                elif al[i] <= tgt:
                    px = min(ao[i], tgt); close_pos("snapback", i, px, px, px, "target")

        # 3) end of session: flat everything at the close
        if last:
            for s in SETUPS:
                if pos[s] is not None:
                    close_pos(s, i, bc[i], ac[i], c[i], "session_end")
            break

        # 4) the exit rule: a body closing back through the 9 EMA
        for s in ("cross", "hold"):
            p = pos[s]
            if p is not None:
                if (p["side"] > 0 and c[i] < ema[i]) or (p["side"] < 0 and c[i] > ema[i]):
                    exit_next[s] = True

        if i == 0:
            continue
        in_trend = TREND_WINDOW_MIN[0] <= mins[i] < TREND_WINDOW_MIN[1]
        in_hold = HOLD_WINDOW_MIN[0] <= mins[i] < HOLD_WINDOW_MIN[1]

        # 5) trading the cross
        if diff[i - 1] <= 0 < diff[i]:
            armed, armed_until, cross_level = 1, i + CONFIRM_BARS, vwap[i]
        elif diff[i - 1] >= 0 > diff[i]:
            armed, armed_until, cross_level = -1, i + CONFIRM_BARS, vwap[i]
        if armed and i > armed_until:
            armed = 0
        if armed and in_trend and pos["cross"] is None and not exit_next["cross"]:
            if armed > 0 and diff[i] > 0 and c[i] > ema[i] and c[i] > cross_level:
                pending["cross"] = (1, None); armed = 0
            elif armed < 0 and diff[i] < 0 and c[i] < ema[i] and c[i] < cross_level:
                pending["cross"] = (-1, None); armed = 0

        # 6) trading the hold: pop off the open, wick retest of the 9 EMA that holds
        if in_hold and i >= HOLD_MIN_BARS and pos["hold"] is None and not exit_next["hold"]:
            if c[i] > sess_open and diff[i] > 0 and l[i] <= ema[i] < c[i] and c[i] > vwap[i]:
                pending["hold"] = (1, None)
            elif c[i] < sess_open and diff[i] < 0 and h[i] >= ema[i] > c[i] and c[i] < vwap[i]:
                pending["hold"] = (-1, None)

        # 7) trading the distance: stretched past the band, turns back over the 9 EMA
        if in_trend and pos["snapback"] is None and i >= STRETCH_LOOKBACK and sig[i] > 0:
            j = i - STRETCH_LOOKBACK
            lo_band = vwap[j:i + 1] - STRETCH_SIGMA * sig[j:i + 1]
            hi_band = vwap[j:i + 1] + STRETCH_SIGMA * sig[j:i + 1]
            k = max(0, i - SWING_LOOKBACK)
            if (c[i - 1] < ema[i - 1] and c[i] > ema[i] and c[i] > o[i] and c[i] < vwap[i]
                    and (l[j:i + 1] < lo_band).any()):
                pending["snapback"] = (1, bl[k:i + 1].min())
            elif (c[i - 1] > ema[i - 1] and c[i] < ema[i] and c[i] < o[i] and c[i] > vwap[i]
                    and (h[j:i + 1] > hi_band).any()):
                pending["snapback"] = (-1, ah[k:i + 1].max())


def backtest(m):
    trades = []
    for tf in TIMEFRAMES:
        df = add_indicators(resample(m, tf))
        d = {k: df[k].to_numpy() for k in df.columns}
        d["ts"] = df.index.to_numpy()
        for name, (tz, op, cl) in SESSIONS.items():
            for a, b, mins in session_slices(df, tz, op, cl):
                run_session(d, a, b, mins, tf, trades, {"tf": f"{tf}m", "session": name})
    return pd.DataFrame(trades)


# ---- reporting --------------------------------------------------------------
def stats(t):
    if t.empty:
        return pd.Series(dtype=float)
    pnl = t["pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = t.sort_values("exit_time")["pnl"].cumsum()
    return pd.Series({
        "trades": len(t),
        "win_%": 100 * len(wins) / len(t),
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
        "exp_net": pnl.mean(),
        "exp_gross": t["pnl_gross"].mean(),
        "total_net": pnl.sum(),
        "total_gross": t["pnl_gross"].sum(),
        "max_dd": (eq.cummax().clip(lower=0) - eq).max(),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-11-11")
    ap.add_argument("--end", default="2026-11-10")
    ap.add_argument("--offline", action="store_true", help="use only already-cached days")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    os.makedirs(args.out, exist_ok=True)

    m = load_minutes(start, end, args.offline)
    spread = (m["ac"] - m["bc"])
    print(f"bars: {len(m):,}  {m.index[0]} -> {m.index[-1]}")
    print(f"price: {m['bc'].iloc[0]:.2f} -> {m['bc'].iloc[-1]:.2f}")
    print(f"spread USD: median {spread.median():.3f}  mean {spread.mean():.3f}  p95 {spread.quantile(.95):.3f}")

    t = backtest(m)
    t.to_csv(os.path.join(args.out, "trades.csv.gz"), index=False)
    summary = t.groupby(["setup", "tf", "session"]).apply(stats, include_groups=False).reset_index()
    summary.to_csv(os.path.join(args.out, "summary.csv"), index=False, float_format="%.4f")
    pd.set_option("display.width", 220)
    print(summary.round(2).to_string(index=False))
    side = t.groupby(["setup", "side"]).apply(stats, include_groups=False).reset_index()
    side.to_csv(os.path.join(args.out, "by_side.csv"), index=False, float_format="%.4f")
    print(side.round(2).to_string(index=False))
    with open(os.path.join(args.out, "data_info.txt"), "w") as f:
        f.write(f"bars={len(m)}\nfirst={m.index[0]}\nlast={m.index[-1]}\n"
                f"first_price={m['bc'].iloc[0]:.2f}\nlast_price={m['bc'].iloc[-1]:.2f}\n"
                f"spread_median={spread.median():.3f}\nspread_mean={spread.mean():.3f}\n"
                f"spread_p95={spread.quantile(.95):.3f}\n")


if __name__ == "__main__":
    main()
