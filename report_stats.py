"""Numbers for the in-game reports (totals in progress also show the scoring pace; an over that is \"losing\" at halftime is normal): reads report.json (written by the Bet Tracker page to the sync branch every few minutes while
it is open) and prints a plain summary of today's results: Rich's bets, AI-LOX, and the model's straight-up / spread / total record.
Usage: python3 report_stats.py report.json [YYYY-MM-DD (Central date, default today)] [CFB|NFL|ALL]"""
import sys, json, datetime as dt
from zoneinfo import ZoneInfo
CT = ZoneInfo('America/Chicago')
R = json.load(open(sys.argv[1]))
day = sys.argv[2] if len(sys.argv) > 2 else dt.datetime.now(CT).date().isoformat()
LG = (sys.argv[3] if len(sys.argv) > 3 else 'ALL').upper()
gen = dt.datetime.fromisoformat(R['generated'].replace('Z', '+00:00')); age = (dt.datetime.now(dt.timezone.utc) - gen).total_seconds() / 60
print(f"DATA AS OF {gen.astimezone(CT):%I:%M %p} CT ({age:.0f} min old){'  ** STALE: the app has not been open lately **' if age > 20 else ''}")
def ok(b): return LG == 'ALL' or b.get('league') == LG
def status(b):
    if b.get('result'): return b['result']
    legs = b.get('legs')
    if legs:
        if any(l.get('score') and l['score']['state'] == 'post' and (l.get('margin_vs_line') or 0) < 0 for l in legs): return 'L(dead)'
        if any(l.get('margin_vs_line') is not None and l['margin_vs_line'] < 0 for l in legs): return 'losing'
        if all(l.get('margin_vs_line') is not None and l['margin_vs_line'] > 0 for l in legs): return 'winning'
        return 'pending'
    m = b.get('margin_vs_line'); s = b.get('score') or {}
    if m is None: return 'pending'
    if s.get('state') == 'post': return 'W' if m > 0 else 'L' if m < 0 else 'P'
    return 'winning' if m > 0 else 'losing' if m < 0 else 'even'
import re
def elapsed(detail):
    d = (detail or '').lower()
    if 'half' in d: return 0.5
    if 'final' in d: return 1.0
    m = re.match(r'(\d+):(\d+) - (\d)', d)
    if not m: return None
    q = int(m.group(3)); left = int(m.group(1)) + int(m.group(2)) / 60
    return min(((q - 1) * 15 + (15 - left)) / 60, 1.0) if q <= 4 else 1.0
def pace(sc, line):
    """for a total in progress: projected final total at the current scoring pace"""
    e = elapsed(sc.get('detail')) if sc else None
    if not e or e >= 1 or e < 0.15: return ''
    return f", on pace for {(sc['as'] + sc['hs']) / e:.0f} vs {line}"
def money(b, st):
    if st == 'W': return b['to_win']
    if st in ('L', 'L(dead)'): return -b['stake']
    return 0.0
def block(name, bets):
    bets = [b for b in bets if b['date'] == day and ok(b)]
    if not bets: print(f'\n{name}: none today'); return
    S = [(b, status(b)) for b in bets]
    w = sum(s == 'W' for _, s in S); l = sum(s in ('L', 'L(dead)') for _, s in S); p = sum(s == 'P' for _, s in S)
    net = sum(money(b, s) for b, s in S); staked = sum(b['stake'] for b in bets)
    live_w = sum(s == 'winning' for _, s in S); live_l = sum(s == 'losing' for _, s in S); pend = sum(s in ('pending', 'even') for _, s in S)
    print(f"\n{name}: settled {w}-{l}-{p}, net ${net:+.2f} on ${staked:.2f} staked today | live: {live_w} winning, {live_l} losing, {pend} not started/even")
    for b, s in sorted(S, key=lambda x: x[0]['kick'] or ''):
        sc = b.get('score') or {}
        where = f"{sc.get('away')} {sc.get('as')}-{sc.get('hs')} {sc.get('home')} ({sc.get('detail')})" if sc and sc.get('state') != 'pre' else ''
        if b.get('legs'): where = ' | '.join(f"{l['bet']}: " + (f"{l['score']['as']}-{l['score']['hs']} {l['score']['detail']}" if l.get('score') and l['score']['state'] != 'pre' else 'not started') + (f" (by {l['margin_vs_line']:+g}" + (pace(l['score'], l['bet'].split()[1]) if l['bet'].split()[0] in ('Over', 'Under') and l['score']['state'] == 'in' else '') + ')' if l.get('margin_vs_line') is not None else '') for l in b['legs'])
        elif b.get('market') == 'total' and sc.get('state') == 'in': where += pace(sc, b.get('line'))
        mv = f" by {b['margin_vs_line']:+g} vs line" if b.get('margin_vs_line') is not None and not b.get('legs') else ''
        print(f"  [{s}] {b['bet']} ({b['game']}) ${b['stake']:.2f} to win ${b['to_win']:.2f}{mv}  {where}")
block('MY BETS', R['mine'])
block('MY BETS THAT MATCH AI-LOX', [b for b in R['mine'] if b.get('ai_lox')])
block('ALL AI-LOX (suggested stakes, bet or not)', R['ai_lox'])
G = [g for g in R['games'] if g.get('model') and g['date'] == day and (LG == 'ALL' or g['league'] == LG)]
fin = [g for g in G if g['state'] == 'post']
su = sum((g['model']['home'] - g['model']['away'] > 0) == (g['hs'] - g['as'] > 0) for g in fin if g['hs'] != g['as'])
ats = [(g['model']['home_spread'] - g['model']['line_spread'], (g['hs'] - g['as']) + g['model']['line_spread']) for g in fin if g['model'].get('line_spread') is not None]
ats = [(a, r) for a, r in ats if a and r]; atsw = sum((a < 0) == (r > 0) for a, r in ats)
ou = [(g['model']['total'] - g['model']['line_total'], g['as'] + g['hs'] - g['model']['line_total']) for g in fin if g['model'].get('line_total') is not None]
ou = [(a, r) for a, r in ou if a and r]; ouw = sum((a > 0) == (r > 0) for a, r in ou)
miss = [abs((g['hs'] - g['as']) - (g['model']['home'] - g['model']['away'])) for g in fin]
print(f"\nMODEL today: {len(fin)} of {len(G)} modeled games final | winners {su}/{len(fin)} | vs spread {atsw}-{len(ats) - atsw} | totals {ouw}-{len(ou) - ouw} | avg margin miss {sum(miss) / max(len(miss), 1):.1f} pts")
best = sorted(fin, key=lambda g: abs((g['hs'] - g['as']) - (g['model']['home'] - g['model']['away'])))
if best: g = best[0]; print(f"  closest call: {g['away']} {g['as']}-{g['hs']} {g['home']} (model {g['model']['away']:.0f}-{g['model']['home']:.0f})")
if len(best) > 1: g = best[-1]; print(f"  worst miss: {g['away']} {g['as']}-{g['hs']} {g['home']} (model {g['model']['away']:.0f}-{g['model']['home']:.0f})")
live = [g for g in G if g['state'] == 'in']
if live: print('  live now: ' + '; '.join(f"{g['away']} {g['as']}-{g['hs']} {g['home']} {g['detail']} (model {g['model']['away']:.0f}-{g['model']['home']:.0f})" for g in live[:12]))
