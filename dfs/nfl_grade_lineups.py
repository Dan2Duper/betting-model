"""Grades the lineups this site proposes (the "AI-LOX" of DFS), kept apart from Rich's own entries.

Every build snapshots each slate's top cash and tournament lineups until the slate locks (first kickoff of its games). Once the official
stats for that week are out (nflverse, usually the next morning), a later build scores them with DraftKings rules and compares them with
the best lineup anyone could have built from that slate's salaries in hindsight. Results live in dfs_nfl.json -> lineup_grades."""
import os, json, re, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd
import nfl_data as d
ET = ZoneInfo('America/New_York')
TOP = 3   # lineups kept per mode per slate

def slate_lock(df, GW):
    teams = set(df.team.dropna())
    g = GW[GW.team.isin(teams)]
    t = []
    for r in g.itertuples():
        try: t.append(dt.datetime.fromisoformat(f'{r.gameday}T{r.gametime}').replace(tzinfo=ET).astimezone(dt.timezone.utc))
        except Exception: pass
    return min(t) if t else None

def _who(row, cpt=False):
    pid = row.get('player_id')
    return dict(pid=pid if isinstance(pid, str) else None, team=row['team'], pos=row['pos'], name=row['name'], cpt=cpt)

def snapshot(df, pc, pg, kind):
    def one(L):
        if kind == 'classic': ps = [_who(df.loc[i]) for i in L['players']]
        else: ps = [_who(df.loc[L['cpt']], True)] + [_who(df.loc[i]) for i in L['flex']]
        return dict(players=ps, proj=L['proj'], salary=L['salary'])
    return dict(cash=[one(L) for L in pc[:TOP]], gpp=[one(L) for L in pg[:TOP]])

def remember(LOG, s, df, pc, pg, season, week, GW, now):
    lock = slate_lock(df, GW)
    if lock is not None and now >= lock: return            # already locked: keep the snapshot taken before kickoff
    LOG[str(s.get('dg'))] = dict(dg=s.get('dg'), label=s.get('label'), kind=s['kind'], season=season, week=week,
                                 lock=lock.isoformat() if lock else None, built=now.isoformat(), **snapshot(df, pc, pg, s['kind']))

_n = lambda s: re.sub(r'\s+', ' ', re.sub(r"[.'’\-]|\b(jr|sr|ii|iii|iv|v)\b", '', str(s).lower())).strip()

def _actuals(season, week):
    P, _ = d.load([season]); x = P[(P.week == week) & (P.season_type == 'REG')].copy()
    if not len(x): return None
    x['dk'] = d.dk_points(x)
    T = d.team_rows([season]); T = T[(T.season == season) & (T.week == week)]
    return dict(by_id=x.set_index('player_id').dk.to_dict(), by_name={(_n(r.player_display_name), r.team): r.dk for r in x.itertuples()},
                teams_done=set(x.team), dst=T.set_index('team').dst.to_dict(), k=T.set_index('team').k.to_dict())

def _pts(p, A):
    if p['pos'] == 'DST': v = A['dst'].get(p['team'])
    elif p['pos'] == 'K': v = A['k'].get(p['team'])
    else: v = A['by_id'].get(p['pid']) if p.get('pid') else None
    if v is None and p['pos'] not in ('DST', 'K'): v = A['by_name'].get((_n(p['name']), p['team']))
    if v is None or v != v: v = 0.0 if p['team'] in A['teams_done'] else None
    return None if v is None else float(v) * (1.5 if p.get('cpt') else 1.0)

def _optimal(e, A, salaries_dir):
    """Best possible lineup from the slate's salaries with the actual points (hindsight), or None."""
    import dk_slate, optimizer as opt
    f = os.path.join(salaries_dir or '', f"NFL_{e['dg']}.json")
    if not salaries_dir or not os.path.exists(f): return None
    pool = pd.DataFrame(dk_slate.parse(json.load(open(f))))
    pool['proj'] = [(_pts(dict(pid=None, team=r.team, pos=r.pos, name=r.name), A) or 0.0) for r in pool.itertuples()]
    pool = pool[pool.proj > 0].reset_index(drop=True)
    if not len(pool): return None
    pool['floor'] = pool['ceil'] = pool.proj; pool['own'] = 0.0; pool['cpt_own'] = 0.0
    if e['kind'] == 'classic':
        df, L = opt.classic(pool, 'cash', n=1)
        return round(float(df.loc[L[0]].proj.sum()), 2) if L else None
    df, L = opt.showdown(pool, 'cash', n=1)
    return round(float(1.5 * df.loc[L[0]['cpt']].proj + df.loc[L[0]['flex']].proj.sum()), 2) if L else None

def grade(LOG, GR, now, salaries_dir=None):
    """Grade every snapshot whose games are over and whose stats are out. Returns the new grade entries."""
    new, cache = [], {}
    for k, e in list(LOG.items()):
        lock = dt.datetime.fromisoformat(e['lock']) if e.get('lock') else None
        if lock and now < lock + dt.timedelta(hours=8): continue
        key = (e['season'], e['week'])
        if key not in cache:
            try: cache[key] = _actuals(*key)
            except Exception as ex: print('lineup grading: no stats yet', key, ex); cache[key] = None
        A = cache[key]
        teams = {p['team'] for m in ('cash', 'gpp') for L in e[m] for p in L['players']}
        if not A or not teams <= A['teams_done'] | set(A['dst']):
            if lock and now > lock + dt.timedelta(days=10): LOG.pop(k, None)
            continue
        g = dict(dg=e['dg'], label=e['label'], kind=e['kind'], season=e['season'], week=e['week'], lock=e.get('lock'))
        for m in ('cash', 'gpp'):
            g[m] = [dict(proj=L['proj'], actual=round(sum(_pts(p, A) or 0.0 for p in L['players']), 2),
                         players=[f"{'CPT ' if p.get('cpt') else ''}{p['name']} {(_pts(p, A) or 0):.1f}" for p in L['players']]) for L in e[m]]
        try: g['optimal'] = _optimal(e, A, salaries_dir)
        except Exception as ex: print('lineup grading: optimal failed', ex); g['optimal'] = None
        GR.append(g); new.append(g); LOG.pop(k, None)
    del GR[:-200]
    return new
