"""Self-updating rule hit rates. Run after grade.py each week.
Every graded live pick (picks_log.csv: every time a rule fired, bet or not) updates that rule's hit rate:
    p = (p_backtest * n_backtest + live wins) / (n_backtest + live decided picks)
so the backtest is the starting belief and live results steadily take over. Stakes (fractional Kelly on p) shrink
automatically for a rule that is decaying. Kill switch: after 50 live picks a bet rule whose updated p falls below
break-even is turned into a lean (no stakes); a lean is promoted to a bet rule after 50 live picks at 55%+.
Usage: python3 rules_learn.py"""
import os, sys, json, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import PKG
RJ = f'{PKG}/rules.json'; PL = f'{PKG}/logs/picks_log.csv'
R = json.load(open(RJ)); rules = R['betting']['rules']
SEED = f'{PKG}/rules_seed.json'   # new rules shipped from the package branch: add any that rules.json doesn't have yet
if os.path.exists(SEED):
    for k, v in json.load(open(SEED)).items():
        if not k.startswith('_') and k not in rules: rules[k] = dict(v)
P = pd.read_csv(PL) if os.path.exists(PL) else pd.DataFrame(columns=['rule', 'result'])
P = P[P.result.isin(['W', 'L'])]
BE = {'nfl_teaser_leg': 0.739}
changes = []
for name, r in rules.items():
    if name.startswith('_') or not isinstance(r, dict) or 'p' not in r: continue
    if 'p_bt' not in r: r['p_bt'], r['n_bt'] = r['p'], r.get('n', 0)      # remember the backtest starting point once
    g = P[P.rule == name]; w, n = int((g.result == 'W').sum()), len(g)
    p_new = (r['p_bt'] * r['n_bt'] + w) / (r['n_bt'] + n) if (r['n_bt'] + n) else r['p_bt']
    r['live_w'], r['live_n'] = w, n; old = r['p']; r['p'] = round(p_new, 4); r['n'] = int(r['n_bt'] + n)
    be = BE.get(name, 0.524)
    if n >= 50 and not r.get('lean_only') and w / n < be and p_new < be:
        r['lean_only'] = True; r['retired'] = f'{w}-{n - w} live'; changes.append(f'{name}: retired to lean ({w}-{n - w})')
    elif n >= 50 and r.get('lean_only') and not r.get('lean_forever') and w / n >= 0.55:
        r['lean_only'] = False; r.pop('retired', None); changes.append(f'{name}: promoted to bet rule ({w}-{n - w})')
    if abs(old - r['p']) >= 0.0005: changes.append(f'{name}: hit rate {old:.3f} -> {r["p"]:.3f} (live {w}-{n - w})')
json.dump(R, open(RJ, 'w'), indent=1)
print('RULES LEARNED' + (':\n  ' + '\n  '.join(changes) if changes else ': no changes'))
