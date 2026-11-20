"""Build this week's NFL DFS file for the Bet Tracker: projections, estimated ownership and ranked lineup pools
(cash + tournament) for the main Classic slate and every Showdown. Writes dfs_nfl.json.
Usage: python3 build_nfl.py [--season 2026] [--week N] [--out dfs_nfl.json] [--pool FILE.json (offline test pool)]"""
import os, sys, re, json, argparse, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nfl_data as d, nfl_model as m, ownership, optimizer as opt

def norm(s):
    s = re.sub(r"[.'’\-]", '', str(s).lower()); s = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def upcoming_week(G, season):
    g = G[(G.season == season) & G.pts.isna()]
    return int(g.week.min()) if len(g) else None

_MODEL = {}

def project(pool, season, week):
    """pool: DK players (name, pos, team, opp, salary...). Returns pool with proj/floor/ceil."""
    S = list(range(2019, season + 1))
    if not _MODEL: d.fetch(S, force_current=season)
    hist, _ = d.load(S)
    ids = hist[hist.position.isin(d.POS)].sort_values(['season', 'week']).groupby(['player_id']).tail(1)
    ids['key'] = ids.player_display_name.map(norm)
    off = pool[pool.pos.isin(d.POS)].copy(); off['key'] = off.name.map(norm)
    # match on name+team first, then name alone (traded players)
    a = off.merge(ids[['key', 'team', 'player_id', 'position']], on=['key', 'team'], how='left')
    miss = a.player_id.isna()
    if miss.any():
        b = off[miss.values].merge(ids.drop_duplicates('key', keep='last')[['key', 'player_id', 'position']], on='key', how='left')
        a.loc[miss, ['player_id', 'position']] = b[['player_id', 'position']].values
    a['position'] = a.pos
    ex = a[a.player_id.notna()][['player_id', 'name', 'position', 'team', 'opp']].rename(columns={'name': 'player_display_name', 'opp': 'opponent_team'})
    ex = ex.assign(season=season, week=week, season_type='REG')
    P = d.features(S, extra=ex)
    teams = pool[pool.pos.isin(['DST', 'K'])].drop_duplicates('team')[['team', 'opp']].rename(columns={'opp': 'opponent_team'}).assign(season=season, week=week, season_type='REG')
    T = d.team_rows(S, extra=teams)
    if 'M' not in _MODEL: _MODEL['M'] = m.train(P[~P.upcoming], T[~T.upcoming], S)   # train once, reuse for every slate
    M = _MODEL['M']
    TU = T[T.upcoming & T.ew_dst.notna()]
    R, TT = m.predict(M, P[P.upcoming], TU) if len(TU) else (m.predict(M, P[P.upcoming]), TU)
    R = R.drop_duplicates('player_id')
    a = a.merge(R[['player_id', 'proj', 'floor', 'ceil', 'implied', 'ew_tgt_sh', 'ew_car_sh', 'dvp', 'n_prior']], on='player_id', how='left')
    dst = pool[pool.pos == 'DST'].merge(TT[['team', 'proj', 'floor', 'ceil', 'implied']], on='team', how='left')
    k = pool[pool.pos == 'K'].merge(T[T.upcoming][['team', 'ew_k', 'implied']], on='team', how='left')
    k['proj'] = 0.5 * k.ew_k.fillna(7.5) + 0.5 * 0.34 * k.implied.fillna(22); k['floor'] = 0.45 * k.proj; k['ceil'] = 1.6 * k.proj
    out = pd.concat([a.drop(columns=['key', 'position']), dst, k.drop(columns=['ew_k'])], ignore_index=True)
    # rookies / no history: price-implied projection at 80% of the position's going rate
    rate = (out.proj / out.salary).groupby(out.pos).median()
    nohist = out.proj.isna()
    out.loc[nohist, 'proj'] = 0.8 * out.salary[nohist] * out.pos[nohist].map(rate).fillna(0.002)
    out.loc[nohist, 'floor'] = 0.3 * out.proj[nohist]; out.loc[nohist, 'ceil'] = 2.0 * out.proj[nohist]
    out['no_history'] = nohist
    # one starting QB per team: the QB who threw the most passes in his team's latest game (next in line if he is out)
    last = hist[hist.position == 'QB'].sort_values(['season', 'week']).groupby('team').tail(40)
    lastwk = last.groupby('team')[['season', 'week']].max().reset_index().merge(last, on=['team', 'season', 'week'])
    qbs = out[out.pos == 'QB'].copy()
    att = lastwk.set_index('player_id').attempts.to_dict()
    qbs['recent_att'] = qbs.player_id.map(att).fillna(0)
    qbs['out'] = qbs.status.isin(['O', 'OUT', 'IR', 'D'])
    for t, g in qbs.groupby('team'):
        g = g[~g.out].sort_values(['recent_att', 'salary'], ascending=False)
        for i in g.index[1:]:
            out.loc[i, ['proj', 'floor', 'ceil']] = [0.1 * out.proj[i], 0, 0.6 * out.proj[i]]; out.loc[i, 'backup'] = True
    # lean on DraftKings' prices a little: salary carries news the stats don't (role changes, injuries, demotions).
    # 75% model + 25% the salary's going rate; a near-minimum-priced player can't project above 2x his price's rate.
    rate = (out.proj / out.salary).groupby(out.pos).transform('median')
    implied = out.salary * rate
    hist = ~out.no_history
    out.loc[hist, 'proj'] = 0.75 * out.proj[hist] + 0.25 * implied[hist]
    near_min = out.salary <= out.groupby('pos').salary.transform('min') * 1.25
    out['proj'] = out.proj.where(~near_min, out.proj.clip(upper=2 * implied))
    # injury status from DraftKings
    out.loc[out.status.isin(['O', 'OUT', 'IR', 'D']), ['proj', 'floor', 'ceil']] = 0
    q = out.status.isin(['Q'])
    out.loc[q, ['proj', 'floor', 'ceil']] = out.loc[q, ['proj', 'floor', 'ceil']] * [0.85, 0.5, 0.95]
    out['floor'] = out[['floor', 'proj']].min(axis=1); out['ceil'] = out[['ceil', 'proj']].max(axis=1)
    return out

def describe_classic(df, L, mode):
    x = df.loc[L]; qb = x[x.pos == 'QB'].iloc[0]
    mates = x[(x.team == qb.team) & x.pos.isin(['WR', 'TE'])]; back = x[(x.team == qb.opp) & (x.pos != 'DST')]
    s = []
    if mode == 'gpp':
        s.append(f"{qb['name']} stacked with {', '.join(mates.name)}" + (f", brought back with {', '.join(back.name)}" if len(back) else '') + '.')
        lev = x[x.own < 8].sort_values('ceil', ascending=False).head(2)
        if len(lev): s.append(f"Low-owned upside: {', '.join(f'{r.name} (~{r.own:.0f}%)' for r in lev.itertuples())}.")
    else:
        top = x.sort_values('floor', ascending=False).head(3)
        s.append(f"Built for a safe score: highest floors are {', '.join(top.name)}.")
    val = x.assign(v=x.proj / (x.salary / 1000)).sort_values('v', ascending=False).iloc[0]
    s.append(f"Best value: {val['name']} at ${val.salary:,} ({val.proj:.1f} pts).")
    return ' '.join(s)

def pack_classic(df, lineups, mode):
    res = []
    for L in lineups:
        x = df.loc[L]
        order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'DST': 5}
        x = x.assign(o=x.pos.map(order)).sort_values(['o', 'salary'], ascending=[True, False])
        res.append(dict(players=[int(i) for i in x.index], salary=int(x.salary.sum()), proj=round(float(x.proj.sum()), 1), floor=round(float(x.floor.sum()), 1),
                        ceil=round(float(x.ceil.sum()), 1), own=round(float(x.own.sum()), 1), note=describe_classic(df, L, mode)))
    return res

def pack_showdown(df, lineups, mode):
    res = []
    for L in lineups:
        c = df.loc[L['cpt']]; f = df.loc[L['flex']].sort_values('salary', ascending=False)
        proj = 1.5 * c.proj + f.proj.sum(); sal = c.cpt_salary + f.salary.sum()
        note = f"Captain {c['name']} ({c.pos}, {c.team})."
        if mode == 'gpp': note += f" Captain owned ~{c.cpt_own:.0f}%; lineup total ownership {f.own.sum() + c.cpt_own:.0f}%."
        res.append(dict(cpt=int(L['cpt']), flex=[int(i) for i in f.index], salary=int(sal), proj=round(float(proj), 1),
                        ceil=round(float(1.5 * c.ceil + f.ceil.sum()), 1), floor=round(float(1.5 * c.floor + f.floor.sum()), 1), note=note))
    return res

def players_json(df):
    cols = ['name', 'pos', 'team', 'opp', 'salary', 'cpt_salary', 'status', 'proj', 'floor', 'ceil', 'own', 'cpt_own', 'implied', 'no_history', 'draftable_id', 'cpt_draftable_id']
    x = df.reindex(columns=cols).copy()
    for c in ('proj', 'floor', 'ceil', 'own', 'cpt_own', 'implied'): x[c] = x[c].astype(float).round(1)
    return json.loads(x.to_json(orient='records'))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--season', type=int); ap.add_argument('--week', type=int)
    ap.add_argument('--out', default='dfs_nfl.json'); ap.add_argument('--pool'); ap.add_argument('--salaries', help='folder of DraftKings draftables JSON saved by the phone'); ap.add_argument('--todo', help='dfs_todo.json'); ap.add_argument('--gpp', type=int, default=150); ap.add_argument('--cash', type=int, default=20)
    A = ap.parse_args()
    today = dt.date.today(); season = A.season or (today.year if today.month >= 8 else today.year - 1)
    d.fetch([season], force_current=season); G = d.games(); week = A.week or upcoming_week(G, season)
    slates = []
    if A.pool:      # offline test
        for s in json.load(open(A.pool)): slates.append(s)
    elif A.salaries:   # salaries the phone fetched from DraftKings and the relay saved to the data branch
        import dk_slate
        todo = json.load(open(A.todo)) if A.todo and os.path.exists(A.todo) else dict(slates=[])
        for s in todo['slates']:
            f = os.path.join(A.salaries, f"{s['sport']}_{s['dg']}.json")
            if s['sport'] != 'NFL' or not os.path.exists(f): continue
            try: slates.append(dict(s, players=dk_slate.parse(json.load(open(f)))))
            except Exception as e: print('could not read', f, e)
    else:
        import dk_slate
        main_g, sds = dk_slate.pick_slates(dk_slate.draft_groups('NFL'))
        if main_g: slates.append(dict(kind='classic', dg=main_g['dg'], label=f"Main slate {main_g.get('label', '')}", players=dk_slate.players(main_g['dg'])))
        for g in sds: slates.append(dict(kind='showdown', dg=g['dg'], label=g.get('label', ''), players=dk_slate.players(g['dg'])))
    out = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(), season=season, week=week, weights=opt.W, slates=[])
    for s in slates:
      try:
        pool = pd.DataFrame(s['players'])
        df = project(pool, season, week)
        df = df[df.proj > 0].reset_index(drop=True)
        if s['kind'] == 'classic':
            df = ownership.classic(df)
            df_c, cash = opt.classic(df, 'cash', n=A.cash, min_diff=1)
            df_g, gpp = opt.classic(df, 'gpp', n=A.gpp, min_diff=3, seed=7)
            pc, pg = pack_classic(df, cash, 'cash'), pack_classic(df, gpp, 'gpp')
        else:
            df = ownership.showdown(df)
            if 'K' not in set(df.pos): pass
            df_c, cash = opt.showdown(df, 'cash', n=A.cash, min_diff=1)
            df_g, gpp = opt.showdown(df, 'gpp', n=min(A.gpp, 80), min_diff=2, seed=7)
            pc, pg = pack_showdown(df, cash, 'cash'), pack_showdown(df, gpp, 'gpp')
        game = sorted(set(df.team))
        out['slates'].append(dict(kind=s['kind'], dg=s.get('dg'), label=s.get('label') or ' vs '.join(game), players=players_json(df), cash=pc, gpp=pg))
        print(f"{s['kind']} {s.get('label','')}: {len(df)} players, {len(pc)} cash + {len(pg)} tournament lineups")
      except Exception:
        import traceback; traceback.print_exc(); print('skipped', s.get('label'))
    if not out['slates']: sys.exit('no slates built')
    json.dump(out, open(A.out, 'w'), separators=(',', ':'))
    print('wrote', A.out)

if __name__ == '__main__':
    main()
