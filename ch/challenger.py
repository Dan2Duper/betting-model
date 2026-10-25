"""Challenger model (Oct 2026): self-learning ratings + weekly re-weighting learning layer, NFL and CFB.

Stage 1 - Kalman ratings: every team has an offense and a defense rating (points scale) with its own uncertainty. Each game
nudges them by a blend of points scored and play-by-play EPA; how far depends on how sure we are about each team. Between seasons
ratings regress (NFL toward average, college toward the program's own long-run level) and uncertainty widens. NFL also adjusts
for the starting quarterback vs the team's usual one. Knobs live in knobs.json (tuned walk-forward on 2014-2025).

Stage 2 - learning layer: online ridge regression with forgetting that re-weighs, every week, the challenger ratings, the
previous production model, (college) Elo, and situational inputs. Situational inputs start at zero weight under a strong
prior and only earn weight by predicting games they had not seen yet.

Only information known by the Tuesday run is used (no weather, injuries, referees or game-day QB news: the page adds those live).
Usage: python3 ch/challenger.py backtest NFL|CFB     (walk-forward check)
       from ch.challenger import predict_nfl, predict_cfb   (weekly snapshot)"""
import os, sys, json, glob
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG); sys.path.insert(0, f'{PKG}/cfb')
from paths import DATA
KN = json.load(open(f'{HERE}/knobs.json'))
REN = {'OAK': 'LV', 'SD': 'LAC', 'STL': 'LA'}
TZ = {t: 0 for t in ['NE', 'NYJ', 'NYG', 'BUF', 'MIA', 'PHI', 'WAS', 'PIT', 'BAL', 'CLE', 'CIN', 'DET', 'ATL', 'CAR', 'TB', 'JAX', 'IND']}
TZ.update({t: -1 for t in ['CHI', 'GB', 'MIN', 'KC', 'NO', 'HOU', 'DAL', 'TEN']}); TZ.update({'DEN': -2, 'ARI': -3, 'LV': -3, 'LA': -3, 'LAC': -3, 'SF': -3, 'SEA': -3})
P4 = {'SEC', 'Big Ten', 'Big 12', 'ACC', 'Pac-12'}
ELEV = {'Air Force': 6620, 'Wyoming': 7220, 'Colorado': 5360, 'Colorado State': 5000, 'New Mexico': 5100, 'Utah': 4660, 'Utah State': 4700,
        'BYU': 4550, 'Nevada': 4500, 'UTEP': 3800, 'New Mexico State': 3900, 'Texas Tech': 3240, 'Boise State': 2700, 'Washington State': 2350,
        'Arizona': 2400, 'UNLV': 2000, 'Idaho': 2600}

# ---------------------------------------------------------------- learning layer
class OnlineRidge:
    def __init__(self, prior, prec, forget):
        self.th0 = np.asarray(prior, float); self.Ap = np.diag(prec).astype(float); self.A = self.Ap.copy(); self.b = self.Ap @ self.th0; self.f = forget
    @property
    def theta(self): return np.linalg.solve(self.A, self.b)
    def update(self, X, y):
        f = self.f ** len(y); self.A = f * self.A + (1 - f) * self.Ap; self.b = f * self.b + (1 - f) * (self.Ap @ self.th0)
        self.A += X.T @ X; self.b += X.T @ y
def walk(D, feats, target, prior, n_eff, forget, train_mask):
    """Predict every week from earlier weeks only, then learn from that week (rows in train_mask with a result)."""
    var = D.loc[train_mask, feats].var().replace(0, 1).fillna(1).values
    m = OnlineRidge(prior, np.asarray(n_eff) * var, forget); pred = np.full(len(D), np.nan)
    for _, wk in D.groupby(['season', 'week'], sort=False):
        ix = D.index.get_indexer(wk.index); X = wk[feats].values.astype(float); pred[ix] = X @ m.theta
        ok = train_mask[ix] & wk[target].notna().values
        if ok.any(): m.update(X[ok], wk[target].values[ok].astype(float))
    return pred, m

def shrunk_prior(keys, vals, k):
    s, n, out = {}, {}, np.zeros(len(keys))
    for i, (kk, v) in enumerate(zip(keys, vals)):
        out[i] = s.get(kk, 0.0) / (n.get(kk, 0) + k)
        if not (isinstance(v, float) and np.isnan(v)): s[kk] = s.get(kk, 0.0) + v; n[kk] = n.get(kk, 0) + 1
    return out
def luck_pre(team_rows):
    """season-to-date close-game luck: wins minus expected wins from the margins (expected = logistic of margin/7)."""
    w = np.where(team_rows.mar > 0, 1.0, np.where(team_rows.mar < 0, 0.0, 0.5)); e = 1 / (1 + np.exp(-team_rows.mar / 7))
    return pd.Series(w - e, index=team_rows.index).groupby([team_rows.team, team_rows.season]).transform(lambda s: s.shift().fillna(0).cumsum())

# ================================================================= NFL
def nfl_tables(season):
    import importlib.util as iu
    spec = iu.spec_from_file_location('nfl_engine', f'{PKG}/nfl/engine.py'); E = iu.module_from_spec(spec); sys.path.insert(0, f'{PKG}/nfl'); spec.loader.exec_module(E)
    out = E.load(season); G, TG = out[0], out[1]
    G = G.copy(); TG = TG.copy()
    for c in ('home_team', 'away_team'): G[c] = G[c].replace(REN)
    for c in ('posteam', 'defteam', 'home_team'): TG[c] = TG[c].replace(REN)
    return G, TG
def nfl_qb(TG, half=24, k=250, m0=-0.06):
    hist = {}; pre = np.full(len(TG), np.nan); TG = TG.reset_index(drop=True)
    for i in TG.sort_values(['gameday', 'game_id']).index:
        q = TG.at[i, 'qb_id']
        if pd.isna(q): continue
        h = hist.get(q, (0.0, 0.0)); pre[i] = (h[0] + k * m0) / (h[1] + k); d = 0.5 ** (1 / half)
        hist[q] = (h[0] * d + (TG.at[i, 'qb_epa_sum'] or 0), h[1] * d + (TG.at[i, 'qb_db'] or 0))
    TG['qbv'] = pre
    after = {q: (h[0] + k * m0) / (h[1] + k) for q, h in hist.items()}
    return TG, after
def nfl_rows(G, TG):
    TG, after = nfl_qb(TG); TG['epa_sum'] = TG.epa_all * TG.plays
    key = TG.set_index(['game_id', 'posteam'])
    last_qb = TG.dropna(subset=['qb_id']).sort_values('gameday').groupby('posteam').qb_id.last()
    rows = []
    for g in G.itertuples():
        r = {}
        for side, t in (('h', g.home_team), ('a', g.away_team)):
            try: x = key.loc[(g.game_id, t)]; r[f'{side}_epa'], r[f'{side}_qbv'], r[f'{side}_qb'] = x.epa_sum, x.qbv, x.qb_id
            except KeyError: q = last_qb.get(t); r[f'{side}_epa'], r[f'{side}_qbv'], r[f'{side}_qb'] = np.nan, after.get(q, np.nan), q
        rows.append(dict(game_id=g.game_id, season=g.season, week=g.week, home=g.home_team, away=g.away_team, neutral=g.location == 'Neutral',
                         hs=g.home_score, as_=g.away_score, line_m=g.spread_line, line_t=g.total_line, espn=g.espn, **r))
    D = pd.DataFrame(rows).sort_values(['season', 'week', 'game_id']).reset_index(drop=True); D['done'] = D.hs.notna()
    tr = TG[TG.season <= 2016].dropna(subset=['epa_sum', 'points']); EP = np.polyfit(tr.epa_sum, tr.points, 1)
    return D, EP
def nfl_kalman(D, EP, par):
    alpha, qw, qs, rho, r, hfa, bq, p0 = par
    teams = sorted(set(D.home) | set(D.away)); TI = {t: i for i, t in enumerate(teams)}; NT = len(teams)
    x = np.zeros(2 * NT); P = np.eye(2 * NT) * p0; mu = 22.0; season = None; qbe = {}; ph = np.full(len(D), np.nan); pa = ph.copy()
    for (s, w), wk in D.groupby(['season', 'week'], sort=False):
        if s != season:
            if season is not None: x *= rho; P = P * rho ** 2 + np.eye(2 * NT) * qs
            season = s
        else: P = P + np.eye(2 * NT) * qw
        q = {}
        for g in wk.itertuples():
            hh = 0 if g.neutral else hfa / 2
            qh = bq * 38 * (g.h_qbv - qbe.get(g.home, g.h_qbv)) if not np.isnan(g.h_qbv) else 0.0
            qa = bq * 38 * (g.a_qbv - qbe.get(g.away, g.a_qbv)) if not np.isnan(g.a_qbv) else 0.0
            i, j = TI[g.home], TI[g.away]; q[g.Index] = (hh, qh, qa)
            ph[g.Index] = mu + x[i] + x[NT + j] + hh + qh; pa[g.Index] = mu + x[j] + x[NT + i] - hh + qa
        for g in wk.itertuples():
            if not g.done or np.isnan(g.h_epa) or np.isnan(g.a_epa): continue
            i, j = TI[g.home], TI[g.away]; hh, qh, qa = q[g.Index]
            for (o, dd, pts, epa, sgn, qq) in ((i, j, g.hs, g.h_epa, 1, qh), (j, i, g.as_, g.a_epa, -1, qa)):
                y = alpha * pts + (1 - alpha) * (EP[0] * epa + EP[1])
                Hv = np.zeros(2 * NT); Hv[o] = 1; Hv[NT + dd] = 1
                res = y - (mu + x[o] + x[NT + dd] + sgn * hh + qq); PH = P @ Hv; S = Hv @ PH + r; K = PH / S
                x = x + K * res; P = P - np.outer(K, PH); mu += 0.002 * res
            for t, v in ((g.home, g.h_qbv), (g.away, g.a_qbv)):
                if not np.isnan(v): qbe[t] = v if t not in qbe else 0.8 * qbe[t] + 0.2 * v
        x[:NT] -= x[:NT].mean(); x[NT:] -= x[NT:].mean()
    return ph, pa
def nfl_feats(G, TG, D):
    G = G.set_index('game_id').loc[D.game_id].reset_index()
    F = pd.DataFrame(index=D.index)
    hr, ar = G.home_rest.fillna(7).clip(4, 14).values, G.away_rest.fillna(7).clip(4, 14).values
    hour = G.gametime.fillna('13:00').str[:2].astype(int).values
    tzd = (G.home_team.map(TZ).fillna(0) - G.away_team.map(TZ).fillna(0)).values
    F['rest_diff'] = (hr - ar) / 7; F['bye_diff'] = (hr >= 13).astype(float) - (ar >= 13).astype(float)
    F['west_early'] = np.where((hour <= 13) & (tzd > 0), tzd, 0) / 3; F['east_west'] = np.where(tzd < 0, tzd, 0) / 3
    F['body_clock'] = np.where(G.gametime.fillna('13:00').values >= '20:00', -tzd / 3, 0.0)
    # per-team history from game results
    L = pd.concat([pd.DataFrame(dict(game_id=G.game_id, season=G.season, week=G.week, team=G.home_team, home=1, mar=G.home_score - G.away_score, coach=G.home_coach)),
                   pd.DataFrame(dict(game_id=G.game_id, season=G.season, week=G.week, team=G.away_team, home=0, mar=G.away_score - G.home_score, coach=G.away_coach))])
    L = L.sort_values(['team', 'season', 'week']).reset_index(drop=True)
    L['prev_mar'] = L.groupby(['team', 'season']).mar.shift().fillna(0)
    L['luck'] = luck_pre(L.fillna({'mar': 0}))
    first_coach = L.groupby(['team', 'season']).coach.transform('first'); prev_coach = L.groupby('team').coach.shift()
    L['new_coach'] = (L.groupby(['team', 'season']).cumcount().eq(0) & (L.coach != prev_coach)).groupby([L.team, L.season]).transform('max').astype(float)
    L['away_run'] = L.groupby(['team', 'season']).home.transform(lambda s: (1 - s).groupby((s != s.shift()).cumsum()).cumsum())
    TGs = TG.sort_values(['posteam', 'season', 'week']).copy()
    TGs['qb_change'] = (TGs.qb_id != TGs.groupby('posteam').qb_id.shift()).astype(float); TGs.loc[TGs.groupby('posteam').head(1).index, 'qb_change'] = 0
    o = TGs[['game_id', 'posteam', 'fumbles', 'fum_lost']]; d = o.rename(columns={'posteam': 'opp', 'fumbles': 'o_fum', 'fum_lost': 'o_lost'})
    TGs = TGs.merge(d, left_on=['game_id', 'defteam'], right_on=['game_id', 'opp'], how='left')
    TGs['fl'] = 0.5 * TGs.fumbles.fillna(0) - TGs.fum_lost.fillna(0) + TGs.o_lost.fillna(0) - 0.5 * TGs.o_fum.fillna(0)
    TGs['fum_luck'] = TGs.groupby(['posteam', 'season']).fl.transform(lambda s: s.shift().fillna(0).cumsum())
    TGs['tempo'] = TGs.groupby(['posteam', 'season']).plays.transform(lambda s: s.shift().expanding().mean())
    lgp = TGs.groupby('season').plays.mean()
    Li = L.set_index(['game_id', 'team']); Ti = TGs.set_index(['game_id', 'posteam'])
    # season-to-date state for teams in games not yet played: their latest values
    lastL = L[L.mar.notna()].groupby('team').tail(1).set_index('team'); lastT = TGs.groupby('posteam').tail(1).set_index('posteam')
    nxt = {'fum_luck': lastT.fum_luck + lastT.fl, 'tempo': TGs.groupby('posteam').tail(6).groupby('posteam').plays.mean()}   # values going into the next game
    cur_season = TGs.groupby('posteam').season.last()
    def lv(gid, t, col, src='L'):
        S = Li if src == 'L' else Ti
        try: return S.at[(gid, t), col]
        except KeyError:
            if src == 'T' and col in nxt and gid[:4].isdigit() and int(gid[:4]) == cur_season.get(t): return nxt[col].get(t, np.nan)
            return np.nan
    h = lambda col, src='L': np.array([lv(g, t, col, src) for g, t in zip(G.game_id, G.home_team)], float)
    a = lambda col, src='L': np.array([lv(g, t, col, src) for g, t in zip(G.game_id, G.away_team)], float)
    n0 = lambda v: np.nan_to_num(v)
    F['bounce'] = (np.clip(-n0(h('prev_mar')), 0, 40) - np.clip(-n0(a('prev_mar')), 0, 40)) / 14
    F['luck'] = n0(h('luck')) - n0(a('luck')); F['new_coach'] = n0(h('new_coach')) - n0(a('new_coach'))
    F['qb_change'] = n0(h('qb_change', 'T')) - n0(a('qb_change', 'T'))
    F['fum_luck'] = n0(h('fum_luck', 'T')) - n0(a('fum_luck', 'T'))
    F['road_trip'] = -(n0(a('away_run')) >= 3).astype(float)
    F['short_cross'] = -((G.away_rest.fillna(7).values <= 5) & (np.abs(tzd) >= 2)).astype(float)
    mar = (G.home_score - G.away_score).values
    F['team_hfa'] = shrunk_prior(G.home_team.values, np.where(G.location == 'Neutral', np.nan, mar - G.spread_line.values), 30) / 3
    tp = (h('tempo', 'T') + a('tempo', 'T')) / 2 - G.season.map(lgp).values; F['tempo'] = n0(tp) / 5
    F['dome'] = G.roof.isin(['dome', 'closed']).astype(float).values; F['div'] = G.div_game.fillna(0).astype(float).values
    F['late'] = (G.week >= 13).astype(float).values; F['one'] = 1.0
    return F
def nfl_frame(season):
    G, TG = nfl_tables(season)
    D, EP = nfl_rows(G, TG); k = KN['NFL']
    D['ph'], D['pa'] = nfl_kalman(D, EP, k['kalman'])
    D = pd.concat([D, nfl_feats(G, TG, D)], axis=1)
    ch = pd.read_parquet(f'{HERE}/hist/nfl_champ.parquet')
    D = D.merge(ch, on='game_id', how='left')
    pl = f'{PKG}/logs/predictions_log.csv'      # production model's pre-game predictions for games after the history file
    if os.path.exists(pl):
        L = pd.read_csv(pl, dtype={'id': str}); L = L[L.league == 'NFL'].drop_duplicates('id', keep='last')
        if 'pred_home0' in L: L['pred_home'] = L.pred_home0.fillna(L.pred_home); L['pred_away'] = L.pred_away0.fillna(L.pred_away)   # previous model's numbers
        m = dict(zip(L.id, zip(L.pred_home - L.pred_away, L.pred_home + L.pred_away)))
        k2 = [m.get(str(int(e)) if pd.notna(e) else gid, m.get(gid)) for e, gid in zip(D.espn, D.game_id)]
        miss = D.pm.isna()
        D.loc[miss, 'pm'] = [v[0] if v else np.nan for v, ms in zip(k2, miss) if ms]
        D.loc[miss, 'pt'] = [v[1] if v else np.nan for v, ms in zip(k2, miss) if ms]
    D['cm'] = D.ph - D.pa; D['ct'] = D.ph + D.pa
    D['pm'] = D.pm.fillna(D.cm); D['pt'] = D.pt.fillna(D.ct)        # before 2019 / unlogged weeks: lean on the challenger
    D['mar'] = D.hs - D.as_; D['tot'] = D.hs + D.as_
    return D
NFL_M = ['rest_diff', 'bye_diff', 'west_early', 'east_west', 'qb_change', 'bounce', 'luck', 'new_coach', 'body_clock', 'fum_luck', 'team_hfa', 'road_trip', 'short_cross']
NFL_T = ['dome', 'div', 'late', 'tempo']

# ================================================================= CFB
def cfb_frame(season):
    H = pd.read_parquet(f'{HERE}/hist/cfb_games.parquet'); H = H[H.season < season]
    TGH = pd.read_parquet(f'{HERE}/hist/cfb_tg.parquet'); TGH = TGH[TGH.season < season]
    cur, tgc = [], []
    for s in range(max(2026, season), season + 1) if season > 2025 else []:
        f = f'{DATA}/cfb/repo/schedules/csv/cfb_schedules_{s}.csv'
        if os.path.exists(f): cur.append(pd.read_csv(f))
        t = f'{DATA}/cfb/tg_{s}.parquet'
        if os.path.exists(t): tgc.append(pd.read_parquet(t))
    TG = pd.concat([TGH] + [t[['game_id', 'season', 'week', 'pos_team', 'def_pos_team', 'plays', 'turnovers', 'epa', 'stype']] for t in tgc], ignore_index=True)
    TG['epa_sum'] = TG.epa * TG.plays
    if cur:
        S = pd.concat(cur); S = S[(S.home_division == 'fbs') | (S.away_division == 'fbs')].drop_duplicates('game_id')
        e = TG.set_index(['game_id', 'pos_team']).epa_sum
        S['h_epa'] = [e.get((g, t), np.nan) for g, t in zip(S.game_id, S.home_team)]; S['a_epa'] = [e.get((g, t), np.nan) for g, t in zip(S.game_id, S.away_team)]
        for c in ('line_m', 'line_t', 'open_m', 'open_t'): S[c] = np.nan
        H = pd.concat([H, S[[c for c in H.columns if c in S.columns]]], ignore_index=True)
    D = H[H.home_team.notna() & H.away_team.notna()].copy()
    D['start'] = pd.to_datetime(D.start_date, utc=True, errors='coerce'); D['wk'] = D.week.where(D.season_type == 'regular', 20)
    D = D.sort_values(['season', 'start', 'game_id']).reset_index(drop=True)
    D['ph'], D['pa'] = cfb_kalman(D, KN['CFB']['kalman'])
    ch = pd.read_parquet(f'{HERE}/hist/cfb_champ.parquet'); D = D.merge(ch, on='game_id', how='left')
    pl = f'{PKG}/logs/predictions_log.csv'
    if os.path.exists(pl):
        L = pd.read_csv(pl, dtype={'id': str}); L = L[L.league == 'CFB'].drop_duplicates('id', keep='last').set_index('id')
        if 'pred_home0' in L: L['pred_home'] = L.pred_home0.fillna(L.pred_home); L['pred_away'] = L.pred_away0.fillna(L.pred_away)
        miss = D.ph_c.isna() & D.game_id.astype(str).isin(L.index)
        D.loc[miss, 'ph_c'] = D.loc[miss, 'game_id'].astype(str).map(L.pred_home).values; D.loc[miss, 'pa_c'] = D.loc[miss, 'game_id'].astype(str).map(L.pred_away).values
    D = pd.concat([D, cfb_feats(D, TG)], axis=1)
    D['cm'] = D.ph - D.pa; D['ct'] = D.ph + D.pa; D['pm'] = (D.ph_c - D.pa_c).fillna(D.cm); D['pt'] = (D.ph_c + D.pa_c).fillna(D.ct)
    D['elo'] = ((D.home_pregame_elo - D.away_pregame_elo) * 0.0422 * 2 + np.where(D.neutral_site.fillna(False).astype(bool), 0, 2.5)).fillna(0)
    D['mar'] = D.home_points - D.away_points; D['tot'] = D.home_points + D.away_points
    return D
def cfb_kalman(D, par):
    alpha, qw, qs, rho, r, hfa, p0, fcs = par
    teams = sorted(set(D.home_team) | set(D.away_team)); TI = {t: i for i, t in enumerate(teams)}; NT = len(teams)
    fbs = set(pd.concat([D.home_team[D.home_division == 'fbs'], D.away_team[D.away_division == 'fbs']])); isf = np.array([t in fbs for t in teams])
    tr = D[(D.season <= 2016)].dropna(subset=['h_epa', 'a_epa', 'home_points', 'away_points'])
    EP = np.polyfit(np.r_[tr.h_epa, tr.a_epa], np.r_[tr.home_points, tr.away_points], 1)
    o = np.where(isf, 0.0, -fcs / 2); d = np.where(isf, 0.0, fcs / 2); vo = np.full(NT, p0); vd = np.full(NT, p0); po, pd_ = o.copy(), d.copy()
    mu = 28.0; season = None; ph = np.full(len(D), np.nan); pa = ph.copy()
    hi, ai = D.home_team.map(TI).values, D.away_team.map(TI).values; neu = D.neutral_site.fillna(False).values.astype(bool)
    hs, as_ = D.home_points.values.astype(float), D.away_points.values.astype(float); he, ae = D.h_epa.values.astype(float), D.a_epa.values.astype(float)
    key = list(zip(D.season.values, D.wk.values)); s0 = 0; blocks = []
    for k in range(1, len(D) + 1):
        if k == len(D) or key[k] != key[s0]: blocks.append((s0, k)); s0 = k
    for s0, s1 in blocks:
        s = key[s0][0]
        if s != season:
            if season is not None:
                po = 0.7 * po + 0.3 * o; pd_ = 0.7 * pd_ + 0.3 * d; o = rho * o + (1 - rho) * po; d = rho * d + (1 - rho) * pd_
                vo = rho ** 2 * vo + qs; vd = rho ** 2 * vd + qs
            season = s
        else: vo = vo + qw; vd = vd + qw
        for k in range(s0, s1):
            h, a = hi[k], ai[k]; hh = 0.0 if neu[k] else hfa / 2; ph[k] = mu + o[h] + d[a] + hh; pa[k] = mu + o[a] + d[h] - hh
        for k in range(s0, s1):
            if np.isnan(hs[k]): continue
            h, a = hi[k], ai[k]; hh = 0.0 if neu[k] else hfa / 2
            for (x, y_, pts, ep, sg) in ((h, a, hs[k], he[k], 1), (a, h, as_[k], ae[k], -1)):
                y = pts if np.isnan(ep) else alpha * pts + (1 - alpha) * (EP[0] * ep + EP[1])
                res = y - (mu + o[x] + d[y_] + sg * hh); S = vo[x] + vd[y_] + r; ko, kd = vo[x] / S, vd[y_] / S
                o[x] += ko * res; d[y_] += kd * res; vo[x] *= (1 - ko); vd[y_] *= (1 - kd); mu += 0.001 * res
    return ph, pa
def cfb_feats(D, TG):
    from campus import C
    F = pd.DataFrame(index=D.index)
    et = D.start.dt.tz_convert('America/New_York'); hr = (et.dt.hour + et.dt.minute / 60).values; month = et.dt.month.values
    neu = D.neutral_site.fillna(False).astype(bool).values
    lat = lambda t: C[t][0] if t in C else np.nan; tz = lambda t: C[t][2] if t in C else np.nan
    hl, al = D.home_team.map(lat).values, D.away_team.map(lat).values; htz, atz = D.home_team.map(tz).values, D.away_team.map(tz).values
    L = pd.concat([pd.DataFrame(dict(game_id=D.game_id, season=D.season, start=D.start, team=D.home_team, home=np.where(neu, 1, 1), away=np.where(neu, 0, 0),
                                     mar=D.home_points - D.away_points, oelo=D.away_pregame_elo, elo=D.home_pregame_elo, post=D.home_postgame_elo)),
                   pd.DataFrame(dict(game_id=D.game_id, season=D.season, start=D.start, team=D.away_team, home=0, away=np.where(neu, 0, 1),
                                     mar=D.away_points - D.home_points, oelo=D.home_pregame_elo, elo=D.away_pregame_elo, post=D.away_postgame_elo))])
    L = L.sort_values(['team', 'start']).reset_index(drop=True)
    g = L.groupby(['team', 'season'])
    L['prev_mar'] = g.mar.shift(); L['prev_oelo'] = g.oelo.shift(); L['prev_start'] = g.start.shift()
    L['luck'] = luck_pre(L.fillna({'mar': 0}))
    L['away_run'] = g.away.transform(lambda s: s.groupby((s != s.shift()).cumsum()).cumsum() * s)
    L['next_opp_elo'] = g.oelo.shift(-1)
    top = D.groupby('season').home_pregame_elo.transform(lambda s: s.quantile(0.85)); q85 = dict(zip(D.season, top))
    L['letdown'] = ((L.prev_mar > 0) & (L.prev_oelo >= L.season.map(q85))).astype(float)
    L['lookahead'] = ((L.next_opp_elo >= L.season.map(q85)) & (L.oelo < L.elo)).astype(float)
    L['rest'] = ((L.start - L.prev_start).dt.days).clip(4, 15).fillna(7)
    T = TG.copy(); dd = T[['game_id', 'pos_team', 'turnovers']].rename(columns={'pos_team': 'def_pos_team', 'turnovers': 'takeaways'})
    T = T.merge(dd, on=['game_id', 'def_pos_team'], how='left'); T['tom'] = T.takeaways.fillna(0) - T.turnovers.fillna(0)
    T = T.sort_values(['pos_team', 'season', 'week'])
    T['to_luck'] = T.groupby(['pos_team', 'season']).tom.transform(lambda s: s.shift().fillna(0).cumsum())
    T['tempo'] = T.groupby(['pos_team', 'season']).plays.transform(lambda s: s.shift().expanding().mean())
    lgp = T.groupby('season').plays.mean()
    last = T.groupby(['pos_team', 'season']).tail(1)   # for games not yet in the play-by-play: the team's totals so far
    last = last.assign(to_next=last.to_luck + last.tom, tempo_next=last.groupby(['pos_team', 'season']).plays.transform('mean'))
    Ti = T.set_index(['game_id', 'pos_team']); Li = L.set_index(['game_id', 'team'])
    lastT = last.set_index(['pos_team', 'season'])
    def tv(col, side):
        out = []
        for gid, t, s in zip(D.game_id, D[side], D.season):
            try: out.append(Ti.at[(gid, t), col])
            except KeyError:
                try: out.append(lastT.at[(t, s), 'to_next' if col == 'to_luck' else 'tempo_next'])
                except KeyError: out.append(np.nan)
        return np.array(out, float)
    lv = lambda col, side: np.array([Li.at[(gid, t), col] if (gid, t) in Li.index else np.nan for gid, t in zip(D.game_id, D[side])], float)
    n0 = np.nan_to_num
    F['rest_diff'] = (lv('rest', 'home_team') - lv('rest', 'away_team')) / 7
    F['bounce'] = (np.clip(-n0(lv('prev_mar', 'home_team')), 0, 50) - np.clip(-n0(lv('prev_mar', 'away_team')), 0, 50)) / 21
    F['letdown'] = n0(lv('letdown', 'home_team')) - n0(lv('letdown', 'away_team'))
    F['luck'] = n0(lv('luck', 'home_team')) - n0(lv('luck', 'away_team'))
    F['tz_travel'] = np.where(neu, 0, n0(np.abs(atz - htz))) / 3
    F['altitude'] = ((~neu) & (D.home_team.map(lambda t: ELEV.get(t, 600)).values >= 4000) & (D.away_team.map(lambda t: ELEV.get(t, 600)).values < 2500)).astype(float)
    F['south_in_cold'] = ((~neu) & (al <= 33.5) & (hl >= 40) & (month >= 11)).astype(float)
    F['north_in_heat'] = ((~neu) & (al >= 39) & (hl <= 31.5) & (month <= 9)).astype(float)
    F['body_clock'] = np.where(hr >= 19.5, n0(atz - htz) / 3, 0.0)
    F['to_luck'] = (n0(tv('to_luck', 'home_team')) - n0(tv('to_luck', 'away_team'))) / 5
    F['lookahead'] = n0(lv('lookahead', 'home_team')) - n0(lv('lookahead', 'away_team'))
    F['road_trip'] = -(n0(lv('away_run', 'away_team')) >= 3).astype(float)
    mar = (D.home_points - D.away_points).values
    F['team_hfa'] = shrunk_prior(D.home_team.values, np.where(neu, np.nan, mar - D.line_m.values), 20) / 3
    F['tempo'] = n0((tv('tempo', 'home_team') + tv('tempo', 'away_team')) / 2 - D.season.map(lgp).values) / 8
    F['conf'] = D.conference_game.fillna(False).astype(float).values; F['late'] = (hr >= 22).astype(float)
    F['early_season'] = (D.week <= 4).astype(float).values; F['late_season'] = (D.week >= 10).astype(float).values; F['one'] = 1.0
    return F
CFB_M = ['rest_diff', 'bounce', 'letdown', 'luck', 'tz_travel', 'altitude', 'south_in_cold', 'north_in_heat', 'body_clock', 'to_luck', 'lookahead', 'team_hfa', 'road_trip']
CFB_T = ['late', 'early_season', 'late_season', 'conf', 'altitude', 'tempo']

# ================================================================= learning layer runs
def setup(lg, kind):
    if lg == 'NFL': base, p0 = (['cm', 'pm'], [0.5, 0.5]) if kind == 'm' else (['ct', 'pt', 'one'], [0.5, 0.5, 0.0]); ang = NFL_M if kind == 'm' else NFL_T
    else: base, p0 = (['cm', 'pm', 'elo'], [0.4, 0.4, 0.2]) if kind == 'm' else (['ct', 'pt', 'one'], [0.5, 0.5, 0.0]); ang = CFB_M if kind == 'm' else CFB_T
    k = KN[lg]['learn'][kind]
    return base + ang, list(p0) + [0.0] * len(ang), [k['base_n']] * len(base) + [k['ang_n']] * len(ang), k['forget']
def train_mask(lg, D):
    if lg == 'NFL': return (D.season >= 2019).values & D.done.values
    return ((D.home_division == 'fbs') & (D.away_division == 'fbs') & (D.season_type == 'regular') & (D.season >= 2016)).values & D.home_points.notna().values
def learn(lg, D):
    out = {}; tm = train_mask(lg, D)
    for kind, y in (('m', 'mar'), ('t', 'tot')):
        feats, prior, n_eff, fg = setup(lg, kind)
        D[feats] = D[feats].fillna(0)
        p, m = walk(D, feats, y, prior, n_eff, fg, tm); D['s2_' + kind] = p
        out[kind] = {f: round(float(v), 3) for f, v in zip(feats, m.theta)}
    D['s2_ph'] = (D.s2_t + D.s2_m) / 2; D['s2_pa'] = (D.s2_t - D.s2_m) / 2
    return D, out
def predict_nfl(season, game_ids):
    D = nfl_frame(season); D, W = learn('NFL', D); P = D[D.game_id.isin(game_ids)]
    return {g: (round(float(a), 2), round(float(h), 2)) for g, a, h in zip(P.game_id, P.s2_pa, P.s2_ph)}, W
def predict_cfb(season, game_ids):
    D = cfb_frame(season); D, W = learn('CFB', D); P = D[D.game_id.astype(str).isin([str(g) for g in game_ids])]
    return {str(g): (round(float(a), 2), round(float(h), 2)) for g, a, h in zip(P.game_id, P.s2_pa, P.s2_ph)}, W

# ================================================================= backtest
def backtest(lg):
    D = nfl_frame(2026) if lg == 'NFL' else cfb_frame(2026)
    D, W = learn(lg, D)
    if lg == 'NFL': T = D[(D.season >= 2023) & (D.season <= 2025) & D.done & D.line_m.notna()]
    else: T = D[(D.season >= 2022) & (D.season <= 2025) & train_mask('CFB', D) & D.line_m.notna() & D.ph_c.notna()]
    def ats(g, r, t=0.01):
        v = (np.abs(g) >= t) & (r != 0); return f'{(np.sign(g[v]) == np.sign(r[v])).mean():.3f} n={int(v.sum())}'
    res = {}
    for nm, m, t in (('current model', 'pm', 'pt'), ('challenger ratings', 'cm', 'ct'), ('challenger + learning layer', 's2_m', 's2_t'), ('Vegas line', 'line_m', 'line_t')):
        o = dict(mae_margin=round(float(np.abs(T.mar - T[m]).mean()), 3), mae_total=round(float(np.abs(T.tot - T[t]).mean()), 3),
                 winners=round(float(((T[m] > 0) == (T.mar > 0))[T.mar != 0].mean()), 3))
        if m != 'line_m': o['ats'] = ats((T[m] - T.line_m).values, (T.mar - T.line_m).values); o['ou'] = ats((T[t] - T.line_t).values, (T.tot - T.line_t).values)
        res[nm] = o
    return dict(n=len(T), models=res, weights_now=W)

if __name__ == '__main__':
    if sys.argv[1] == 'backtest': print(json.dumps(backtest(sys.argv[2]), indent=1))
