"""Weekly snapshot for the self-updating Bet Tracker: base predictions for every game in the next CFB and NFL week,
plus everything the page needs to finish the job in the browser (QB values, wind/QB coefficients, coordinates, rules, stats).
Usage: python3 export_snapshot.py SEASON [--cfb-week W] [--nfl-week N]   -> logs/snapshot.json (+ appends logs/predictions_log.csv)"""
import os, sys, json, argparse, datetime as dt
import numpy as np, pandas as pd
PKG = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, PKG); sys.path.insert(0, f'{PKG}/cfb'); sys.path.insert(0, f'{PKG}/nfl')
from paths import DATA
ap = argparse.ArgumentParser(); ap.add_argument('season', type=int); ap.add_argument('--cfb-week', type=int); ap.add_argument('--nfl-week', type=int)
A = ap.parse_args(); S = A.season
RULES = json.load(open(f'{PKG}/rules.json'))
snap = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(), season=S, rules=RULES, stats=json.load(open(f'{PKG}/stats/stats.json')))
snap['keys'] = json.load(open(f'{PKG}/keys.json'))   # key-number score distributions for cover %, likely scores, NFL key-number lean
preds_out = []

# ---------------- CFB ----------------
import importlib.util as iu
spec = iu.spec_from_file_location('cfbp', f'{PKG}/cfb/predict.py'); CP = iu.module_from_spec(spec); spec.loader.exec_module(CP)
from campus import C
sch = pd.read_csv(f'{DATA}/cfb/repo/schedules/csv/cfb_schedules_{S}.csv')
reg = sch[(sch.season_type == 'regular') & (sch.home_division == 'fbs') & (sch.away_division == 'fbs')]
cw = A.cfb_week or int(reg[reg.completed != True].week.min())
_, O, Dd, NG = CP.ratings(S, cw)
DOME_WORDS = ('Dome', 'Ford Field', 'Mercedes-Benz', 'Superdome', 'Lucas Oil', 'AT&T Stadium', 'Allegiant', 'U.S. Bank', 'NRG', 'Alamodome', 'State Farm Stadium')
cg = []
for g in reg[reg.week == cw].itertuples():
    a, h = g.away_team, g.home_team
    if a not in O.index or h not in O.index or pd.isna(g.home_pregame_elo) or pd.isna(g.away_pregame_elo): continue
    hf = 0.0 if g.neutral_site else 1.0; rt = (g.away_pregame_elo - g.home_pregame_elo) * 0.0422
    pa, ph = CP.team_pts(O, Dd, a, h, -hf, rt), CP.team_pts(O, Dd, h, a, hf, -rt)
    dome = isinstance(g.venue, str) and any(w in g.venue for w in DOME_WORDS)
    cg.append(dict(id=str(g.game_id), kick=g.start_date, tbd=bool(g.start_time_tbd), away=a, home=h, neutral=bool(g.neutral_site), venue=g.venue if isinstance(g.venue, str) else '',
                   away_conf=g.away_conference if isinstance(g.away_conference, str) else '', home_conf=g.home_conference if isinstance(g.home_conference, str) else '',
                   tz_away=C[a][2] if a in C else None, tz_home=C[h][2] if h in C else None,
                   lat=None if g.neutral_site or h not in C else C[h][0], lon=None if g.neutral_site or h not in C else C[h][1], dome=dome,
                   pa=round(pa, 2), ph=round(ph, 2), games_a=int(NG.get(a, 0)), games_h=int(NG.get(h, 0))))
    preds_out.append(dict(season=S, league='CFB', week=cw, date=str(pd.Timestamp(g.start_date).tz_convert('America/Chicago').date()), id=str(g.game_id), away=a, home=h, pred_away=round(pa, 1), pred_home=round(ph, 1)))
# ---- QBs per team (for the page's injury/news check) ----
ps = pd.read_parquet(f'{DATA}/cfb/repo/player_stats/parquet/player_stats_{S}.parquet', columns=['game_id', 'week', 'team', 'completion_player', 'incompletion_player', 'interception_thrown_player'])
att = pd.concat([ps[['game_id', 'week', 'team', c]].rename(columns={c: 'qb'}) for c in ('completion_player', 'incompletion_player', 'interception_thrown_player')]).dropna(subset=['qb'])
pp = att.rename(columns={'team': 'pos_team', 'qb': 'passer_player_name'})
cqb = {}
teams_needed = {t for g in cg for t in (g['away'], g['home'])}
for t, d in pp[pp.pos_team.isin(teams_needed)].groupby('pos_team'):
    tot = d.passer_player_name.value_counts()
    last = d[d.week == d.week.max()].passer_player_name.value_counts().index[0]
    prim = tot.index[0]
    cqb[t] = dict(last=last, primary=prim, share_last=round(float(tot.get(last, 0) / tot.sum()), 2), share_primary=round(float(tot.iloc[0] / tot.sum()), 2),
                  starts_last=int(d[d.passer_player_name == last].game_id.nunique()), games=int(d.game_id.nunique()))
ids = {}
for g in reg.itertuples():
    ids[g.home_team] = int(g.home_id); ids[g.away_team] = int(g.away_id)
for g in cg: g['espn_a'] = ids.get(g['away']); g['espn_h'] = ids.get(g['home'])
# ---- Elo rank (top 60 = "major") from the latest Elo each FBS team carries into this week ----
el = pd.concat([reg[['week', 'home_team', 'home_pregame_elo', 'home_postgame_elo']].set_axis(['week', 'team', 'pre', 'post'], axis=1),
                reg[['week', 'away_team', 'away_pregame_elo', 'away_postgame_elo']].set_axis(['week', 'team', 'pre', 'post'], axis=1)])
el['elo'] = el.pre.where(el.week == cw, el.post)
el = el[(el.week <= cw) & el.elo.notna()].sort_values('week').groupby('team').elo.last()
erank = el.rank(ascending=False, method='min').astype(int).to_dict()
for g in cg: g['away_rank'], g['home_rank'] = erank.get(g['away']), erank.get(g['home'])
# ---- last game for each team (letdown / bounce-back rules): margin and whether it beat an Elo top-20% team ----
done = sch[(sch.season_type == 'regular') & (sch.completed == True) & (sch.week < cw) & sch.home_points.notna()]
q81 = pd.concat([sch[sch.season_type == 'regular'].home_pregame_elo, sch[sch.season_type == 'regular'].away_pregame_elo]).quantile(0.81)
lg_ = pd.concat([pd.DataFrame(dict(team=done.home_team, week=done.week, mar=done.home_points - done.away_points, oelo=done.away_pregame_elo)),
                 pd.DataFrame(dict(team=done.away_team, week=done.week, mar=done.away_points - done.home_points, oelo=done.home_pregame_elo))]).sort_values('week').groupby('team').tail(1).set_index('team')
allg = pd.concat([pd.DataFrame(dict(team=done.home_team, week=done.week, mar=done.home_points - done.away_points)), pd.DataFrame(dict(team=done.away_team, week=done.week, mar=done.away_points - done.home_points))]).sort_values('week')
LS = {t: next((i for i, v in enumerate(list(d.mar)[::-1]) if v >= 0), len(d)) for t, d in allg.groupby('team')}   # current losing streak
for g in cg:
    for sd in ('away', 'home'):
        t = g[sd]
        if t in lg_.index:
            m, oe = lg_.at[t, 'mar'], lg_.at[t, 'oelo']; g[f'{sd}_prev_mar'] = int(m); g[f'{sd}_prev_beat_top'] = bool(m > 0 and pd.notna(oe) and oe >= q81)
        else: g[f'{sd}_prev_mar'] = None; g[f'{sd}_prev_beat_top'] = False
        g[f'{sd}_loss_streak'] = int(LS.get(t, 0))
snap['cfb'] = dict(week=cw, games=cg, qbs=cqb)
print('CFB week', cw, len(cg), 'games')

# ---------------- NFL ----------------
import engine as E, model as MD
MODEL = json.load(open(f'{PKG}/nfl/model.json'))
E.init(S)
nw = A.nfl_week or int(E.G[(E.G.season == S) & E.G.home_score.isna()].week.min())
wk = E.G[(E.G.season == S) & (E.G.week == nw) & E.G.home_score.isna()].copy()
roof_home = E.G[E.G.roof.notna()].groupby('home_team').roof.agg(lambda r: r.mode().iloc[0])
wk['roof'] = wk.roof.fillna(wk.home_team.map(roof_home))
INTL = {'Stade de France': 'outdoors', 'Tottenham Hotspur Stadium': 'outdoors', 'Wembley Stadium': 'outdoors', 'Allianz Arena': 'outdoors',
        'Estadio Azteca': 'outdoors', 'Croke Park': 'outdoors', 'Santiago Bernabeu': 'closed', 'Melbourne Cricket Ground': 'outdoors', 'Olympiastadion': 'outdoors'}
wk['roof'] = [INTL.get(s, r) if loc == 'Neutral' else r for s, r, loc in zip(wk.stadium, wk.roof, wk.location)]
wk['wind'] = 0.0
cur = E.TG[E.TG.season == S].sort_values('week')
starters = cur.groupby('posteam').tail(1).set_index('posteam').qb_id.to_dict()
R = MD.prep(pd.DataFrame(E.game_rows(S, nw, wk, E.H, E.P, starters=starters))); R['p'] = MD.predict(MODEL, R)
b = dict(zip(MODEL['feats'], MODEL['beta'][1:])); sd = MODEL['sd']
coef = dict(wind=b['wind'] / sd['wind'], qb_own=b['qb_val'] / sd['qb_val'] + b['qb_delta'] / sd['qb_delta'], qb_opp=b['opp_qb_val'] / sd['opp_qb_val'])
# QB table: everyone who started since last season, with current values
names = E.TG.dropna(subset=['qb_id']).sort_values('gameday').groupby('qb_id').qb_name.last()
recent = E.TG[(E.TG.season >= S - 1)].dropna(subset=['qb_id'])
qbs = {}
for q in recent.qb_id.unique():
    v = E.qb_now_at(q); qbs[names[q]] = round(float(v) if pd.notna(v) else -0.06, 4)
team_qbs = {t: list(dict.fromkeys(g.sort_values('gameday', ascending=False).qb_id.map(names)))[:4] for t, g in recent.groupby('posteam')}
STAD = {'ARI': (33.528, -112.263), 'ATL': (33.755, -84.401), 'BAL': (39.278, -76.623), 'BUF': (42.774, -78.787), 'CAR': (35.226, -80.853), 'CHI': (41.862, -87.617),
        'CIN': (39.095, -84.516), 'CLE': (41.506, -81.700), 'DAL': (32.748, -97.093), 'DEN': (39.744, -105.020), 'DET': (42.340, -83.046), 'GB': (44.501, -88.062),
        'HOU': (29.685, -95.411), 'IND': (39.760, -86.164), 'JAX': (30.324, -81.637), 'KC': (39.049, -94.484), 'LV': (36.091, -115.184), 'LAC': (33.953, -118.339),
        'LA': (33.953, -118.339), 'MIA': (25.958, -80.239), 'MIN': (44.974, -93.258), 'NE': (42.091, -71.264), 'NO': (29.951, -90.081), 'NYG': (40.814, -74.074),
        'NYJ': (40.814, -74.074), 'PHI': (39.901, -75.168), 'PIT': (40.447, -80.016), 'SF': (37.403, -121.970), 'SEA': (47.595, -122.332), 'TB': (27.976, -82.503),
        'TEN': (36.166, -86.771), 'WAS': (38.908, -76.864)}
SCOORD = {'Stade de France': (48.924, 2.360), 'Tottenham Hotspur Stadium': (51.604, -0.066), 'Wembley Stadium': (51.556, -0.280), 'Allianz Arena': (48.219, 11.625),
          'Estadio Azteca': (19.303, -99.150), 'Croke Park': (53.361, -6.251), 'Olympiastadion': (52.515, 13.239), 'Melbourne Cricket Ground': (-37.820, 144.983)}
DIV = {t: d for d, ts in {'AFC East': 'BUF MIA NE NYJ', 'AFC North': 'BAL CIN CLE PIT', 'AFC South': 'HOU IND JAX TEN', 'AFC West': 'DEN KC LV LAC',
       'NFC East': 'DAL NYG PHI WAS', 'NFC North': 'CHI DET GB MIN', 'NFC South': 'ATL CAR NO TB', 'NFC West': 'ARI LA SF SEA'}.items() for t in ts.split()}
ng = []
for g in wk.itertuples():
    h = R[(R.game_id == g.game_id) & (R.team == g.home_team)]; a = R[(R.game_id == g.game_id) & (R.team == g.away_team)]
    if h.empty or a.empty: continue
    kick = pd.Timestamp(f'{g.gameday} {g.gametime}', tz='America/New_York').tz_convert('UTC').isoformat()
    lat, lon = SCOORD.get(g.stadium, STAD.get(g.home_team)) if g.location == 'Neutral' else STAD.get(g.home_team, (None, None))
    ng.append(dict(id=g.game_id, espn=str(int(g.espn)) if pd.notna(g.espn) else '', kick=kick, away=g.away_team, home=g.home_team, neutral=g.location == 'Neutral',
                   stadium=g.stadium, outdoor=g.roof in ('outdoors', 'open'), lat=lat, lon=lon, away_conf=DIV[g.away_team], home_conf=DIV[g.home_team],
                   pa=round(float(a.p.iloc[0]), 2), ph=round(float(h.p.iloc[0]), 2), qa=names.get(starters.get(g.away_team), ''), qh=names.get(starters.get(g.home_team), ''),
                   qa0=float(a.qb_val.iloc[0]), qh0=float(h.qb_val.iloc[0]), line_spread=-g.spread_line if pd.notna(g.spread_line) else None,
                   line_total=g.total_line if pd.notna(g.total_line) else None))
    wind_default = 8.0 if g.roof in ('outdoors', 'open') else 0.0
    preds_out.append(dict(season=S, league='NFL', week=nw, date=str(g.gameday), id=str(int(g.espn)) if pd.notna(g.espn) else g.game_id, away=g.away_team, home=g.home_team,
                          pred_away=round(float(a.p.iloc[0]) + coef['wind'] * wind_default, 1), pred_home=round(float(h.p.iloc[0]) + coef['wind'] * wind_default, 1)))
# season primary QB per team: most starts this season, tie -> more starts last two seasons
prim = {}
for t, g in recent.groupby('posteam'):
    cs = g[g.season == S].qb_id.value_counts(); two = g.qb_id.value_counts()
    if len(cs):
        top = cs[cs == cs.max()].index; prim[t] = names[max(top, key=lambda q: two.get(q, 0))]
snap['nfl'] = dict(week=nw, games=ng, coef=coef, qbs=qbs, team_qbs=team_qbs, primary=prim, qb_default=-0.06)
print('NFL week', nw, len(ng), 'games')

# factor weights for the stats tab
CM = json.load(open(f'{PKG}/cfb/model.json'))
snap['stats']['cfb']['weights'] = sorted([[k, round(v, 2)] for k, v in CM['coef'].items()], key=lambda kv: -abs(kv[1]))
snap['stats']['nfl']['weights'] = sorted([[k, round(v, 2)] for k, v in zip(MODEL['feats'], MODEL['beta'][1:])], key=lambda kv: -abs(kv[1]))
# ---------------- save ----------------
json.dump(snap, open(f'{PKG}/logs/snapshot.json', 'w'), default=lambda o: None if isinstance(o, float) and np.isnan(o) else str(o))
pl = f'{PKG}/logs/predictions_log.csv'
new = pd.DataFrame(preds_out)
old = pd.read_csv(pl, dtype={'id': str}) if os.path.exists(pl) else new.iloc[:0]
pd.concat([old, new]).drop_duplicates(['league', 'id'], keep='last').to_csv(pl, index=False)
print('snapshot saved')
