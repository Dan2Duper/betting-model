"""MLB daily job: MLB Stats API -> the same tables the backtest uses -> features.build() -> model.json -> mlb.json for the page.
History through the last Retrosheet season comes from mlb/hist/; every later game comes from the Stats API box scores (cached in
CACHE so each game is downloaded once). The target day's games get features built only from earlier days, exactly as in the backtest.
Usage: python3 mlb/live.py --cache DIR --out mlb.json [--date YYYY-MM-DD] [--days 2] [--check]
  --date  first day to predict (default: today, US Central). A past date replays that day without using its results.
  --check also grades every Stats-API-season game played before --date (walk-forward) and prints / stores the record."""
import os, sys, json, time, argparse, datetime as dt, concurrent.futures as cf
import numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from features import build, FEATS
API = 'https://statsapi.mlb.com/api/v1'
TEAM = {108: 'ANA', 109: 'ARI', 110: 'BAL', 111: 'BOS', 112: 'CHN', 113: 'CIN', 114: 'CLE', 115: 'COL', 116: 'DET', 117: 'HOU', 118: 'KCA', 119: 'LAN', 120: 'WAS', 121: 'NYN',
        133: 'ATH', 134: 'PIT', 135: 'SDN', 136: 'SEA', 137: 'SFN', 138: 'SLN', 139: 'TBA', 140: 'TEX', 141: 'TOR', 142: 'MIN', 143: 'PHI', 144: 'ATL', 145: 'CHA', 146: 'MIA', 147: 'NYA', 158: 'MIL'}
S = requests.Session(); S.headers['User-Agent'] = 'bet-tracker (personal project)'
def get(url, tries=5):
    for i in range(tries):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 200: return r.json()
        except Exception as e: err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'failed: {url}')

def schedule(**q):
    url = f'{API}/schedule?sportId=1&' + '&'.join(f'{k}={v}' for k, v in q.items())
    return [g for d in get(url).get('dates', []) for g in d.get('games', [])]
def is_final(g):
    st = g.get('status', {}); return st.get('abstractGameState') == 'Final' and st.get('detailedState') in ('Final', 'Game Over', 'Completed Early') and g['teams']['home'].get('score') is not None
def num_of(g): return int(g.get('gameNumber', 1)) if g.get('doubleHeader', 'N') != 'N' else 0

# ---------------- id crosswalks (MLBAM -> Retrosheet), kept in CACHE/xwalk.json ----------------
def load_xwalk(cache, hist_games):
    f = f'{cache}/xwalk.json'; X = json.load(open(f)) if os.path.exists(f) else {}
    if 'players' not in X or time.time() - X.get('players_ts', 0) > 20 * 86400:
        P = {}
        for h in '0123456789abcdef':
            try: d = pd.read_csv(f'https://raw.githubusercontent.com/chadwickbureau/register/master/data/people-{h}.csv', usecols=['key_mlbam', 'key_retro'], low_memory=False)
            except Exception as e: print('register part', h, 'failed', e); continue
            d = d.dropna(); P.update({str(int(m)): r for m, r in zip(d.key_mlbam, d.key_retro)})
        if P: X['players'] = P; X['players_ts'] = time.time()
        print('crosswalk players', len(X.get('players', {})))
    last = int(hist_games.season.max())
    if X.get('venues_from') != last:   # learn venue and plate-umpire ids from the last three Retrosheet seasons, matched game by game
        hg = hist_games[hist_games.season >= last - 2]
        key = {(str(pd.Timestamp(r.date).date()), r.home, int(r.num)): r for r in hg.itertuples()}
        ven, ump = {}, {}
        for g in [g for s in range(last - 2, last + 1) for g in schedule(season=s, gameType='R', hydrate='officials,venue')]:
            if not is_final(g): continue
            r = key.get((g.get('officialDate'), TEAM.get(g['teams']['home']['team']['id']), num_of(g)))
            if r is None: continue
            ven.setdefault(str(g['venue']['id']), {}).setdefault(r.park, 0); ven[str(g['venue']['id'])][r.park] += 1
            for o in g.get('officials', []):
                if o.get('officialType') == 'Home Plate' and r.ump:
                    u = ump.setdefault(str(o['official']['id']), {}); u[r.ump] = u.get(r.ump, 0) + 1
        X['venues'] = {k: max(v, key=v.get) for k, v in ven.items()}; X['umps'] = {k: max(v, key=v.get) for k, v in ump.items()}; X['venues_from'] = last
        print('crosswalk venues', len(X['venues']), 'umpires', len(X['umps']))
    X.setdefault('hands', {})
    json.dump(X, open(f, 'w')); return X
def pid(X, m): return X['players'].get(str(m), f'm{m}')

# ---------------- box score -> rows ----------------
WDIR = {'out to cf': 'tocf', 'out to lf': 'tolf', 'out to rf': 'torf', 'in from cf': 'fromcf', 'in from lf': 'fromlf', 'in from rf': 'fromrf', 'l to r': 'ltor', 'r to l': 'rtol'}
def parse_weather(cond, wind):
    """'72 degrees, Partly Cloudy.' + '8 mph, Out To CF.' -> temp, sky, windspeed, winddir (Retrosheet codes)."""
    temp, sky, ws, wd = np.nan, '', np.nan, 'unknown'
    try: temp = float(str(cond).split(' degrees')[0])
    except Exception: pass
    c = str(cond).lower()
    if 'dome' in c or 'roof closed' in c: sky = 'dome'
    w = str(wind).lower().rstrip('.')
    try: ws = float(w.split(' mph')[0])
    except Exception: pass
    for k, v in WDIR.items():
        if k in w: wd = v
    return temp, sky, ws, wd
def game_row(g, X):
    h, a = g['teams']['home'], g['teams']['away']
    return dict(gid=f"pk{g['gamePk']}", pk=int(g['gamePk']), season=int(g.get('season') or g['officialDate'][:4]), date=g['officialDate'], num=num_of(g), away=TEAM.get(a['team']['id'], str(a['team']['id'])), home=TEAM.get(h['team']['id'], str(h['team']['id'])),
                park=X['venues'].get(str(g.get('venue', {}).get('id')), f"v{g.get('venue', {}).get('id')}"), venue=g.get('venue', {}).get('name', ''), dn='D' if g.get('dayNight') == 'day' else 'N', home_lg='', usedh='true',
                start_utc=g.get('gameDate'), sched7=int(g.get('scheduledInnings', 9) or 9) < 9)
def box_rows(g, X):
    b = get(f"{API}/game/{g['gamePk']}/boxscore"); row = game_row(g, X)
    row['hruns'] = float(g['teams']['home']['score']); row['aruns'] = float(g['teams']['away']['score'])
    info = {i.get('label'): i.get('value') for i in b.get('info', []) if isinstance(i, dict)}
    row['temp'], row['sky'], row['windspeed'], row['winddir'] = parse_weather(info.get('Weather', ''), info.get('Wind', ''))
    hp = [o for o in b.get('officials', []) if o.get('officialType') == 'Home Plate']
    row['ump'] = X['umps'].get(str(hp[0]['official']['id']), f"m{hp[0]['official']['id']}") if hp else ''
    pit, bat = [], []
    for side, pre in (('away', 'a'), ('home', 'h')):
        t = b['teams'][side]; team = row['away' if side == 'away' else 'home']; pl = t.get('players', {})
        starters = sorted([p for p in pl.values() if str(p.get('battingOrder', '')).endswith('00')], key=lambda p: int(p['battingOrder']))
        row[pre + '_lineup'] = ','.join(pid(X, p['person']['id']) for p in starters[:9])
        row[pre + '_pos'] = ','.join(str((p.get('allPositions') or [p.get('position', {})])[0].get('code', '')) for p in starters[:9])
        order = t.get('pitchers', []); row[pre + 'sp'] = pid(X, order[0]) if order else ''
        row[pre + 'sp_name'] = pl.get(f'ID{order[0]}', {}).get('person', {}).get('fullName', '') if order else ''
        for p in pl.values():
            i = pid(X, p['person']['id']); st = p.get('stats', {}); B = st.get('batting') or {}; P = st.get('pitching') or {}
            if B.get('plateAppearances', 0) > 0:
                bat.append(dict(gid=row['gid'], date=row['date'], team=team, pid=i, B_PA=B.get('plateAppearances', 0), B_AB=B.get('atBats', 0), B_H=B.get('hits', 0), B_2B=B.get('doubles', 0), B_3B=B.get('triples', 0),
                                B_HR=B.get('homeRuns', 0), B_BB=B.get('baseOnBalls', 0), B_IBB=B.get('intentionalWalks', 0), B_SO=B.get('strikeOuts', 0), B_HP=B.get('hitByPitch', 0), B_SF=B.get('sacFlies', 0), B_SB=B.get('stolenBases', 0)))
            if P.get('battersFaced', 0) > 0 or P.get('outs', 0) > 0:
                gs = int(order and p['person']['id'] == order[0])
                pit.append(dict(gid=row['gid'], date=row['date'], team=team, pid=i, P_G=1, P_GS=gs, P_OUT=P.get('outs', 0), P_TBF=P.get('battersFaced', 0), P_R=P.get('runs', 0), P_ER=P.get('earnedRuns', 0), P_H=P.get('hits', 0),
                                P_HR=P.get('homeRuns', 0), P_BB=P.get('baseOnBalls', 0), P_IBB=P.get('intentionalWalks', 0), P_SO=P.get('strikeOuts', 0), P_HP=P.get('hitByPitch', P.get('hitBatsmen', 0)),
                                P_GO=P.get('groundOuts', 0), P_AO=P.get('airOuts', 0), P_PITCH=P.get('numberOfPitches', P.get('pitchesThrown', 0))))
    return row, pit, bat

def update_cache(cache, X, seasons, before, max_new=4000):
    """Download box scores for finished regular-season games not in the cache yet."""
    T = {n: (pd.read_parquet(f'{cache}/live_{n}.parquet') if os.path.exists(f'{cache}/live_{n}.parquet') else pd.DataFrame()) for n in ('games', 'pit', 'bat')}
    have = set(T['games'].pk) if len(T['games']) else set()
    todo = [g for s in seasons for g in schedule(season=s, gameType='R') if is_final(g) and g['officialDate'] < before and g['gamePk'] not in have]
    todo = list({g['gamePk']: g for g in todo}.values())[:max_new]
    if todo:
        R, Pt, Bt, bad = [], [], [], 0
        with cf.ThreadPoolExecutor(8) as ex:
            for fut in cf.as_completed([ex.submit(box_rows, g, X) for g in todo]):
                try: r, p, b = fut.result(); R.append(r); Pt += p; Bt += b
                except Exception as e: bad += 1; err = e
        print(f'box scores: {len(R)} new, {bad} failed' + (f' (last error: {err!r})' if bad else ''))
        for n, new in (('games', R), ('pit', Pt), ('bat', Bt)):
            T[n] = pd.concat([T[n], pd.DataFrame(new)], ignore_index=True); T[n].to_parquet(f'{cache}/live_{n}.parquet', compression='zstd')
    return T

def hands_for(X, ids, cache):
    """Bats/throws for players the Retrosheet rosters don't know (this season's rookies), from the people endpoint."""
    need = [i for i in ids if i.startswith('m') and i not in X['hands']]
    for k in range(0, len(need), 100):
        try:
            for p in get(f"{API}/people?personIds={','.join(i[1:] for i in need[k:k + 100])}").get('people', []):
                X['hands'][f"m{p['id']}"] = [p.get('batSide', {}).get('code', 'R').replace('S', 'B'), p.get('pitchHand', {}).get('code', 'R')]
        except Exception as e: print('people lookup failed', e)
    if need: json.dump(X, open(f'{cache}/xwalk.json', 'w'))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cache', required=True); ap.add_argument('--out', required=True); ap.add_argument('--date'); ap.add_argument('--days', type=int, default=2)
    ap.add_argument('--check', action='store_true'); ap.add_argument('--max-new', type=int, default=4000)
    ap.add_argument('--hist-last', type=int, help='parity test: pretend Retrosheet history ends with this season'); A = ap.parse_args()
    os.makedirs(A.cache, exist_ok=True)
    day0 = A.date or str((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).date()); d0 = dt.date.fromisoformat(day0)
    H = {n: pd.read_parquet(f'{HERE}/hist/{n}.parquet') for n in ('games', 'pit', 'bat', 'hands')}
    for n in ('games', 'pit', 'bat'): H[n]['date'] = pd.to_datetime(H[n].date).dt.strftime('%Y-%m-%d')
    if A.hist_last:
        for n in ('games', 'pit', 'bat'): H[n] = H[n][H[n].date < f'{A.hist_last + 1}-01-01']
    M = json.load(open(f'{HERE}/model.json')); RULES = json.load(open(f'{os.path.dirname(HERE)}/rules.json')).get('mlb', {})
    X = load_xwalk(A.cache, H['games'])
    seasons = list(range(int(H['games'].season.max()) + 1, d0.year + 1))
    T = update_cache(A.cache, X, seasons, day0, A.max_new)
    LG = T['games'][T['games'].date < day0] if len(T['games']) else T['games']
    # ids learned after a game was cached (a rookie gets his Retrosheet id, a park or umpire gets mapped) are applied to the cached rows
    rp = lambda i: X['players'].get(str(i)[1:], i) if str(i).startswith('m') else i
    if len(LG):
        LG = LG.copy(); LG['park'] = [X['venues'].get(str(p)[1:], p) if str(p).startswith('v') else p for p in LG.park]
        LG['ump'] = [X['umps'].get(str(u)[1:], u) if str(u).startswith('m') else u for u in LG.ump]
        for c in ('asp', 'hsp'): LG[c] = LG[c].map(rp)
        for c in ('a_lineup', 'h_lineup'): LG[c] = [','.join(rp(i) for i in str(v).split(',')) for v in LG[c]]
        for n in ('pit', 'bat'): T[n] = T[n].assign(pid=T[n].pid.map(rp))
    # ---- the slate ----
    slate = []
    for g in schedule(startDate=day0, endDate=str(d0 + dt.timedelta(days=A.days - 1)), gameType='R', hydrate='probablePitcher,lineups,venue,weather,officials'):
        if g.get('status', {}).get('detailedState') in ('Postponed', 'Cancelled'): continue
        r = game_row(g, X); r['hruns'] = np.nan; r['aruns'] = np.nan; r['ump'] = ''
        wx = g.get('weather') or {}
        if wx.get('temp') and not A.hist_last:
            r['temp'], r['sky'], r['windspeed'], r['winddir'] = parse_weather(f"{wx.get('temp')} degrees, {wx.get('condition', '')}", wx.get('wind', '')); r['wx'] = 'posted'
            if r['temp'] != r['temp']: r['temp'] = 72.0
            if r['windspeed'] != r['windspeed']: r['windspeed'] = 0.0
        else: r['temp'], r['sky'], r['windspeed'], r['winddir'], r['wx'] = 72.0, '', 0.0, 'unknown', 'neutral'      # 72F, no wind until the game-day weather is posted
        last = pd.concat([H['games'], LG]).sort_values('date') if len(LG) else H['games'].sort_values('date')
        for side, pre in (('away', 'a'), ('home', 'h')):
            t = g['teams'][side]; pp = t.get('probablePitcher') or {}
            r[pre + 'sp'] = pid(X, pp['id']) if pp.get('id') else ''; r[pre + 'sp_name'] = pp.get('fullName', 'TBD')
            lu = (g.get('lineups') or {}).get(side + 'Players') or []
            if len(lu) >= 9:
                r[pre + '_lineup'] = ','.join(pid(X, p['id']) for p in lu[:9]); r[pre + '_pos'] = ','.join(str(p.get('primaryPosition', {}).get('code', '')) for p in lu[:9]); r[pre + '_lineup_src'] = 'posted'
            else:   # not posted yet: the team's most recent lineup
                team = r['away' if side == 'away' else 'home']; q = last[(last.home == team) | (last.away == team)]
                if len(q):
                    z = q.iloc[-1]; k = 'h' if z.home == team else 'a'; r[pre + '_lineup'] = z[k + '_lineup']; r[pre + '_pos'] = z[k + '_pos']
                else: r[pre + '_lineup'] = ','.join([''] * 9); r[pre + '_pos'] = ','.join([''] * 9)
                r[pre + '_lineup_src'] = 'last game'
        slate.append(r)
    SL = pd.DataFrame(slate); print('slate', day0, len(SL), 'games; live-season games in cache', len(LG))
    # ---- one table, one feature build ----
    GC = list(H['games'].columns)
    G = pd.concat([H['games'], LG.reindex(columns=GC) if len(LG) else None, SL.reindex(columns=GC) if len(SL) else None], ignore_index=True)
    G['date'] = pd.to_datetime(G.date); G['num'] = G.num.astype(int); G['season'] = G.date.dt.year
    for c in ('ump', 'asp', 'hsp', 'sky', 'winddir', 'usedh', 'a_lineup', 'h_lineup', 'a_pos', 'h_pos'): G[c] = G[c].fillna('')
    lp = T['pit'][T['pit'].date < day0] if len(T['pit']) else T['pit']; lb = T['bat'][T['bat'].date < day0] if len(T['bat']) else T['bat']
    pit = pd.concat([H['pit'], lp], ignore_index=True); bat = pd.concat([H['bat'], lb], ignore_index=True)
    pit['date'] = pd.to_datetime(pit.date); bat['date'] = pd.to_datetime(bat.date)
    ids = set(G[G.season > H['games'].season.max()][['asp', 'hsp']].values.ravel()) | {i for s in G[G.season > H['games'].season.max()][['a_lineup', 'h_lineup']].values.ravel() for i in str(s).split(',')}
    hands_for(X, sorted(i for i in ids if i), A.cache)
    hands = pd.concat([H['hands'], pd.DataFrame([(q, v[0], v[1]) for k, v in X['hands'].items() for q in {k, rp(k)}], columns=['pid', 'bats', 'throws'])]).drop_duplicates('pid')
    F = build(G, pit, bat, hands)
    mu, sd, beta = np.array(M['mu']), np.array(M['sd']), np.array(M['beta'])
    F['lam'] = np.exp(M['b0'] + ((F[FEATS].values - mu) / sd) @ beta)
    h = F[F.is_home == 1].set_index('gid'); a = F[F.is_home == 0].set_index('gid')
    P = pd.DataFrame(dict(lam_h=h.lam, lam_a=a.lam)); P['p_home'] = 1 / (1 + np.exp(-(M['win']['a'] + M['win']['k'] * np.log(P.lam_h / P.lam_a))))
    out = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(), date=day0, model=dict(trained=M['trained'], over=M['over'], win=M['win']), rules=RULES,
               wx={f: float(b / s) for f, b, s in zip(FEATS, beta, sd) if f in ('temp', 'wind_out', 'wind_in', 'wind_x', 'dome')},      # per-unit log-run weights (temp is vs 72F)
               games=[])
    nm = {}
    for r in SL.itertuples():
        if r.gid not in P.index: continue
        p = P.loc[r.gid]; fa, fh = a.loc[r.gid], h.loc[r.gid]   # a-row = away batters vs the home pitcher
        out['games'].append(dict(id=r.gid, pk=int(r.pk), date=r.date, kick=r.start_utc, away=r.away, home=r.home, venue=r.venue, park=r.park, num=int(r.num), sched7=bool(r.sched7),
                                 sp_away=r.asp_name, sp_home=r.hsp_name, sp_away_known=bool(r.asp), sp_home_known=bool(r.hsp), lineup_away=r.a_lineup_src, lineup_home=r.h_lineup_src,
                                 pa=round(float(p.lam_a), 2), ph=round(float(p.lam_h), 2), p_home=round(float(p.p_home), 4), total=round(float(p.lam_a + p.lam_h), 2),
                                 wx=r.wx, temp=round(float(fh.temp)), wind_out=round(float(fh.wind_out)), wind_in=round(float(fh.wind_in)), dome=bool(fh.dome), park_factor=round(float(fh.park), 3), sp_away_k=round(float(fh.sp_k), 2), sp_home_k=round(float(fa.sp_k), 2), lu_away=round(float(fa.lu_woba), 3), lu_home=round(float(fh.lu_woba), 3)))
    if A.check and len(LG):
        C = LG.set_index('gid').join(P, how='inner'); C = C[C.lam_h.notna()]
        hw = (C.hruns > C.aruns).astype(int); ll = float(-(hw * np.log(C.p_home) + (1 - hw) * np.log(1 - C.p_home)).mean())
        out['check'] = dict(games=int(len(C)), model_winner=round(float(((C.p_home > 0.5) == (hw == 1)).mean()), 4), logloss=round(ll, 4), home_win=round(float(hw.mean()), 4),
                            model_total=round(float((C.lam_h + C.lam_a).mean()), 2), actual_total=round(float((C.hruns + C.aruns).mean()), 2), total_mae=round(float((C.lam_h + C.lam_a - C.hruns - C.aruns).abs().mean()), 2),
                            unknown_players=round(float(np.mean([i.startswith('m') for s in LG.h_lineup for i in str(s).split(',') if i])), 4), unknown_parks=int(LG.park.astype(str).str.startswith('v').sum()),
                            unknown_umps=int(LG.ump.astype(str).str.startswith('m').sum()), weather_missing=int(LG.temp.isna().sum()))
        print('WALK-FORWARD CHECK', json.dumps(out['check']))
    json.dump(out, open(A.out, 'w')); print('wrote', A.out, len(out['games']), 'games')
    for g in out['games'][:6]: print(' ', g['away'], g['pa'], '@', g['home'], g['ph'], 'p_home', g['p_home'], g['sp_away'], 'vs', g['sp_home'], g['lineup_home'])

if __name__ == '__main__': main()
