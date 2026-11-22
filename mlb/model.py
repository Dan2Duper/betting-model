"""MLB run model, walk-forward. For each test season the model is fitted on earlier seasons only (2010 .. season-1), then predicts
that season's games from features that already use only earlier dates. Two engines are compared: a Poisson GLM (transparent, portable
to the page) and a gradient-boosted Poisson model. Win probability and over probability are calibrated on earlier seasons' out-of-sample
predictions, never on the season being tested.
Usage: python3 mlb/model.py [DATA_DIR]   -> DATA_DIR/preds.parquet + printed season table; also writes mlb/model.json and mlb/stats.json"""
import os, sys, json, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import PoissonRegressor, LogisticRegression
from sklearn.preprocessing import StandardScaler
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import FEATS
warnings.filterwarnings('ignore')
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata')
FIRST_TEST = 2012

def fit_glm(X, y, alpha=0.002):
    sc = StandardScaler().fit(X); m = PoissonRegressor(alpha=alpha, max_iter=500).fit(sc.transform(X), y)
    return dict(mu=sc.mean_.tolist(), sd=sc.scale_.tolist(), beta=m.coef_.tolist(), b0=float(m.intercept_), feats=FEATS)
def pred_glm(M, X): return np.exp(M['b0'] + ((X - np.array(M['mu'])) / np.array(M['sd'])) @ np.array(M['beta']))
def fit_gbm(X, y):
    import lightgbm as lgb
    return lgb.LGBMRegressor(objective='poisson', n_estimators=400, learning_rate=0.02, num_leaves=12, min_child_samples=200, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.7, reg_lambda=5.0, verbose=-1, random_state=7).fit(X, y)

def walk_forward(F, engine='glm', seasons=None):
    P = []
    for S in (seasons or sorted(s for s in F.season.unique() if s >= FIRST_TEST)):
        tr = F[F.season < S]; te = F[F.season == S]
        if engine == 'glm': M = fit_glm(tr[FEATS].values, tr.runs.values); lam = pred_glm(M, te[FEATS].values)
        else: M = fit_gbm(tr[FEATS].values, tr.runs.values); lam = M.predict(te[FEATS].values)
        P.append(pd.DataFrame(dict(gid=te.gid.values, season=S, is_home=te.is_home.values, lam=lam)))
    P = pd.concat(P)
    h = P[P.is_home == 1].set_index('gid'); a = P[P.is_home == 0].set_index('gid')
    return pd.DataFrame(dict(season=h.season, lam_h=h.lam, lam_a=a.lam)).reset_index()

def imp(o): o = np.asarray(o, float); return np.where(o < 0, -o / (-o + 100), 100 / (o + 100))
def rl_x(d): return np.column_stack([d.z.values, d.mt.values - 9.0])
def skellam_2(lh, la, n=40):
    """Plain independent-Poisson margin: P(home by 2+), P(away by 2+), ties split in proportion (what the run model implies with no baseball rules)."""
    from scipy.stats import poisson
    k = np.arange(n); ph = poisson.pmf(k[None, :], np.asarray(lh)[:, None]); pa = poisson.pmf(k[None, :], np.asarray(la)[:, None])
    J = ph[:, :, None] * pa[:, None, :]; d = k[:, None] - k[None, :]
    h2 = (J * (d >= 2)).sum((1, 2)); a2 = (J * (d <= -2)).sum((1, 2)); tie = (J * (d == 0)).sum((1, 2))
    return h2 / (1 - tie), a2 / (1 - tie)
def calibrate(R):
    """Walk-forward calibration: season S uses only seasons < S. p_home from log(lam_h/lam_a); p_over from (model total - line)."""
    R = R.copy(); R['z'] = np.log(R.lam_h / R.lam_a); R['mt'] = R.lam_h + R.lam_a; R['p_home'] = np.nan; R['p_over'] = np.nan; R['k_ml'] = np.nan
    R['p_h2'] = np.nan; R['p_a2'] = np.nan   # run line: P(home wins by 2+), P(away wins by 2+)
    for S in sorted(R.season.unique())[1:]:
        tr = R[R.season < S]; te = R.season == S
        m = LogisticRegression(C=1e6).fit(tr[['z']].values, tr.home_win.values)
        R.loc[te, 'p_home'] = m.predict_proba(R.loc[te, ['z']].values)[:, 1]; R.loc[te, 'k_ml'] = m.coef_[0][0]
        for col, y in (('p_h2', tr.margin >= 2), ('p_a2', tr.margin <= -2)):
            mr = LogisticRegression(C=1e6).fit(rl_x(tr), y.astype(int).values); R.loc[te, col] = mr.predict_proba(rl_x(R[te]))[:, 1]
        t = tr[tr.tot.notna() & (tr.total != tr.tot)]
        if len(t) > 500:
            m2 = LogisticRegression(C=1e6).fit((t.mt - t.tot).values.reshape(-1, 1), (t.total > t.tot).astype(int).values)
            ok = te & R.tot.notna(); R.loc[ok, 'p_over'] = m2.predict_proba((R.loc[ok, 'mt'] - R.loc[ok, 'tot']).values.reshape(-1, 1))[:, 1]
    return R

if __name__ == '__main__':
    F = pd.read_parquet(f'{D}/feat.parquet'); G = pd.read_parquet(f'{D}/games.parquet')
    keep = ['gid', 'date', 'home', 'away', 'hruns', 'aruns', 'ml_h', 'ml_a', 'tot', 'over', 'under', 'ml_suspect', 'tot_suspect', 'open_ml_h', 'open_ml_a', 'open_tot', 'open_over', 'open_under', 'cons_p_h', 'open_p_h', 'src', 'rl', 'rl_h', 'rl_a', 'rl_suspect']
    out = {}
    for eng in ('glm', 'gbm'):
        R = walk_forward(F, eng).merge(G[keep], on='gid'); R['home_win'] = (R.hruns > R.aruns).astype(int); R['total'] = R.hruns + R.aruns; R['margin'] = R.hruns - R.aruns
        R = calibrate(R); out[eng] = R
        R['p_rl'] = np.where(R.rl == -1.5, R.p_h2, 1 - R.p_a2); R['rl_mkt'] = imp(R.rl_h) / (imp(R.rl_h) + imp(R.rl_a)); R['rl_cover'] = (R.margin + R.rl > 0).astype(float)
        ih, ia = imp(R.ml_h), imp(R.ml_a); R['mkt_p'] = ih / (ih + ia)
        print(f'\n=== {eng.upper()} walk-forward ===')
        rows = []
        for S, d in R.groupby('season'):
            e = d.dropna(subset=['mkt_p', 'p_home']); t = d.dropna(subset=['tot'])
            ll = lambda p, y: float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
            rows.append(dict(season=S, games=len(d), rmse_runs=round(float(np.sqrt((((d.lam_h - d.hruns) ** 2 + (d.lam_a - d.aruns) ** 2) / 2).mean())), 3),
                             model_total_mae=round(float((t.lam_h + t.lam_a - t.total).abs().mean()), 3), line_total_mae=round(float((t.tot + 0.45 - t.total).abs().mean()), 3),
                             corr_model_line=round(float(np.corrcoef(t.lam_h + t.lam_a, t.tot)[0, 1]), 3),
                             model_logloss=round(ll(e.p_home.values, e.home_win.values), 4) if len(e) else None, market_logloss=round(ll(e.mkt_p.values, e.home_win.values), 4) if len(e) else None,
                             pick_winner=round(float(((e.p_home > 0.5) == (e.home_win == 1)).mean()), 3) if len(e) else None, market_winner=round(float(((e.mkt_p > 0.5) == (e.home_win == 1)).mean()), 3) if len(e) else None))
            r = d[d.rl.notna() & ~d.rl_suspect & d.p_rl.notna()]
            if len(r): rows[-1].update(rl_games=len(r), rl_model_ll=round(ll(r.p_rl.values, r.rl_cover.values), 4), rl_market_ll=round(ll(r.rl_mkt.values, r.rl_cover.values), 4))
        print(pd.DataFrame(rows).to_string(index=False))
    out['glm'].merge(out['gbm'][['gid', 'lam_h', 'lam_a', 'p_home', 'p_over']], on='gid', suffixes=('', '_gbm')).to_parquet(f'{D}/preds.parquet')
    R = out['glm']; r = R[R.season >= 2014].dropna(subset=['p_h2']); sh, sa = skellam_2(r.lam_h.values, r.lam_a.values); llf = lambda p, y: float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    print(f"\nRun line, {len(r)} games 2014+: home wins by 2+ in {(r.margin >= 2).mean():.3f} (fitted {r.p_h2.mean():.3f}, plain Poisson {sh.mean():.3f}); away by 2+ in {(r.margin <= -2).mean():.3f} (fitted {r.p_a2.mean():.3f}, plain Poisson {sa.mean():.3f})")
    print(f"  log loss home-by-2+: fitted {llf(r.p_h2.values, (r.margin >= 2).values):.4f}, plain Poisson {llf(sh, (r.margin >= 2).values):.4f} | away-by-2+: fitted {llf(r.p_a2.values, (r.margin <= -2).values):.4f}, plain Poisson {llf(sa, (r.margin <= -2).values):.4f}")
    q = r[r.rl.notna() & ~r.rl_suspect]; print(f"  vs the closing run line ({len(q)} games): model log loss {llf(q.p_rl.values, q.rl_cover.values):.4f}, no-vig market {llf(q.rl_mkt.values, q.rl_cover.values):.4f}")
    M = fit_glm(F[FEATS].values, F.runs.values); print('\nGLM weights (per 1 sd of the feature, % change in runs):')
    print(pd.Series(np.round((np.exp(np.array(M['beta'])) - 1) * 100, 2), index=FEATS).sort_values().to_string())
    # ---- saved model for the season (fitted on every season in hand) + the numbers the page shows ----
    PKG = os.path.dirname(os.path.abspath(__file__)); R = out['glm']
    c = R.dropna(subset=['mkt_p']); wl = LogisticRegression(C=1e6).fit(c[['z']].values, c.home_win.values)
    t = R[R.tot.notna() & ~R.tot_suspect & (R.total != R.tot)]; ol = LogisticRegression(C=1e6).fit((t.mt - t.tot).values.reshape(-1, 1), (t.total > t.tot).astype(int).values)
    RLM = {}
    for col, y in (('h2', R.margin >= 2), ('a2', R.margin <= -2)):
        mr = LogisticRegression(C=1e6).fit(rl_x(R), y.astype(int).values); RLM[col] = dict(a=float(mr.intercept_[0]), z=float(mr.coef_[0][0]), t=float(mr.coef_[0][1]))
    M.update(rl=RLM)
    M.update(trained=f"{int(F.season.min())}-{int(F.season.max())}", n=int(len(F)), win=dict(a=float(wl.intercept_[0]), k=float(wl.coef_[0][0])),
             over=dict(a=float(ol.intercept_[0]), b=float(ol.coef_[0][0]), offset=round(float(-ol.intercept_[0] / ol.coef_[0][0]), 3)),
             _note='runs = exp(b0 + sum(beta * (x - mu) / sd)); P(home) = logistic(a + k*ln(runs_home/runs_away)); P(over) = logistic(a + b*(model total - line)). The model total is a MEAN; a betting total is a median, about `offset` runs lower. Run line: P(home by 2+) = logistic(rl.h2.a + rl.h2.z*ln(runs_home/runs_away) + rl.h2.t*(model total - 9)), P(away by 2+) the same with rl.a2; home -1.5 covers with P(home by 2+), home +1.5 with 1 - P(away by 2+).')
    json.dump(M, open(f'{PKG}/model.json', 'w'), indent=1)
    ll = lambda p, y: float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    seasons = []
    for S, d in R.groupby('season'):
        e = d.dropna(subset=['mkt_p', 'p_home']); t = d[d.tot.notna() & ~d.tot_suspect]
        if not len(e): continue
        seasons.append(dict(season=int(S), games=int(len(e)), model_winner=round(float(((e.p_home > 0.5) == (e.home_win == 1)).mean()), 3), market_winner=round(float(((e.mkt_p > 0.5) == (e.home_win == 1)).mean()), 3),
                            model_logloss=round(ll(e.p_home.values, e.home_win.values), 4), market_logloss=round(ll(e.mkt_p.values, e.home_win.values), 4),
                            model_total_mae=round(float((t.mt - t.total).abs().mean()), 2), line_total_mae=round(float((t.tot + M['over']['offset'] - t.total).abs().mean()), 2)))
    e = R.dropna(subset=['mkt_p', 'p_home'])
    stats = dict(built=str(pd.Timestamp.now().date()), engine='Poisson GLM, walk-forward by season', seasons=seasons,
                 overall=dict(games=int(len(e)), model_winner=round(float(((e.p_home > 0.5) == (e.home_win == 1)).mean()), 4), market_winner=round(float(((e.mkt_p > 0.5) == (e.home_win == 1)).mean()), 4),
                              model_logloss=round(ll(e.p_home.values, e.home_win.values), 4), market_logloss=round(ll(e.mkt_p.values, e.home_win.values), 4)),
                 weights={f: round(float((np.exp(b) - 1) * 100), 2) for f, b in zip(FEATS, M['beta'])})
    if os.path.exists(f'{D}/rules_bt.json'): stats['rules'] = [{k: v for k, v in t.items() if k != 'seasons'} for t in json.load(open(f'{D}/rules_bt.json'))]
    json.dump(stats, open(f'{PKG}/stats.json', 'w'), indent=1); print('saved mlb/model.json, mlb/stats.json', M['win'], M['over'], stats['overall'])
