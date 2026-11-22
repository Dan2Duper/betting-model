"""Fresh game-day inputs for the MLB daily job (same idea as dfs/nfl_live.py). Everything here fails soft: if a feed is down the
build carries on with what it has and says so in mlb.json.
  parks()       park latitude / longitude / field direction / roof, from the MLB Stats API venue records (fallback table below)
  forecasts()   Open-Meteo first-pitch forecast at each park -> temperature, wind speed and wind direction RELATIVE TO THE FIELD
                (out to center, in from left, left to right ...), the same codes the model was trained on; roof closed -> dome
  espn_lines()  DraftKings moneyline, run line and total (with prices) as ESPN's MLB scoreboard lists them"""
import time, math, datetime as dt
import requests
API = 'https://statsapi.mlb.com/api/v1'
S = requests.Session(); S.headers['User-Agent'] = 'bet-tracker (personal project)'
def _get(url, tries=3, timeout=30):
    why = ''
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200: return r.json()
            why = f'HTTP {r.status_code} {r.text[:160]}'
            if r.status_code in (400, 404): break
        except Exception as e: why = repr(e)[:160]
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'failed ({why}): {url[:120]}')

# Fallback when the Stats API venue record is missing a field: home park by team code -> (lat, lon, bearing from home plate to
# center field in degrees clockwise from north, roof: O open / R retractable / D dome). Bearings are approximate (good to ~15 degrees).
PARKS = {'ANA': (33.800, -117.883, 44, 'O'), 'ARI': (33.445, -112.067, 0, 'R'), 'ATL': (33.891, -84.468, 145, 'O'), 'BAL': (39.284, -76.622, 31, 'O'), 'BOS': (42.346, -71.097, 45, 'O'),
         'CHN': (41.948, -87.655, 37, 'O'), 'CHA': (41.830, -87.634, 127, 'O'), 'CIN': (39.097, -84.507, 122, 'O'), 'CLE': (41.496, -81.685, 0, 'O'), 'COL': (39.756, -104.994, 4, 'O'),
         'DET': (42.339, -83.049, 150, 'O'), 'HOU': (29.757, -95.355, 343, 'R'), 'KCA': (39.051, -94.480, 46, 'O'), 'LAN': (34.074, -118.240, 26, 'O'), 'MIA': (25.778, -80.220, 128, 'R'),
         'MIL': (43.028, -87.971, 129, 'R'), 'MIN': (44.982, -93.278, 90, 'O'), 'NYN': (40.757, -73.846, 13, 'O'), 'NYA': (40.829, -73.926, 75, 'O'), 'ATH': (38.580, -121.514, 45, 'O'),
         'PHI': (39.906, -75.166, 9, 'O'), 'PIT': (40.447, -80.006, 116, 'O'), 'SDN': (32.707, -117.157, 0, 'O'), 'SFN': (37.778, -122.389, 85, 'O'), 'SEA': (47.591, -122.332, 49, 'R'),
         'SLN': (38.623, -90.193, 62, 'O'), 'TBA': (27.768, -82.653, 45, 'D'), 'TEX': (32.747, -97.084, 30, 'R'), 'TOR': (43.641, -79.389, 345, 'R'), 'WAS': (38.873, -77.007, 28, 'O')}
# When each retractable roof is shut, from what the clubs actually did in 2026 against the outdoor forecast (mlb/research/wx_check.py,
# 567 games; in-sample for these cut-offs): Houston and Miami are closed essentially always, Texas unless it is a mild dry evening,
# Arizona only above ~90F, Milwaukee and Toronto when it is cool, Seattle only for rain. Rain (40%+) closes every one of them.
def roof_closed(home, temp, rain):
    if rain >= 40 or home in ('HOU', 'MIA'): return True
    if home == 'TEX': return not (64 <= temp < 70)
    if home == 'ARI': return temp > 90
    if home == 'MIL': return temp < 66
    if home == 'TOR': return temp < 58
    if home == 'SEA': return False
    return temp < 60

def parks(X, max_age_days=30):
    """X['parks'][venue id] = [lat, lon, azimuth, roof]. Refreshed from the Stats API every `max_age_days`; kept in the crosswalk file."""
    if X.get('parks') and time.time() - X.get('parks_ts', 0) < max_age_days * 86400: return X['parks']
    out = dict(X.get('parks') or {})
    try:
        for v in _get(f'{API}/venues?sportId=1&hydrate=location,fieldInfo').get('venues', []):
            loc = v.get('location') or {}; c = loc.get('defaultCoordinates') or {}; roof = str((v.get('fieldInfo') or {}).get('roofType', '')).lower()
            if c.get('latitude') is None or c.get('longitude') is None: continue
            out[str(v['id'])] = [float(c['latitude']), float(c['longitude']), (float(loc['azimuthAngle']) if loc.get('azimuthAngle') is not None else None),
                                 'D' if roof.startswith('dome') or roof == 'indoor' else 'R' if roof.startswith('retract') else 'O']
        X['parks'] = out; X['parks_ts'] = time.time(); print('parks from the Stats API:', len(out), '| with field direction:', sum(1 for p in out.values() if p[2] is not None))
    except Exception as e: print('venue lookup failed (using the built-in park table):', e)
    return out

def park_of(P, venue_id, home):
    """[lat, lon, azimuth, roof] for a game: Stats API record first, each missing piece filled from the home team's fallback row."""
    a = list(P.get(str(venue_id)) or [None, None, None, None]); f = PARKS.get(home)
    if a[0] is None and f: a[0], a[1] = f[0], f[1]; a[3] = a[3] or f[3]
    if a[2] is None and f and a[0] is not None and abs(a[0] - f[0]) < 0.05 and abs(a[1] - f[1]) < 0.05: a[2] = f[2]     # only if it is the team's own park
    if a[3] is None: a[3] = f[3] if f else 'O'
    return a

def field_wind(from_deg, azimuth):
    """Compass direction the wind comes FROM + the field's home-plate-to-center bearing -> the scorer's code (tocf, fromlf, ltor ...)."""
    rel = ((from_deg + 180.0 - azimuth + 180.0) % 360.0) - 180.0      # where the wind is blowing TO, measured clockwise from center field
    a = abs(rel)
    if a <= 22.5: return 'tocf'
    if a <= 67.5: return 'torf' if rel > 0 else 'tolf'
    if a <= 112.5: return 'ltor' if rel > 0 else 'rtol'
    if a <= 157.5: return 'fromlf' if rel > 0 else 'fromrf'
    return 'fromcf'

def forecasts(games, P):
    """games: [{gid, venue_id, home, start_utc}] -> {gid: {temp, windspeed, winddir, sky, rain, gust, humid, roof, src}}. One Open-Meteo call for the slate."""
    out = {}; G = []
    for g in games:
        p = park_of(P, g.get('venue_id'), g.get('home'))
        if p[0] is None or not g.get('start_utc'): continue
        if p[3] == 'D': out[g['gid']] = dict(temp=72.0, windspeed=0.0, winddir='unknown', sky='dome', roof='dome', src='dome'); continue
        G.append((g, p))
    Q = '&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,wind_direction_10m,wind_gusts_10m&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=GMT&forecast_days=4'
    W = {}   # one call per park (a day's games share parks only in doubleheaders); a failed park just stays neutral
    for g, p in G:
        k = (round(p[0], 3), round(p[1], 3))
        if k in W: continue
        try: W[k] = _get(f'https://api.open-meteo.com/v1/forecast?latitude={p[0]:.4f}&longitude={p[1]:.4f}' + Q, tries=2, timeout=20)
        except Exception as e: W[k] = None; print('weather forecast failed for', g.get('home'), e)
    for chunk in [G]:
        for (g, p) in chunk:
            w = W.get((round(p[0], 3), round(p[1], 3)))
            if not w: continue
            try:
                h = w['hourly']; t0 = dt.datetime.fromisoformat(g['start_utc'].replace('Z', '+00:00')).astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
                k = h['time'].index(t0.strftime('%Y-%m-%dT%H:00')); ks = [x for x in (k, k + 1, k + 2) if x < len(h['time'])]     # first pitch and the two hours after
                avg = lambda name: sum(h[name][x] for x in ks if h[name][x] is not None) / max(sum(1 for x in ks if h[name][x] is not None), 1)
                temp, ws, hum = avg('temperature_2m'), avg('wind_speed_10m'), avg('relative_humidity_2m'); rain = max((h['precipitation_probability'][x] or 0) for x in ks); gust = max((h['wind_gusts_10m'][x] or 0) for x in ks)
                # mean direction as a vector, so 350 and 10 degrees average to north
                sx = sum(math.sin(math.radians(h['wind_direction_10m'][x])) for x in ks if h['wind_direction_10m'][x] is not None); cx = sum(math.cos(math.radians(h['wind_direction_10m'][x])) for x in ks if h['wind_direction_10m'][x] is not None)
                wfrom = math.degrees(math.atan2(sx, cx)) % 360.0
                closed = p[3] == 'R' and roof_closed(g.get('home'), temp, rain)
                if closed: out[g['gid']] = dict(temp=72.0, windspeed=0.0, winddir='unknown', sky='dome', roof='closed (expected)', rain=float(rain), src='forecast'); continue
                out[g['gid']] = dict(temp=round(float(temp), 1), windspeed=round(float(ws), 1), winddir=field_wind(wfrom, p[2]) if p[2] is not None else 'unknown', sky='', rain=float(rain), gust=round(float(gust), 1),
                                     humid=round(float(hum)), wind_from=round(wfrom), roof='open (expected)' if p[3] == 'R' else '', src='forecast')
            except Exception as e: print('forecast row failed', g.get('gid'), e)
    return out

# ---------------- DraftKings lines as ESPN lists them ----------------
ESPN_TEAM = {'LAA': 'ANA', 'CHC': 'CHN', 'CHW': 'CHA', 'CWS': 'CHA', 'KC': 'KCA', 'LAD': 'LAN', 'NYM': 'NYN', 'NYY': 'NYA', 'SD': 'SDN', 'SF': 'SFN', 'STL': 'SLN', 'TB': 'TBA', 'WSH': 'WAS', 'OAK': 'ATH', 'AZ': 'ARI'}
def _num(v):
    try:
        s = ''.join(ch for ch in str(v) if ch in '0123456789.+-'); return float(s) if s not in ('', '+', '-', '.') else None
    except Exception: return None
def _am(v):
    """American odds or None ('EVEN' = +100)."""
    if v is None: return None
    if str(v).strip().upper() in ('EVEN', 'EV'): return 100.0
    x = _num(v); return x if x is not None and abs(x) >= 100 else None
def parse_odds(o):
    """One ESPN odds record (either feed shape) -> {mh, ma, total, over, under, rl, rl_h, rl_a, src}; rl is the HOME run line (-1.5 / +1.5)."""
    if not o: return None
    g = lambda d, *ks: next((x for x in [_dig(d, k) for k in ks] if x is not None), None)
    out = dict(mh=_am(g(o, 'homeTeamOdds.moneyLine', 'moneyline.home.close.odds', 'moneyline.home.open.odds')), ma=_am(g(o, 'awayTeamOdds.moneyLine', 'moneyline.away.close.odds', 'moneyline.away.open.odds')),
               total=_num(g(o, 'overUnder', 'total.over.close.line')), over=_am(g(o, 'overOdds', 'total.over.close.odds')), under=_am(g(o, 'underOdds', 'total.under.close.odds')),
               rl=None, rl_h=_am(g(o, 'pointSpread.home.close.odds', 'homeTeamOdds.spreadOdds')), rl_a=_am(g(o, 'pointSpread.away.close.odds', 'awayTeamOdds.spreadOdds')), src=(o.get('provider') or {}).get('name') or 'ESPN')
    hl = _num(g(o, 'pointSpread.home.close.line'))
    if hl is None and o.get('spread') is not None:   # older shape: one unsigned / favorite-signed number plus favorite flags
        sp = abs(_num(o.get('spread')) or 0)
        if sp: hl = -sp if _dig(o, 'homeTeamOdds.favorite') else sp if _dig(o, 'awayTeamOdds.favorite') else None
    if hl is not None and abs(hl) == 1.5: out['rl'] = hl
    if out['total'] is not None and not (4 <= out['total'] <= 20): out['total'] = None
    return out if any(out[k] is not None for k in ('mh', 'total', 'rl')) else None
def _dig(d, path):
    for k in path.split('.'):
        if not isinstance(d, dict): return None
        d = d.get(k)
    return d
def espn_lines(day):
    """Every MLB game ESPN lists for `day` (YYYY-MM-DD): teams in the model's codes, start, state, score and the listed line."""
    j = _get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates=' + day.replace('-', ''), tries=2)
    out = []
    for ev in j.get('events', []):
        try:
            c = ev['competitions'][0]; H = next(x for x in c['competitors'] if x['homeAway'] == 'home'); A = next(x for x in c['competitors'] if x['homeAway'] == 'away')
            st = (ev.get('status') or {}).get('type') or {}
            out.append(dict(espn=str(ev['id']), kick=ev.get('date'), home=ESPN_TEAM.get(H['team']['abbreviation'], H['team']['abbreviation']), away=ESPN_TEAM.get(A['team']['abbreviation'], A['team']['abbreviation']),
                            state=st.get('state', ''), done=bool(st.get('completed')), hs=_num(H.get('score')), as_=_num(A.get('score')), odds=parse_odds((c.get('odds') or [None])[0])))
        except Exception as e: print('espn event skipped:', e)
    return out
def match_espn(E, home, away, start_utc):
    """The ESPN game for a model game: same teams, closest start (doubleheaders)."""
    c = [e for e in E if e['home'] == home and e['away'] == away]
    if not c: return None
    try:
        t = dt.datetime.fromisoformat(start_utc.replace('Z', '+00:00')); key = lambda e: abs((dt.datetime.fromisoformat(e['kick'].replace('Z', '+00:00')) - t).total_seconds())
        return sorted(c, key=key)[0]
    except Exception: return c[0]
