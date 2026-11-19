"""NFL DFS projections: median, floor (15th pct) and ceiling (85th pct) DK points per player, plus DST.
Gradient-boosted trees per position, trained walk-forward on nflverse history. `python3 nfl_model.py backtest` prints accuracy vs a naive baseline."""
import sys, json, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as HGB
import nfl_data as d

FEATS = ['ew_dk', 'ew_dk_long', 'ssn_dk', 'ssn_n', 'n_prior', 'dk_sd', 'dk_p90', 'ew_car_sh', 'ew_tgt_sh', 'ew_air_sh', 'ew_att_sh', 'ew_opps',
         'ew_qb_rush', 'ew_team_share', 'vegas_share', 'implied', 'opp_implied', 'spread', 'total_line', 'home', 'dvp', 'dome', 'wind']
DST_FEATS = ['ew_dst', 'ew_def_sacks', 'opp_give', 'implied', 'opp_implied', 'spread', 'total_line', 'home', 'dome', 'wind']
QS = {'proj': None, 'floor': 0.15, 'ceil': 0.85}

def _fit(X, y, q):
    m = HGB(loss='quantile', quantile=q, max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40) if q else \
        HGB(loss='squared_error', max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40)
    return m.fit(X, y)

def usable(P):
    return P[(P.n_prior >= 1) & P.implied.notna()]

def train(P, T, seasons):
    tr = usable(P[P.season.isin(seasons)]); M = {}
    for pos in d.POS:
        x = tr[tr.position == pos]
        M[pos] = {k: _fit(x[FEATS], x.dk, q) for k, q in QS.items()}
    t = T[T.season.isin(seasons) & T.implied.notna() & T.ew_dst.notna()]
    M['DST'] = {k: _fit(t[DST_FEATS], t.dst, q) for k, q in QS.items()}
    return M

def predict(M, P, T=None):
    out = []
    for pos in d.POS:
        x = P[P.position == pos].copy()
        if len(x):
            for k in QS: x[k] = M[pos][k].predict(x[FEATS])
            out.append(x)
    R = pd.concat(out) if out else P.iloc[:0]
    if T is not None and len(T):
        T = T.copy()
        for k in QS: T[k] = M['DST'][k].predict(T[DST_FEATS])
        return R, T
    return R

def backtest():
    S = list(range(2019, 2027)); P = d.features(S); T = d.team_rows(S)
    rep = {}
    for test in (2024, 2025, 2026):
        M = train(P, T, [s for s in S if s < test])
        R, TT = predict(M, usable(P[P.season == test]), T[(T.season == test) & T.implied.notna() & T.ew_dst.notna()])
        R = R[R.ew_dk >= 4]                     # the fantasy-relevant pool
        for pos in d.POS + ['DST']:
            x = TT if pos == 'DST' else R[R.position == pos]
            y = x.dst if pos == 'DST' else x.dk
            base = x.ew_dst if pos == 'DST' else x.naive
            rep[f'{test} {pos}'] = dict(n=len(x), mae_model=round(float((x.proj - y).abs().mean()), 2), mae_naive=round(float((base - y).abs().mean()), 2),
                                        corr_model=round(float(np.corrcoef(x.proj, y)[0, 1]), 3), corr_naive=round(float(np.corrcoef(base, y)[0, 1]), 3),
                                        below_floor=round(float((y < x.floor).mean()), 3), above_ceil=round(float((y > x.ceil).mean()), 3))
    df = pd.DataFrame(rep).T; print(df.to_string()); return df

if __name__ == '__main__' and sys.argv[1:2] == ['backtest']:
    backtest()
