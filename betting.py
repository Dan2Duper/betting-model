"""Turn model picks into the day's bets and stakes. Rules and budget live in rules.json -> betting."""
import json, os, pandas as pd
PKG = os.path.dirname(os.path.abspath(__file__))
BETLOG = f'{PKG}/logs/bets_log.csv'
CTX = ['away', 'home', 'away_conf', 'home_conf', 'kick_hour', 'wind', 'line_spread', 'line_total', 'model_total', 'model_home_spread']
COLS = ['date', 'league', 'week', 'game_id', 'game', 'bet', 'rule', 'market', 'side', 'line', 'odds', 'hit_rate', 'n', 'stake', 'to_win', 'result', 'profit'] + CTX

def payout(odds): return 100 / abs(odds) if odds < 0 else odds / 100

def bet_text(o, p):
    if p['market'] == 'total': return f"{o['away']}/{o['home']} {p['side'].upper()} {p['line']:g}"
    team = o['home'] if p['side'] == 'home' else o['away']; ln = p['line'] if p['side'] == 'home' else -p['line']
    return f"{team} {ln:+g}"

def ctx(o):
    return dict(away=o['away'], home=o['home'], away_conf=o.get('away_conf', ''), home_conf=o.get('home_conf', ''), kick_hour=round(float(o.get('kick_hour') or 0), 2),
                wind=o.get('wind'), line_spread=o.get('line_spread'), line_total=o.get('line_total'), model_total=o.get('pred_total'), model_home_spread=o.get('pred_home_spread'))

def select(preds, skip=()):
    B = json.load(open(f'{PKG}/rules.json'))['betting']; R = B['rules']
    cand = []
    for o in preds:
        if f"{o['away']} @ {o['home']}" in skip: continue
        for p in o.get('picks', []):
            r = R.get(p['rule'])
            if r is None: continue                         # e.g. *_model_disagrees -> excluded
            if r.get('lean_only') or r['p'] < (B['cfb_min_hit'] if o['league'] == 'CFB' else B['nfl_min_hit']): continue
            ps = (r['p'] * r['n'] + 0.5 * B['shrink_k']) / (r['n'] + B['shrink_k'])
            b = payout(B['default_odds']); kelly = max(ps - (1 - ps) / b, 0.001)
            cand.append(dict(date=str(o['date']), league=o['league'], week=o.get('week'), game_id=str(o['game_id']), game=f"{o['away']} @ {o['home']}",
                             bet=bet_text(o, p), rule=p['rule'], market=p['market'], side=p['side'], line=p['line'], odds=B['default_odds'],
                             hit_rate=r['p'], n=r['n'], kelly=kelly, result='', profit='', **ctx(o)))
    # one bet per game (best rule), top N per day, stakes scaled to the daily budget
    df = pd.DataFrame(cand)
    if df.empty: return df
    df = df.sort_values(['hit_rate', 'kelly'], ascending=False).drop_duplicates('game_id')
    out = []
    for d, g in df.groupby('date'):
        g = g.head(B['max_bets_per_day']).copy()
        raw = B['daily_budget'] * g.kelly / g.kelly.sum()
        st = raw.clip(lower=B['min_stake']).round().astype(int)
        st.iloc[st.values.argmax()] += B['daily_budget'] - st.sum()   # make the day add up exactly
        g['stake'] = st; g['to_win'] = (g.stake * payout(B['default_odds'])).round(2); out.append(g)
    return pd.concat(out)[COLS].sort_values(['date', 'stake'], ascending=[True, False])

def log(bets):
    old = pd.read_csv(BETLOG, dtype=str) if os.path.exists(BETLOG) else pd.DataFrame(columns=COLS)
    graded = old[old.result.fillna('') != '']
    keep = old[(old.result.fillna('') != '') | ~old.date.isin(bets.date.unique())]   # re-running a day replaces its ungraded bets
    new = bets[~bets.set_index(['game_id', 'rule']).index.isin(graded.set_index(['game_id', 'rule']).index)]
    pd.concat([keep, new.astype(str)])[COLS].to_csv(BETLOG, index=False)
