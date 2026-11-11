import sys, glob, os, pandas as pd, numpy as np
src, out = sys.argv[1], sys.argv[2]
parts = []
for f in sorted(glob.glob(os.path.join(src, '*_ticks.csv'))):
    d = pd.read_csv(f, header=None, names=['ts', 'a', 'b', 'av', 'bv'])
    if d.empty: continue
    parts.append(d)
d = pd.concat(parts, ignore_index=True)
d['ts'] = pd.to_datetime(d.ts, format='%Y.%m.%d %H:%M:%S.%f', utc=True)
d['bid'] = d[['a', 'b']].min(axis=1); d['ask'] = d[['a', 'b']].max(axis=1)
d['spr'] = d.ask - d.bid
print('col order check: first col < second col fraction', (d.a < d.b).mean())
d = d.sort_values('ts').set_index('ts')
m = d.bid.resample('1min').ohlc().dropna()
m['spread'] = d.spr.resample('1min').median().reindex(m.index)
m.index.name = 'datetime'
m.reset_index().to_csv(out, index=False)
print(len(m), m.index[0], m.index[-1])
s = m.spread * 1e4
print('spread pips median', s.median().round(2), 'mean', s.mean().round(2))
hr = m.groupby(m.index.hour).spread.median() * 1e4
print(hr.round(1).to_dict())
