"""
Python port of SweepOB_EA.mq5 (v1.10) for backtesting on M1 OHLC data.

Faithful to the EA's logic:
  * CSMC engine (LuxAlgo legs, BOS/CHoCH, order blocks) on HTF (H1, len 10) and LTF internal (len 5)
  * H1 trend bias, H1 order block POIs + previous-day high/low POIs
  * Killzones in IST, arming, LTF sweep of last internal pivot, LTF CHoCH, LTF OB limit entry
  * SL beyond OB / sweep extreme + buffer, TP = RR, break-even at +R, limit expiry N LTF bars
  * One exposure at a time, max N entries per (server) day, one setup per killzone
  * v1.10 options: min SL size, killzone on/off, market entry, POI selection, max spread
Order handling always runs on M1 bars, like MT5 "1 minute OHLC" mode: tick path O -> L -> H -> C
(up bar) or O -> H -> L -> C (down bar). Bars are BID; ASK = BID + spread (per-minute when the
data has a `spread` column, otherwise a constant).
Server time follows the usual "New York close" broker clock: UTC+2 in winter, UTC+3 while US DST.
Killzones are evaluated in true IST.
"""
import sys, math, os, json
import numpy as np
import pandas as pd

H1 = 3600
IST_OFF = 19800

P = dict(htf_len=10, ltf_len=5, ltf_sec=60, max_htf_ob=6, use_htf_ob=True, use_prev_day=True,
         require_sweep=True, require_choch=True, max_bars_in_zone=60,
         kz=["05:30-08:30", "12:30-14:30", "18:30-20:30"], kz_on=[True, True, True],
         ob_level="near", entry_mode="limit", limit_expiry_bars=30, sl_buffer_pts=20, max_sl_pts=0,
         min_sl_pts=0, rr=3.0, be_r=1.0, max_trades_day=3, one_per_kz=True, bias_mode="htf",
         max_spread_pts=0, reduce_after=0, reduced_mult=0.5, pause_after=0, pause_setups=5)


class SMC:
    def __init__(self, t, o, h, l, c, length, sec, keep_ob=False, max_ob=6):
        self.t, self.o, self.h, self.l, self.c = t, o, h, l, c
        self.len = max(length, 2); self.sec = sec
        self.keep_ob = keep_ob; self.max_ob = max_ob
        self.leg = -1; self.hiLvl = 0.0; self.loLvl = 0.0; self.hiT = 0; self.loT = 0
        self.hiI = -1; self.loI = -1
        self.hiX = True; self.loX = True; self.trend = 0
        self.ev = {}
        self.ob = []
        self.trend_arr = np.zeros(len(t), dtype=np.int8)
        self.hi_arr = np.zeros(len(t)); self.lo_arr = np.zeros(len(t))

    def find_ob(self, k, d, pivI):
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
    return int(s[0:2]) * 60 + int(s[3:5]), int(s[6:8]) * 60 + int(s[9:11])


def load(csv):
    df = pd.read_csv(csv)
    dt = pd.to_datetime(df['datetime'], utc=True)
    utc = ((dt - pd.Timestamp('1970-01-01', tz='UTC')) // pd.Timedelta('1s')).values.astype(np.int64)
    ny_off = dt.dt.tz_convert('America/New_York').map(lambda x: x.utcoffset().total_seconds()).values
    srv_off = (ny_off + 7 * 3600).astype(np.int64)        # NY close: UTC+2 winter / UTC+3 summer
    spr = df['spread'].values.astype(float) if 'spread' in df else None
    return utc, srv_off, *(df[x].values.astype(float) for x in ('open', 'high', 'low', 'close')), spr


def backtest(data, point, spread_pips, pip, warmup_days=21, params=P, label=""):
    utc, srv_off, o, h, l, c, spr_col = data
    t = utc + srv_off                                       # server time of each M1 bar
    spread = spr_col if spr_col is not None else np.full(len(t), spread_pips * pip)
    digits = round(-math.log10(point))
    LS = params['ltf_sec']

    def agg(key):
        g = pd.DataFrame({'k': key, 'o': o, 'h': h, 'l': l, 'c': c}).groupby('k', sort=True)
        a = g.agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
        return a.index.values.astype(np.int64), a.o.values, a.h.values, a.l.values, a.c.values

    hT, hO, hH, hL, hC = agg(t // H1 * H1)
    htf = SMC(hT, hO, hH, hL, hC, params['htf_len'], H1, keep_ob=True, max_ob=params['max_htf_ob'])
    htf.run()
    db = pd.DataFrame({'t': t // 86400 * 86400, 'h': h, 'l': l}).groupby('t').agg(h=('h', 'max'), l=('l', 'min'))
    dT = db.index.values; dH = db.h.values; dL = db.l.values

    lT, lO, lH, lL, lC = agg(t // LS * LS)
    ltf = SMC(lT, lO, lH, lL, lC, params['ltf_len'], LS)
    ltf.run()
    ltf_index = {tt: k for k, tt in enumerate(lT)}

    kzs = [parse_kz(s) for s in params['kz']]

    def to_utc(st):   # server -> utc for an LTF bar time (offset of nearby M1 bar)
        return st - srv_off[min(np.searchsorted(t, st), len(t) - 1)]

    def ist_min(st): return ((to_utc(st) + IST_OFF) % 86400) // 60
    def ist_day(st): return (to_utc(st) + IST_OFF) // 86400

    def kz_index(st):
        m = ist_min(st)
        for i, (a, b) in enumerate(kzs):
            if not params['kz_on'][i]: continue
            if a < b:
                if a <= m < b: return i
            elif m >= a or m < b: return i
        return -1

    def npx(p): return round(round(p / point) * point, digits)

    start_live = t[0] + warmup_days * 86400
    state = 0; g = {}
    used = set(); kz_traded = [-1, -1, -1]
    prevH = prevL = 0.0
    order = None; pos = None
    trades = []
    entries_per_day = {}
    stats = dict(setups=0, skipped_exposure=0, skipped_day=0, skipped_spread=0, expired=0, invalid=0,
                 market=0, limit=0)
    cur_spread = [0.0]
    ms = dict(streak=0, pause_left=0, pause_armed_at=-1)   # loss-streak protection state

    def plan(d, e, ext):
        edge = e['obBot'] if d == -1 else e['obTop']
        mid = (e['obTop'] + e['obBot']) / 2
        entry = edge if params['ob_level'] == 'near' else mid
        far = max(e['obTop'], ext) if d == -1 else min(e['obBot'], ext)
        sl = far + params['sl_buffer_pts'] * point if d == -1 else far - params['sl_buffer_pts'] * point
        if (d == -1 and sl <= entry) or (d == 1 and sl >= entry): return None
        if params['min_sl_pts'] > 0 and abs(entry - sl) < params['min_sl_pts'] * point:
            sl = entry + params['min_sl_pts'] * point if d == -1 else entry - params['min_sl_pts'] * point
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
                           R=pnl / pos['risk'], pips=pnl / pip, risk_pips=pos['risk'] / pip,
                           open_utc=pos['utc'], w=pos.get('w', 1.0)))
        if why == 'SL': ms['streak'] += 1
        elif why == 'TP' or (why == 'EOD' and pnl > 0): ms['streak'] = 0
        pos = None

    def add_entry(tt):
        day = tt // 86400; entries_per_day[day] = entries_per_day.get(day, 0) + 1

    def process_price(p, tt, uu):
        nonlocal order, pos
        ask = p + cur_spread[0]
        if order is not None:
            d = order['dir']
            if tt - order['setup'] > params['limit_expiry_bars'] * LS:
                order = None; stats['expired'] += 1
            elif (d == -1 and p >= order['entry']) or (d == 1 and ask <= order['entry']):
                pos = dict(dir=d, entry=order['entry'], sl=order['sl'], sl0=order['sl'], tp=order['tp'],
                           risk=abs(order['entry'] - order['sl']), t=tt, utc=uu, kind='limit', w=order['w'])
                add_entry(tt); order = None
        if pos is not None:
            d = pos['dir']; be = params['be_r']
            if d == 1:
                if p <= pos['sl']: return close_pos(pos['sl'], tt, 'BE' if pos['sl'] == pos['entry'] else 'SL')
                if p >= pos['tp']: return close_pos(pos['tp'], tt, 'TP')
                if be > 0 and pos['sl'] < pos['entry'] and p - pos['entry'] >= be * (pos['entry'] - pos['sl']):
                    pos['sl'] = pos['entry']
            else:
                if ask >= pos['sl']: return close_pos(pos['sl'], tt, 'BE' if pos['sl'] == pos['entry'] else 'SL')
                if ask <= pos['tp']: return close_pos(pos['tp'], tt, 'TP')
                if be > 0 and pos['sl'] > pos['entry'] and pos['entry'] - ask >= be * (pos['sl'] - pos['entry']):
                    pos['sl'] = pos['entry']

    def run_bar_ticks(i):
        s = cur_spread[0]
        pts = [l[i], h[i], c[i]] if c[i] >= o[i] else [h[i], l[i], c[i]]
        prev = o[i]
        for q in pts:
            lo_, hi_ = min(prev, q), max(prev, q)
            for _ in range(4):   # re-scan: a fill / BE move can create new levels on the same segment
                levels = []
                if order is not None: levels.append(order['entry'] - (s if order['dir'] == 1 else 0))
                if pos is not None:
                    be = params['be_r'] if params['be_r'] > 0 else 1e9
                    if pos['dir'] == 1:
                        levels += [pos['sl'], pos['tp'], pos['entry'] + be * (pos['entry'] - pos['sl'])]
                    else:
                        levels += [pos['sl'] - s, pos['tp'] - s, pos['entry'] - be * (pos['sl'] - pos['entry']) - s]
                hits = sorted([x for x in levels if lo_ <= x <= hi_], reverse=(q < prev))
                if not hits: break
                before = (id(order), id(pos), pos['sl'] if pos else None)
                for x in hits: process_price(x, t[i] + 30, utc[i])
                if (id(order), id(pos), pos['sl'] if pos else None) == before: break
            process_price(q, t[i] + 59, utc[i])
            prev = q

    def try_enter(d, e, ext, i):
        nonlocal order, pos
        stats['setups'] += 1
        tt = t[i]
        if order is not None or pos is not None: stats['skipped_exposure'] += 1; return False
        if entries_per_day.get(tt // 86400, 0) >= params['max_trades_day']: stats['skipped_day'] += 1; return False
        if params['max_spread_pts'] > 0 and spread[i] / point > params['max_spread_pts']:
            stats['skipped_spread'] += 1; return False
        if params['pause_after'] > 0 and ms['streak'] >= params['pause_after'] and ms['pause_armed_at'] != len(trades):
            ms['pause_armed_at'] = len(trades); ms['pause_left'] = params['pause_setups']; ms['streak'] = 0
        if ms['pause_left'] > 0:
            ms['pause_left'] -= 1; stats['paused'] = stats.get('paused', 0) + 1; return False
        w = params['reduced_mult'] if params['reduce_after'] > 0 and ms['streak'] >= params['reduce_after'] else 1.0
        pl = plan(d, e, ext)
        if pl is None: stats['invalid'] += 1; return False
        entry, sl, tp = pl
        bid = o[i]; ask = bid + spread[i]
        market = params['entry_mode'] == 'market' or ((bid >= entry) if d == -1 else (ask <= entry))
        if market:
            entry = bid if d == -1 else ask
            if (d == -1 and sl <= entry) or (d == 1 and sl >= entry): stats['invalid'] += 1; return False
            risk = abs(entry - sl)
            tp = entry - params['rr'] * risk if d == -1 else entry + params['rr'] * risk
        entry, sl, tp = npx(entry), npx(sl), npx(tp)
        if market:
            pos = dict(dir=d, entry=entry, sl=sl, sl0=sl, tp=tp, risk=abs(entry - sl), t=tt, utc=utc[i], kind='market', w=w)
            add_entry(tt); stats['market'] += 1
        else:
            order = dict(dir=d, entry=entry, sl=sl, tp=tp, setup=tt, w=w); stats['limit'] += 1
        return True

    def arm(d, obIdx, key, kz, k):
        nonlocal state
        state = 1
        g.update(dir=d, ob=obIdx, ext=lH[k] if d == -1 else lL[k], bars=0, kz=kz)
        liq = ltf.hi_arr[k] if d == -1 else ltf.lo_arr[k]
        g['liq'] = liq
        g['swept'] = (liq <= 0) or (lH[k] > liq if d == -1 else lL[k] < liq)
        used.add(key)

    def strategy(k, i):
        """closed LTF bar k, decision executed at the open tick of M1 bar i"""
        nonlocal state, prevH, prevL
        bt = lT[k]; bh, bl = lH[k], lL[k]
        live = t[i] >= start_live
        closeT = bt + LS
        kz = kz_index(bt)
        bias = htf.trend_at(closeT)
        skip_new = False
        if state == 1:
            g['bars'] += 1
            g['ext'] = max(g['ext'], bh) if g['dir'] == -1 else min(g['ext'], bl)
            if not g['swept'] and g['liq'] > 0 and ((g['dir'] == -1 and bh > g['liq']) or (g['dir'] == 1 and bl < g['liq'])):
                g['swept'] = True
            cancel = g['bars'] > params['max_bars_in_zone']
            if params['bias_mode'] == 'htf' and bias != g['dir']: cancel = True
            if g['ob'] >= 0:
                tm = htf.ob[g['ob']]['tMit']
                if tm != 0 and tm <= closeT: cancel = True
            fired = False
            if not cancel:
                for e in ltf.ev.get(bt, []):
                    if e['dir'] != g['dir']: continue
                    if (not params['require_choch'] or e['choch']) and (not params['require_sweep'] or g['swept']) and e['obT'] > 0:
                        done = try_enter(g['dir'], e, g['ext'], i) if live else plan(g['dir'], e, g['ext']) is not None
                        if done and g['kz'] >= 0: kz_traded[g['kz']] = ist_day(bt) * 10 + g['kz']
                        state = 0; fired = True
                    break
            if fired or not cancel: skip_new = True
            else: state = 0
        if not skip_new and kz >= 0 and not (params['one_per_kz'] and kz_traded[kz] == ist_day(bt) * 10 + kz):
            canSell = params['bias_mode'] == 'both' or bias == -1
            canBuy = params['bias_mode'] == 'both' or bias == 1
            for d in (-1, 1):
                if (d == -1 and not canSell) or (d == 1 and not canBuy): continue
                armed = False
                if params['use_htf_ob']:
                    j = htf.find_touched_ob(d, closeT, bh, bl)
                    if j >= 0:
                        key = ('O', htf.ob[j]['t'], d)
                        if key not in used: arm(d, j, key, kz, k); armed = True
                if not armed and params['use_prev_day']:
                    di = np.searchsorted(dT, bt, side='right') - 1
                    if di >= 1:
                        lvl = dH[di - 1] if d == -1 else dL[di - 1]
                        key = ('D', dT[di - 1], d)
                        crossed = (bh > lvl and prevH <= lvl) if d == -1 else (bl < lvl and prevL >= lvl)
                        if lvl > 0 and crossed and key not in used: arm(d, -1, key, kz, k); armed = True
                if armed: break
        prevH, prevL = bh, bl

    n = len(t)
    for i in range(1, n):
        cur_spread[0] = spread[i]
        lt_now, lt_prev = t[i] // LS * LS, t[i - 1] // LS * LS
        if lt_now != lt_prev:
            strategy(ltf_index[lt_prev], i)
        process_price(o[i], t[i], utc[i])
        if pos is not None or order is not None:
            run_bar_ticks(i)
    if pos is not None: close_pos(c[-1], t[-1], 'EOD')
    return trades, stats


# csv, point, default spread (pips, used when the csv has no spread column), pip size
PAIRS = {
    'EURUSD': ('eurusd/EURUSD_1m.csv', 1e-5, 1.0, 1e-4),
    'GBPUSD': ('gbpusd/GBPUSD_1m.csv', 1e-5, 1.4, 1e-4),
    'USDJPY': ('usdjpy/USDJPY_1m.csv', 1e-3, 1.2, 1e-2),
    'USDCHF': ('usdchf/USDCHF_1m.csv', 1e-5, 1.5, 1e-4),
    'AUDUSD': ('audusd/AUDUSD_1m.csv', 1e-5, 1.2, 1e-4),
    'USDCAD': ('usdcad/USDCAD_1m.csv', 1e-5, 1.6, 1e-4),
    'GBPCAD': ('gbpcad/GBPCAD_1m.csv', 1e-5, 3.0, 1e-4),   # 2018 Dukascopy ticks, real spread per minute
}

_cache = {}


def run_all(base, params, pairs=None, spread_mult=1.0):
    out = []
    for pair, (f, pt, sp, pip) in PAIRS.items():
        if pairs and pair not in pairs: continue
        path = os.path.join(base, f)
        if not os.path.exists(path): continue
        if path not in _cache: _cache[path] = load(path)
        data = _cache[path]
        if spread_mult != 1.0:
            data = data[:-1] + ((data[-1] * spread_mult) if data[-1] is not None else None,)
        tr, st = backtest(data, pt, sp * spread_mult, pip, params=params, label=pair)
        out += tr
    return pd.DataFrame(out)


if __name__ == '__main__':
    base = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else 'base'
    over = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    params = dict(P); params.update(over)
    df = run_all(base, params)
    df.to_csv(f'trades_{tag}.csv', index=False)
    print(df.groupby('pair').R.agg(['size', 'sum']).round(1))
