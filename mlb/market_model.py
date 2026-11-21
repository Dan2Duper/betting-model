"""Second look for an edge: instead of predicting runs and comparing with the line, learn directly what the CLOSING line misses.
Walk-forward logistic / boosted models of over-vs-under and home-win given the closing line plus every feature; season S is
predicted by a model fitted on seasons < S only. Usage: python3 mlb/market_model.py [DATA_DIR]"""
import os, sys, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import FEATS
from backtest import load, grade_total, grade_ml, summarize, imp
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata')
R = load(); F = pd.read_parquet(f'{D}/feat.parquet')
SIDE = [f for f in FEATS if f not in ('is_home', 'park', 'lg_rpg', 'lg_rpg_s', 'dome', 'temp', 'wind_out', 'wind_in', 'wind_x', 'dh', 'sched7', 'day_game', 'ump_idx')]
h = F[F.is_home == 1].set_index('gid'); a = F[F.is_home == 0].set_index('gid')
X = pd.concat([h[SIDE].add_suffix('_hb'), a[SIDE].add_suffix('_ab'), h[['park', 'lg_rpg', 'lg_rpg_s', 'dome', 'temp', 'wind_out', 'wind_in', 'wind_x', 'dh', 'sched7', 'day_game', 'ump_idx']]], axis=1)
R = R.merge(X, left_on='gid', right_index=True, suffixes=('', '_f'))
XC = [c for c in X.columns]
R['gap'] = R.lam_h + R.lam_a - R.tot; R['mlogit'] = np.log(R.mkt_p / (1 - R.mkt_p)); R['z'] = np.log(R.lam_h / R.lam_a)
def fit_pred(tr, te, cols, y, eng):
    if eng == 'lr':
        sc = StandardScaler().fit(tr[cols].values); m = LogisticRegression(C=0.02, max_iter=2000).fit(sc.transform(tr[cols].values), y)
        return m.predict_proba(sc.transform(te[cols].values))[:, 1]
    import lightgbm as lgb
    m = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.02, num_leaves=8, min_child_samples=300, subsample=0.8, subsample_freq=1, colsample_bytree=0.6, reg_lambda=10, verbose=-1, random_state=3).fit(tr[cols].values, y)
    return m.predict_proba(te[cols].values)[:, 1]
for eng in ('lr', 'gbm'):
    R[f'po_{eng}'] = np.nan; R[f'ph_{eng}'] = np.nan
    for S in range(2014, 2026):
        tr = R[(R.season < S) & (R.season >= 2012) & R.tot.notna() & ~R.tot_suspect & (R.total != R.tot)]; te = R[(R.season == S) & R.tot.notna()]
        R.loc[te.index, f'po_{eng}'] = fit_pred(tr, te, XC + ['tot', 'gap'], (tr.total > tr.tot).astype(int).values, eng)
        tr = R[(R.season < S) & (R.season >= 2012) & R.mkt_p.notna() & ~R.ml_suspect]; te = R[(R.season == S) & R.mkt_p.notna()]
        R.loc[te.index, f'ph_{eng}'] = fit_pred(tr, te, XC + ['mlogit', 'z'], tr.home_win.values, eng)
f = lambda s: f"{s.get('w', 0)}-{s.get('l', 0)} {100 * s.get('hit', 0):.1f}% (be {100 * s.get('be', 0):.1f}) roi {100 * s.get('roi', 0):+.1f}% z {s.get('z', 0):+.1f}" if s.get('n') else 'none'
print(f"{'what-the-line-misses model':34s} | {'2014-20':46s} | 2021-25")
for eng in ('lr', 'gbm'):
    for th in (0.53, 0.55, 0.57):
        for side in ('over', 'under'):
            p = R[f'po_{eng}']; m = (p >= th) if side == 'over' else (p <= 1 - th); d = R[m & ~R.tot_suspect & R.tot.notna()]
            pr, o = grade_total(d, np.full(len(d), side)); d = d.assign(pr=pr, o=o)
            print(f"{eng} total {side} p>={th:<19} | {f(summarize(d[d.season <= 2020].pr, d[d.season <= 2020].o)):46s} | {f(summarize(d[d.season >= 2021].pr, d[d.season >= 2021].o))}")
    for th in (0.02, 0.03, 0.05):
        e = R[f'ph_{eng}'] - R.mkt_p; d = R[(e.abs() >= th) & ~R.ml_suspect & R.ml_h.notna()]; sd = np.where(e[d.index] > 0, 'home', 'away')
        pr, o = grade_ml(d, sd); d = d.assign(pr=pr, o=o)
        print(f"{eng} moneyline edge>={th:<16} | {f(summarize(d[d.season <= 2020].pr, d[d.season <= 2020].o)):46s} | {f(summarize(d[d.season >= 2021].pr, d[d.season >= 2021].o))}")
R[['gid', 'po_lr', 'po_gbm', 'ph_lr', 'ph_gbm']].to_parquet(f'{D}/market_preds.parquet')
