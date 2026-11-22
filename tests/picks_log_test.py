"""Unit test of the MLB pick log (mlb/picks.py): a pick follows the line until first pitch, freezes at first pitch, is graded from
the final, and gets closing-line value from the relay's snapshot. python3 tests/picks_log_test.py"""
import sys, os, json, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mlb')); import picks
M = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mlb', 'model.json'))); RJ = dict(total_gap_min=0.75, total_offset=0.45, wrigley_wind_out_mph=10)
T = lambda h, m=0: dt.datetime(2027, 6, 15, h, m, tzinfo=dt.timezone.utc)
def game(line):
    g = dict(id='pk1', pk=1, espn='900', date='2027-06-15', kick='2027-06-15T23:10:00Z', away='NYA', home='BOS', pa=5.3, ph=4.1, p_home=0.40, total=9.4, park='BOS07', wind_out=0, dome=False)
    g['p_h2'], g['p_a2'] = picks.rl_probs(M, 4.1, 5.3); g['line'] = line; return picks.game_picks(g, M, RJ)
L1 = dict(mh=120, ma=-140, total=8.0, over=-110, under=-110, rl=1.5, rl_h=-170, rl_a=145); L2 = dict(L1, total=8.5, ma=-150, mh=128)
log = dict(season=2027, games={})
assert picks.update_log(log, [game(L1)], {}, {}, T(14)) == 1; r = log['games']['1']
assert r['picks']['ml']['side'] == 'away' and r['picks']['tot']['side'] == 'over' and r['picks']['tot']['line'] == 8.0 and r['away'] == 'NYY', r
assert [x['rule'] for x in r['rules']] == ['mlb_total_gap_over'], r['rules']          # 9.4 - 0.45 - 8.0 = +0.95 runs
assert picks.update_log(log, [game(L2)], {}, {}, T(21)) == 1; r = log['games']['1']    # line moved before first pitch: the pick follows it
assert r['picks']['tot']['line'] == 8.5 and r['open']['total'] == 8.0 and r['rules'] == [], r   # gap now +0.45: no lean any more
assert picks.update_log(log, [game(dict(L2, total=9.5))], {}, {}, T(23, 30)) == 0     # after first pitch: frozen
assert log['games']['1']['picks']['tot']['line'] == 8.5
n = picks.update_log(log, [], {'1': (6, 3)}, {'900': dict(L2, total=9.0, ma=-165, mh=140)}, T(23, 59) + dt.timedelta(hours=4)); r = log['games']['1']
assert r['final'] == [6, 3] and r['res']['su'] == 'W' and r['res']['tot'] == 'W' and r['res']['rl'] in ('W', 'L'), r['res']
assert abs(r['units']['ml'] - 100 / 150) < 1e-3 and abs(r['units']['tot'] - 100 / 110) < 1e-3, r['units']
assert r['clv']['tot'] == 0.5 and r['clv']['ml'] > 0, r['clv']                          # over 8.5 closed 9.0; the Yankees got more expensive
assert picks.update_log(log, [game(L1)], {'1': (6, 3)}, {}, T(23, 59) + dt.timedelta(days=1)) == 0   # nothing rewrites a finished game
rec = picks.record(log); assert rec['model']['su']['W'] == 1 and rec['clv']['tot']['beat'] == 1.0, rec
st = picks.learn(log, {'mlb_total_gap_over': dict(p=0.5447, n=1700, lean_only=True)}); assert st['mlb_total_gap_over']['live_n'] == 0 and st['mlb_total_gap_over']['lean_only']
big = dict(season=2027, games={str(i): dict(date='2027-05-01', res=dict(rules={'mlb_total_gap_over': 'W' if i % 5 else 'L'})) for i in range(60)})
st = picks.learn(big, {'mlb_total_gap_over': dict(p=0.5447, n=1700, p_bt=0.5447, n_bt=1700, lean_only=True)}); assert st['mlb_total_gap_over']['lean_only'] is False and st['mlb_total_gap_over'].get('promoted'), st   # 48-12 live: promoted
print('pick log test passed:', json.dumps(r['picks']), r['res'], r['units'], r['clv'])
