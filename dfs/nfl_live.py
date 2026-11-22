"""Fresh game-day inputs for the NFL DFS build, fetched at build time (GitHub Actions can reach these; DraftKings can't be reached from there).
- statuses(): injury / inactive tags from ESPN's injury feed and Sleeper's player file (both update through Sunday morning).
- forecasts(games): wind, gusts, temperature and rain chance at kickoff for every stadium (Open-Meteo, free, no key).
Every fetch fails soft: if a feed is down, the build carries on with what it has and says so."""
import json, re, urllib.request, datetime as dt
UA = {'User-Agent': 'Mozilla/5.0 (betting-model DFS)'}
ESPN_FIX = {'WSH': 'WAS', 'LAR': 'LA', 'JAC': 'JAX', 'LVR': 'LV'}
SEV = {'None': 0, 'Q': 1, 'D': 2, 'O': 3, 'IR': 3}

def _get(url, timeout=40):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r: return json.load(r)

def norm(s):
    s = re.sub(r"[.'’\-]", '', str(s).lower()); s = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def statuses():
    """-> dict(by_id={gsis_id: tag}, by_name={(name, team): tag}, sources=[...], notes=[...]). Tags: Q, D, O, IR, None (cleared)."""
    out = dict(by_id={}, by_name={}, sources=[], notes=[])
    try:
        J = _get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries')
        m = {'Out': 'O', 'Injured Reserve': 'IR', 'Doubtful': 'D', 'Questionable': 'Q', 'Suspension': 'O', 'Active': 'None'}
        n = 0
        for t in J.get('injuries', []):
            for x in t.get('injuries', []):
                a = x.get('athlete') or {}; team = ((a.get('team') or {}).get('abbreviation') or '')
                tag = m.get(x.get('status') or '', None)
                if tag is None: continue
                out['by_name'][(norm(a.get('displayName', '')), ESPN_FIX.get(team, team))] = tag; n += 1
        out['sources'].append(f'ESPN injuries ({n})')
    except Exception as e: out['notes'].append(f'ESPN injuries unavailable: {e}')
    try:
        S = _get('https://api.sleeper.app/v1/players/nfl', timeout=90)
        m = {'Out': 'O', 'IR': 'IR', 'PUP': 'IR', 'Sus': 'O', 'COV': 'O', 'Doubtful': 'D', 'Questionable': 'Q'}
        n = 0
        for p in S.values():
            g = p.get('gsis_id'); tag = m.get(p.get('injury_status') or '')
            if g and tag: out['by_id'][str(g).strip()] = tag; n += 1
        out['sources'].append(f'Sleeper ({n} tagged)')
    except Exception as e: out['notes'].append(f'Sleeper unavailable: {e}')
    return out

def merge_status(dk, pid, name, team, S):
    """Fresh feeds win over the DraftKings tag (DK's is from whenever salaries were pulled). Worst tag across feeds."""
    if not S['sources']: return dk
    tags = [S['by_id'].get(str(pid))] if pid == pid and pid else []
    tags.append(S['by_name'].get((norm(name), team)))
    tags = [t for t in tags if t]
    if not tags: return 'None'
    return max(tags, key=lambda t: SEV.get(t, 0))

# stadium_id (nflverse) -> lat, lon
STADIUMS = {
    'SEA00': (47.595, -122.332), 'CAR00': (35.226, -80.853), 'CIN00': (39.095, -84.516), 'DET00': (42.340, -83.046), 'HOU00': (29.685, -95.411),
    'IND00': (39.760, -86.164), 'JAX00': (30.324, -81.637), 'PIT00': (40.447, -80.016), 'NAS00': (36.166, -86.771), 'LAX01': (33.953, -118.339),
    'VEG00': (36.091, -115.184), 'MIN01': (44.974, -93.258), 'PHI00': (39.901, -75.168), 'NYC01': (40.814, -74.074), 'KAN00': (39.049, -94.484),
    'BUF00': (42.774, -78.787), 'ATL97': (33.755, -84.401), 'BAL00': (39.278, -76.623), 'CHI98': (41.862, -87.617), 'BOS00': (42.091, -71.264),
    'TAM00': (27.976, -82.503), 'DEN00': (39.744, -105.020), 'PHO00': (33.528, -112.263), 'DAL00': (32.748, -97.093), 'SFO01': (37.403, -121.970),
    'GNB00': (44.501, -88.062), 'CLE00': (41.506, -81.700), 'MIA00': (25.958, -80.239), 'WAS00': (38.908, -76.864), 'NOR00': (29.951, -90.081),
    'LON00': (51.556, -0.280), 'LON01': (51.456, -0.342), 'LON02': (51.604, -0.066), 'MEX00': (19.303, -99.150), 'MUN01': (48.219, 11.625),
    'FRA00': (50.069, 8.645), 'BER00': (52.515, 13.239), 'RIO00': (-22.912, -43.230), 'SAO00': (-23.545, -46.474), 'MAD01': (40.453, -3.688),
    'PAR00': (48.924, 2.360), 'MEL00': (-37.820, 144.983), 'DUB00': (53.361, -6.251),
}

def forecasts(G):
    """G: upcoming games with game_id, gameday (YYYY-MM-DD), gametime (HH:MM US Eastern), stadium_id, roof.
    -> {game_id: dict(wind, gust, temp, rain, roof_used)}; dome/closed games get wind 0."""
    res, notes = {}, []
    now = dt.date.today()
    for r in G.drop_duplicates('game_id').itertuples():
        roof = str(r.roof) if r.roof == r.roof else ''
        if roof in ('dome', 'closed'): res[r.game_id] = dict(wind=0.0, gust=0.0, temp=None, rain=None, roof_used=roof); continue
        ll = STADIUMS.get(str(r.stadium_id))
        if not ll: notes.append(f'no coordinates for {r.stadium_id}'); continue
        try:
            day = dt.date.fromisoformat(str(r.gameday))
            if (day - now).days > 15 or day < now: continue
            u = (f'https://api.open-meteo.com/v1/forecast?latitude={ll[0]}&longitude={ll[1]}&hourly=wind_speed_10m,wind_gusts_10m,temperature_2m,precipitation_probability'
                 f'&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=America/New_York&start_date={day}&end_date={day}')
            J = _get(u)
            h = int(str(r.gametime)[:2]) if r.gametime == r.gametime else 13
            H = J['hourly']; idx = [i for i, t in enumerate(H['time']) if h <= int(t[11:13]) <= h + 3]   # kickoff through ~3 hours
            avg = lambda k: round(sum(H[k][i] for i in idx if H[k][i] is not None) / max(1, len(idx)), 1)
            w, gu, tp, rn = avg('wind_speed_10m'), avg('wind_gusts_10m'), avg('temperature_2m'), avg('precipitation_probability')
            used = 'outdoors'
            if roof in ('retractable', 'open', '', 'nan'):   # retractable roofs usually close for cold, rain or wind
                used = 'closed' if (tp < 55 or rn >= 40 or w >= 15) else 'open'
                if used == 'closed': w, gu = 0.0, 0.0
            res[r.game_id] = dict(wind=w, gust=gu, temp=tp, rain=rn, roof_used=used)
        except Exception as e: notes.append(f'forecast failed for {r.game_id}: {e}')
    return res, notes
