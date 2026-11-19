"""Estimated DraftKings ownership. No free history of real ownership exists, so this follows how the field builds:
value (points per $1K) and raw projection drive it, scaled so each position's total matches its roster slots.
Every number it produces is labeled an estimate on the page; it is re-fit once real results are logged."""
import numpy as np

CLASSIC_TOTAL = {'QB': 100, 'RB': 235, 'WR': 355, 'TE': 110, 'DST': 100}
TEMP = {'QB': 0.85, 'RB': 0.8, 'WR': 0.85, 'TE': 0.8, 'DST': 0.9}

def _soft(score, total, temp, cap):
    z = (score - score.mean()) / (score.std() or 1)
    w = np.exp(z / temp); own = total * w / w.sum()
    for _ in range(20):                    # nobody can be in more than `cap`% of lineups
        over = own > cap
        if not over.any(): break
        extra = (own[over] - cap).sum(); own[over] = cap
        under = ~over; own[under] += extra * own[under] / own[under].sum()
    return own

def classic(df):
    df = df.copy(); df['own'] = 0.0
    for pos, total in CLASSIC_TOTAL.items():
        m = df.pos == pos
        if m.sum() == 0: continue
        x = df[m]; value = x.proj / (x.salary / 1000)
        score = 0.65 * (value - value.mean()) / (value.std() or 1) + 0.35 * (x.proj - x.proj.mean()) / (x.proj.std() or 1)
        df.loc[m, 'own'] = _soft(score.values, total, TEMP[pos], 45)
    return df

def showdown(df):
    df = df.copy()
    v = df.proj / (df.salary / 1000)
    flex = 0.6 * (v - v.mean()) / (v.std() or 1) + 0.4 * (df.proj - df.proj.mean()) / (df.proj.std() or 1)
    df['own'] = _soft(flex.values, 500, 0.6, 85)
    cpt = (df.proj - df.proj.mean()) / (df.proj.std() or 1) - 1.5 * df.pos.isin(['K', 'DST'])
    df['cpt_own'] = _soft(cpt.values, 100, 0.45, 45)
    return df
