"""Build today's MLB DFS file for the Bet Tracker (dfs_mlb.json, same shape as dfs_nfl.json): projections, estimated ownership and
ranked lineup pools (cash + tournament) for the main Classic slate and every Showdown the phone has sent salaries for.

Projections come from the daily MLB job (mlb/live.py -> <cache>/dfs_proj.json): every starter from the POSTED lineups and probable
pitchers when the Stats API has them (that is what overrides DraftKings' stale tags: a scratched hitter simply is not in the lineup),
else the last game's lineup, discounted by how often that spot's hitter really starts again (2021-25: 87-90% for spots 1-4 down to
49% for the 9th). The self-tuning layer (dfs/mlb_tune.py) then adds its adjustments, grades yesterday's and nudges its weights.
Usage: python3 dfs/build_mlb.py --cache DATA/mlb --salaries DATA/salaries --todo DATA/dfs_todo.json --out DATA/dfs_mlb.json [--mlb DATA/mlb.json]
       --fake-salaries   no DraftKings file needed: prices are made up from the projections (tests and the spring dry run)"""
import os, sys, re, json, argparse, unicodedata, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mlb_opt, mlb_own, mlb_tune as tune, ownership, optimizer as opt
DK_TEAM = {'LAA': 'ANA', 'CHC': 'CHN', 'CWS': 'CHA', 'CHW': 'CHA', 'KC': 'KCA', 'KCR': 'KCA', 'LAD': 'LAN', 'NYM': 'NYN', 'NYY': 'NYA', 'SD': 'SDN', 'SDP': 'SDN', 'SF': 'SFN', 'SFG': 'SFN', 'STL': 'SLN', 'TB': 'TBA', 'TBR': 'TBA',
           'WSH': 'WAS', 'WAS': 'WAS', 'OAK': 'ATH', 'AZ': 'ARI'}
SHOW = {'ANA': 'LAA', 'CHN': 'CHC', 'CHA': 'CWS', 'KCA': 'KC', 'LAN': 'LAD', 'NYN': 'NYM', 'NYA': 'NYY', 'SDN': 'SD', 'SFN': 'SF', 'SLN': 'STL', 'TBA': 'TB', 'WAS': 'WSH'}
START_AGAIN = {1: 0.867, 2: 0.882, 3: 0.895, 4: 0.856, 5: 0.775, 6: 0.72, 7: 0.646, 8: 0.549, 9: 0.486}   # share of last game's starters, by batting spot, in the next lineup (2021-25)
OUT_TAGS = ('O', 'OUT', 'IL', 'IR', 'D', 'NA', 'SUSP')

def norm(s):
    s = unicodedata.normalize('NFD', str(s)); s = ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()
    s = re.sub(r"[.'’\-]", '', s); s = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def fake_pool(R, kind='classic', game=None, seed=3):
    """Made-up DraftKings prices from the projections (a price is roughly linear in the projection, with noise), so the whole
    build can be exercised without a salary file."""
    rng = np.random.default_rng(seed); x = R if game is None else R[R.gid == game]; rows = []
    for r in x.itertuples():
        p = r.base * (1 + rng.normal(0, 0.12))
        sal = (4500 + 330 * p) if r.role == 'P' else (2000 + 280 * p)
        sal = int(np.clip(round(sal / 100) * 100, 4000 if r.role == 'P' else 2000, 11500 if r.role == 'P' else 6500))
        pos = 'SP' if r.role == 'P' else ('OF' if r.dkpos == 'DH' else r.dkpos)
        if r.role == 'H' and rng.random() < 0.15: pos = {'2B': '2B/SS', 'SS': '2B/SS', '1B': '1B/OF', '3B': '1B/3B', 'OF': '1B/OF', 'C': 'C/1B'}.get(pos, pos)
        d = dict(dk_id=len(rows), name=r.name or r.pid, pos=pos, team=SHOW.get(r.team, r.team), opp=SHOW.get(r.opp, r.opp), game=f'{r.gid}', start=r.start, status='None', salary=sal, cpt_salary=None, draftable_id=None, cpt_draftable_id=None)
        if kind == 'showdown': d['cpt_salary'] = int(round(sal * 1.5 / 100) * 100)
        rows.append(d)
    return rows

def project(pool, R, W, state=None, day=None):
    """pool: DraftKings players (dk_slate.parse). R: today's projection rows (dfs_proj.json). Returns the pool with proj/floor/ceil,
    batting spot, tuning adjustment and notes; players with no row (bench, relievers, not starting) project 0."""
    P = pd.DataFrame(pool); P['team_r'] = P.team.map(lambda t: DK_TEAM.get(t, t)); P['key'] = P.name.map(norm); P['is_p'] = P.pos.map(lambda p: mlb_opt.elig(p) == ['P'])
    R = R.copy(); R['key'] = R.name.map(norm); R['is_p'] = R.role == 'P'
    R2 = R.rename(columns={'team': 'team_raw', 'opp': 'opp_raw', 'name': 'name_r', 'start': 'start_r'})
    a = P.merge(R2, left_on=['key', 'team_r', 'is_p'], right_on=['key', 'team_raw', 'is_p'], how='left')
    miss = a.base.isna().values
    if miss.any():   # traded this week (DraftKings and the box scores disagree on his team): match on the name alone when it is unique
        u = R2.drop_duplicates(['key', 'is_p'], keep=False); b = P[miss].merge(u, on=['key', 'is_p'], how='left')
        for c in [c for c in R2.columns if c not in ('key', 'is_p')]: a.loc[miss, c] = b[c].values
    a['matched'] = a.base.notna(); a['proj'] = a.base.fillna(0.0); a['floor'] = a['floor'].fillna(0.0); a['ceil'] = a['ceil'].fillna(0.0)
    for v in sorted({x for V in tune.VARS.values() for x in V}): a[v] = a[v].fillna(0.0) if v in a else 0.0
    a['role'] = np.where(a.is_p, 'P', 'H'); a['slot'] = a.slot.fillna(0).astype(int); a['adj'] = 0.0; a['why'] = ''; a['conf'] = a.lineup.fillna('') == 'posted'
    # self-tuning adjustments on the base projection
    m = a.matched.values
    if state is not None and m.any():
        adj, why = tune.adjust(a[m], state['w']); adj = np.clip(adj, -0.3 * a.proj[m].values, 0.3 * a.proj[m].values + 0.75)
        if day: tune.remember(state, day, [dict(pid=('P:' if r.role == 'P' else '') + r.pid, role=r.role, base=round(float(r.proj), 2), **{v: round(float(getattr(r, v)), 4) for v in tune.VARS[r.role]}) for r in a[m].itertuples()])
        a.loc[m, 'adj'] = np.round(adj, 2); a.loc[m, 'why'] = why
        a.loc[m, 'proj'] = a.proj[m] + adj; a.loc[m, 'ceil'] = a.ceil[m] + adj; a.loc[m & a.is_p.values, 'floor'] = (a.floor[m & a.is_p.values] + 0.5 * a.adj[m & a.is_p.values]).clip(lower=0)
    # lineup not posted yet: the hitter is only there with the odds his spot usually is; a pitcher who is not the listed probable has no row at all
    unc = m & ~a.conf.values & ~a.is_p.values
    if unc.any():
        k = a.slot[unc].map(START_AGAIN).fillna(0.6).values; a.loc[unc, 'proj'] = a.proj[unc] * k; a.loc[unc, 'floor'] = 0.0
        a.loc[unc, 'why'] = [(w + '; ' if w else '') + f'lineup not posted ({int(100 * q)}% to start)' for w, q in zip(a.why[unc], k)]
    # DraftKings' own tag only matters when the Stats API has not confirmed him in today's lineup
    tag = a.status.fillna('None').astype(str).str.upper().isin(OUT_TAGS) & ~a.conf
    a.loc[tag, ['proj', 'floor', 'ceil']] = 0.0
    a['floor'] = a[['floor', 'proj']].min(axis=1); a['ceil'] = a[['ceil', 'proj']].max(axis=1)
    a['rain_risk'] = (a.rain.fillna(0) >= 50) & ~a.dome.fillna(False).astype(bool)
    return a

def describe(df, L, mode):
    x = df.loc[L['players']]; h = x[~x.is_p]; pt = x[x.is_p]; s = []
    top = h.team.value_counts()
    if mode == 'gpp' and len(top) and top.iloc[0] >= 4:
        t = top.index[0]; st = h[h.team == t].sort_values('slot'); s.append(f"{t} stack ({', '.join(f'{r.name} #{r.slot}' for r in st.itertuples())}) against {st.opp.iloc[0]}; team priced for {st.team_runs.iloc[0]:.1f} runs.")
        lev = x[x.own < 6].sort_values('ceil', ascending=False).head(2)
        if len(lev): s.append(f"Low-owned upside: {', '.join(f'{r.name} (~{r.own:.0f}%)' for r in lev.itertuples())}.")
    else: s.append(f"Built for a safe score around {' and '.join(pt.name)} on the mound" + (f"; most bats from {top.index[0]} ({top.iloc[0]})." if len(top) else '.'))
    unc = x[~x.conf & ~x.is_p]
    if len(unc): s.append(f"{len(unc)} hitter{'s' if len(unc) > 1 else ''} not confirmed in a posted lineup yet: {', '.join(unc.name)}.")
    wet = x[x.rain_risk]
    if len(wet): s.append(f"Rain risk: {', '.join(sorted(set(wet.team)))}.")
    return ' '.join(s)

def pack_classic(df, lineups, mode):
    out = []
    for L in lineups:
        x = df.loc[L['players']]
        out.append(dict(players=[int(i) for i in L['players']], slots=L['slots'], salary=int(x.salary.sum()), proj=round(float(x.proj.sum()), 1), floor=round(float(x.floor.sum()), 1), ceil=round(float(x.ceil.sum()), 1),
                        own=round(float(x.own.sum()), 1), note=describe(df, L, mode)))
    return out
def pack_showdown(df, lineups, mode):
    out = []
    for L in lineups:
        c = df.loc[L['cpt']]; f = df.loc[L['flex']].sort_values('salary', ascending=False)
        note = f"Captain {c['name']} ({c.pos}, {c.team})." + (f" Captain owned ~{c.cpt_own:.0f}%; lineup total ownership {f.own.sum() + c.cpt_own:.0f}%." if mode == 'gpp' else '')
        out.append(dict(cpt=int(L['cpt']), flex=[int(i) for i in f.index], salary=int(c.cpt_salary + f.salary.sum()), proj=round(float(1.5 * c.proj + f.proj.sum()), 1), ceil=round(float(1.5 * c.ceil + f.ceil.sum()), 1),
                        floor=round(float(1.5 * c.floor + f.floor.sum()), 1), note=note))
    return out
def players_json(df):
    cols = ['name', 'pos', 'team', 'opp', 'salary', 'cpt_salary', 'status', 'proj', 'floor', 'ceil', 'own', 'cpt_own', 'implied', 'slot', 'conf', 'adj', 'why', 'rain_risk', 'draftable_id', 'cpt_draftable_id', 'wind']
    x = df.assign(implied=df.team_runs, wind=np.where(df.wind_out.fillna(0) > 0, df.wind_out, -df.wind_in.fillna(0))).reindex(columns=cols).copy()
    for c in ('proj', 'floor', 'ceil', 'own', 'cpt_own', 'implied', 'adj', 'wind'): x[c] = x[c].astype(float).round(1)
    return json.loads(x.to_json(orient='records'))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cache', required=True); ap.add_argument('--salaries'); ap.add_argument('--todo'); ap.add_argument('--mlb'); ap.add_argument('--out', default='dfs_mlb.json'); ap.add_argument('--prev')
    ap.add_argument('--gpp', type=int, default=30); ap.add_argument('--cash', type=int, default=8); ap.add_argument('--fake-salaries', action='store_true'); A = ap.parse_args()
    pf = f'{A.cache}/dfs_proj.json'
    if not os.path.exists(pf): print('no projections file yet (the MLB build writes it):', pf); return
    J = json.load(open(pf)); day = J['day']; R = pd.DataFrame(J['rows'])
    # tuning state rides in the output file from build to build; grade the days that now have box scores
    prev = A.prev or A.out; ST = None
    try: ST = json.load(open(prev)).get('tune') if os.path.exists(prev) else None
    except Exception as e: print('could not read the tuning state:', e)
    if not ST or not isinstance(ST.get('pending'), dict): ST = tune.new_state()
    def actual_for(d):
        a = (J.get('actual') or {}).get(d)
        return pd.DataFrame([dict(pid=k, dk=v) for k, v in a.items()]) if a else None
    for e in tune.grade_all(ST, day, actual_for): print('graded', e['day'], {k: (v['mae_base'], v['mae_adj']) for k, v in e['role'].items()})
    if not len(R): print('no starters to project for', day);
    slates = []
    if len(R) and A.fake_salaries:
        slates.append(dict(sport='MLB', kind='classic', dg=0, label=f'Test slate {day} (made-up prices)', players=fake_pool(R)))
        g0 = R.gid.iloc[0]; slates.append(dict(sport='MLB', kind='showdown', dg=1, label=f'Test showdown {g0} (made-up prices)', players=fake_pool(R, 'showdown', g0)))
    elif len(R) and A.salaries:
        import dk_slate
        todo = json.load(open(A.todo)) if A.todo and os.path.exists(A.todo) else dict(slates=[])
        for s in todo['slates']:
            f = os.path.join(A.salaries, f"{s['sport']}_{s['dg']}.json")
            if s['sport'] != 'MLB' or not os.path.exists(f): continue
            try: slates.append(dict(s, players=dk_slate.parse(json.load(open(f)))))
            except Exception as e: print('could not read', f, e)
    out = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(), sport='MLB', day=day, weights=mlb_opt.W, slates=[],
               live=dict(statuses=['MLB Stats API lineups + probables'], lineups_posted=int((R.lineup == 'posted').sum()) if len(R) else 0, starters=int(len(R)), weights=ST['w'],
                         last_grade=(ST.get('log') or [None])[-1]))
    today_start = lambda p: str(p.get('start') or '')[:10]
    for s in slates:
        try:
            pool = [p for p in s['players']]
            df = project(pool, R, mlb_opt.W, ST, day)
            n_match = int(df.matched.sum()); df = df[df.proj > 0].reset_index(drop=True)
            if len(df) < 12 and s['kind'] == 'classic': print('skipped', s.get('label'), f'only {len(df)} projected players matched (salaries for another day?)'); continue
            if s['kind'] == 'classic':
                df = mlb_own.classic(df)
                dfc = df[~(df.rain_risk & (df.rain >= 70))].reset_index(drop=True) if (df.rain_risk & (df.rain >= 70)).any() else df      # cash: no one from a game likely to be rained out
                _, cash = mlb_opt.classic(dfc, 'cash', n=A.cash, min_diff=1)
                if dfc is not df:   # map back to the full table's rows
                    ix = {(r.name, r.team): i for i, r in zip(df.index, df.itertuples())}; cash = [dict(players=[ix[(dfc.name[i], dfc.team[i])] for i in L['players']], slots=L['slots']) for L in cash]
                _, gpp = mlb_opt.classic(df, 'gpp', n=A.gpp, min_diff=3, seed=7)
                df['is_p'] = df.pos.map(lambda p: mlb_opt.elig(p) == ['P'])
                bad = [b for L in cash + gpp for b in mlb_opt.check(df, L)]
                if bad: print('RULE CHECK FAILED', sorted(set(bad))[:5])
                pc, pg = pack_classic(df, cash, 'cash'), pack_classic(df, gpp, 'gpp')
            else:
                df['cpt_salary'] = df.cpt_salary.fillna(df.salary * 1.5); df = ownership.showdown(df)
                _, cash = opt.showdown(df, 'cash', n=A.cash, min_diff=1); _, gpp = opt.showdown(df, 'gpp', n=min(A.gpp, 40), min_diff=2, seed=7)
                pc, pg = pack_showdown(df, cash, 'cash'), pack_showdown(df, gpp, 'gpp')
            out['slates'].append(dict(sport='MLB', kind=s['kind'], dg=s.get('dg'), label=s.get('label') or '', players=players_json(df), cash=pc, gpp=pg))
            print(f"{s['kind']} {s.get('label', '')}: {len(pool)} DraftKings players, {n_match} matched to projected starters, {len(df)} in the pool, {len(pc)} cash + {len(pg)} tournament lineups")
        except Exception:
            import traceback; traceback.print_exc(); print('skipped', s.get('label'))
    out['tune'] = ST
    json.dump(out, open(A.out, 'w'), separators=(',', ':')); print('wrote', A.out, len(out['slates']), 'slates')

if __name__ == '__main__':
    main()
