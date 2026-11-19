"""DraftKings lineup builder (integer programming with PuLP/CBC).
Cash: the highest typical score (median, nudged toward the floor). Tournament (GPP): the MIT 'picking winners' approach --
each lineup maximizes upside-weighted points minus an ownership penalty, under stacking rules, and must differ from every
earlier lineup by a set number of players, so the pool spreads its bets instead of repeating one idea."""
import math, numpy as np, pulp

W = dict(cash_floor=0.25, gpp_ceil=0.5, gpp_own=0.8, own_floor=1.0, noise=0.12)   # starting weights from published work; tuned by backtests

def _score(df, mode, rng, cpt=False):
    if mode == 'cash':
        s = (1 - W['cash_floor']) * df.proj + W['cash_floor'] * df.floor
    else:
        own = (df.cpt_own if cpt and 'cpt_own' in df else df.own).clip(lower=W['own_floor'])
        s = df.proj + W['gpp_ceil'] * (df.ceil - df.proj) - W['gpp_own'] * np.log(own / 10)
    if rng is not None:
        s = s + rng.normal(0, 1, len(df)) * W['noise'] * (df.ceil - df.floor).clip(lower=1) / 2
    return s.values

def _solve(prob):
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=20))
    return pulp.LpStatus[prob.status] == 'Optimal'

def classic(df, mode='gpp', n=1, seed=0, lock=(), ban=(), min_diff=None, salary_min=None):
    """df rows: name, pos (QB/RB/WR/TE/DST), team, opp, salary, proj, floor, ceil, own. Returns a list of lineups (row-index lists)."""
    df = df.reset_index(drop=True); rng = np.random.default_rng(seed); I = range(len(df))
    min_diff = min_diff or (1 if mode == 'cash' else 2)
    out = []
    for k in range(n):
        s = _score(df, mode, rng if k else None)
        p = pulp.LpProblem('dk', pulp.LpMaximize); x = [pulp.LpVariable(f'x{i}', cat='Binary') for i in I]
        p += pulp.lpSum(s[i] * x[i] for i in I)
        P = lambda pos: [i for i in I if df.pos[i] == pos]
        p += pulp.lpSum(df.salary[i] * x[i] for i in I) <= 50000
        if salary_min: p += pulp.lpSum(df.salary[i] * x[i] for i in I) >= salary_min
        p += pulp.lpSum(x) == 9
        p += pulp.lpSum(x[i] for i in P('QB')) == 1; p += pulp.lpSum(x[i] for i in P('DST')) == 1
        p += pulp.lpSum(x[i] for i in P('RB')) >= 2; p += pulp.lpSum(x[i] for i in P('WR')) >= 3; p += pulp.lpSum(x[i] for i in P('TE')) >= 1
        p += pulp.lpSum(x[i] for i in P('RB') + P('WR') + P('TE')) == 7
        teams = sorted(set(df.team))
        for t in teams:   # no offense against your own defense
            d = [i for i in P('DST') if df.team[i] == t]; off = [i for i in I if df.opp[i] == t and df.pos[i] != 'DST']
            if d and off: p += pulp.lpSum(x[i] for i in off) <= 8 * (1 - pulp.lpSum(x[i] for i in d))
        if mode == 'gpp':
            for t in teams:
                q = [i for i in P('QB') if df.team[i] == t]
                if not q: continue
                qv = pulp.lpSum(x[i] for i in q)
                catch = [i for i in I if df.team[i] == t and df.pos[i] in ('WR', 'TE')]
                own_rb = [i for i in P('RB') if df.team[i] == t]
                opp = df.opp[q[0]]; back = [i for i in I if df.team[i] == opp and df.pos[i] in ('WR', 'TE', 'RB')]
                p += pulp.lpSum(x[i] for i in catch) >= 2 * qv           # QB + 2 pass catchers
                if back: p += pulp.lpSum(x[i] for i in back) >= qv       # bring-back from the other side
                if own_rb: p += pulp.lpSum(x[i] for i in own_rb) <= 3 * (1 - qv)   # no QB + his own RB
        for i in I:
            if df.name[i] in lock: p += x[i] == 1
            if df.name[i] in ban: p += x[i] == 0
        for L in out: p += pulp.lpSum(x[i] for i in L) <= 9 - min_diff
        if not _solve(p): break
        out.append([i for i in I if x[i].value() > 0.5])
    return df, out

def showdown(df, mode='gpp', n=1, seed=0, lock=(), ban=(), min_diff=None):
    """Captain mode: 1 CPT (1.5x points, cpt_salary) + 5 FLEX, both teams represented."""
    df = df.reset_index(drop=True); rng = np.random.default_rng(seed); I = range(len(df))
    min_diff = min_diff or (1 if mode == 'cash' else 2)
    out = []
    for k in range(n):
        sf = _score(df, mode, rng if k else None); sc = 1.5 * _score(df, mode, rng if k else None, cpt=True)
        p = pulp.LpProblem('sd', pulp.LpMaximize)
        c = [pulp.LpVariable(f'c{i}', cat='Binary') for i in I]; f = [pulp.LpVariable(f'f{i}', cat='Binary') for i in I]
        p += pulp.lpSum(sc[i] * c[i] + sf[i] * f[i] for i in I)
        p += pulp.lpSum(c) == 1; p += pulp.lpSum(f) == 5
        for i in I: p += c[i] + f[i] <= 1
        p += pulp.lpSum(df.cpt_salary[i] * c[i] + df.salary[i] * f[i] for i in I) <= 50000
        for t in set(df.team): p += pulp.lpSum(c[i] + f[i] for i in I if df.team[i] == t) >= 1
        for i in I:
            if df.pos[i] in ('K', 'DST'): p += c[i] == 0               # kicker/defense captains almost never win
        if mode == 'gpp':
            for i in I:
                if df.pos[i] == 'QB':                                  # QB captain needs one of his pass catchers
                    mates = [j for j in I if df.team[j] == df.team[i] and df.pos[j] in ('WR', 'TE')]
                    if mates: p += pulp.lpSum(f[j] for j in mates) >= c[i]
        for i in I:
            if df.name[i] in lock: p += c[i] + f[i] == 1
            if df.name[i] in ban: p += c[i] + f[i] == 0
        for L in out:
            p += pulp.lpSum(c[i] + f[i] for i in L['all']) <= 6 - min_diff
        if not _solve(p): break
        cap = [i for i in I if c[i].value() > 0.5][0]
        out.append(dict(cpt=cap, flex=[i for i in I if f[i].value() > 0.5], all=[cap] + [i for i in I if f[i].value() > 0.5]))
    return df, out
