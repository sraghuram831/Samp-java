"""Money-management 'recovery' schemes applied to a trade list (R multiples), shared account."""
import sys, pandas as pd, numpy as np

def sim(R, scheme, base=0.5, cap=8.0):
    eq = 100.0; peak = 100.0; dd = 0.0; risk = base; streak = 0; pause = 0; maxrisk = 0
    for r in R:
        if pause > 0: pause -= 1; continue
        if scheme == 'fixed': risk = base
        elif scheme == 'martingale':            # double after each loss, reset on win, max 4 steps
            risk = base * 2 ** min(streak, 4)
        elif scheme == 'recover_dd':            # size so one TP (3R) wins back the current drawdown
            deficit = (peak - eq) / eq * 100
            risk = min(cap, max(base, deficit / 3 + base))
        elif scheme == 'anti_martingale':       # +50% after a win, reset on loss
            pass
        elif scheme == 'half_after_2L':         # halve risk after 2 losses in a row, restore on win
            risk = base / 2 if streak >= 2 else base
        elif scheme == 'pause_after_3L':        # skip next 5 setups after 3 losses in a row
            risk = base
        maxrisk = max(maxrisk, risk)
        eq *= 1 + risk * r / 100
        peak = max(peak, eq); dd = max(dd, (peak - eq) / peak * 100)
        if r < -0.05: streak += 1
        elif r > 0.05: streak = 0
        if scheme == 'anti_martingale': risk = base if r < -0.05 else (min(risk * 1.5, 2) if r > 0.05 else risk)
        if scheme == 'pause_after_3L' and streak >= 3: pause, streak = 5, 0
        if eq <= 0: return -100, 100, maxrisk
    return round(eq - 100, 1), round(dd, 1), maxrisk

rows = []
for f in sys.argv[1:]:
    d = pd.read_csv(f).sort_values('open_utc')
    for s in ['fixed', 'martingale', 'recover_dd', 'anti_martingale', 'half_after_2L', 'pause_after_3L']:
        ret, dd, mr = sim(d.R.values, s)
        rows.append(dict(trades=f.split('trades_')[1][:-4], scheme=s, return_pct=ret, maxDD_pct=dd, max_risk_pct=round(mr, 2)))
print(pd.DataFrame(rows).to_string(index=False))
