"""Build the player-week panel with as-of matchup features (no lookahead).
Usage: python3 -I build.py /home/claude/nfl /home/claude/bt/panel.parquet"""
import sys, numpy as np, pandas as pd
D, OUT = sys.argv[1], sys.argv[2]
YRS = range(2020, 2026)
HL = 8.0  # half-life in games

# ---------- player box scores -> DK points ----------
sp = pd.concat([pd.read_csv(f'{D}/sp_{y}.csv', low_memory=False) for y in YRS])
sp = sp[sp.season_type == 'REG'].copy()
f = lambda c: sp[c].fillna(0)
sp['dk'] = (0.04*f('passing_yards') + 4*f('passing_tds') - f('passing_interceptions') + 3*(f('passing_yards') >= 300)
            + 0.1*f('rushing_yards') + 6*f('rushing_tds') + 3*(f('rushing_yards') >= 100)
            + f('receptions') + 0.1*f('receiving_yards') + 6*f('receiving_tds') + 3*(f('receiving_yards') >= 100)
            + 2*(f('passing_2pt_conversions') + f('rushing_2pt_conversions') + f('receiving_2pt_conversions'))
            - (f('rushing_fumbles_lost') + f('receiving_fumbles_lost') + f('sack_fumbles_lost')) + 6*f('special_teams_tds'))
sp['pos'] = sp.position.replace({'FB': 'RB'})
sp = sp[sp.pos.isin(['QB', 'RB', 'WR', 'TE'])]
sp['gkey'] = sp.season*100 + sp.week
tm = sp.groupby(['team', 'gkey']).agg(t_tgt=('targets', 'sum'), t_car=('carries', 'sum')).reset_index()
sp = sp.merge(tm, on=['team', 'gkey'])
sp['tgt_sh'] = sp.targets.fillna(0)/sp.t_tgt.clip(lower=1)
sp['car_sh'] = sp.carries.fillna(0)/sp.t_car.clip(lower=1)
sp['adot_n'] = sp.receiving_air_yards.fillna(0); sp['tg'] = sp.targets.fillna(0)

def ew_prior(df, key, cols, hl=HL, season_mult=0.6):
    """exp-weighted mean of each col over the key's strictly earlier rows (by gkey)."""
    df = df.sort_values([key, 'gkey']).copy()
    out = {c: np.full(len(df), np.nan) for c in cols}; n = np.zeros(len(df))
    a = 0.5**(1/hl)
    for _, idx in df.groupby(key).indices.items():
        s = {c: 0.0 for c in cols}; w = 0.0; last_season = None
        for i in idx:
            ssn = df.season.iat[i]
            if last_season is not None and ssn != last_season:
                for c in cols: s[c] *= season_mult
                w *= season_mult
            if w > 0:
                for c in cols: out[c][i] = s[c]/w
            n[i] = w
            for c in cols:
                v = df[c].iat[i]
                if pd.notna(v): s[c] = a*s[c] + v
                else: s[c] = a*s[c]
            w = a*w + 1; last_season = ssn
    for c in cols: df['ew_'+c] = out[c]
    df['ew_n'] = n
    return df

sp = ew_prior(sp, 'player_id', ['dk', 'tgt_sh', 'car_sh', 'tg', 'adot_n'])
sp['ew_adot'] = sp.ew_adot_n/sp.ew_tg.clip(lower=0.3)

# ---------- schedule / Vegas / weather ----------
g = pd.read_csv(f'{D}/games.csv'); g = g[(g.game_type == 'REG') & g.season.isin(YRS)]
rows = []
for side, opp, sgn in [('home', 'away', 1), ('away', 'home', -1)]:
    x = g[['game_id', 'season', 'week', 'total_line', 'spread_line', 'roof', 'surface', 'temp', 'wind', 'div_game', 'gametime', 'weekday', 'stadium']].copy()
    x['team'] = g[f'{side}_team']; x['opp'] = g[f'{opp}_team']; x['home'] = int(sgn == 1)
    x['rest'] = g[f'{side}_rest']; x['opp_rest'] = g[f'{opp}_rest']
    x['implied'] = g.total_line/2 + sgn*g.spread_line/2; x['opp_implied'] = g.total_line/2 - sgn*g.spread_line/2
    x['spread'] = sgn*g.spread_line  # + = this team favored
    x['qb_id'] = g[f'{side}_qb_id']
    rows.append(x)
sch = pd.concat(rows); sch['gkey'] = sch.season*100 + sch.week
TZ = {'SEA': -8, 'SF': -8, 'LA': -8, 'LAR': -8, 'LAC': -8, 'LV': -8, 'OAK': -8, 'ARI': -7, 'DEN': -7, 'KC': -6, 'DAL': -6, 'HOU': -6,
      'CHI': -6, 'GB': -6, 'MIN': -6, 'NO': -6, 'TEN': -6}
sch['tz'] = sch.team.map(TZ).fillna(-5)
sch['west_early'] = ((sch.tz <= -7) & (sch.home == 0) & (sch.gametime <= '13:05') & (sch.weekday == 'Sunday')).astype(int)
sch['prime'] = ((sch.gametime >= '19:00') | (sch.weekday.isin(['Monday', 'Thursday']))).astype(int)
sch['dome'] = sch.roof.isin(['dome', 'closed']).astype(int)

# ---------- team unit ratings from play-by-play ----------
pbp = pd.concat([pd.read_parquet(f'{D}/pbp_{y}.parquet', columns=['game_id', 'play_id', 'season', 'week', 'season_type', 'posteam', 'defteam', 'qb_dropback', 'rush', 'pass', 'epa', 'success', 'sack', 'qb_hit', 'yards_gained', 'pass_oe', 'air_yards', 'qb_kneel', 'qb_spike', 'wp', 'down']) for y in YRS])
pbp = pbp[(pbp.season_type == 'REG') & pbp.posteam.notna() & pbp.epa.notna() & (pbp.qb_kneel != 1) & (pbp.qb_spike != 1)]
pbp = pbp[(pbp.qb_dropback == 1) | (pbp.rush == 1)]
pbp['db'] = pbp.qb_dropback == 1
pbp['pr'] = ((pbp.sack == 1) | (pbp.qb_hit == 1)).where(pbp.db)
pbp['pe'] = pbp.epa.where(pbp.db); pbp['ps'] = pbp.success.where(pbp.db); pbp['px'] = (pbp.yards_gained >= 20).where(pbp.db)
pbp['re'] = pbp.epa.where(~pbp.db); pbp['rs'] = pbp.success.where(~pbp.db); pbp['rx'] = (pbp.yards_gained >= 10).where(~pbp.db)
neutral = pbp.wp.between(0.2, 0.8) & (pbp.down <= 2)
pbp['proe'] = (pbp.pass_oe/100).where(neutral)
U = ['pe', 'ps', 'px', 're', 'rs', 'rx', 'pr', 'proe']
agg = {c: (c, 'mean') for c in U}; agg['plays'] = ('epa', 'size')
off = pbp.groupby(['posteam', 'defteam', 'season', 'week']).agg(**agg).reset_index().rename(columns={'posteam': 'team', 'defteam': 'opp'})
off['gkey'] = off.season*100 + off.week
lg = off[U + ['plays']].mean()
# raw as-of ratings, then one pass of opponent adjustment
o1 = ew_prior(off.rename(columns={}), 'team', U + ['plays'])
d1 = ew_prior(off.rename(columns={'team': 'o', 'opp': 'team'}).rename(columns={'o': 'opp'}), 'team', U + ['plays'])
o1 = o1[['team', 'gkey'] + ['ew_'+c for c in U + ['plays']]].rename(columns={'ew_'+c: 'or_'+c for c in U + ['plays']})
d1 = d1[['team', 'gkey'] + ['ew_'+c for c in U + ['plays']]].rename(columns={'ew_'+c: 'dr_'+c for c in U + ['plays']})
adj = off.merge(d1.rename(columns={'team': 'opp'}), on=['opp', 'gkey'], how='left').merge(o1.rename(columns={'team': 'opp2'}), left_on=['team', 'gkey'], right_on=['opp2', 'gkey'], how='left')
oa = off.copy(); da = off.copy()
for c in U + ['plays']:
    oa[c] = off[c] - (adj['dr_'+c].fillna(lg[c]) - lg[c])   # offense game value adjusted for defense faced
# defense adjusted for offense faced
adj2 = off.merge(o1, on=['team', 'gkey'], how='left')
for c in U + ['plays']:
    da[c] = off[c] - (adj2['or_'+c].fillna(lg[c]) - lg[c])
OFF = ew_prior(oa, 'team', U + ['plays'])[['team', 'gkey', 'ew_n'] + ['ew_'+c for c in U + ['plays']]]
OFF.columns = ['team', 'gkey', 'o_n'] + ['o_'+c for c in U + ['plays']]
DEF = ew_prior(da.rename(columns={'team': 'o', 'opp': 'team'}).rename(columns={'o': 'opp'}), 'team', U + ['plays'])[['team', 'gkey', 'ew_n'] + ['ew_'+c for c in U + ['plays']]]
DEF.columns = ['opp', 'gkey', 'd_n'] + ['d_'+c for c in U + ['plays']]
GAME = off[['team', 'opp', 'gkey'] + U + ['plays']].rename(columns={c: 'g_'+c for c in U + ['plays']})

# ---------- DvP (opp DK allowed to position, as-of) ----------
dv = sp.groupby(['opponent_team', 'pos', 'gkey', 'season']).dk.sum().reset_index().rename(columns={'opponent_team': 'opp'})
dv['k'] = dv.opp + '_' + dv.pos
dv = ew_prior(dv, 'k', ['dk'])[['opp', 'pos', 'gkey', 'ew_dk']].rename(columns={'ew_dk': 'dvp'})

# ---------- coverage: man rate per defense, man/zone splits per receiver ----------
pt = pd.concat([pd.read_parquet(f'{D}/part_{y}.parquet', columns=['nflverse_game_id', 'play_id', 'defense_man_zone_type', 'defense_coverage_type', 'time_to_throw', 'was_pressure', 'number_of_pass_rushers', 'defenders_in_box']) for y in range(2021, 2026)])
pt = pt.rename(columns={'nflverse_game_id': 'game_id'})
pbp2 = pd.concat([pd.read_parquet(f'{D}/pbp_{y}.parquet', columns=['game_id', 'play_id', 'season', 'week', 'season_type', 'posteam', 'defteam', 'receiver_player_id', 'yards_gained', 'complete_pass', 'pass_attempt', 'sack', 'qb_dropback', 'rush', 'epa']) for y in range(2021, 2026)])
pbp2 = pbp2[pbp2.season_type == 'REG'].merge(pt, on=['game_id', 'play_id'], how='left')
pbp2['gkey'] = pbp2.season*100 + pbp2.week
cv = pbp2[pbp2.defense_man_zone_type.isin(['MAN_COVERAGE', 'ZONE_COVERAGE'])].copy()
cv['man'] = (cv.defense_man_zone_type == 'MAN_COVERAGE').astype(float)
cv['blitz'] = (cv.number_of_pass_rushers >= 5).astype(float)
dm = cv.groupby(['defteam', 'season', 'week', 'gkey']).agg(man=('man', 'mean'), blitz=('blitz', 'mean')).reset_index()
dm = ew_prior(dm, 'defteam', ['man', 'blitz'])[['defteam', 'gkey', 'ew_man', 'ew_blitz']].rename(columns={'defteam': 'opp', 'ew_man': 'd_man', 'ew_blitz': 'd_blitz'})
tg = cv[cv.receiver_player_id.notna() & (cv.pass_attempt == 1)].copy()
tg['y'] = tg.yards_gained.fillna(0)
rs = tg.groupby(['receiver_player_id', 'season', 'week', 'gkey', 'man']).agg(y=('y', 'sum'), t=('y', 'size')).reset_index()
rs = rs.pivot_table(index=['receiver_player_id', 'season', 'week', 'gkey'], columns='man', values=['y', 't'], fill_value=0)
rs.columns = [f'{a}{int(b)}' for a, b in rs.columns]; rs = rs.reset_index()
rs = ew_prior(rs, 'receiver_player_id', ['y0', 't0', 'y1', 't1'], hl=16, season_mult=0.8)
K = 15; LYZ = tg[tg.man == 0].y.mean(); LYM = tg[tg.man == 1].y.mean()
rs['ypt_zone'] = (rs.ew_y0*rs.ew_n + K*LYZ)/(rs.ew_t0*rs.ew_n + K)
rs['ypt_man'] = (rs.ew_y1*rs.ew_n + K*LYM)/(rs.ew_t1*rs.ew_n + K)
rs['man_edge'] = (rs.ypt_man - LYM) - (rs.ypt_zone - LYZ)
rs = rs[['receiver_player_id', 'gkey', 'man_edge', 'ypt_man', 'ypt_zone']].rename(columns={'receiver_player_id': 'player_id'})

# ---------- corners (PFR coverage stats), as-of ----------
pl = pd.read_parquet(f'{D}/players.parquet')
cbids = set(pl[pl.position.isin(['CB', 'DB'])].pfr_id.dropna())
ad = pd.concat([pd.read_parquet(f'{D}/advdef_{y}.parquet') for y in YRS])
ad = ad[(ad.game_type == 'REG') & ad.pfr_player_id.isin(cbids)].copy()
ad['gkey'] = ad.season*100 + ad.week
for c in ['def_targets', 'def_yards_allowed', 'def_receiving_td_allowed', 'def_completions_allowed', 'def_ints']: ad[c] = ad[c].fillna(0)
ad = ew_prior(ad, 'pfr_player_id', ['def_targets', 'def_yards_allowed', 'def_receiving_td_allowed', 'def_ints'], hl=12, season_mult=0.75)
LYPT = ad.def_yards_allowed.sum()/ad.def_targets.sum(); KC = 40
ad['wt'] = ad.ew_def_targets*ad.ew_n            # effective prior targets
ad['cb_ypt'] = (ad.ew_def_yards_allowed*ad.ew_n + KC*LYPT)/(ad.wt + KC)
ad['cb_vol'] = ad.ew_def_targets                # targets/game drawn (starter proxy)
ad = ad.merge(pl[['pfr_id', 'height', 'weight']].drop_duplicates('pfr_id'), left_on='pfr_player_id', right_on='pfr_id', how='left')
q = ad[(ad.cb_vol >= 3.5) & (ad.wt >= 30)].copy()
q['pct'] = q.groupby('gkey').cb_ypt.rank(pct=True)   # low = best
best = q.sort_values('cb_ypt').groupby(['team', 'gkey']).head(1)
cb1 = best[['team', 'gkey', 'pfr_player_id', 'cb_ypt', 'pct', 'height']].rename(columns={'team': 'opp', 'pfr_player_id': 'cb1_id', 'cb_ypt': 'cb1_ypt', 'pct': 'cb1_pct', 'height': 'cb1_ht'})
# defense's usual CB1 missing: best-rated CB of the team's previous game who did not play this game
prev = best.sort_values('gkey').groupby('team').shift(0)
played = set(zip(ad.pfr_player_id, ad.team, ad.gkey))
bt = best.sort_values(['team', 'gkey'])
bt['prev_cb1'] = bt.groupby('team').pfr_player_id.shift(1); bt['prev_pct'] = bt.groupby('team').pct.shift(1)
# use all team games (not just ones where a qualifying CB existed)
tg_all = ad[['team', 'gkey']].drop_duplicates().sort_values(['team', 'gkey'])
lastbest = {}
miss = []
for r in tg_all.itertuples():
    lb = lastbest.get(r.team)
    m = np.nan
    if lb is not None:
        m = float((lb[0], r.team, r.gkey) not in played and lb[1] <= 0.25)
    miss.append((r.team, r.gkey, m))
    row = best[(best.team == r.team) & (best.gkey == r.gkey)]
    if len(row): lastbest[r.team] = (row.pfr_player_id.iat[0], row.pct.iat[0])
miss = pd.DataFrame(miss, columns=['opp', 'gkey', 'cb1_out'])

# ---------- receiver traits ----------
cmb = pd.read_parquet(f'{D}/combine.parquet')[['pfr_id', 'forty']].dropna().drop_duplicates('pfr_id')
tr = pl[['gsis_id', 'pfr_id', 'height', 'weight']].merge(cmb, on='pfr_id', how='left').rename(columns={'gsis_id': 'player_id'})
ngs = pd.read_parquet(f'{D}/ngs_rec.parquet')
ngs = ngs[(ngs.week > 0) & (ngs.season_type == 'REG')][['player_gsis_id', 'season', 'week', 'avg_separation', 'avg_cushion', 'targets']].rename(columns={'player_gsis_id': 'player_id'})
ngs['gkey'] = (ngs.season*100 + ngs.week).astype('int64'); ngs['sepT'] = ngs.avg_separation*ngs.targets
ngs = ew_prior(ngs, 'player_id', ['sepT', 'targets'], hl=12, season_mult=0.8)
ngs['sep'] = ngs.ew_sepT/ngs.ew_targets
# as-of separation for a given gkey: last ngs row strictly before (merge_asof)
ngs = ngs[['player_id', 'gkey', 'sep']].dropna().sort_values('gkey')

# ---------- assemble ----------
P = sp.merge(sch.drop(columns=['season', 'week']), left_on=['team', 'gkey'], right_on=['team', 'gkey'], how='inner')
P = P.merge(OFF, on=['team', 'gkey'], how='left').merge(DEF, on=['opp', 'gkey'], how='left').merge(GAME, on=['team', 'opp', 'gkey'], how='left')
P = P.merge(dv, on=['opp', 'pos', 'gkey'], how='left').merge(dm, on=['opp', 'gkey'], how='left').merge(rs, on=['player_id', 'gkey'], how='left')
P = P.merge(cb1, on=['opp', 'gkey'], how='left').merge(miss, on=['opp', 'gkey'], how='left')
P = P.merge(tr.drop_duplicates('player_id'), on='player_id', how='left')
P['gkey'] = P.gkey.astype('int64'); P = P.sort_values('gkey'); P = pd.merge_asof(P, ngs.rename(columns={'gkey': 'ngk'}).sort_values('ngk'), left_on='gkey', right_on='ngk', by='player_id', allow_exact_matches=False)
# team role ranks (as-of): WR1/WR2 by ew target share among those who played
P['wr_rank'] = np.nan
m = P.pos == 'WR'
P.loc[m, 'wr_rank'] = P[m].groupby(['team', 'gkey']).ew_tgt_sh.rank(ascending=False, method='first')
# vacated opportunity: teammates with established roles who did not play
P = P.sort_values(['player_id', 'gkey'])
roles = P[['player_id', 'team', 'gkey', 'season', 'ew_tgt_sh', 'ew_car_sh', 'pos']].copy()
vac = []
for (team, ssn), grp in roles.groupby(['team', 'season']):
    keys = sorted(grp.gkey.unique()); last = {}; seen = {}
    for j, k in enumerate(keys):
        cur = grp[grp.gkey == k]; ids = set(cur.player_id)
        recent = set(keys[max(0, j-2):j])
        gone = [pid for pid in last if pid not in ids and seen[pid] in recent]
        vac.append((team, k, sum(last[p][0] for p in gone), sum(last[p][1] for p in gone)))
        for r in cur.itertuples():
            last[r.player_id] = (r.ew_tgt_sh if r.ew_tgt_sh == r.ew_tgt_sh else 0, r.ew_car_sh if r.ew_car_sh == r.ew_car_sh else 0)
            seen[r.player_id] = k
vac = pd.DataFrame(vac, columns=['team', 'gkey', 'vac_tgt', 'vac_car'])
P = P.merge(vac, on=['team', 'gkey'], how='left')
# starting QB change vs team's usual starter
P = P.sort_values(['team', 'gkey'])
qbs = sch[['team', 'gkey', 'qb_id', 'season']].drop_duplicates(['team', 'gkey']).sort_values(['team', 'gkey'])
us = []
for (team, ssn), grp in qbs.groupby(['team', 'season']):
    cnt = {}
    for r in grp.itertuples():
        us.append(max(cnt, key=cnt.get) if cnt else None); cnt[r.qb_id] = cnt.get(r.qb_id, 0) + 1
qbs['usual'] = us
qbs['qb_change'] = ((qbs.qb_id != qbs.usual) & qbs.usual.notna()).astype(int)
P = P.merge(qbs[['team', 'gkey', 'qb_change']], on=['team', 'gkey'], how='left')
P = P[P.season >= 2021]
P.to_parquet(OUT)
print(P.shape, P.groupby('pos').size().to_dict())
print(P[['ew_dk', 'implied', 'o_pe', 'd_pe', 'dvp', 'd_man', 'man_edge', 'cb1_pct', 'cb1_out', 'sep', 'forty', 'height', 'vac_tgt', 'qb_change', 'wind']].notna().mean().round(2).to_dict())
