"""MLB DFS projections for today's slate, written by the daily job next to its cache (dfs_proj.json) for dfs/build_mlb.py.
One row per starting hitter and starting pitcher: base projection / floor / ceiling (dfs/mlb_proj.py, trees cached per season),
the batting-order spot and whether that lineup is posted or carried over from the last game, and the self-tuning variables
(dfs/mlb_tune.py): DraftKings' implied runs vs the run model, the opposing bullpen's recent pitches, the plate umpire, the park for
the batter's side. Also the real DraftKings points of the last few days' starters, so yesterday's projections can be graded."""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); DFS = os.path.join(os.path.dirname(HERE), 'dfs')
def export(G, pit, bat, hands, F, day0, M, cache, games, names, out_path):
    if DFS not in sys.path: sys.path.insert(0, DFS)
    import mlb_proj, mlb_tune
    from features import day
    h, p = mlb_proj.project_day(G, pit, bat, hands, F, day0, M, model_cache=f'{cache}/dfs_models.joblib')
    gids = {g['id'] for g in games}; h = h[h.gid.isin(gids)]; p = p[p.gid.isin(gids)]
    if not len(h) and not len(p): json.dump(dict(day=day0, rows=[], actual={}), open(out_path, 'w')); return 0
    R = pd.concat([h.assign(role='H'), p.assign(role='P')], ignore_index=True)
    # the lines in hand (DraftKings via ESPN) as a tiny games table; games without a line simply get 0 for the Vegas variables
    L = {g['id']: (g.get('line') or {}) for g in games}; gm = {g['id']: g for g in games}
    Gl = pd.DataFrame([dict(gid=k, ml_h=v.get('mh'), ml_a=v.get('ma'), tot=v.get('total'), home=gm[k]['home'], ml_suspect=False, tot_suspect=False) for k, v in L.items()]).astype({'ml_h': float, 'ml_a': float, 'tot': float})
    # opposing bullpen: reliever pitches over the previous three days
    rel = pit[pit.P_GS == 0].assign(day=day(pit[pit.P_GS == 0].date)); rp = rel.groupby(['team', 'day']).P_PITCH.sum()
    Fs = F[F.gid.isin(gids)][['gid', 'team', 'opp', 'date', 'ump_k', 'ump_bb']].copy(); d_ = day(Fs.date)
    Fs['pen_pit3'] = sum(rp.reindex(pd.MultiIndex.from_arrays([Fs.opp, d_ - k])).fillna(0).values for k in (1, 2, 3))
    try: PH = mlb_tune.park_hand(bat, G, hands); PH = PH[PH.gid.isin(gids)]
    except Exception as e: print('park-by-hand table failed:', e); PH = None
    R = mlb_tune.add_vars(R, Gl, Fs, PH, M['win'])
    rows = []
    for r in R.itertuples():
        g = gm[r.gid]; isH = r.role == 'H'; side = 'home' if r.team == g['home'] else 'away'
        rows.append(dict(role=r.role, pid=r.pid, name=names.get(r.pid, ''), team=r.team, opp=r.opp, gid=r.gid, pk=g['pk'], date=g['date'], start=g['kick'],
                         slot=int(r.slot) if isH else 0, dkpos=(r.dkpos if isH else 'P'), bats=(r.bats if isH else ''), base=round(float(r.proj), 2), floor=round(float(r.floor), 2), ceil=round(float(r.ceil), 2),
                         lineup=g.get('lineup_' + side, '') if isH else ('posted' if g.get('sp_' + side + '_known') else ''), team_runs=round(float(r.lam if isH else r.lam_for), 2), opp_runs=round(float(r.opp_lam if isH else r.lam_against), 2),
                         dome=bool(g.get('dome')), rain=float((g.get('wx_more') or {}).get('rain') or 0), wind_out=g.get('wind_out', 0), wind_in=g.get('wind_in', 0), temp=g.get('temp'),
                         **{v: round(float(getattr(r, v, 0) or 0), 4) for v in sorted({x for V in mlb_tune.VARS.values() for x in V})}))
    # real DraftKings points of recent starters (for grading the projections remembered on those days)
    d0 = pd.Timestamp(day0); actual = {}
    b = bat[(bat.date >= d0 - pd.Timedelta(days=5)) & (bat.date < d0)]; q = pit[(pit.date >= d0 - pd.Timedelta(days=5)) & (pit.date < d0) & (pit.P_GS > 0)]
    if len(b):
        for (d, pid), v in b.assign(dk=mlb_proj.dk_bat(b)).groupby([b.date.dt.strftime('%Y-%m-%d'), 'pid']).dk.sum().items(): actual.setdefault(d, {})[pid] = round(float(v), 2)
    if len(q):
        for (d, pid), v in q.assign(dk=mlb_proj.dk_pit(q)).groupby([q.date.dt.strftime('%Y-%m-%d'), 'pid']).dk.sum().items(): actual.setdefault(d, {})['P:' + pid] = round(float(v), 2)
    nn = sum(1 for r in rows if not r['name'])
    if nn: print('DFS: starters with no name on file (cannot be matched to DraftKings):', nn, [r['pid'] for r in rows if not r['name']][:8])
    json.dump(dict(day=day0, built=pd.Timestamp.now(tz='UTC').isoformat(), rows=rows, actual=actual), open(out_path, 'w'), separators=(',', ':'))
    return len(rows)
