"""NFL DFS data: DraftKings points from nflverse weekly stats, and the pre-game features the projection model uses.
Every feature is built only from games played BEFORE the one being projected, so backtests are honest (walk-forward)."""
import os, warnings, numpy as np, pandas as pd
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
DATA = os.environ.get('BM_DATA', '/home/claude/bm_data')
D = f'{DATA}/dfs'
NV = 'https://github.com/nflverse/nflverse-data/releases/download'
POS = ['QB', 'RB', 'WR', 'TE']

def fetch(seasons, force_current=None):
    """Download nflverse weekly player/team stats and schedules (only what is missing; the current season is always refreshed)."""
    import urllib.request
    os.makedirs(D, exist_ok=True)
    def get(url, f, force=False):
        if force or not os.path.exists(f): urllib.request.urlretrieve(url, f)
    for y in seasons:
        cur = y == force_current
        get(f'{NV}/stats_player/stats_player_week_{y}.csv', f'{D}/stats_player_week_{y}.csv', cur)
        get(f'{NV}/stats_team/stats_team_week_{y}.csv', f'{D}/stats_team_week_{y}.csv', cur)
    get(f'{NV}/schedules/games.csv', f'{D}/games.csv', True)

def dk_points(p):
    """DraftKings classic NFL scoring for offensive players."""
    f = lambda c: p[c].fillna(0) if c in p else 0
    pts = (0.04 * f('passing_yards') + 4 * f('passing_tds') - 1 * f('passing_interceptions') + 3 * (f('passing_yards') >= 300)
           + 0.1 * f('rushing_yards') + 6 * f('rushing_tds') + 3 * (f('rushing_yards') >= 100)
           + f('receptions') + 0.1 * f('receiving_yards') + 6 * f('receiving_tds') + 3 * (f('receiving_yards') >= 100)
           + 6 * f('special_teams_tds') + 6 * f('fumble_recovery_tds')
           - 1 * (f('sack_fumbles_lost') + f('rushing_fumbles_lost') + f('receiving_fumbles_lost'))
           + 2 * (f('passing_2pt_conversions') + f('rushing_2pt_conversions') + f('receiving_2pt_conversions')))
    return pts

def dk_kicker(t):
    f = lambda c: t[c].fillna(0)
    return (3 * (f('fg_made_0_19') + f('fg_made_20_29') + f('fg_made_30_39')) + 4 * f('fg_made_40_49') + 5 * (f('fg_made_50_59') + f('fg_made_60_')) + f('pat_made'))

def dk_dst(t, pa):
    f = lambda c: t[c].fillna(0)
    pa_pts = np.select([pa == 0, pa <= 6, pa <= 13, pa <= 20, pa <= 27, pa <= 34], [10, 7, 4, 1, 0, -1], -4)
    return (f('def_sacks') + 2 * f('def_interceptions') + 2 * f('fumble_recovery_opp') + 6 * f('def_tds') + 6 * f('special_teams_tds')
            + 2 * f('def_safeties') + 2 * (f('def_punt_blocks') + f('def_fg_blocks') + f('def_pat_blocks')) + pa_pts)

def games():
    g = pd.read_csv(f'{D}/games.csv')
    g = g[g.game_type.isin(['REG', 'WC', 'DIV', 'CON', 'SB'])]
    rows = []
    for side, opp, sgn in (('home', 'away', 1), ('away', 'home', -1)):
        x = g[['game_id', 'season', 'week', 'gameday', 'gametime', 'roof', 'wind', 'temp', 'spread_line', 'total_line', f'{side}_team', f'{opp}_team', f'{side}_score', f'{opp}_score']].copy()
        x.columns = ['game_id', 'season', 'week', 'gameday', 'gametime', 'roof', 'wind', 'temp', 'spread_line', 'total_line', 'team', 'opp', 'pts', 'opp_pts']
        x['home'] = 1 if side == 'home' else 0
        # nflverse spread_line: points the home team is favored by
        x['implied'] = (x.total_line + sgn * x.spread_line) / 2
        x['opp_implied'] = x.total_line - x.implied
        x['spread'] = -sgn * x.spread_line        # negative = this team favored
        rows.append(x)
    return pd.concat(rows, ignore_index=True)

def load(seasons):
    P = pd.concat([pd.read_csv(f'{D}/stats_player_week_{y}.csv', low_memory=False) for y in seasons if os.path.exists(f'{D}/stats_player_week_{y}.csv')])
    T = pd.concat([pd.read_csv(f'{D}/stats_team_week_{y}.csv', low_memory=False) for y in seasons if os.path.exists(f'{D}/stats_team_week_{y}.csv')])
    return P, T

def _ewm_prior(df, keys, col, hl):
    return df.groupby(keys)[col].transform(lambda s: s.shift(1).ewm(halflife=hl, min_periods=1).mean())

def features(seasons, extra=None):
    """One row per offensive player-game (QB/RB/WR/TE) with pre-game features and the DK points actually scored (target).
    `extra`: rows for upcoming games (player_id, player_display_name, position, season, week, team, opponent_team) to project."""
    P, T = load(seasons); G = games()
    P = P[P.position.isin(POS) & (P.season_type == 'REG')]
    if extra is not None and len(extra): P = pd.concat([P, extra], ignore_index=True)
    P = P.copy()
    P['dk'] = dk_points(P)
    if extra is not None and len(extra): P.loc[P.index[-len(extra):], 'dk'] = np.nan
    P['upcoming'] = False
    if extra is not None and len(extra): P.loc[P.index[-len(extra):], 'upcoming'] = True
    T['team_carries'] = T.carries; T['team_targets'] = T.targets; T['team_air'] = T.receiving_air_yards; T['team_att'] = T.attempts
    P = P.merge(T[['season', 'week', 'team', 'team_carries', 'team_targets', 'team_air', 'team_att']], on=['season', 'week', 'team'], how='left')
    P['car_sh'] = P.carries / P.team_carries.replace(0, np.nan)
    P['tgt_sh'] = P.targets / P.team_targets.replace(0, np.nan)
    P['air_sh'] = P.receiving_air_yards / P.team_air.replace(0, np.nan)
    P['att_sh'] = P.attempts / P.team_att.replace(0, np.nan)
    P['opps'] = P.carries.fillna(0) + P.targets.fillna(0)
    P['qb_rush'] = np.where(P.position == 'QB', P.rushing_yards.fillna(0), 0)
    P = P.merge(G[['season', 'week', 'team', 'game_id', 'gameday', 'gametime', 'roof', 'wind', 'temp', 'implied', 'opp_implied', 'spread', 'total_line', 'home', 'pts']].rename(columns={'game_id': 'gid'}),
                on=['season', 'week', 'team'], how='left')
    P['team_share'] = P.dk / (P.pts.replace(0, np.nan))
    P = P.sort_values(['player_id', 'season', 'week']).reset_index(drop=True)
    k = ['player_id']
    for c, hl in (('dk', 3), ('car_sh', 3), ('tgt_sh', 3), ('air_sh', 3), ('att_sh', 2), ('opps', 3), ('qb_rush', 3), ('team_share', 4)):
        P[f'ew_{c}'] = _ewm_prior(P, k, c, hl)
    P['ew_dk_long'] = _ewm_prior(P, k, 'dk', 10)
    P['n_prior'] = P.groupby(k).cumcount()
    P['dk_sd'] = P.groupby(k)['dk'].transform(lambda s: s.shift(1).rolling(16, min_periods=3).std())
    P['dk_p90'] = P.groupby(k)['dk'].transform(lambda s: s.shift(1).rolling(16, min_periods=4).quantile(0.9))
    # same-season form
    P['ssn_dk'] = P.groupby(['player_id', 'season'])['dk'].transform(lambda s: s.shift(1).expanding().mean())
    P['ssn_n'] = P.groupby(['player_id', 'season']).cumcount()
    # defense vs position: DK points each defense allowed to a position, relative to league average (prior games only)
    a = P.groupby(['season', 'week', 'opponent_team', 'position'], as_index=False).dk.sum(min_count=1).rename(columns={'opponent_team': 'def'})
    a['lg'] = a.groupby(['season', 'position']).dk.transform('mean')
    a['rel'] = a.dk / a.lg
    a = a.sort_values(['def', 'position', 'season', 'week'])
    a['dvp'] = a.groupby(['def', 'position'])['rel'].transform(lambda s: s.shift(1).ewm(halflife=6, min_periods=1).mean())
    P = P.merge(a[['season', 'week', 'def', 'position', 'dvp']].rename(columns={'def': 'opponent_team'}), on=['season', 'week', 'opponent_team', 'position'], how='left')
    P['dvp'] = P.dvp.fillna(1.0)
    P['dome'] = P.roof.isin(['dome', 'closed']).astype(int)
    P['wind'] = np.where(P.dome == 1, 0, P.wind.fillna(P.wind.median()))
    P['naive'] = P.ew_dk                       # baseline: exponentially weighted recent DK points
    P['vegas_share'] = P.ew_team_share * P.implied
    return P

def team_rows(seasons, extra=None):
    """One row per team-game for DST and K, with pre-game features (prior games only). `extra`: (season, week, team, opponent_team) to project."""
    P, T = load(seasons); G = games()
    T = T[T.season_type == 'REG']
    T['upcoming'] = False
    if extra is not None and len(extra): T = pd.concat([T, extra.assign(upcoming=True)], ignore_index=True)
    T = T.copy()
    T = T.merge(G[['season', 'week', 'team', 'gid' if 'gid' in G else 'game_id', 'implied', 'opp_implied', 'spread', 'total_line', 'home', 'opp_pts', 'roof', 'wind']].rename(columns={'game_id': 'gid'}), on=['season', 'week', 'team'], how='left')
    T['dst'] = dk_dst(T, T.opp_pts.fillna(21)).astype(float)
    T['k'] = dk_kicker(T).astype(float)
    T.loc[T.upcoming, ['dst', 'k']] = np.nan
    T = T.sort_values(['team', 'season', 'week']).reset_index(drop=True)
    for c in ('dst', 'k', 'def_sacks'):
        T[f'ew_{c}'] = T.groupby('team')[c].transform(lambda s: s.shift(1).ewm(halflife=4, min_periods=1).mean())
    # how much the OPPONENT's offense gives up to defenses (sacks taken, turnovers)
    T['give'] = T.sacks_suffered.fillna(0) + 2 * (T.passing_interceptions.fillna(0) + T.fumbles_lost_total.fillna(0) if 'fumbles_lost_total' in T else T.passing_interceptions.fillna(0))
    T['ew_give'] = T.groupby('team')['give'].transform(lambda s: s.shift(1).ewm(halflife=5, min_periods=1).mean())
    T = T.merge(T[['season', 'week', 'team', 'ew_give']].rename(columns={'team': 'opponent_team', 'ew_give': 'opp_give'}), on=['season', 'week', 'opponent_team'], how='left')
    T['dome'] = T.roof.isin(['dome', 'closed']).astype(int)
    T['wind'] = np.where(T.dome == 1, 0, T.wind.fillna(8))
    return T
