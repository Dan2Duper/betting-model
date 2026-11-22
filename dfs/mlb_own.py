"""Estimated DraftKings MLB ownership (same idea as dfs/ownership.py: value and raw projection drive it, scaled so each position's
total matches its roster slots). A multi-position hitter is counted at the first position DraftKings lists. Tournament fields
pile onto the top-priced aces and the hitters in the highest-total games, so pitchers get a sharper curve than hitters.
Every number is an estimate, labeled so on the page."""
import numpy as np
from ownership import _soft, showdown   # showdown ownership is position-free: reuse it
TOTAL = {'P': 200, 'C': 100, '1B': 100, '2B': 100, '3B': 100, 'SS': 100, 'OF': 300}
TEMP = {'P': 0.6}

def classic(df):
    from mlb_opt import elig
    df = df.copy(); df['own'] = 0.0; prim = df.pos.map(lambda p: (elig(p) or ['OF'])[0])
    for pos, total in TOTAL.items():
        m = (prim == pos).values
        if m.sum() == 0: continue
        x = df[m]; value = x.proj / (x.salary / 1000)
        score = 0.6 * (value - value.mean()) / (value.std() or 1) + 0.4 * (x.proj - x.proj.mean()) / (x.proj.std() or 1)
        if pos != 'P' and 'team_runs' in x: score = score + 0.35 * (x.team_runs - x.team_runs.mean()) / (x.team_runs.std() or 1)   # the field chases the big team totals
        df.loc[m, 'own'] = _soft(np.asarray(score, float), total, TEMP.get(pos, 0.8), 60 if pos == 'P' else 35)
    return df
