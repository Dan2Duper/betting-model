"""Self-tuning adjustment layer on top of the MLB DFS projections (same design as dfs/nfl_tune.py).

The tree model (mlb_proj.py) makes the base projection from skill, lineup spot, park, weather and the run model. This layer adds
variables the base model cannot see or under-weights, each with its own weight for hitters (H) and starting pitchers (P):
  veg_gap    DraftKings' implied runs for the team minus the run model's (hitters: their own team; pitchers: the lineup they face)
  veg_win    pitchers: the moneyline's win chance minus the run model's
  pen_tired  hitters: pitches the opposing bullpen threw over the last 3 days, vs a normal load
  ump_k / ump_bb  plate umpire strikeout and walk rates vs the league
  park_hand  home-run park factor for this batter's side of the plate, vs the park's overall factor
Starting weights: walk-forward fit on the base model's 2022-25 misses (python3 dfs/research/mlb_vars.py DATA_DIR ROWS.parquet ->
dfs/mlb_tune_init.json); a role's variables start switched on only if they lowered out-of-sample error, otherwise they start at 0
and are still graded. Every build remembers base projection + variable values per player; once that day's box scores are in, the
next build grades each variable (helped = MAE gain), refits on decayed history pulled toward the long-run fit and moves each weight
30% of the way."""
import os, json, math, numpy as np, pandas as pd
VARS = {'H': ['veg_gap', 'pen_tired', 'ump_k', 'ump_bb', 'park_hand'], 'P': ['veg_gap', 'veg_win', 'ump_k', 'ump_bb']}
LABEL = dict(veg_gap='Vegas runs vs the model', veg_win='Vegas win chance vs the model', pen_tired='tired bullpen', ump_k="umpire's strikeout zone", ump_bb="umpire's walk rate", park_hand='park for his side of the plate')
INIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mlb_tune_init.json')
DECAY, PRIOR_N, STEP = 0.995, 4000.0, 0.3      # daily grading: slower decay and a heavier prior than the weekly NFL layer

def vegas_runs(mh, ma, total, win, offset=0.5):
    """DraftKings moneyline + total -> the runs each side is priced for, using the run model's own win curve
    P(home) = logistic(a + k * ln(home runs / away runs)). Returns (home, away, P(home)) or (None, None, None)."""
    try:
        ih = -mh / (-mh + 100.0) if mh < 0 else 100.0 / (mh + 100.0); ia = -ma / (-ma + 100.0) if ma < 0 else 100.0 / (ma + 100.0)
        ph = ih / (ih + ia); r = math.exp((math.log(ph / (1 - ph)) - win['a']) / win['k']); T = float(total) + offset
        return T * r / (1 + r), T / (1 + r), ph
    except Exception: return None, None, None


def _ft():
    """mlb/features.py helpers (decayed walk-forward tables), imported on first use."""
    import sys
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mlb')
    if p not in sys.path: sys.path.insert(0, p)
    import features
    return features

def park_hand(bat, G, hands):
    """Walk-forward HR park factor by batter side: (HR per PA at the park for that side / league rate for that side) divided by the same
    for all batters, minus 1. + = the park helps this side more than it helps hitters in general. Keyed (gid, side)."""
    b = bat[['gid', 'pid', 'date', 'B_PA', 'B_HR']].merge(G[['gid', 'park', 'asp', 'hsp', 'home']], on='gid'); th = hands.set_index('pid').throws; bh = hands.set_index('pid').bats
    side = b.pid.map(bh).fillna('R'); b['side'] = np.where(side == 'B', 'L', side)      # switch hitters mostly bat left (most starters are right-handed)
    b['day'] = _ft().day(b.date); b['key'] = b.park + '_' + b.side; b['lg'] = b.side; b['all'] = 'MLB'
    C = ['B_HR', 'B_PA']; PS = _ft().dec_table(b, 'key', C, 900.0); PA = _ft().dec_table(b, 'park', C, 900.0); LS = _ft().dec_table(b, 'lg', C, 365.0); LA = _ft().dec_table(b, 'all', C, 365.0)
    g = G[['gid', 'park', 'date']].copy(); g['day'] = _ft().day(g.date); out = []
    la = _ft().asof(g.assign(all='MLB')[['all', 'day']], LA, 'all', C, 365.0); pa = _ft().asof(g[['park', 'day']], PA, 'park', C, 900.0); la_r = (la.B_HR / la.B_PA.clip(lower=1)).values
    f_all = ((pa.B_HR + la_r * 3000) / (pa.B_PA + 3000)).values / np.maximum(la_r, 1e-6)
    for s in ('L', 'R'):
        ls = _ft().asof(g.assign(lg=s)[['lg', 'day']], LS, 'lg', C, 365.0); ps = _ft().asof(g.assign(key=g.park + '_' + s)[['key', 'day']], PS, 'key', C, 900.0); ls_r = (ls.B_HR / ls.B_PA.clip(lower=1)).values
        f = ((ps.B_HR + ls_r * 1500) / (ps.B_PA + 1500)).values / np.maximum(ls_r, 1e-6)
        out.append(pd.DataFrame(dict(gid=g.gid.values, side=s, park_hand=np.where(f_all > 0, f / np.maximum(f_all, 1e-6) - 1, 0.0))))
    return pd.concat(out, ignore_index=True)

def add_vars(R, G, FX, PH, win=None):
    """Adds the tuning variables to projection rows R (role H / P). G: games with closing ml_h, ml_a, tot. FX: run-model rows with pen_pit3,
    ump_k, ump_bb (batting-team rows). Missing information = 0."""
    win = win or json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mlb', 'model.json')))['win']; R = R.copy(); g = G.set_index('gid')
    v = [vegas_runs(a, b, t, win) if a == a and b == b and t == t and not sus else (None, None, None) for a, b, t, sus in zip(g.ml_h, g.ml_a, g.tot, g.ml_suspect | g.tot_suspect)]
    V = pd.DataFrame(v, index=g.index, columns=['vh', 'va', 'vp']).astype(float); V['home'] = g.home
    R = R.merge(V, left_on='gid', right_index=True, how='left'); ih = (R.team == R.home).values
    fx = FX[['gid', 'team', 'pen_pit3', 'ump_k', 'ump_bb']]
    isH = (R.role == 'H').values
    own = np.where(ih, R.vh, R.va); other = np.where(ih, R.va, R.vh)
    lam_own = np.where(isH, R.get('lam'), R.get('lam_for')); lam_opp = np.where(isH, R.get('opp_lam'), R.get('lam_against'))
    R['veg_gap'] = np.where(isH, own - lam_own, other - lam_opp); R['veg_gap'] = R.veg_gap.fillna(0.0).clip(-2.5, 2.5)
    pm_home = 1 / (1 + np.exp(-(win['a'] + win['k'] * np.log(np.where(ih, lam_own, lam_opp) / np.where(ih, lam_opp, lam_own)))))
    R['veg_win'] = np.where(isH, 0.0, np.where(ih, R.vp - pm_home, (1 - R.vp) - (1 - pm_home))); R['veg_win'] = R.veg_win.fillna(0.0).clip(-0.25, 0.25)
    R = R.merge(fx, on=['gid', 'team'], how='left')                      # batting-team row: the OPPOSING pen's workload, the game's umpire
    R['pen_tired'] = np.where(isH, ((R.pen_pit3 - 145.0) / 60.0).fillna(0.0), 0.0)
    R.loc[~isH, ['ump_k', 'ump_bb']] = R[~isH].merge(FX[['gid', 'team', 'ump_k', 'ump_bb']].rename(columns={'team': 'opp', 'ump_k': 'uk', 'ump_bb': 'ub'}), on=['gid', 'opp'], how='left')[['uk', 'ub']].values
    R['ump_k'] = ((R.ump_k.fillna(1.0) - 1.0) * 100).astype(float); R['ump_bb'] = ((R.ump_bb.fillna(1.0) - 1.0) * 100).astype(float)
    if PH is not None and 'bats_l' in R:
        side = np.where((R.bats_l.fillna(0) == 1) | (R.bats_s.fillna(0) == 1), 'L', 'R'); R['side'] = side
        R = R.merge(PH, on=['gid', 'side'], how='left'); R['park_hand'] = np.where(isH, R.park_hand.fillna(0.0) * 100, 0.0)
    else: R['park_hand'] = 0.0
    return R

def _ridge_std(X, y, lam):
    mu, sd = X.mean(0), X.std(0) + 1e-9; Z = (X - mu) / sd
    return np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean())) / sd
def fit(X, y):
    """Ridge weights, switched to half or zero where the evidence is weak (|t| < 2 / < 1), plus the t-values."""
    w = _ridge_std(X, y, lam=0.02 * len(y)); A = np.c_[np.ones(len(y)), X]; beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = y - A @ beta; se = np.sqrt(np.diag(r.var() * np.linalg.pinv(A.T @ A)))[1:]; t = np.where(se > 0, beta[1:] / np.where(se > 0, se, 1), 0)
    return w * np.where(np.abs(t) >= 2, 1.0, np.where(np.abs(t) >= 1, 0.5, 0.0)), t

def new_state():
    I = json.load(open(INIT))
    return dict(w={p: dict(v) for p, v in I['w'].items()}, w0=I['w'], sd=I['sd'], xtx={}, xty={}, pending={}, log=[])

def adjust(df, W):
    """df rows need 'role' (H / P) and the variable columns (0 = nothing to say). -> adjustment in DK points and a short reason."""
    adj = np.zeros(len(df)); why = [''] * len(df)
    for i, row in enumerate(df.itertuples(index=False)):
        w = W.get(row.role)
        if not w: continue
        parts = [(v, w[v] * float(getattr(row, v, 0) or 0)) for v in w]; adj[i] = sum(c for _, c in parts)
        big = sorted([x for x in parts if abs(x[1]) >= 0.25], key=lambda x: -abs(x[1]))[:2]
        why[i] = '; '.join(f"{LABEL[v]} {c:+.1f}" for v, c in big)
    return adj, why

def remember(state, day, rows):
    """Keep a day's base projections and variable values (latest build before the games wins) so they can be graded tomorrow."""
    P = state.setdefault('pending', {}) or {}
    if not isinstance(P, dict): P = {}
    old = {r['pid']: r for r in (P.get(day) or {}).get('rows', [])}
    for r in rows: old[r['pid']] = r
    P[day] = dict(day=day, rows=list(old.values())); state['pending'] = dict(sorted(P.items())[-4:])

def grade(state, pend, actual):
    """pend: one remembered day; actual: DataFrame pid, dk (starters' real DraftKings points). Returns the log entry or None."""
    if not pend or actual is None or not len(actual): return None
    rows = pd.DataFrame(pend['rows']).merge(actual, on='pid', how='inner')
    if len(rows) < 20: return None
    entry = dict(day=pend['day'], n=int(len(rows)), role={})
    for role, V in VARS.items():
        x = rows[rows.role == role]
        if len(x) < 6: continue
        X = x.reindex(columns=V).fillna(0).values.astype(float); y = (x.dk - x.base).values.astype(float)
        w = np.array([state['w'][role].get(v, 0.0) for v in V]); w0 = np.array([state['w0'][role].get(v, 0.0) for v in V]); sd = np.array([state['sd'][role].get(v, 1.0) for v in V])
        mae_base, mae_adj = np.abs(y).mean(), np.abs(y - X @ w).mean()
        gain = {v: round(float(np.abs(y - X @ w + X[:, j] * w[j]).mean() - mae_adj), 3) for j, v in enumerate(V)}      # + = the variable helped
        xtx = np.array(state['xtx'].get(role, np.zeros((len(V), len(V))).tolist())) * DECAY + X.T @ X
        xty = np.array(state['xty'].get(role, np.zeros(len(V)).tolist())) * DECAY + X.T @ y
        Dg = np.diag(sd ** 2 * PRIOR_N + 1e-6); target = np.linalg.solve(xtx + Dg, xty + Dg @ w0); new = w + STEP * (target - w)
        state['xtx'][role], state['xty'][role] = np.round(xtx, 4).tolist(), np.round(xty, 4).tolist()
        state['w'][role] = {v: round(float(b), 4) for v, b in zip(V, new)}
        entry['role'][role] = dict(n=int(len(x)), mae_base=round(float(mae_base), 3), mae_adj=round(float(mae_adj), 3),
                                   vars={v: dict(w_before=round(float(a), 4), w_after=round(float(b), 4), helped=gain[v]) for v, a, b in zip(V, w, new)})
    state['log'] = (state.get('log') or [])[-45:] + [entry]; state.get('pending', {}).pop(pend['day'], None)
    return entry

def grade_all(state, today, actual_for):
    """Grade every remembered day before `today`. actual_for(day) -> DataFrame pid, dk (or None while box scores are missing)."""
    done = []
    for k, pend in list((state.get('pending') or {}).items()):
        if k >= today: continue
        e = grade(state, pend, actual_for(k))
        if e: done.append(e)
        elif (pd.Timestamp(today) - pd.Timestamp(k)).days > 3: state['pending'].pop(k, None)      # stale: never got box scores (postponed slate)
    return done
