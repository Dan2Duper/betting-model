"""Is the park FORECAST worth using before the game-day weather is posted? Out-of-sample check on a Stats-API season (2026):
every game is predicted three ways - neutral weather (72F, calm: what the build used before), Open-Meteo's archived FORECAST for the
park at first pitch (wind turned into out / in / across with the field's bearing; retractable roofs closed by rule), and the weather
MLB posted at game time (the best case) - and the forecast itself is compared with the posted numbers.
Runs on GitHub Actions (needs the Stats API cache on the data branch and Open-Meteo):
  python3 mlb/research/wx_check.py --cache DATA_BRANCH/mlb [--season 2026]   -> prints + mlb/research/wx_check_<season>.json (when --out)"""
import os, sys, json, argparse, time, math, datetime as dt
import numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); MLB = os.path.dirname(HERE); sys.path.insert(0, MLB)
from features import build, FEATS
import inputs
ap = argparse.ArgumentParser(); ap.add_argument('--cache', required=True); ap.add_argument('--season', type=int, default=2026); ap.add_argument('--out'); A = ap.parse_args()
H = {n: pd.read_parquet(f'{MLB}/hist/{n}.parquet') for n in ('games', 'pit', 'bat', 'hands')}
L = {n: pd.read_parquet(f'{A.cache}/live_{n}.parquet') for n in ('games', 'pit', 'bat')}
X = json.load(open(f'{A.cache}/xwalk.json')); M = json.load(open(f'{MLB}/model.json'))
is_am = lambda i: str(i)[:1] == 'm' and str(i)[1:].isdigit(); rp = lambda i: X['players'].get(str(i)[1:], i) if is_am(i) else i
LG = L['games'][L['games'].season == A.season].copy()
LG['park'] = [X['venues'].get(str(p)[1:], p) if str(p).startswith('v') else p for p in LG.park]; LG['ump'] = [X['umps'].get(str(u)[1:], u) if is_am(u) else u for u in LG.ump]
for c in ('asp', 'hsp'): LG[c] = LG[c].map(rp)
for c in ('a_lineup', 'h_lineup'): LG[c] = [','.join(rp(i) for i in str(v).split(',')) for v in LG[c]]
for n in ('pit', 'bat'): L[n] = L[n].assign(pid=L[n].pid.map(rp))
# ---- archived forecasts, one call per home park ----
P = inputs.parks({}); from live import TEAM
teams = {TEAM[t['id']]: t['venue']['id'] for t in requests.get(inputs.API + '/teams?sportId=1&hydrate=venue', timeout=30).json().get('teams', []) if t['id'] in TEAM}
d0, d1 = str(pd.to_datetime(LG.date).min().date()), str((pd.to_datetime(LG.date).max() + pd.Timedelta(days=1)).date())
FC = {}
for home in sorted(LG.home.unique()):
    p = inputs.park_of(P, teams.get(home), home)
    if p[0] is None or p[3] == 'D': FC[home] = (p, None); continue
    url = (f'https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={p[0]:.4f}&longitude={p[1]:.4f}&start_date={d0}&end_date={d1}'
           '&hourly=temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_direction_10m&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=GMT')
    try: j = inputs._get(url, tries=3, timeout=60); h = j['hourly']; FC[home] = (p, pd.DataFrame(h).set_index('time'))
    except Exception as e: print('forecast archive failed for', home, e); FC[home] = (p, None)
    time.sleep(0.4)
print('parks with archived forecasts:', sum(1 for v in FC.values() if v[1] is not None), 'of', len(FC))
rows = []
for r in LG.itertuples():
    p, T = FC.get(r.home, (None, None)); f = dict(f_temp=np.nan, f_ws=np.nan, f_dir='unknown', f_sky='', f_ok=False)
    if p is not None and p[3] == 'D': f = dict(f_temp=72.0, f_ws=0.0, f_dir='unknown', f_sky='dome', f_ok=True)
    elif T is not None and isinstance(r.start_utc, str):
        t0 = dt.datetime.fromisoformat(r.start_utc.replace('Z', '+00:00')).replace(minute=0, second=0, microsecond=0); ks = [(t0 + dt.timedelta(hours=k)).strftime('%Y-%m-%dT%H:00') for k in range(3)]
        w = T.reindex(ks).dropna(subset=['temperature_2m', 'wind_speed_10m', 'wind_direction_10m'])
        if len(w):
            temp = float(w.temperature_2m.mean()); ws = float(w.wind_speed_10m.mean()); rain = float(np.nanmax(w.precipitation_probability.fillna(0).values)) if 'precipitation_probability' in w else 0.0
            wf = math.degrees(math.atan2(np.sin(np.radians(w.wind_direction_10m)).sum(), np.cos(np.radians(w.wind_direction_10m)).sum())) % 360
            closed = p[3] == 'R' and (rain >= 40 or temp < 60 or (r.home in inputs.HOT_ROOF and temp >= 85))
            f = dict(f_temp=72.0, f_ws=0.0, f_dir='unknown', f_sky='dome', f_ok=True) if closed else dict(f_temp=temp, f_ws=ws, f_dir=inputs.field_wind(wf, p[2]) if p[2] is not None else 'unknown', f_sky='', f_ok=True)
    rows.append(f)
LG = pd.concat([LG.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
ok = LG[LG.f_ok]; out = dict(season=A.season, games=int(len(LG)), with_forecast=int(len(ok)))
# ---- forecast vs what MLB posted ----
od = ok[(ok.sky != 'dome') & (ok.f_sky != 'dome') & ok.temp.notna() & ok.windspeed.notna()]
cls = lambda s: s.map(lambda d: 'out' if str(d).startswith('to') else 'in' if str(d).startswith('from') else 'cross' if d in ('ltor', 'rtol') else 'none')
windy = od[(od.windspeed >= 8) & (od.winddir != 'unknown') & (od.f_dir != 'unknown')]
out['vs_posted'] = dict(outdoor_games=int(len(od)), temp_mae=round(float((od.f_temp - od.temp).abs().mean()), 2), temp_corr=round(float(np.corrcoef(od.f_temp, od.temp)[0, 1]), 3),
                        wind_mae=round(float((od.f_ws - od.windspeed).abs().mean()), 2), wind_corr=round(float(np.corrcoef(od.f_ws, od.windspeed)[0, 1]), 3), wind_mean_forecast=round(float(od.f_ws.mean()), 2), wind_mean_posted=round(float(od.windspeed.mean()), 2),
                        windy_games=int(len(windy)), same_direction_code=round(float((windy.f_dir == windy.winddir).mean()), 3), same_out_in_cross=round(float((cls(windy.f_dir) == cls(windy.winddir)).mean()), 3),
                        opposite_out_vs_in=round(float(((cls(windy.f_dir) == 'out') & (cls(windy.winddir) == 'in') | (cls(windy.f_dir) == 'in') & (cls(windy.winddir) == 'out')).mean()), 3))
rt = ok[ok.home.map(lambda t: (FC.get(t, (None,))[0] or [0, 0, 0, 'O'])[3] == 'R')]
out['roof'] = dict(retractable_games=int(len(rt)), posted_closed=round(float((rt.sky == 'dome').mean()), 3), rule_closed=round(float((rt.f_sky == 'dome').mean()), 3), rule_right=round(float(((rt.sky == 'dome') == (rt.f_sky == 'dome')).mean()), 3))
print('FORECAST VS POSTED', json.dumps(out['vs_posted'])); print('ROOF RULE', json.dumps(out['roof']))
# what each retractable roof actually did, by the outdoor forecast (raw forecast temperature / rain, before any roof rule)
raw = []
for r in LG.itertuples():
    p, T = FC.get(r.home, (None, None))
    if p is None or p[3] != 'R' or T is None or not isinstance(r.start_utc, str): continue
    t0 = dt.datetime.fromisoformat(r.start_utc.replace('Z', '+00:00')).replace(minute=0, second=0, microsecond=0); w = T.reindex([(t0 + dt.timedelta(hours=k)).strftime('%Y-%m-%dT%H:00') for k in range(3)]).dropna(subset=['temperature_2m'])
    if len(w): raw.append(dict(home=r.home, temp=float(w.temperature_2m.mean()), rain=float(np.nanmax(w.precipitation_probability.fillna(0).values)), closed=int(r.sky == 'dome')))
RW = pd.DataFrame(raw)
if len(RW):
    RW['band'] = pd.cut(RW.temp, [0, 55, 60, 65, 70, 75, 80, 85, 90, 130]).astype(str); RW['wet'] = np.where(RW.rain >= 40, 'rain40+', 'dry')
    tab = RW.groupby(['home', 'wet', 'band']).closed.agg(['mean', 'size']).round(2); print('ROOF TABLE (share closed, games)'); print(tab.to_string())
    out['roof_table'] = {f'{h}|{w}|{b}': [float(m), int(n)] for (h, w, b), (m, n) in zip(tab.index, tab.values)}
# ---- the model three ways ----
GC = list(H['games'].columns); res = {}
def run(name, G1):
    G = pd.concat([H['games'], G1.reindex(columns=GC)], ignore_index=True); G['date'] = pd.to_datetime(G.date); G['num'] = G.num.astype(int); G['season'] = G.date.dt.year
    for c in ('ump', 'asp', 'hsp', 'sky', 'winddir', 'usedh', 'a_lineup', 'h_lineup', 'a_pos', 'h_pos'): G[c] = G[c].fillna('')
    pit = pd.concat([H['pit'], L['pit']], ignore_index=True); bat = pd.concat([H['bat'], L['bat']], ignore_index=True); pit['date'] = pd.to_datetime(pit.date); bat['date'] = pd.to_datetime(bat.date)
    F = build(G, pit, bat, H['hands']); F = F[F.season == A.season]
    F['lam'] = np.exp(M['b0'] + ((F[FEATS].values - np.array(M['mu'])) / np.array(M['sd'])) @ np.array(M['beta']))
    h = F[F.is_home == 1].set_index('gid'); a = F[F.is_home == 0].set_index('gid'); R = pd.DataFrame(dict(lh=h.lam, la=a.lam, hr=h.runs, ar=a.runs)).dropna()
    R = R[R.index.isin(ok.gid)]; p = 1 / (1 + np.exp(-(M['win']['a'] + M['win']['k'] * np.log(R.lh / R.la)))); y = (R.hr > R.ar).astype(int)
    dev = lambda y_, m: 2 * (np.where(y_ > 0, y_ * np.log(np.maximum(y_, 1e-9) / m), 0) - (y_ - m))
    res[name] = dict(games=int(len(R)), total_mae=round(float((R.lh + R.la - R.hr - R.ar).abs().mean()), 4), deviance=round(float((dev(R.hr.values, R.lh.values).mean() + dev(R.ar.values, R.la.values).mean()) / 2), 5),
                     winner_logloss=round(float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()), 5), winner_pct=round(float(((p > 0.5) == (y == 1)).mean()), 4), mean_total=round(float((R.lh + R.la).mean()), 3), actual_total=round(float((R.hr + R.ar).mean()), 3))
    print(name, json.dumps(res[name])); return R
base = LG.copy(); base['date'] = pd.to_datetime(base.date).dt.strftime('%Y-%m-%d')
n = base.copy(); n['temp'] = 72.0; n['windspeed'] = 0.0; n['winddir'] = 'unknown'; n['sky'] = ''
f = base.copy(); m_ = f.f_ok; f.loc[m_, 'temp'] = f.f_temp[m_]; f.loc[m_, 'windspeed'] = f.f_ws[m_]; f.loc[m_, 'winddir'] = f.f_dir[m_]; f.loc[m_, 'sky'] = f.f_sky[m_]
Rn = run('neutral (72F, calm)', n); Rf = run('park forecast', f); Rp = run('posted at game time', base)
# outdoor windy / cold / hot games are where it should matter
sub = ok[(ok.sky != 'dome') & ((ok.windspeed >= 10) | (ok.temp <= 55) | (ok.temp >= 88))].gid
for nm, R in (('neutral', Rn), ('forecast', Rf), ('posted', Rp)):
    r = R[R.index.isin(sub)]; res[nm + ' | windy, cold or hot games'] = dict(games=int(len(r)), total_mae=round(float((r.lh + r.la - r.hr - r.ar).abs().mean()), 4), bias=round(float((r.lh + r.la - r.hr - r.ar).mean()), 3)); print(nm, 'extreme-weather games', json.dumps(res[nm + ' | windy, cold or hot games']))
# Deviance is the test: it is the run model's own (proper) score. Total MAE is reported but not decisive - a model that runs low looks
# good on MAE because run totals are skewed (neutral weather under-predicts by ~0.4 runs a game).
bias = lambda k: round(res[k]['mean_total'] - res[k]['actual_total'], 3)
out['model'] = res; out['bias'] = {k: bias(k) for k in ('neutral (72F, calm)', 'park forecast', 'posted at game time')}
gain = res['neutral (72F, calm)']['deviance'] - res['park forecast']['deviance']; best = res['neutral (72F, calm)']['deviance'] - res['posted at game time']['deviance']
out['share_of_posted_gain'] = round(gain / best, 3) if best > 0 else None
out['verdict'] = 'use the forecast' if gain > 0 else 'forecast does not beat neutral weather: keep neutral until the game-day weather is posted'
print('VERDICT:', out['verdict']); print('WXJSON', json.dumps(out))
if A.out: json.dump(out, open(A.out, 'w'), indent=1)
