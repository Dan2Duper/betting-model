"""Self-tuning adjustment layer on top of the NFL DFS projection model.

The tree model (nfl_model.py) makes the base projection. This layer adds a handful of research-backed variables the base model
can't see or under-weights (dfs/research/matchup_results_2021_2025.json), each with its own weight per position:
  teammate out (vacated targets / carries), wind, dome, new starting QB, division game, coming off a bye, facing a team off a bye,
  and for a team's top two WRs, facing an elite or a weak top corner.
Weights start from a walk-forward fit on 2022-2025 (`python3 nfl_tune.py init` writes nfl_tune_init.json).
Each week after the games, the build grades the projections it made (base + adjustment) against actual DK points, logs how much
each variable helped or hurt, and nudges the weights a little toward what the newest weeks say (decayed history, pulled toward
the long-run fit so one odd week can't swing anything)."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nfl_data as d

VARS = {
    'QB': ['vac_car_gain', 'wind_x', 'dome', 'qb_new', 'div_game', 'off_bye', 'opp_off_bye'],
    'RB': ['vac_car_gain', 'vac_tgt_gain', 'vac_tgt_raw', 'wind_x', 'qb_new', 'div_game', 'off_bye', 'opp_off_bye'],
    'WR': ['vac_tgt_gain', 'vac_tgt_raw', 'wind_x', 'dome', 'qb_new', 'div_game', 'off_bye', 'opp_off_bye', 'cb_elite', 'cb_weak'],
    'TE': ['vac_tgt_gain', 'vac_tgt_raw', 'wind_x', 'dome', 'qb_new', 'div_game', 'off_bye', 'opp_off_bye'],
}
LABEL = dict(vac_car_gain='teammate out: carries', vac_tgt_gain='teammate out: targets (his share)', vac_tgt_raw='teammate out: targets (team)',
             wind_x='wind over 10 mph', dome='dome', qb_new='new starting QB', div_game='division game', off_bye='off a bye',
             opp_off_bye='opponent off a bye', cb_elite='vs elite top corner', cb_weak='vs weak top corner')
INIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'nfl_tune_init.json')
DECAY, PRIOR_N, STEP = 0.97, 600.0, 0.3

# ---------------- corners ----------------
def cb_table(seasons, status_lookup=None):
    """-> (hist: opp, season, week, cb1_pct), now: {team: cb1_pct}. cb1_pct = league percentile of the defense's best qualifying corner by
    shrunk yards allowed per target, rated on games before (0 = best). `status_lookup(name, team)` drops corners ruled out today."""
    fs = [f'{d.D}/advdef_{y}.parquet' for y in seasons if os.path.exists(f'{d.D}/advdef_{y}.parquet')]
    if not fs or not os.path.exists(f'{d.D}/players.parquet'): return pd.DataFrame(columns=['opponent_team', 'season', 'week', 'cb1_pct']), {}
    pl = pd.read_parquet(f'{d.D}/players.parquet', columns=['pfr_id', 'position'])
    ids = set(pl[pl.position.isin(['CB', 'DB'])].pfr_id.dropna())
    ad = pd.concat([pd.read_parquet(f) for f in fs]); ad = ad[(ad.game_type == 'REG') & ad.pfr_player_id.isin(ids)].copy()
    ad['team'] = ad.team.replace({'LAR': 'LA', 'WSH': 'WAS', 'JAC': 'JAX', 'LVR': 'LV', 'OAK': 'LV', 'SD': 'LAC', 'STL': 'LA'})
    ad['t'] = ad.def_targets.fillna(0); ad['y'] = ad.def_yards_allowed.fillna(0)
    ad = ad.sort_values(['pfr_player_id', 'season', 'week']).reset_index(drop=True)
    g = ad.groupby('pfr_player_id')
    for c in ('t', 'y'):
        ad['pre_' + c] = g[c].transform(lambda s: s.shift(1).ewm(halflife=12, min_periods=1).mean())
        ad['post_' + c] = g[c].transform(lambda s: s.ewm(halflife=12, min_periods=1).mean())
    ad['n_pre'] = g.cumcount(); ad['n_post'] = ad.n_pre + 1
    L, K = ad.y.sum() / max(1, ad.t.sum()), 40.0
    def rate(t, y, n):
        gg = np.minimum(n, 17); return (y * gg + K * L) / (t * gg + K), (t >= 3.5) & (t * gg >= 30)
    ad['r_pre'], ad['q_pre'] = rate(ad.pre_t, ad.pre_y, ad.n_pre)
    q = ad[ad.q_pre].copy(); q['pct'] = q.groupby(['season', 'week']).r_pre.rank(pct=True)
    hist = q.sort_values('r_pre').groupby(['team', 'season', 'week']).head(1)[['team', 'season', 'week', 'pct']]
    hist = hist.rename(columns={'team': 'opponent_team', 'pct': 'cb1_pct'})
    # now: each corner's rating after his latest game, if he played in his team's latest game and isn't ruled out
    last = ad.groupby('pfr_player_id').tail(1).copy()
    ad['gk'] = ad.season * 100 + ad.week; last['gk'] = last.season * 100 + last.week
    tl = ad.groupby('team').gk.max().rename('tk').reset_index()
    last = last.merge(tl, on='team'); last = last[last.gk == last.tk]
    last['r'], last['q'] = rate(last.post_t, last.post_y, last.n_post)
    if status_lookup: last = last.loc[np.array([status_lookup(n, t) not in ('O', 'IR', 'D') for n, t in zip(last.pfr_player_name, last.team)], dtype=bool)]
    last = last.loc[last['q'].astype(bool).values].copy(); last['pct'] = last['r'].rank(pct=True)
    now = last.sort_values('r').groupby('team').head(1).set_index('team').pct.to_dict()
    return hist, now

# ---------------- variables ----------------
def add_vars(P, cb_hist, cb_now, out_ids=(), qb_now=None):
    """Adds every tuning variable to P (history + upcoming rows). out_ids: upcoming players ruled out (O/IR/D) today.
    qb_now: {team: player_id of today's starting QB}."""
    P = P.copy(); out_ids = set(out_ids); qb_now = qb_now or {}
    up = P.upcoming.fillna(False).astype(bool)
    P['off_bye'] = (P.rest.fillna(7) >= 10).astype(float); P['opp_off_bye'] = (P.opp_rest.fillna(7) >= 10).astype(float)
    P['div_game'] = P.div_game.fillna(0).astype(float)
    P['wind_x'] = np.where(P.dome == 1, 0, np.maximum(0, P.wind.fillna(0) - 10)).astype(float)
    P['dome'] = P.dome.astype(float)
    # vacated roles: teammates who played in the team's last two games but don't play this one
    playing = P[~up | ~P.player_id.isin(out_ids)]
    vac = {}
    for (team, ssn), grp in playing.groupby(['team', 'season']):
        weeks = sorted(grp.week.unique()); played = {w: set(grp[grp.week == w].player_id) for w in weeks}
        hist_rows = grp[~grp.upcoming.fillna(False).astype(bool)].sort_values('week')
        for j, w in enumerate(weeks):
            recent = weeks[max(0, j - 2):j]
            if not recent: vac[(team, ssn, w)] = (0.0, 0.0); continue
            seen = set().union(*[played[r] for r in recent]); gone = seen - played[w]
            prior = hist_rows[(hist_rows.week < w) & hist_rows.player_id.isin(gone)]
            role = prior.groupby('player_id').tail(3).groupby('player_id')[['tgt_sh', 'car_sh']].mean()
            vac[(team, ssn, w)] = (float(role.tgt_sh.fillna(0).sum()), float(role.car_sh.fillna(0).sum()))
    k = list(zip(P.team, P.season, P.week))
    P['vac_tgt_raw'] = [min(0.7, vac.get(x, (0, 0))[0]) for x in k]
    vc = np.array([min(0.8, vac.get(x, (0, 0))[1]) for x in k])
    P['vac_tgt_gain'] = P.vac_tgt_raw * P.ew_tgt_sh.fillna(0) / np.maximum(0.3, 1 - P.vac_tgt_raw)
    P['vac_car_gain'] = vc * P.ew_car_sh.fillna(0) / np.maximum(0.3, 1 - vc)
    # new starting QB vs the team's usual starter this season
    G = d.games().sort_values(['team', 'season', 'week'])
    usual = {}
    for (team, ssn), grp in G[G.qb_id.notna()].groupby(['team', 'season']):
        cnt = {}
        for r in grp.itertuples():
            usual[(team, ssn, r.week)] = (max(cnt, key=cnt.get) if cnt else None, r.qb_id)
            if r.pts == r.pts: cnt[r.qb_id] = cnt.get(r.qb_id, 0) + 1
        usual[(team, ssn, 'now')] = max(cnt, key=cnt.get) if cnt else None
    def qbnew(team, ssn, wk, upc):
        if upc:
            u = usual.get((team, ssn, 'now')); s = qb_now.get(team)
        else:
            u, s = usual.get((team, ssn, wk), (None, None))
        return float(bool(u) and bool(s) and u != s)
    P['qb_new'] = [qbnew(t, s, w, u) for t, s, w, u in zip(P.team, P.season, P.week, up)]
    # top corner faced, for each team's top two WRs (by recent target share)
    wr = P.position == 'WR'
    P['wr_rank'] = np.nan
    act = wr & ~(up & P.player_id.isin(out_ids))
    P.loc[act, 'wr_rank'] = P[act].groupby(['team', 'season', 'week']).ew_tgt_sh.rank(ascending=False, method='first')
    P = P.drop(columns=[c for c in ('cb1_pct',) if c in P]).merge(cb_hist, on=['opponent_team', 'season', 'week'], how='left')
    if cb_now: P.loc[P.upcoming.fillna(False).astype(bool), 'cb1_pct'] = P.loc[P.upcoming.fillna(False).astype(bool), 'opponent_team'].map(cb_now)
    top2 = P.wr_rank <= 2
    P['cb_elite'] = (top2 & (P.cb1_pct <= 0.10)).astype(float)
    P['cb_weak'] = (top2 & (P.cb1_pct > 0.67)).astype(float)
    return P

def pool_mask(P):
    return (P.n_prior >= 1) & (((P.position == 'QB') & (P.ew_dk >= 10)) | ((P.position != 'QB') & (P.ew_dk >= 4)))

# ---------------- fitting / tuning ----------------
def _ridge_std(X, y, lam):
    mu, sd = X.mean(0), X.std(0) + 1e-9; Z = (X - mu) / sd
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return b / sd

def init():
    """Walk-forward fit: the base model is trained on seasons before each test season (2022-2025); weights are fit on its residuals."""
    import nfl_model as m
    S = list(range(2019, 2026)); d.fetch(S); P = d.features(S); T = d.team_rows(S)
    ch, _ = cb_table(S); P = add_vars(P, ch, {})
    R = []
    for test in (2022, 2023, 2024, 2025):
        M = m.train(P, T, [s for s in S if s < test])
        x = P[(P.season == test) & pool_mask(P) & P.implied.notna()].copy()
        x = m.predict(M, x); R.append(x)
    R = pd.concat(R); R['res'] = R.dk - R.proj
    out = dict(w={}, sd={}, report={})
    for pos, V in VARS.items():
        x = R[R.position == pos]; X = x[V].fillna(0).values; y = x.res.values
        def tfit(Xa, ya):
            w = _ridge_std(Xa, ya, lam=0.02 * len(ya))
            A = np.c_[np.ones(len(ya)), Xa]; beta, *_ = np.linalg.lstsq(A, ya, rcond=None)
            r = ya - A @ beta; se = np.sqrt(np.diag(r.var() * np.linalg.pinv(A.T @ A)))[1:]
            t = np.where(se > 0, beta[1:] / np.where(se > 0, se, 1), 0)
            keep = np.where(np.abs(t) >= 2, 1.0, np.where(np.abs(t) >= 1, 0.5, 0.0))   # weak evidence starts at half or zero weight
            return w * keep, t
        w, t = tfit(X, y)
        ad = np.zeros(len(x))       # honesty check: leave one season out
        for s in x.season.unique():
            tr, te = (x.season != s).values, (x.season == s).values
            ad[te] = X[te] @ tfit(X[tr], y[tr])[0]
        out['w'][pos] = {v: round(float(b), 3) for v, b in zip(V, w)}
        out['sd'][pos] = {v: round(float(s), 4) for v, s in zip(V, X.std(0))}
        rm = lambda e: round(float(np.sqrt(np.mean(e ** 2))), 3)
        out['report'][pos] = dict(n=len(x), rmse_base=rm(y), rmse_adj_oos=rm(y - ad), mae_base=round(float(np.abs(y).mean()), 3),
                                  mae_adj_oos=round(float(np.abs(y - ad).mean()), 3), t={v: round(float(a), 1) for v, a in zip(V, t)},
                                  big_vacancy_rows=int((x.vac_tgt_raw >= 0.15).sum() + (x.vac_car_gain >= 0.1).sum()),
                                  rmse_on_vacancy_rows=[rm(y[m]), rm((y - ad)[m])] if (m := ((x.vac_tgt_raw >= 0.15) | (x.vac_car_gain >= 0.1)).values).any() else None)
    # a position's variables start switched on only if they lowered out-of-sample error; otherwise they start at zero and are still
    # graded every week, so live results can earn them weight
    out['fit_w'] = {p: dict(v) for p, v in out['w'].items()}
    for p, r in out['report'].items():
        if r['rmse_adj_oos'] >= r['rmse_base']: out['w'][p] = {k: 0.0 for k in out['w'][p]}
        r['starts_on'] = any(out['w'][p].values())
    json.dump(out, open(INIT, 'w'), indent=1); print(json.dumps(out, indent=1))

def new_state():
    I = json.load(open(INIT))
    return dict(w={p: dict(v) for p, v in I['w'].items()}, w0=I['w'], sd=I['sd'], xtx={}, xty={}, pending={}, log=[])

def adjust(df, W):
    """df rows need 'position'/'pos' and the variable columns. -> adjustment in DK points (capped) and a short reason string."""
    pos = df['position'] if 'position' in df else df['pos']
    adj = np.zeros(len(df)); why = [''] * len(df)
    for i, (p, row) in enumerate(zip(pos, df.itertuples(index=False))):
        w = W.get(p)
        if not w: continue
        parts = [(v, w[v] * float(getattr(row, v, 0) or 0)) for v in w]
        adj[i] = sum(c for _, c in parts)
        big = sorted([x for x in parts if abs(x[1]) >= 0.4], key=lambda x: -abs(x[1]))[:3]
        why[i] = '; '.join(f"{LABEL[v]} {c:+.1f}" for v, c in big)
    return adj, why

def remember(state, season, week, rows):
    """Keep this week's base projections and variable values so they can be graded once the games are played."""
    P = state.setdefault('pending', {}) or {}
    if not isinstance(P, dict): P = {}
    k = f'{season}-{week}'; old = {r['player_id']: r for r in (P.get(k) or {}).get('rows', [])}
    for r in rows: old[r['player_id']] = r
    P[k] = dict(season=season, week=week, rows=list(old.values()))
    state['pending'] = dict(sorted(P.items())[-3:])

def grade(state, pend, actual):
    """pend: one remembered week; actual: DataFrame player_id, dk. Grades each variable and nudges weights. Returns the log entry or None."""
    if not pend or actual is None or not len(actual): return None
    rows = pd.DataFrame(pend['rows']).merge(actual, on='player_id', how='inner')
    if len(rows) < 30: return None
    entry = dict(season=pend['season'], week=pend['week'], n=int(len(rows)), pos={})
    for pos, V in VARS.items():
        x = rows[rows.pos == pos]
        if len(x) < 8: continue
        X = x[V].fillna(0).values; y = (x.dk - x.base).values
        w = np.array([state['w'][pos][v] for v in V]); w0 = np.array([state['w0'][pos][v] for v in V])
        sd = np.array([state['sd'][pos][v] for v in V])
        mae_base, mae_adj = np.abs(y).mean(), np.abs(y - X @ w).mean()
        gain = {v: round(float(np.abs(y - X @ w + X[:, j] * w[j]).mean() - mae_adj), 3) for j, v in enumerate(V)}   # + = variable helped
        xtx = np.array(state['xtx'].get(pos, np.zeros((len(V), len(V))).tolist())) * DECAY + X.T @ X
        xty = np.array(state['xty'].get(pos, np.zeros(len(V)).tolist())) * DECAY + X.T @ y
        Dg = np.diag(sd ** 2 * PRIOR_N + 1e-6)
        target = np.linalg.solve(xtx + Dg, xty + Dg @ w0)
        new = w + STEP * (target - w)
        state['xtx'][pos], state['xty'][pos] = xtx.tolist(), xty.tolist()
        state['w'][pos] = {v: round(float(b), 3) for v, b in zip(V, new)}
        entry['pos'][pos] = dict(n=int(len(x)), mae_base=round(float(mae_base), 2), mae_adj=round(float(mae_adj), 2),
                                 vars={v: dict(w_before=round(float(a), 3), w_after=round(float(b), 3), helped=gain[v]) for v, a, b in zip(V, w, new)})
    state['log'] = (state.get('log') or [])[-40:] + [entry]
    state.get('pending', {}).pop(f"{pend['season']}-{pend['week']}", None)
    return entry

def grade_all(state, season, week):
    """Grade every remembered week that is over (before the current one) and has box scores."""
    done = []
    for k, pend in list((state.get('pending') or {}).items()):
        if (pend['season'], pend['week']) >= (season, week): continue
        P, _ = d.load([pend['season']])
        x = P[(P.week == pend['week']) & (P.season_type == 'REG')].copy(); x['dk'] = d.dk_points(x)
        e = grade(state, pend, x[['player_id', 'dk']])
        if e: done.append(e)
        elif (season, week) > (pend['season'], pend['week'] + 2): state['pending'].pop(k, None)   # stale, never got box scores
    return done

if __name__ == '__main__' and sys.argv[1:2] == ['init']:
    init()
