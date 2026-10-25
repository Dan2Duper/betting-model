"""Grade the bet log (and pick log) against final scores; print win rate, profit and ROI. Usage: python3 grade.py
Lines are home-team lines (negative = home favored)."""
import os, sys, glob, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import PKG, DATA
def finals():
    out = {}
    for f in glob.glob(f'{DATA}/cfb/repo/schedules/csv/cfb_schedules_*.csv'):
        s = pd.read_csv(f); s = s[s.completed == True]
        for r in s.itertuples(): out[('CFB', str(r.game_id))] = (r.home_points, r.away_points)
    if os.path.exists(f'{DATA}/nfl/games.csv'):
        g = pd.read_csv(f'{DATA}/nfl/games.csv'); g = g[g.home_score.notna()]
        for r in g.itertuples():
            out[('NFL', str(r.game_id))] = (r.home_score, r.away_score)
            if pd.notna(r.espn): out[('NFL', str(int(r.espn)))] = (r.home_score, r.away_score)   # your own NFL bets use ESPN ids
    return out
F = finals()
import subprocess
if os.path.exists(f'{PKG}/logs/page_changes.json'):   # merge what the page saved (AI-LOX it made, your bets) before grading
    subprocess.run([sys.executable, '-I', f'{PKG}/tracker.py'], check=False)
import json
def grade(r):
    prev = r.result if isinstance(r.result, str) else ''
    if getattr(r, 'market', '') == 'teaser':   # all legs must cover their teased lines
        try: legs = json.loads(r.legs) if isinstance(r.legs, str) else []
        except Exception: return prev
        res = []
        for l in legs:
            k = ('NFL', str(l['game_id']))
            if k not in F: return prev
            hp, ap = F[k]; d = (hp - ap) + float(l['line']); d = -d if l['side'] == 'away' else d
            res.append('W' if d > 0 else ('L' if d < 0 else 'P'))
        return 'L' if 'L' in res else ('P' if 'P' in res else 'W')
    if getattr(r, 'market', '') == 'parlay':
        import ast
        try: legs = json.loads(r.legs) if str(r.legs).startswith('[{"') else ast.literal_eval(str(r.legs))
        except Exception: return prev
        res = []
        for l in legs:
            k = (l.get('league', r.league), str(l['game_id']))
            if k not in F: return prev
            hp, ap = F[k]
            if l['market'] == 'ml': d = (hp - ap) * (1 if l['side'] == 'home' else -1)
            else:
                ln = float(l['line']); d = (hp + ap) - ln if l['market'] == 'total' else (hp - ap) + ln
                if (l['market'] == 'total' and l['side'] == 'under') or (l['market'] == 'spread' and l['side'] == 'away'): d = -d
            res.append('W' if d > 0 else ('L' if d < 0 else 'P'))
        return 'L' if 'L' in res else ('P' if all(x == 'P' for x in res) else 'W')
    k = (r.league, str(r.game_id))
    if k not in F: return prev
    hp, ap = F[k]
    if r.market == 'ml':
        d = (hp - ap) * (1 if r.side == 'home' else -1); return 'W' if d > 0 else ('L' if d < 0 else 'P')
    line = float(r.line)
    d = (hp + ap) - line if r.market == 'total' else (hp - ap) + line
    if (r.market == 'total' and r.side == 'under') or (r.market == 'spread' and r.side == 'away'): d = -d
    return 'W' if d > 0 else ('L' if d < 0 else 'P')
for name in ('picks_log', 'bets_log', 'my_bets_log'):
    f = f'{PKG}/logs/{name}.csv'
    if not os.path.exists(f): continue
    L = pd.read_csv(f, dtype={'game_id': str}); L['result'] = [grade(r) for r in L.itertuples()]
    if name in ('bets_log', 'my_bets_log'):
        odds = L.odds.astype(float); b = (100 / odds.abs()).where(odds < 0, odds / 100)
        L['profit'] = [round(float(s) * bb, 2) if res == 'W' else (-float(s) if res == 'L' else (0.0 if res == 'P' else '')) for s, bb, res in zip(L.stake, b, L.result)]
    L.to_csv(f, index=False)
    g = L[L.result.isin(['W', 'L', 'P'])]
    print(f"== {dict(picks_log='every model flag', bets_log='ALL AI-LOX (suggested stakes)', my_bets_log='MY BETS (real money)')[name]}: {len(g)} graded, {len(L) - len(g)} pending")
    if not len(g): continue
    by = ['league', 'rule'] if name == 'picks_log' else ['league']
    t = g.groupby(by).result.agg(W=lambda s: (s == 'W').sum(), L=lambda s: (s == 'L').sum(), P=lambda s: (s == 'P').sum())
    t['win%'] = (t.W / (t.W + t.L)).round(3)
    if name in ('bets_log', 'my_bets_log'):
        gg = g.assign(stake=g.stake.astype(float), profit=g.profit.astype(float))
        t['staked $'] = gg.groupby(by).stake.sum(); t['profit $'] = gg.groupby(by).profit.sum().round(2); t['ROI'] = (t['profit $'] / t['staked $']).round(3)
        tot = gg.result.value_counts(); gg = gg.sort_values('date')
        won = gg.profit[gg.profit > 0].sum(); lost = -gg.profit[gg.profit < 0].sum()
        run = gg.profit.cumsum(); dd = (run - run.cummax()).min()
        streak, worst = 0, 0
        for res in gg.result:
            streak = streak + 1 if res == 'L' else (streak if res == 'P' else 0); worst = max(worst, streak)
        print(f"TOTAL {tot.get('W', 0)}-{tot.get('L', 0)}-{tot.get('P', 0)}  win% {tot.get('W', 0) / max(tot.get('W', 0) + tot.get('L', 0), 1):.1%}  (need 52.4% to break even at -110)")
        print(f"  staked ${gg.stake.sum():.0f} | won ${won:.2f} | LOST ${lost:.2f} | net ${gg.profit.sum():+.2f} | ROI {gg.profit.sum() / gg.stake.sum():+.1%}")
        print(f"  worst drop from a high: ${dd:.2f} | longest losing streak: {worst}")
        days = gg.groupby('date').profit.sum(); print('  by day: ' + ', '.join(f'{d} ${v:+.2f}' for d, v in days.items()))
    print(t.to_string())
import subprocess
subprocess.run([sys.executable, '-I', f'{PKG}/rules_learn.py'], check=False)   # live results update each rule's hit rate / kill switch
subprocess.run([sys.executable, '-I', f'{PKG}/tracker.py'], check=False)
