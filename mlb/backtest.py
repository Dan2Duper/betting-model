"""Test every candidate MLB rule against the CLOSING line, out of sample.
The model's numbers are walk-forward (preds.parquet). Each rule is judged twice: a discovery window (2013-2020) and a hold-out
window (2021-2025, DraftKings closing prices incl. real over/under juice). A rule passes only if it wins in BOTH windows and the
hold-out clears break-even by itself. Games whose "closing" line looks like a live in-game line or a late scratch are left out.
Usage: python3 mlb/backtest.py [DATA_DIR]  -> DATA_DIR/rules_bt.json + printed table"""
import os, sys, json
import numpy as np, pandas as pd
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
def pay(o): o = np.asarray(o, float); return np.where(o < 0, 100 / -o, o / 100)
def imp(o): o = np.asarray(o, float); return np.where(o < 0, -o / (-o + 100), 100 / (o + 100))

def load():
    R = pd.read_parquet(f'{D}/preds.parquet'); F = pd.read_parquet(f'{D}/feat.parquet'); G = pd.read_parquet(f'{D}/games.parquet')
    h = F[F.is_home == 1].set_index('gid'); a = F[F.is_home == 0].set_index('gid')
    X = pd.DataFrame(dict(temp=h.temp, dome=h.dome, wind_out=h.wind_out, wind_in=h.wind_in, park_id=h.park_id, ump_idx=h.ump_idx, park=h.park,
                          pen3_h=a.pen_out3, pen3_a=h.pen_out3, pen1_h=a.pen_out1, pen1_a=h.pen_out1,          # a-row holds the HOME pen (the away batters' opponent)
                          spk_h=a.sp_k_s, spk_a=h.sp_k_s, spra_h=a.sp_ra, spra_a=h.sp_ra, spexp_h=a.sp_exp, spexp_a=h.sp_exp, day_game=h.day_game, sched7=h.sched7))
    R = R.merge(X, left_on='gid', right_index=True).merge(G[['gid', 'num', 'home_lg', 'away_lg', 'dow']], on='gid')
    R['date'] = pd.to_datetime(R.date); R = R.sort_values(['date', 'gid']).reset_index(drop=True)
    R['mkt_p'] = imp(R.ml_h) / (imp(R.ml_h) + imp(R.ml_a)); R['margin'] = R.hruns - R.aruns
    # previous game for each team (runs scored, result, streak) - earlier dates only
    tg = pd.concat([pd.DataFrame(dict(team=G.home, date=G.date, num=G.num, rs=G.hruns, ra=G.aruns)), pd.DataFrame(dict(team=G.away, date=G.date, num=G.num, rs=G.aruns, ra=G.hruns))]).sort_values(['team', 'date', 'num'])
    tg['win'] = (tg.rs > tg.ra).astype(int); tg['season'] = tg.date.dt.year
    g = tg.groupby(['team', 'season'])
    tg['prev_rs'] = g.rs.shift(1); tg['prev_ra'] = g.ra.shift(1); tg['prev_date'] = g.date.shift(1)
    st = []
    for _, d in g:
        s, o = 0, []
        for w in d.win.values: o.append(s); s = (s - 1 if s < 0 else -1) if w == 0 else (s + 1 if s > 0 else 1)
        st += o
    tg['streak'] = st   # entering the game: +n wins in a row, -n losses
    tg['wpct'] = (g.win.cumsum() - tg.win) / g.cumcount().clip(lower=1)
    for side, col in (('h', 'home'), ('a', 'away')):
        t = tg.rename(columns={c: f'{c}_{side}' for c in ('prev_rs', 'prev_ra', 'prev_date', 'streak', 'wpct')})
        R = R.merge(t[['team', 'date', 'num'] + [f'{c}_{side}' for c in ('prev_rs', 'prev_ra', 'prev_date', 'streak', 'wpct')]], left_on=[col, 'date', 'num'], right_on=['team', 'date', 'num'], how='left').drop(columns='team')
    return R

def grade_total(d, side):
    o = np.where(side == 'over', d.over.fillna(-110), d.under.fillna(-110)).astype(float)
    diff = (d.total - d.tot).values * np.where(side == 'over', 1, -1)
    return np.where(diff > 0, pay(o), np.where(diff < 0, -1.0, 0.0)), o
def grade_ml(d, side):
    o = np.where(side == 'home', d.ml_h, d.ml_a).astype(float); w = np.where(side == 'home', d.margin > 0, d.margin < 0)
    return np.where(w, pay(o), -1.0), o

def summarize(pr, o):
    pr = np.asarray(pr, float); o = np.asarray(o, float); dec = pr != 0; n = int(dec.sum())
    if n == 0: return dict(n=0)
    w = int((pr > 0).sum()); be = float(imp(o[dec]).mean()); hit = w / n
    sd = float(np.sqrt(be * (1 - be) / n))
    return dict(n=n, w=w, l=n - w, hit=round(hit, 4), be=round(be, 4), z=round((hit - be) / sd, 2), units=round(float(pr.sum()), 1), roi=round(float(pr.sum() / len(pr)), 4), avg_odds=int(round(float(np.median(o)))))

DISC, HOLD = (2013, 2020), (2021, 2025)
def test(R, name, market, mask, side, note=''):
    d = R[mask & R.season.between(DISC[0], HOLD[1])].copy()
    d = d[d.tot.notna() & ~d.tot_suspect] if market == 'total' else d[d.ml_h.notna() & ~d.ml_suspect]
    sd = side[d.index] if isinstance(side, pd.Series) else np.full(len(d), side)
    pr, o = (grade_total if market == 'total' else grade_ml)(d, np.asarray(sd))
    d['pr'] = pr; d['o'] = o
    out = dict(rule=name, market=market, note=note)
    for k, (a, b) in (('disc', DISC), ('hold', HOLD), ('all', (DISC[0], HOLD[1]))):
        x = d[d.season.between(a, b)]; out[k] = summarize(x.pr.values, x.o.values)
    out['seasons'] = {int(s): summarize(x.pr.values, x.o.values) for s, x in d.groupby('season')}
    h, c = out['hold'], out['disc']
    out['verdict'] = ('PASS' if h.get('n', 0) >= 150 and h['z'] >= 1.64 and h['roi'] > 0 and c.get('n', 0) >= 150 and c['roi'] > 0 and out['all']['z'] >= 2.0 else
                      'lean' if h.get('n', 0) >= 100 and h['roi'] > 0 and c.get('roi', -1) > -0.01 else 'fail')
    return out

def run(R):
    T = []
    side_ou = lambda m: pd.Series(np.where(m, 'over', 'under'), index=R.index)
    # ---- model total vs the closing total ----
    for lo in (0.54, 0.56, 0.58, 0.60):
        T.append(test(R, f'model_over_p{int(lo * 100)}', 'total', R.p_over >= lo, 'over', f'model P(over) >= {lo}'))
        T.append(test(R, f'model_under_p{int(lo * 100)}', 'total', R.p_over <= 1 - lo, 'under', f'model P(under) >= {lo}'))
    gap = R.lam_h + R.lam_a - R.tot
    for g in (0.75, 1.0, 1.5):
        T.append(test(R, f'model_gap_over_{g}', 'total', gap - 0.45 >= g, 'over', f'model total {g}+ runs over the line (after the mean-vs-median offset)'))
        T.append(test(R, f'model_gap_under_{g}', 'total', gap - 0.45 <= -g, 'under', f'model total {g}+ runs under the line'))
    # ---- model moneyline edge vs the no-vig closing price ----
    e = R.p_home - R.mkt_p; sd = pd.Series(np.where(e > 0, 'home', 'away'), index=R.index)
    for th in (0.02, 0.03, 0.04, 0.06, 0.08):
        T.append(test(R, f'model_ml_edge_{int(th * 100)}', 'ml', e.abs() >= th, sd, f'model win prob {int(th * 100)}+ pts above the no-vig close'))
    dog = pd.Series(np.where(sd == 'home', R.ml_h, R.ml_a), index=R.index) > 0
    T.append(test(R, 'model_ml_edge_4_dog', 'ml', (e.abs() >= 0.04) & dog, sd, 'same, underdogs only')); T.append(test(R, 'model_ml_edge_4_fav', 'ml', (e.abs() >= 0.04) & ~dog, sd, 'same, favorites only'))
    e2 = R.p_home_gbm - R.mkt_p; sd2 = pd.Series(np.where(e2 > 0, 'home', 'away'), index=R.index)
    T.append(test(R, 'both_models_ml_edge_3', 'ml', (e.abs() >= 0.03) & (e2.abs() >= 0.03) & (np.sign(e) == np.sign(e2)), sd, 'GLM and boosted model both 3+ pts off the close, same side'))
    # ---- weather ----
    wr = R.park_id == 'CHI11'
    T += [test(R, 'wrigley_wind_out10_over', 'total', wr & (R.wind_out >= 10), 'over', 'Wrigley, wind out 10+ mph'), test(R, 'wrigley_wind_in10_under', 'total', wr & (R.wind_in >= 10), 'under', 'Wrigley, wind in 10+ mph'),
          test(R, 'wind_out15_over', 'total', R.wind_out >= 15, 'over', 'any park, wind out 15+ mph'), test(R, 'wind_in12_under', 'total', R.wind_in >= 12, 'under', 'any park, wind in 12+ mph'),
          test(R, 'wind_in8_under', 'total', R.wind_in >= 8, 'under', 'any park, wind in 8+ mph'),
          test(R, 'hot90_over', 'total', (R.temp >= 90) & (R.dome == 0), 'over', '90F+ outdoors'), test(R, 'cold50_under', 'total', (R.temp <= 50) & (R.dome == 0), 'under', '50F or colder outdoors'),
          test(R, 'cold50_over', 'total', (R.temp <= 50) & (R.dome == 0), 'over', '50F or colder outdoors (market over-adjusts?)')]
    # ---- umpire, total size, bullpen ----
    T += [test(R, 'ump_over', 'total', R.ump_idx >= 1.025, 'over', "plate umpire's games run 2.5%+ above league"), test(R, 'ump_under', 'total', R.ump_idx <= 0.975, 'under', "plate umpire's games run 2.5%+ below league"),
          test(R, 'total_105_under', 'total', R.tot >= 10.5, 'under', 'closing total 10.5+'), test(R, 'total_7_over', 'total', R.tot <= 7, 'over', 'closing total 7 or lower'), test(R, 'total_7_under', 'total', R.tot <= 7, 'under', 'closing total 7 or lower'),
          test(R, 'tired_pens_over', 'total', (R.pen3_h >= 36) & (R.pen3_a >= 36), 'over', 'both bullpens threw 12+ innings in the last 3 days'),
          test(R, 'tired_pen_fade', 'ml', (R.pen3_h - R.pen3_a).abs() >= 21, pd.Series(np.where(R.pen3_h < R.pen3_a, 'home', 'away'), index=R.index), 'bet the team whose bullpen is 7+ innings fresher over 3 days'),
          test(R, 'sunday_day_under', 'total', (R.dow == 'Sun') & (R.day_game == 1), 'under', 'Sunday day games')]
    # ---- price / situation sides ----
    T += [test(R, 'home_dog', 'ml', R.ml_h >= 100, 'home', 'all home underdogs'), test(R, 'home_dog_150', 'ml', R.ml_h >= 150, 'home', 'home underdogs +150 or longer'),
          test(R, 'road_dog_150', 'ml', R.ml_a >= 150, 'away', 'road underdogs +150 or longer'), test(R, 'big_fav_200', 'ml', (R.ml_h <= -200) | (R.ml_a <= -200), pd.Series(np.where(R.ml_h <= -200, 'home', 'away'), index=R.index), 'favorites -200 or shorter'),
          test(R, 'fade_big_fav_250', 'ml', (R.ml_h <= -250) | (R.ml_a <= -250), pd.Series(np.where(R.ml_h <= -250, 'away', 'home'), index=R.index), 'dogs against favorites -250 or shorter'),
          test(R, 'small_home_fav', 'ml', R.ml_h.between(-130, -105), 'home', 'home favorites -105 to -130')]
    blow_h, blow_a = R.prev_rs_h >= 10, R.prev_rs_a >= 10
    T += [test(R, 'fade_after_10runs', 'ml', blow_h ^ blow_a, pd.Series(np.where(blow_h, 'away', 'home'), index=R.index), 'fade a team that scored 10+ in its last game'),
          test(R, 'after_10runs_under', 'total', blow_h | blow_a, 'under', 'a team scored 10+ in its last game'),
          test(R, 'back_after_shutout', 'ml', (R.prev_rs_h == 0) ^ (R.prev_rs_a == 0), pd.Series(np.where(R.prev_rs_h == 0, 'home', 'away'), index=R.index), 'back a team that was shut out in its last game'),
          test(R, 'losing_streak5_dog', 'ml', ((R.streak_h <= -5) & (R.ml_h > 0)) | ((R.streak_a <= -5) & (R.ml_a > 0)), pd.Series(np.where((R.streak_h <= -5) & (R.ml_h > 0), 'home', 'away'), index=R.index), 'underdog on a 5+ game losing streak'),
          test(R, 'win_streak5_fade', 'ml', (R.streak_h >= 5) ^ (R.streak_a >= 5), pd.Series(np.where(R.streak_h >= 5, 'away', 'home'), index=R.index), 'fade a team on a 5+ game win streak'),
          test(R, 'rookie_sp_fade', 'ml', (R.spexp_h < 4.6) ^ (R.spexp_a < 4.6), pd.Series(np.where(R.spexp_h < 4.6, 'away', 'home'), index=R.index), 'fade a starter with under ~100 career batters faced'),
          test(R, 'rookie_sp_back', 'ml', (R.spexp_h < 4.6) ^ (R.spexp_a < 4.6), pd.Series(np.where(R.spexp_h < 4.6, 'home', 'away'), index=R.index), 'back a starter with under ~100 career batters faced'),
          test(R, 'interleague_home', 'ml', R.home_lg != R.away_lg, 'home', 'interleague home teams')]
    return T

if __name__ == '__main__':
    R = load(); T = run(R)
    json.dump(T, open(f'{D}/rules_bt.json', 'w'), indent=1)
    f = lambda s: f"{s.get('w', 0)}-{s.get('l', 0)} {100 * s.get('hit', 0):.1f}% (be {100 * s.get('be', 0):.1f}) roi {100 * s.get('roi', 0):+.1f}% z {s.get('z', 0):+.1f}" if s.get('n') else 'none'
    print(f"{'rule':27s} {'verdict':5s} | {'discovery 2013-20':46s} | hold-out 2021-25")
    for t in T: print(f"{t['rule']:27s} {t['verdict']:5s} | {f(t['disc']):46s} | {f(t['hold'])}")
