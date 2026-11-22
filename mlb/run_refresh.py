"""Driver for the data-branch workflow "MLB build": a push of mlb/_refresh.json to the data branch (the relay cron does that before each
first-pitch window, and at set times each day) starts it. Normal run: today's slate -> mlb.json, mlb/log_<season>.json, dfs_mlb.json.
_refresh.json may carry {"test": {"date": "YYYY-MM-DD", "days": 1, "check": true}} to replay a past day into /tmp (nothing saved),
or {"parity": 2025} for the Retrosheet self-test. Usage: python3 mlb/run_refresh.py DATA_BRANCH_DIR"""
import os, sys, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); DB = sys.argv[1]
try: J = json.load(open(f'{DB}/mlb/_refresh.json'))
except Exception: J = {}
print('refresh request:', json.dumps(J)[:300])
def run(args): print('+', ' '.join(args), flush=True); return subprocess.run([sys.executable] + args).returncode
if J.get('parity'):
    y = int(J['parity']); sys.exit(run([f'{HERE}/live.py', '--cache', '/tmp/par', '--out', '/tmp/par.json', '--date', f'{y}-11-15', '--days', '1', '--check', '--hist-last', str(y - 1)]))
if J.get('feeds_test'):   # self-test of the outside feeds (off-season safe): venue records, a forecast at every park, ESPN's odds shape
    sys.path.insert(0, HERE); import inputs, datetime as dt
    X = {}; P = inputs.parks(X); print('parks:', len(P)); import requests
    teams = {t['id']: t for t in requests.get(inputs.API + '/teams?sportId=1&hydrate=venue', timeout=30).json().get('teams', [])}
    sys.path.insert(0, HERE); from live import TEAM
    t0 = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).replace(hour=23, minute=5, second=0, microsecond=0)
    G = [dict(gid=TEAM[i], venue_id=t['venue']['id'], home=TEAM[i], start_utc=t0.strftime('%Y-%m-%dT%H:%M:%SZ')) for i, t in teams.items() if i in TEAM]
    for g in G: print('PARK', g['home'], g['venue_id'], P.get(str(g['venue_id'])), '| fallback', inputs.PARKS.get(g['home']), '| used', inputs.park_of(P, g['venue_id'], g['home']))
    F = inputs.forecasts(G, P)
    for k, v in sorted(F.items()): print('FORECAST', k, json.dumps(v))
    for sport in ('football/nfl', 'basketball/nba', 'baseball/mlb'):
        j = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/scoreboard', timeout=30).json(); n = 0
        for ev in j.get('events', []):
            c = ev['competitions'][0]; o = (c.get('odds') or [None])[0]
            if o and n < 2: n += 1; print('ESPN', sport, ev.get('shortName'), (ev.get('status') or {}).get('type', {}).get('state'), 'RAW', json.dumps(o)[:1500]); print('  PARSED', inputs.parse_odds(o))
        print('ESPN', sport, len(j.get('events', [])), 'events,', n, 'with odds shown')
    sys.exit(0)
if J.get('wx_check'): sys.exit(run([f'{HERE}/research/wx_check.py', '--cache', f'{DB}/mlb', '--season', str(int(J['wx_check']))]))
T = J.get('test')
if T:   # replay: work on a copy so the real cache and log are untouched
    subprocess.run(['cp', '-r', f'{DB}/mlb', '/tmp/mlbtest'], check=True)
    for f in os.listdir('/tmp/mlbtest'):
        if f.startswith('log_') or f == 'lines.json': os.remove(f'/tmp/mlbtest/{f}')
    rc = 0
    for d in (T.get('dates') or [T.get('date')]):
        a = [f'{HERE}/live.py', '--cache', '/tmp/mlbtest', '--out', '/tmp/mlb_test.json', '--days', str(int(T.get('days', 1)))] + (['--date', str(d)] if d else []) + (['--check'] if T.get('check') else []) + (['--now', str(T['now'])] if T.get('now') else [])
        rc = rc or run(a)
        try:
            O = json.load(open('/tmp/mlb_test.json')); print('TEST OUTPUT', d, json.dumps({k: O[k] for k in O if k != 'games'})[:1500])
            for g in O['games'][:3]: print('GAME', json.dumps(g)[:1800])
        except Exception as e: print('no test output', e)
    if T.get('dfs'): rc = rc or run([f'{os.path.dirname(HERE)}/dfs/build_mlb.py', '--mlb', '/tmp/mlb_test.json', '--cache', '/tmp/mlbtest', '--salaries', f'{DB}/salaries', '--out', '/tmp/dfs_mlb_test.json'] + (['--fake-salaries'] if T.get('fake_salaries') else []))
    sys.exit(rc)
rc = run([f'{HERE}/live.py', '--cache', f'{DB}/mlb', '--out', f'{DB}/mlb.json', '--days', str(int(J.get('days', 2)))] + (['--check'] if J.get('check') else []))
b = f'{os.path.dirname(HERE)}/dfs/build_mlb.py'
if rc == 0 and os.path.exists(b):   # DFS lineups from the same build (a DFS failure must not block the picks)
    r2 = run([b, '--mlb', f'{DB}/mlb.json', '--cache', f'{DB}/mlb', '--salaries', f'{DB}/salaries', '--todo', f'{DB}/dfs_todo.json', '--out', f'{DB}/dfs_mlb.json'])
    if r2: print('DFS build failed (picks were still saved)')
sys.exit(rc)
