"""Run variants of the EA rules and report in-sample / out-of-sample / GBPCAD results in R."""
import sys, json
import pandas as pd
from multiprocessing import Pool
from sweepob_bt import P, run_all

SPLIT = pd.Timestamp('2026-07-01', tz='UTC').timestamp()

def summarize(d):
    if len(d) == 0: return dict(n=0, R=0.0, avgR=0.0, PF=0.0, DD=0.0)
    d = d.sort_values('close_t'); d = d.assign(R=d.R * d.w); eq = d.R.cumsum()
    dd = (eq.cummax().clip(lower=0) - eq).max()
    loss = -d.R[d.R < 0].sum()
    return dict(n=len(d), R=round(d.R.sum(), 1), avgR=round(d.R.mean(), 2),
                PF=round(d.R[d.R > 0].sum() / loss, 2) if loss > 0 else 99, DD=round(dd, 1))

def job(arg):
    name, over = arg
    p = dict(P); p.update(over)
    d = run_all('data', p)
    d.to_csv(f'runs/trades_{name}.csv', index=False)
    maj = d[d.pair != 'GBPCAD']
    row = {'config': name}
    for lbl, part in [('IS', maj[maj.open_utc < SPLIT]), ('OOS', maj[maj.open_utc >= SPLIT]),
                      ('GBPCAD', d[d.pair == 'GBPCAD']), ('ALL', d)]:
        for k, v in summarize(part).items(): row[f'{lbl}_{k}'] = v
    return row

if __name__ == '__main__':
    grid = json.load(open(sys.argv[1]))
    import os; os.makedirs('runs', exist_ok=True)
    with Pool(min(len(grid), os.cpu_count())) as pool:
        rows = pool.map(job, list(grid.items()))
    df = pd.DataFrame(rows)
    cols = ['config'] + [c for c in df.columns if c.endswith(('_n', '_R', '_PF'))] + ['ALL_DD']
    print(df[cols].to_string(index=False))
    df.to_csv(sys.argv[1].replace('.json', '_results.csv'), index=False)
