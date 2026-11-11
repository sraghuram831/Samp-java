"""
Python port of SweepOB_EA.mq5 for backtesting on M1 OHLC data.

Faithful to the EA's logic:
  * CSMC engine (LuxAlgo legs, BOS/CHoCH, order blocks) on H1 (len 10) and M1 internal (len 5)
  * H1 trend bias, H1 order block POIs + previous-day high/low POIs
  * Killzones in IST, arming, M1 sweep of last internal pivot, M1 CHoCH, M1 OB limit entry
  * SL beyond OB / sweep extreme + buffer, TP = RR, break-even at +1R, limit expiry 30 bars
  * One exposure at a time, max 3 entries per (server) day, one setup per killzone
Fills simulated like MT5 "1 minute OHLC" mode: tick path O -> L -> H -> C (bull bar)
or O -> H -> L -> C (bear bar). Bars are treated as BID; ASK = BID + spread.
Server time = UTC+3 (summer, matches the US-DST data window); killzones use true IST.
"""
import sys, math
import numpy as np
import pandas as pd

H1 = 3600
M1 = 60

P = dict(htf_len=10, ltf_len=5, max_htf_ob=6, use_htf_ob=True, use_prev_day=True,
         require_sweep=True, require_choch=True, max_bars_in_zone=60,
         kz=["05:30-08:30", "12:30-14:30", "18:30-20:30"],
         ob_level="near", limit_expiry_bars=30, sl_buffer_pts=20, max_sl_pts=0,
         rr=3.0, be_r=1.0, max_trades_day=3, one_per_kz=True, bias_mode="htf")

SERVER_OFF = 3 * 3600
IST_OFF = 19800


class SMC:
    def __init__(self, t, o, h, l, c, length, sec, keep_ob=False, keep_trend=False, max_ob=6):
        self.t, self.o, self.h, self.l, self.c = t, o, h, l, c
        self.len = max(length, 2); self.sec = sec
        self.keep_ob = keep_ob; self.keep_trend = keep_trend; self.max_ob = max_ob
        self.leg = -1; self.hiLvl = 0.0; self.loLvl = 0.0; self.hiT = 0; self.loT = 0
        self.hiI = -1; self.loI = -1
        self.hiX = True; self.loX = True; self.trend = 0
        self.ev = {}          # bar time -> list of events
        self.ob = []          # dicts
        self.trend_arr = np.zeros(len(t), dtype=np.int8)
        self.hi_arr = np.zeros(len(t)); self.lo_arr = np.zeros(len(t))

    def find_ob(self, k, d, pivI):
        # candles from pivot .. k-1, scanned from k-1 backwards; strict compare (ties -> later bar)
        best = k - 1
        for i in range(k - 2, pivI - 1, -1):
            if d == -1 and self.h[i] > self.h[best]: best = i
            if d == 1 and self.l[i] < self.l[best]: best = i
        if best < pivI: return None
        return self.h[best], self.l[best], self.t[best]

    def run(self):
        n = len(self.t); L = self.len
        h, l, c, t = self.h, self.l, self.c, self.t
        for k in range(L + 2, n):
            j = k - L
            mx = h[j + 1:k + 1].max(); mn = l[j + 1:k + 1].min()
            newLeg = self.leg
            if h[j] > mx: newLeg = 0
            elif l[j] < mn: newLeg = 1
            if newLeg != self.leg:
                if newLeg == 0: self.hiLvl, self.hiT, self.hiI, self.hiX = h[j], t[j], j, False
                else:           self.loLvl, self.loT, self.loI, self.loX = l[j], t[j], j, False
                self.leg = newLeg
            if self.keep_ob:
                for ob in self.ob:
                    if ob['tMit'] != 0: continue
                    if (ob['dir'] == -1 and c[k] > ob['top']) or (ob['dir'] == 1 and c[k] < ob['bot']):
                        ob['tMit'] = t[k] + self.sec
            if not self.hiX and self.hiT > 0 and c[k] > self.hiLvl:
                ch = self.trend == -1; self.hiX = True; self.trend = 1
                self.on_break(k, 1, ch, self.hiLvl, self.hiT, self.hiI)
            if not self.loX and self.loT > 0 and c[k] < self.loLvl:
                ch = self.trend == 1; self.loX = True; self.trend = -1
                self.on_break(k, -1, ch, self.loLvl, self.loT, self.loI)
            self.trend_arr[k] = self.trend
            self.hi_arr[k] = self.hiLvl; self.lo_arr[k] = self.loLvl

    def on_break(self, k, d, ch, lvl, pT, pI):
        r = self.find_ob(k, d, pI)
        e = dict(t=self.t[k], dir=d, choch=ch, level=lvl, pivT=pT,
                 obTop=r[0] if r else 0, obBot=r[1] if r else 0, obT=r[2] if r else 0)
        self.ev.setdefault(self.t[k], []).append(e)
        if self.keep_ob and r:
            self.ob.append(dict(t=r[2], tBreak=self.t[k], tMit=0, dir=d, top=r[0], bot=r[1]))
            cnt = 0
            for ob in reversed(self.ob):
                if ob['dir'] != d or ob['tMit'] != 0: continue
                cnt += 1
                if cnt > self.max_ob: ob['tMit'] = self.t[k]

    def trend_at(self, tq):
        # latest bar with t + sec <= tq
        i = np.searchsorted(self.t, tq - self.sec, side='right') - 1
        return int(self.trend_arr[i]) if i >= 0 else 0

    def find_touched_ob(self, d, tq, hi, lo):
        for idx in range(len(self.ob) - 1, -1, -1):
            ob = self.ob[idx]
            if ob['dir'] != d: continue
            if ob['tBreak'] + self.sec > tq: continue
            if ob['tMit'] != 0 and ob['tMit'] <= tq: continue
            if hi >= ob['bot'] and lo <= ob['top']: return idx
        return -1


def parse_kz(s):
    a = int(s[0:2]) * 60 + int(s[3:5]); b = int(s[6:8]) * 60 + int(s[9:11])
    return a, b


def backtest(csv, point, spread_pips, pip, warmup_days=21, params=P, label=""):
    df = pd.read_csv(csv)
    ts = (pd.to_datetime(df['datetime'], utc=True) - pd.Timestamp('1970-01-01', tz='UTC')) // pd.Timedelta('1s')
    # server time = UTC+3
    t = (ts.values + SERVER_OFF).astype(np.int64)
    o, h, l, c = (df[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
    spread = spread_pips * pip
    digits = round(-math.log10(point))

    # H1 bars (server time aligned)
    hb = pd.DataFrame({'t': t // H1 * H1, 'o': o, 'h': h, 'l': l, 'c': c}).groupby('t').agg(
        o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last')).reset_index()
    htf = SMC(hb.t.values, hb.o.values, hb.h.values, hb.l.values, hb.c.values,
              params['htf_len'], H1, keep_ob=True, keep_trend=True, max_ob=params['max_htf_ob'])
    htf.run()
    # D1 bars (server midnight)
    db = pd.DataFrame({'t': t // 86400 * 86400, 'h': h, 'l': l}).groupby('t').agg(h=('h', 'max'), l=('l', 'min'))
    dT = db.index.values; dH = db.h.values; dL = db.l.values

    ltf = SMC(t, o, h, l, c, params['ltf_len'], M1)
    ltf.run()

    kzs = [parse_kz(s) for s in params['kz']]

    def ist_min(st):
        s = st - SERVER_OFF + IST_OFF
        return (s % 86400) // 60

    def ist_day(st): return (st - SERVER_OFF + IST_OFF) // 86400

    def kz_index(st):
        m = ist_min(st)
        for i, (a, b) in enumerate(kzs):
            if a < b:
                if a <= m < b: return i
            elif m >= a or m < b: return i
        return -1

    def npx(p): return round(round(p / point) * point, digits)

    start_live = t[0] + warmup_days * 86400
    state = 0; g = {}
    used = set(); kz_traded = [-1, -1, -1]
    prevH = prevL = 0.0
    order = None          # pending limit
    pos = None            # open position
    trades = []
    entries_per_day = {}
    stats = dict(setups=0, skipped_exposure=0, skipped_day=0, expired=0, invalid=0, market=0, limit=0)

    def plan(d, e, ext):
        edge = e['obBot'] if d == -1 else e['obTop']
        mid = (e['obTop'] + e['obBot']) / 2
        entry = edge if params['ob_level'] == 'near' else mid
        far = max(e['obTop'], ext) if d == -1 else min(e['obBot'], ext)
        sl = far + params['sl_buffer_pts'] * point if d == -1 else far - params['sl_buffer_pts'] * point
        if (d == -1 and sl <= entry) or (d == 1 and sl >= entry): return None
        risk = abs(entry - sl)
        if params['max_sl_pts'] > 0 and risk / point > params['max_sl_pts']: return None
        tp = entry - params['rr'] * risk if d == -1 else entry + params['rr'] * risk
        return entry, sl, tp

    def close_pos(exit_px, tt, why):
        nonlocal pos
        d = pos['dir']
        pnl = (exit_px - pos['entry']) * d
        trades.append(dict(pair=label, dir=d, open_t=pos['t'], close_t=tt, entry=pos['entry'],
                           sl0=pos['sl0'], tp=pos['tp'], exit=exit_px, why=why, kind=pos['kind'],
                           R=pnl / pos['risk'], pips=pnl / pip, risk_pips=pos['risk'] / pip))
        pos = None

    def process_price(p, tt):
        """one tick at bid p (ManageOpen + order fills, MT5 order)"""
        nonlocal order, pos
        ask = p + spread
        if order is not None:
            d = order['dir']
            if tt - order['setup'] > params['limit_expiry_bars'] * M1:
                order = None; stats['expired'] += 1
            else:
                fill = (d == -1 and p >= order['entry']) or (d == 1 and ask <= order['entry'])
                if fill:
                    pos = dict(dir=d, entry=order['entry'], sl=order['sl'], sl0=order['sl'], tp=order['tp'],
                               risk=abs(order['entry'] - order['sl']), t=tt, kind='limit')
                    day = tt // 86400; entries_per_day[day] = entries_per_day.get(day, 0) + 1
                    order = None
        if pos is not None:
            d = pos['dir']
            if d == 1:
                if p <= pos['sl']: return close_pos(pos['sl'], tt, 'BE' if pos['sl'] == pos['entry'] else 'SL')
                if p >= pos['tp']: return close_pos(pos['tp'], tt, 'TP')
                if params['be_r'] > 0 and pos['sl'] < pos['entry'] and p - pos['entry'] >= params['be_r'] * (pos['entry'] - pos['sl']):
                    pos['sl'] = pos['entry']
            else:
                if ask >= pos['sl']: return close_pos(pos['sl'], tt, 'BE' if pos['sl'] == pos['entry'] else 'SL')
                if ask <= pos['tp']: return close_pos(pos['tp'], tt, 'TP')
                if params['be_r'] > 0 and pos['sl'] > pos['entry'] and pos['entry'] - ask >= params['be_r'] * (pos['sl'] - pos['entry']):
                    pos['sl'] = pos['entry']

    def run_bar_ticks(i):
        """MT5 1-minute-OHLC style path; the first point (open) is processed separately"""
        bull = c[i] >= o[i]
        pts = [l[i], h[i], c[i]] if bull else [h[i], l[i], c[i]]
        prev = o[i]
        for q in pts:
            # sweep monotone segment prev->q; check every level crossed in order
            levels = []
            if order is not None: levels.append(order['entry'] - (spread if order['dir'] == 1 else 0))
            if pos is not None:
                if pos['dir'] == 1:
                    levels += [pos['sl'], pos['tp'], pos['entry'] + params['be_r'] * (pos['entry'] - pos['sl'])]
                else:
                    levels += [pos['sl'] - spread, pos['tp'] - spread,
                               pos['entry'] - params['be_r'] * (pos['sl'] - pos['entry']) - spread]
            lo_, hi_ = min(prev, q), max(prev, q)
            hits = sorted([x for x in levels if lo_ <= x <= hi_], reverse=(q < prev))
            for x in hits: process_price(x, t[i] + 30)
            process_price(q, t[i] + 59)
            # one more pass in case a fill/BE opened new levels on this same segment
            if order is None and pos is not None:
                levels2 = [pos['sl'], pos['tp']] if pos['dir'] == 1 else [pos['sl'] - spread, pos['tp'] - spread]
                for x in sorted([x for x in levels2 if lo_ <= x <= hi_], reverse=(q < prev)):
                    if pos is None: break
                    process_price(x, t[i] + 59)
            prev = q

    def own_exposure(): return order is not None or pos is not None

    def try_enter(d, e, ext, i_next):
        nonlocal order, pos
        stats['setups'] += 1
        tt = t[i_next]
        if own_exposure(): stats['skipped_exposure'] += 1; return False
        if entries_per_day.get(tt // 86400, 0) >= params['max_trades_day']: stats['skipped_day'] += 1; return False
        pl = plan(d, e, ext)
        if pl is None: stats['invalid'] += 1; return False
        entry, sl, tp = pl
        bid = o[i_next]; ask = bid + spread
        market = (bid >= entry) if d == -1 else (ask <= entry)
        if market:
            entry = bid if d == -1 else ask
            if (d == -1 and sl <= entry) or (d == 1 and sl >= entry): stats['invalid'] += 1; return False
            risk = abs(entry - sl)
            tp = entry - params['rr'] * risk if d == -1 else entry + params['rr'] * risk
        entry, sl, tp = npx(entry), npx(sl), npx(tp)
        if market:
            pos = dict(dir=d, entry=entry, sl=sl, sl0=sl, tp=tp, risk=abs(entry - sl), t=tt, kind='market')
            day = tt // 86400; entries_per_day[day] = entries_per_day.get(day, 0) + 1
            stats['market'] += 1
        else:
            order = dict(dir=d, entry=entry, sl=sl, tp=tp, setup=tt)
            stats['limit'] += 1
        return True

    def arm(d, top, bot, obIdx, key, kz, i):
        nonlocal state
        state = 1
        g.update(dir=d, ob=obIdx, ext=h[i] if d == -1 else l[i], bars=0, kz=kz)
        liq = ltf.hi_arr[i] if d == -1 else ltf.lo_arr[i]
        g['liq'] = liq
        g['swept'] = (liq <= 0) or (h[i] > liq if d == -1 else l[i] < liq)
        used.add(key)

    n = len(t)
    for i in range(n):
        # --- new bar opens: strategy on previous closed bar (OnNewLTFBar), then ManageOpen on open tick
        if i > 0:
            b = i - 1
            live = t[i] >= start_live
            closeT = t[b] + M1
            kz = kz_index(t[b])
            bias = htf.trend_at(closeT)
            skip_new = False
            if state == 1:
                g['bars'] += 1
                g['ext'] = max(g['ext'], h[b]) if g['dir'] == -1 else min(g['ext'], l[b])
                if not g['swept'] and g['liq'] > 0 and ((g['dir'] == -1 and h[b] > g['liq']) or (g['dir'] == 1 and l[b] < g['liq'])):
                    g['swept'] = True
                cancel = g['bars'] > params['max_bars_in_zone']
                if params['bias_mode'] == 'htf' and bias != g['dir']: cancel = True
                if g['ob'] >= 0:
                    tm = htf.ob[g['ob']]['tMit']
                    if tm != 0 and tm <= closeT: cancel = True
                fired = False
                if not cancel:
                    for e in ltf.ev.get(t[b], []):
                        if e['dir'] != g['dir']: continue
                        if (not params['require_choch'] or e['choch']) and (not params['require_sweep'] or g['swept']) and e['obT'] > 0:
                            done = False
                            if live: done = try_enter(g['dir'], e, g['ext'], i)
                            else: done = plan(g['dir'], e, g['ext']) is not None
                            if done and g['kz'] >= 0: kz_traded[g['kz']] = ist_day(t[b]) * 10 + g['kz']
                            state = 0; fired = True
                        break
                if fired: skip_new = True
                elif cancel: state = 0
                else: skip_new = True
            if not skip_new and kz >= 0 and not (params['one_per_kz'] and kz_traded[kz] == ist_day(t[b]) * 10 + kz):
                canSell = params['bias_mode'] == 'both' or bias == -1
                canBuy = params['bias_mode'] == 'both' or bias == 1
                for d in (-1, 1):
                    if (d == -1 and not canSell) or (d == 1 and not canBuy): continue
                    armed = False
                    if params['use_htf_ob']:
                        k = htf.find_touched_ob(d, closeT, h[b], l[b])
                        if k >= 0:
                            key = ('O', htf.ob[k]['t'], d)
                            if key not in used:
                                arm(d, htf.ob[k]['top'], htf.ob[k]['bot'], k, key, kz, b); armed = True
                    if not armed and params['use_prev_day']:
                        di = np.searchsorted(dT, t[b], side='right') - 1
                        if di >= 1:
                            lvl = dH[di - 1] if d == -1 else dL[di - 1]
                            key = ('D', dT[di - 1], d)
                            crossed = (h[b] > lvl and prevH <= lvl) if d == -1 else (l[b] < lvl and prevL >= lvl)
                            if lvl > 0 and crossed and key not in used:
                                arm(d, lvl, lvl, -1, key, kz, b); armed = True
                    if armed: break
            prevH, prevL = h[b], l[b]
        # open tick
        process_price(o[i], t[i])
        if pos is not None or order is not None:
            run_bar_ticks(i)
    if pos is not None: close_pos(c[-1], t[-1], 'EOD')
    return trades, stats


PAIRS = {  # csv, point, all-in spread (pips), pip size
    'EURUSD': ('eurusd/EURUSD_1m.csv', 1e-5, 1.0, 1e-4),
    'GBPUSD': ('gbpusd/GBPUSD_1m.csv', 1e-5, 1.4, 1e-4),
    'USDJPY': ('usdjpy/USDJPY_1m.csv', 1e-3, 1.2, 1e-2),
    'USDCHF': ('usdchf/USDCHF_1m.csv', 1e-5, 1.5, 1e-4),
    'AUDUSD': ('audusd/AUDUSD_1m.csv', 1e-5, 1.2, 1e-4),
    'USDCAD': ('usdcad/USDCAD_1m.csv', 1e-5, 1.6, 1e-4),
}

if __name__ == '__main__':
    import os, json
    base = sys.argv[1]
    spread_mult = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    tag = sys.argv[3] if len(sys.argv) > 3 else 'base'
    over = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    params = dict(P); params.update(over)
    allt = []
    for pair, (f, pt, sp, pip) in PAIRS.items():
        tr, st = backtest(os.path.join(base, f), pt, sp * spread_mult, pip, params=params, label=pair)
        allt += tr
        print(pair, st, len(tr), file=sys.stderr)
    pd.DataFrame(allt).to_csv(f'trades_{tag}.csv', index=False)
