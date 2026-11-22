"""DraftKings MLB Classic lineup builder (integer programming with PuLP/CBC), same method as dfs/optimizer.py.
Roster: P, P, C, 1B, 2B, 3B, SS, OF, OF, OF under $50,000; at most 5 hitters from one team; players from at least 2 games;
never a hitter against one of your own pitchers. A hitter listed at two positions (2B/SS, 1B/OF) can fill either.
Cash: hitters ranked on the projection (a hitter's bad game is a zero, so his floor says nothing), pitchers on projection nudged
toward the floor. Tournament: upside-weighted points minus an ownership penalty, built around a stack of 4 hitters who bat in a
row for one team (from a POSTED batting order whenever any team has one), each lineup different from every earlier one by
`min_diff` players.
Showdown (captain 1.5x + 5 utility) uses dfs/optimizer.showdown as is."""
import numpy as np, pulp
SLOTS = {'P': 2, 'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3}
ORDER = ['P', 'P', 'C', '1B', '2B', '3B', 'SS', 'OF', 'OF', 'OF']
W = dict(cash_floor=0.25, gpp_ceil=0.5, gpp_own=0.8, own_floor=1.0, noise=0.12, stack=4)

def elig(pos):
    """DraftKings position string -> roster slots it can fill ('SP' / 'RP' -> P; '1B/OF' -> 1B, OF)."""
    out = []
    for p in str(pos).upper().split('/'):
        p = 'P' if p in ('SP', 'RP', 'P') else p
        if p in SLOTS and p not in out: out.append(p)
    return out

def _score(df, mode, rng):
    isp = df.is_p.values
    if mode == 'cash': s = np.where(isp, (1 - W['cash_floor']) * df.proj + W['cash_floor'] * df.floor, df.proj)
    else: s = df.proj + W['gpp_ceil'] * (df.ceil - df.proj) - W['gpp_own'] * np.log(df.own.clip(lower=W['own_floor']) / 10)
    s = np.asarray(s, float)
    if rng is not None: s = s + rng.normal(0, 1, len(df)) * W['noise'] * (df.ceil - df.floor).clip(lower=1).values / 2
    return s

def classic(df, mode='gpp', n=1, seed=0, lock=(), ban=(), min_diff=None, stack=None):
    """df rows: name, pos (DraftKings string), team, opp, game, salary, proj, floor, ceil, own, slot (batting order 1-9; 0 / NaN for
    pitchers or unknown). Returns (df, lineups); each lineup = dict(players=[row index in roster order], slots=[...])."""
    df = df.reset_index(drop=True).copy(); rng = np.random.default_rng(seed); I = list(range(len(df)))
    E = [elig(p) for p in df.pos]; df['is_p'] = [e == ['P'] for e in E]
    min_diff = min_diff or (1 if mode == 'cash' else 3); stack = W['stack'] if stack is None else stack
    hit = [i for i in I if not df.is_p[i] and E[i]]; pit = [i for i in I if df.is_p[i]]
    teams = sorted(set(df.team)); games = sorted(set(df.game)) if 'game' in df else []
    slot = df.slot.fillna(0).astype(int).values if 'slot' in df else np.zeros(len(df), int)
    out = []
    for k in range(n):
        s = _score(df, mode, rng if k else None)
        p = pulp.LpProblem('mlb', pulp.LpMaximize); x = {i: pulp.LpVariable(f'x{i}', cat='Binary') for i in hit + pit}
        y = {(i, q): pulp.LpVariable(f'y{i}_{q}', cat='Binary') for i in hit for q in E[i]}
        p += pulp.lpSum(s[i] * x[i] for i in x)
        p += pulp.lpSum(df.salary[i] * x[i] for i in x) <= 50000
        p += pulp.lpSum(x[i] for i in pit) == 2
        for i in hit: p += pulp.lpSum(y[i, q] for q in E[i]) == x[i]
        for q, need in SLOTS.items():
            if q != 'P': p += pulp.lpSum(y[i, q] for i in hit if q in E[i]) == need
        for t in teams:
            th = [i for i in hit if df.team[i] == t]
            if th: p += pulp.lpSum(x[i] for i in th) <= 5
            for j in pit:                                   # no hitters against your own pitcher
                if df.opp[j] == t and th: p += pulp.lpSum(x[i] for i in th) <= 8 * (1 - x[j])
        for g in games: p += pulp.lpSum(x[i] for i in x if df.game[i] == g) <= 9          # at least two games
        if mode == 'gpp' and stack:
            z = []; posted = set(df.team[[i for i in hit if bool(df.conf[i])]]) if 'conf' in df else set()
            for t in teams:
                if posted and t not in posted: continue                                    # a batting order that is not posted yet is a guess: stack only real ones when any exist
                order = {slot[i]: i for i in hit if df.team[i] == t and 1 <= slot[i] <= 9}
                if len(order) < stack: continue
                for a in range(1, 10):
                    win = [((a - 1 + d) % 9) + 1 for d in range(stack)]                     # consecutive batting-order spots, wrapping 9 -> 1
                    if all(w in order for w in win):
                        v = pulp.LpVariable(f'z{t}_{a}', cat='Binary'); z.append(v)
                        p += pulp.lpSum(x[order[w]] for w in win) >= stack * v
            if z: p += pulp.lpSum(z) >= 1
        for i in x:
            if df.name[i] in lock: p += x[i] == 1
            if df.name[i] in ban: p += x[i] == 0
        for L in out: p += pulp.lpSum(x[i] for i in L['players']) <= 10 - min_diff
        p.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=20))
        if pulp.LpStatus[p.status] != 'Optimal': break
        ps = sorted([i for i in pit if x[i].value() > 0.5], key=lambda i: -df.salary[i]); roster = [('P', i) for i in ps]
        for q in ('C', '1B', '2B', '3B', 'SS', 'OF'):
            roster += [(q, i) for i in sorted([i for i in hit if q in E[i] and y[i, q].value() > 0.5], key=lambda i: -df.salary[i])]
        out.append(dict(players=[i for _, i in roster], slots=[q for q, _ in roster]))
    return df, out

def check(df, L):
    """Rule check for one lineup (used by the tests): returns a list of problems, empty when legal."""
    x = df.loc[L['players']]; bad = []
    if len(x) != 10 or x.index.duplicated().any(): bad.append('not 10 different players')
    if x.salary.sum() > 50000: bad.append('over the cap')
    if L['slots'] != ORDER: bad.append('slots ' + ','.join(L['slots']))
    for q, i in zip(L['slots'], L['players']):
        if q not in elig(df.pos[i]): bad.append(f'{df.name[i]} cannot play {q}')
    h = x[[elig(p) != ['P'] for p in x.pos]]; pt = x[[elig(p) == ['P'] for p in x.pos]]
    if len(h) and h.team.value_counts().max() > 5: bad.append('6+ hitters from one team')
    if set(h.team) & set(pt.opp): bad.append('hitter against own pitcher')
    if 'game' in x and x.game.nunique() < 2: bad.append('one game only')
    return bad
