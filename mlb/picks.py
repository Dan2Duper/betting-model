"""MLB picks that count: every modeled game gets three model picks (winner, run line, over/under) made against the DraftKings line
in hand at build time, the rule leans on top, a season log that freezes each game at first pitch, grading from final scores, closing
line value, and daily self-tuning of every rule's hit rate (same formula as rules_learn.py: the backtest is the starting belief,
live results take over; 50 live picks decide promotion to a bet / retirement to a lean).
Nothing here needs Rich's PC or phone: the log lives on the data branch (mlb/log_<season>.json) and the page reads it."""
import json, math, datetime as dt
SHOW = {'ANA': 'LAA', 'CHN': 'CHC', 'CHA': 'CWS', 'KCA': 'KC', 'LAN': 'LAD', 'NYN': 'NYM', 'NYA': 'NYY', 'SDN': 'SD', 'SFN': 'SF', 'SLN': 'STL', 'TBA': 'TB', 'WAS': 'WSH'}
show = lambda c: SHOW.get(c, c)
def imp(o): return -o / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)
def pay(o): return 100.0 / -o if o < 0 else o / 100.0
def novig(a, b): return imp(a) / (imp(a) + imp(b))
def logistic(x): return 1.0 / (1.0 + math.exp(-x))
RULE_NAMES = {'mlb_total_gap_over': 'MLB model total 0.75+ runs over the line (lean)', 'mlb_total_gap_under': 'MLB model total 0.75+ runs under the line (lean)',
              'mlb_wrigley_wind_out_over': 'MLB Wrigley wind out 10+ mph, over (lean)'}

def rl_probs(M, lam_h, lam_a):
    """P(home wins by 2+), P(away wins by 2+) from the saved run-line fit (model.json 'rl')."""
    z = math.log(lam_h / lam_a); t = lam_h + lam_a - 9.0; r = M['rl']
    return logistic(r['h2']['a'] + r['h2']['z'] * z + r['h2']['t'] * t), logistic(r['a2']['a'] + r['a2']['z'] * z + r['a2']['t'] * t)

def game_picks(g, M, RJ):
    """g: one mlb.json game (model numbers + g['line'] or None). Adds g['picks'] and g['rules']. RJ = rules.json['mlb'] thresholds."""
    L = g.get('line') or {}; P = {}; rules = []
    # ---- winner: the team the model makes the favorite; the price and the market's own no-vig number ride along ----
    side = 'home' if g['p_home'] >= 0.5 else 'away'; p = g['p_home'] if side == 'home' else 1 - g['p_home']
    P['ml'] = dict(side=side, p=round(p, 4))
    if L.get('mh') is not None and L.get('ma') is not None:
        mh = novig(L['mh'], L['ma']); P['ml'].update(odds=L['mh'] if side == 'home' else L['ma'], mkt=round(mh if side == 'home' else 1 - mh, 4), edge=round(p - (mh if side == 'home' else 1 - mh), 4))
    # ---- run line: the side whose cover chance beats its no-vig closing price by more (home -1.5 needs a 2+ run win; home +1.5 loses only to a 2+ run loss) ----
    if L.get('rl') is not None and M.get('rl'):
        ph = g['p_h2'] if L['rl'] < 0 else 1 - g['p_a2']
        if L.get('rl_h') is not None and L.get('rl_a') is not None:
            mk = novig(L['rl_h'], L['rl_a']); s = 'home' if ph >= mk else 'away'
            P['rl'] = dict(side=s, line=L['rl'], p=round(ph if s == 'home' else 1 - ph, 4), odds=L['rl_h'] if s == 'home' else L['rl_a'], mkt=round(mk if s == 'home' else 1 - mk, 4), edge=round(abs(ph - mk), 4))
        else:
            s = 'home' if ph >= 0.5 else 'away'; P['rl'] = dict(side=s, line=L['rl'], p=round(ph if s == 'home' else 1 - ph, 4))
    # ---- total ----
    if L.get('total') is not None:
        ov = M['over']; po = logistic(ov['a'] + ov['b'] * (g['total'] - L['total'])); gap = g['total'] - RJ.get('total_offset', 0.45) - L['total']
        s = 'over' if po >= 0.5 else 'under'
        P['tot'] = dict(side=s, line=L['total'], p=round(po if s == 'over' else 1 - po, 4), gap=round(gap, 2), odds=(L.get('over') if s == 'over' else L.get('under')) or -110)
        gm = RJ.get('total_gap_min', 0.75)
        if gap >= gm: rules.append(dict(rule='mlb_total_gap_over', market='total', side='over', line=L['total'], odds=L.get('over') or -110))
        elif gap <= -gm: rules.append(dict(rule='mlb_total_gap_under', market='total', side='under', line=L['total'], odds=L.get('under') or -110))
        if g.get('park') == 'CHI11' and (g.get('wind_out') or 0) >= RJ.get('wrigley_wind_out_mph', 10) and not g.get('dome'):
            rules.append(dict(rule='mlb_wrigley_wind_out_over', market='total', side='over', line=L['total'], odds=L.get('over') or -110))
    g['picks'] = P; g['rules'] = rules
    return g

def _res(d): return 'W' if d > 0 else 'L' if d < 0 else 'P'
def grade(rec):
    """Fill rec['res'] from rec['final'] = [away runs, home runs]. Units are per 1 risked at the logged price."""
    if not rec.get('final'): return rec
    a, h = rec['final']; m = h - a; P = rec.get('picks') or {}; R = {}; U = {}
    R['su'] = _res(m if P['ml']['side'] == 'home' else -m) if P.get('ml') else None
    if P.get('ml') and P['ml'].get('odds') is not None: U['ml'] = round(pay(P['ml']['odds']), 3) if R['su'] == 'W' else -1.0 if R['su'] == 'L' else 0.0
    if P.get('rl'):
        d = m + P['rl']['line']; R['rl'] = _res(d if P['rl']['side'] == 'home' else -d)
        if P['rl'].get('odds') is not None: U['rl'] = round(pay(P['rl']['odds']), 3) if R['rl'] == 'W' else -1.0 if R['rl'] == 'L' else 0.0
    if P.get('tot'):
        d = a + h - P['tot']['line']; R['tot'] = _res(d if P['tot']['side'] == 'over' else -d); o = P['tot'].get('odds') or -110
        U['tot'] = round(pay(o), 3) if R['tot'] == 'W' else -1.0 if R['tot'] == 'L' else 0.0
    R['rules'] = {}
    for r in rec.get('rules') or []:
        d = (a + h - r['line']) if r['market'] == 'total' else (m + r['line']) if r['market'] == 'spread' else m
        if r['side'] in ('under', 'away'): d = -d
        R['rules'][r['rule']] = _res(d)
    rec['res'] = R; rec['units'] = U
    return rec

def clv(rec):
    """Closing line value of each pick: + = the market moved our way after the pick (the real test of an edge)."""
    L, C, P = rec.get('line') or {}, rec.get('close') or {}, rec.get('picks') or {}
    if not C: return rec
    V = {}
    if P.get('ml') and all(x.get(k) is not None for x in (L, C) for k in ('mh', 'ma')):
        a, b = novig(L['mh'], L['ma']), novig(C['mh'], C['ma']); V['ml'] = round((b - a) * (1 if P['ml']['side'] == 'home' else -1), 4)          # win-probability points
    if P.get('tot') and L.get('total') is not None and C.get('total') is not None:
        V['tot'] = round((C['total'] - L['total']) * (1 if P['tot']['side'] == 'over' else -1), 2)                                          # runs
    if P.get('rl') and L.get('rl') == C.get('rl') and all(x.get(k) is not None for x in (L, C) for k in ('rl_h', 'rl_a')):
        a, b = novig(L['rl_h'], L['rl_a']), novig(C['rl_h'], C['rl_a']); V['rl'] = round((b - a) * (1 if P['rl']['side'] == 'home' else -1), 4)
    if V: rec['clv'] = V
    return rec

KEEP = ('id', 'pk', 'espn', 'date', 'kick', 'away', 'home', 'num', 'pa', 'ph', 'p_home', 'total', 'p_h2', 'p_a2', 'sp_away', 'sp_home', 'sp_away_known', 'sp_home_known', 'lineup_away', 'lineup_home',
        'wx', 'temp', 'wind_out', 'wind_in', 'dome', 'park', 'ump', 'line', 'picks', 'rules')
def update_log(log, games, finals, closes, now, status=None):
    """log: {'season', 'games': {pk: rec}}. games: today's mlb.json games (with picks). finals: {pk: (away runs, home runs)}.
    closes: {ESPN game id: line dict} = the relay's last look at the line before first pitch. A game is rewritten on every build until it
    starts; from first pitch on its model numbers, line and picks are frozen. Returns how many records changed."""
    G = log.setdefault('games', {}); n = 0
    for g in games:
        k = str(g['pk']); old = G.get(k); kick = _t(g.get('kick'))
        if kick is not None and now >= kick and old: continue                         # started: frozen
        if kick is not None and now >= kick and not old: continue                     # never seen before first pitch: not a pick
        if not g.get('picks'): continue
        rec = {c: g[c] for c in KEEP if c in g}; rec['away'] = show(g['away']); rec['home'] = show(g['home']); rec['a'] = g['away']; rec['h'] = g['home']; rec['built'] = now.isoformat(timespec='minutes')
        if old and old.get('open'): rec['open'] = old['open']
        elif g.get('line'): rec['open'] = dict(g['line'])                             # first line we saw for this game
        if old != rec: G[k] = rec; n += 1
    for k, rec in G.items():
        c = closes.get(str(rec.get('espn'))) if closes and rec.get('espn') else None      # the relay keys its snapshots by ESPN game id
        if c and not rec.get('final') and rec.get('close') != c: rec['close'] = c; clv(rec); n += 1
        st = (status or {}).get(k) or (status or {}).get(int(k))
        if st and st != rec.get('status') and not rec.get('final'): rec['status'] = st; n += 1
        f = finals.get(k) or finals.get(int(k))
        if f and not rec.get('final'): rec['final'] = [int(f[0]), int(f[1])]; rec.pop('status', None); grade(rec); clv(rec); n += 1
    return n
def _t(s):
    try: return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception: return None

def learn(log, rules_bt, be=0.524):
    """Rule hit rates from the live log (picks graded W/L only). rules_bt: rules.json betting.rules entries for the mlb_* rules."""
    out = {}
    for name, r in rules_bt.items():
        w = l = 0
        for rec in log.get('games', {}).values():
            x = ((rec.get('res') or {}).get('rules') or {}).get(name)
            if x == 'W': w += 1
            elif x == 'L': l += 1
        n = w + l; pb, nb = r.get('p_bt', r.get('p', 0.5)), r.get('n_bt', r.get('n', 0))
        p = (pb * nb + w) / (nb + n) if nb + n else pb
        o = dict(p=round(p, 4), n=int(nb + n), p_bt=pb, n_bt=nb, live_w=w, live_n=n, lean_only=bool(r.get('lean_only')), src=r.get('src', ''), name=RULE_NAMES.get(name, name))
        if n >= 50 and not o['lean_only'] and w / n < be and p < be: o['lean_only'] = True; o['retired'] = f'{w}-{l} live'
        elif n >= 50 and o['lean_only'] and not r.get('lean_forever') and w / n >= 0.55: o['lean_only'] = False; o['promoted'] = f'{w}-{l} live'
        out[name] = o
    return out

def record(log, since=None):
    """Season-to-date scoreboard for the page and the reports: the model's three picks and each rule."""
    S = {k: dict(W=0, L=0, P=0, u=0.0, n_u=0) for k in ('su', 'rl', 'tot')}; RU = {}; C = dict(ml=[], tot=[], rl=[])
    for rec in log.get('games', {}).values():
        if since and rec.get('date', '') < since: continue
        R = rec.get('res')
        for k, v in (rec.get('clv') or {}).items(): C[k].append(v)
        if not R: continue
        for k, uk in (('su', 'ml'), ('rl', 'rl'), ('tot', 'tot')):
            if R.get(k): S[k][R[k]] += 1
            u = (rec.get('units') or {}).get(uk)
            if u is not None: S[k]['u'] += u; S[k]['n_u'] += 1
        for name, x in (R.get('rules') or {}).items(): RU.setdefault(name, dict(W=0, L=0, P=0))[x] += 1
    for v in S.values(): v['u'] = round(v['u'], 2)
    return dict(model=S, rules=RU, clv={k: dict(n=len(v), avg=round(sum(v) / len(v), 4), beat=round(sum(1 for x in v if x > 0) / len(v), 3)) for k, v in C.items() if v})

def pick_rows(log, rule_stats, since):
    """The log as flat pick rows in the page's format (league MLB; 'spread' = run line with the HOME line), newest `since` onward."""
    rows = []
    for rec in log.get('games', {}).values():
        if rec.get('date', '') < since: continue
        base = dict(date=rec['date'], kick=rec.get('kick'), league='MLB', game_id=str(rec.get('espn') or rec['pk']), pk=rec['pk'], game=f"{rec['away']} @ {rec['home']}", away=rec['away'], home=rec['home'],
                    line_total=(rec.get('line') or {}).get('total', ''), model_total=rec.get('total'), src='model')
        for r in rec.get('rules') or []:
            o = dict(base, rule=r['rule'], market=r['market'], side=r['side'], line=r['line'], odds=r.get('odds') or -110, result=((rec.get('res') or {}).get('rules') or {}).get(r['rule'], ''))
            o['id'] = f"{o['date']}|{o['game_id']}|{o['rule']}"; o['hit_rate'] = (rule_stats.get(r['rule']) or {}).get('p'); rows.append(o)
    return rows
