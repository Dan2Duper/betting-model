import sys, json, numpy as np, pandas as pd
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor as HGB
P = pd.read_parquet(sys.argv[1]); R = {}
P = P[(P.ew_n >= 2) & P.ew_dk.notna() & P.implied.notna()].copy()
P = P[((P.pos == 'QB') & (P.ew_dk >= 10)) | ((P.pos != 'QB') & (P.ew_dk >= 4))]
P['ew_n_c'] = P.ew_n.clip(upper=8)
for c in ['ew_tgt_sh', 'ew_car_sh', 'dvp']: P[c] = P[c].fillna(P[c].median())
BASE = ['ew_dk', 'ew_n_c', 'ew_tgt_sh', 'ew_car_sh', 'implied', 'opp_implied', 'spread', 'home', 'dvp']
OF = ['o_pe', 'o_ps', 'o_px', 'o_re', 'o_rs', 'o_rx', 'o_pr', 'o_proe', 'o_plays']
DF = ['d_pe', 'd_ps', 'd_px', 'd_re', 'd_rs', 'd_rx', 'd_pr', 'd_proe', 'd_plays']
NM = dict(pe='pass EPA', ps='pass success', px='explosive pass', re='rush EPA', rs='rush success', rx='explosive run', pr='pressure rate', proe='pass-over-expected', plays='plays/game')
for c in OF + DF: P[c] = P[c].fillna(P[c].mean())
seasons = sorted(P.season.unique())

def loso(df, feats, model='ridge'):
    pred = np.full(len(df), np.nan)
    for s in seasons:
        tr, te = df.season != s, df.season == s
        m = Ridge(alpha=1.0) if model == 'ridge' else HGB(max_iter=250, learning_rate=0.04, min_samples_leaf=40)
        X = df[feats].values
        if model == 'ridge':
            mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9; Xs = (X - mu)/sd
            m.fit(Xs[tr], df.dk.values[tr]); pred[te] = m.predict(Xs[te])
        else:
            m.fit(X[tr], df.dk.values[tr]); pred[te] = m.predict(X[te])
    return pred

def rmse(y, p): return float(np.sqrt(np.mean((y - p)**2)))

# ---------- 1. unit matchups by position ----------
R['matchups'] = {}
for pos in ['QB', 'RB', 'WR', 'TE']:
    d = P[P.pos == pos].copy()
    pa = loso(d, BASE); pb = loso(d, BASE + OF + DF)
    ga = loso(d, BASE, 'hgb'); gb = loso(d, BASE + OF + DF, 'hgb')
    P.loc[d.index, 'resid'] = d.dk - pa
    # standardized effect of each unit stat on the residual (DK pts per 1 SD), full sample
    X = d[OF + DF]; Xs = (X - X.mean())/X.std()
    y = d.dk - pa
    A = np.c_[np.ones(len(d)), Xs.values]
    beta, *_ = np.linalg.lstsq(A, y.values, rcond=None)
    res = y.values - A @ beta; s2 = res.var()*len(d)/(len(d) - A.shape[1])
    se = np.sqrt(np.diag(s2*np.linalg.inv(A.T @ A)))
    eff = {f: (round(float(b), 2), round(float(b/s), 1)) for f, b, s in zip(OF + DF, beta[1:], se[1:])}
    R['matchups'][pos] = dict(n=len(d), rmse_base=round(rmse(d.dk, pa), 3), rmse_plus=round(rmse(d.dk, pb), 3),
                              hgb_base=round(rmse(d.dk, ga), 3), hgb_plus=round(rmse(d.dk, gb), 3),
                              off_share=round(float(np.abs(beta[1:10]).sum()/np.abs(beta[1:]).sum()), 2), eff=eff)

# ---------- 2. which side wins: team-game level ----------
T = P.drop_duplicates(['team', 'gkey'])[['team', 'gkey', 'season'] + OF + DF + ['g_'+u for u in NM]].dropna()
R['side'] = {}
for u in ['pe', 'ps', 're', 'rs', 'pr', 'px', 'proe', 'plays']:
    X = T[['o_'+u, 'd_'+u]]; Xs = (X - X.mean())/X.std(); y = T['g_'+u]
    b, *_ = np.linalg.lstsq(np.c_[np.ones(len(T)), Xs.values], y.values, rcond=None)
    R['side'][NM[u]] = dict(off=round(float(b[1]), 4), dfn=round(float(b[2]), 4), off_pct=round(float(abs(b[1])/(abs(b[1]) + abs(b[2]))*100)))

# ---------- 3. lockdown corner ----------
W = P[P.resid.notna()].copy()
W['role'] = np.where((W.pos == 'WR') & (W.wr_rank == 1), 'WR1', np.where((W.pos == 'WR') & (W.wr_rank == 2), 'WR2', np.where((W.pos == 'WR') & (W.wr_rank >= 3), 'WR3', W.pos)))
W['tier'] = pd.cut(W.cb1_pct, [0, .10, .33, .67, 1.0], labels=['elite (top 10%)', 'good', 'average', 'weak'])
W['tsh_delta'] = W.tgt_sh - W.ew_tgt_sh
W['d_pe_z'] = (W.d_pe - W.d_pe.mean())/W.d_pe.std()
def cell(x): return dict(n=int(len(x)), resid=round(float(x.resid.mean()), 2), se=round(float(x.resid.std()/np.sqrt(max(len(x), 1))), 2), tsh=round(float(x.tsh_delta.mean()*100), 1))
R['cb'] = {r: {str(t): cell(W[(W.role == r) & (W.tier == t)]) for t in W.tier.cat.categories} for r in ['WR1', 'WR2', 'WR3', 'TE', 'RB']}
# controlled for overall pass defense
R['cb_ctrl'] = {}
for r in ['WR1', 'WR2', 'TE', 'RB']:
    x = W[(W.role == r) & W.tier.notna()]
    A = np.c_[np.ones(len(x)), (x.tier == 'elite (top 10%)').astype(float), x.d_pe_z]
    b, *_ = np.linalg.lstsq(A, x.resid.values, rcond=None)
    res = x.resid.values - A @ b; se = np.sqrt(np.diag(res.var()*np.linalg.inv(A.T @ A)))
    R['cb_ctrl'][r] = dict(elite=round(float(b[1]), 2), se=round(float(se[1]), 2), n=int(len(x)))
R['cb1_out'] = {r: {str(int(k)): cell(W[(W.role == r) & (W.cb1_out == k)]) for k in [0, 1]} for r in ['WR1', 'WR2', 'TE']}

# ---------- 4. receiver archetypes vs elite corner ----------
X = W[W.role.isin(['WR1', 'WR2'])].copy()
sep_hi = X.sep.quantile(2/3)
X['big'] = (X.height >= 74) & (X.weight >= 205)
X['speed'] = X.forty <= 4.45
X['sepr'] = X.sep >= sep_hi
X['deep'] = X.ew_adot >= 13
X['short'] = X.ew_adot <= 9
X['elite'] = X.tier == 'elite (top 10%)'
R['arch'] = {}
for a, lab in [('big', 'big-bodied (6-2+, 205+)'), ('speed', 'speed (4.45 40 or faster)'), ('sepr', 'separator (top third separation)'), ('deep', 'deep threat (aDOT 13+)'), ('short', 'short/slot (aDOT 9 or less)')]:
    k = X[X[a].fillna(False)]; o = X[~X[a].fillna(False) & X[a].notna()]
    e, ne = k[k.elite], k[~k.elite]
    R['arch'][lab] = dict(n_all=int(len(k)), n_vs_elite=int(len(e)), vs_elite=round(float(e.resid.mean()), 2), vs_other=round(float(ne.resid.mean()), 2),
                          drop=round(float(e.resid.mean() - ne.resid.mean()), 2), se=round(float(np.sqrt(e.resid.var()/max(len(e), 1) + ne.resid.var()/max(len(ne), 1))), 2))
X['ht_gap'] = X.height - X.cb1_ht
X['gap_bin'] = pd.cut(X.ht_gap, [-20, -1, 1, 3, 20], labels=['WR shorter', 'about even', 'WR +2-3 in', 'WR +4 in or more'])
R['size_gap'] = {str(g): cell(X[X.gap_bin == g]) for g in X.gap_bin.cat.categories}

# ---------- 5. man vs zone fit ----------
Y = W[W.pos.isin(['WR', 'TE']) & W.man_edge.notna() & W.d_man.notna()].copy()
Y['fit'] = (Y.d_man - Y.d_man.mean())*Y.man_edge
Y['fit_bin'] = pd.qcut(Y.fit, 5, labels=['worst fit', '2', '3', '4', 'best fit'])
R['manzone'] = {str(b): cell(Y[Y.fit_bin == b]) for b in Y.fit_bin.cat.categories}
Z = X[X.sepr.notna() & X.d_man.notna()].copy(); Z['manheavy'] = Z.d_man >= Z.d_man.quantile(.75)
R['sep_vs_man'] = {f'{"separator" if s else "non-separator"} vs {"man-heavy D" if m else "other D"}': cell(Z[(Z.sepr == s) & (Z.manheavy == m)]) for s in [True, False] for m in [True, False]}

# ---------- 6. extra angles ----------
E = W.copy(); E['wind_b'] = pd.cut(E.wind.where(E.dome == 0), [-1, 9, 14, 19, 60], labels=['0-9', '10-14', '15-19', '20+'])
R['wind'] = {r: {str(b): cell(E[(E.pos == r) & (E.wind_b == b)]) for b in E.wind_b.cat.categories} for r in ['QB', 'WR', 'TE', 'RB']}
R['cold'] = {r: {'<=32F': cell(E[(E.pos == r) & (E.temp <= 32) & (E.dome == 0)]), '>32F': cell(E[(E.pos == r) & (E.temp > 32)])} for r in ['QB', 'WR', 'RB']}
R['dome'] = {r: {'dome': cell(E[(E.pos == r) & (E.dome == 1)]), 'outdoor': cell(E[(E.pos == r) & (E.dome == 0)])} for r in ['QB', 'WR', 'TE', 'RB']}
R['rest'] = {lab: cell(E[m]) for lab, m in [('short week (<=5 days)', E.rest <= 5), ('normal', E.rest.between(6, 8)), ('off bye (10+)', E.rest >= 10), ('opp off bye', E.opp_rest >= 10)]}
R['west_early'] = {lab: cell(E[E.west_early == k]) for lab, k in [('west team, 1pm ET road', 1), ('other', 0)]}
R['prime'] = {lab: cell(E[E.prime == k]) for lab, k in [('primetime', 1), ('day', 0)]}
R['div'] = {lab: cell(E[E.div_game == k]) for lab, k in [('division', 1), ('non-division', 0)]}
E['vac_b'] = pd.cut(E.vac_tgt, [-0.01, 0.01, 0.12, 0.25, 2], labels=['none', 'small (<12%)', 'one starter (12-25%)', 'big (25%+)'])
R['vacated_tgts'] = {r: {str(b): cell(E[(E.role == r) & (E.vac_b == b)]) for b in E.vac_b.cat.categories} for r in ['WR1', 'WR2', 'WR3', 'TE', 'RB']}
E['vacc_b'] = pd.cut(E.vac_car, [-0.01, 0.05, 0.3, 2], labels=['none', 'some', 'lead back out (30%+)'])
R['vacated_car'] = {str(b): cell(E[(E.pos == 'RB') & (E.vacc_b == b)]) for b in E.vacc_b.cat.categories}
R['qb_change'] = {r: {lab: cell(E[(E.pos == r) & (E.qb_change == k)]) for lab, k in [('new starting QB', 1), ('usual QB', 0)]} for r in ['QB', 'WR', 'TE', 'RB']}
E['blitz_b'] = pd.qcut(E.d_blitz, 3, labels=['low', 'mid', 'high'])
R['qb_vs_blitz'] = {str(b): cell(E[(E.pos == 'QB') & (E.blitz_b == b)]) for b in E.blitz_b.cat.categories}
json.dump(R, open(sys.argv[2], 'w'), indent=1)
print(json.dumps(R, indent=1))
