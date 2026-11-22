"""Candidate MLB variables, each built walk-forward (earlier dates only) and judged two ways:
  A. does it make the run model better out of sample (season S fitted on seasons < S), and
  B. does it say anything the CLOSING line does not already know (discovery 2013-20, hold-out 2021-25), then bet at closing prices.
Groups: wind by park, bullpen fatigue (pitches, back-to-back arms), umpire zone (K/BB), lineup vs the team's usual one, travel/rest.
Usage: python3 mlb/research/variables.py [DATA_DIR]   -> mlb/research/variables_2013_2025.json + DATA_DIR/feat_x.parquet"""
import os, sys, json, warnings
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from features import FEATS, dec_table, asof, day
from model import fit_glm, pred_glm, imp
from backtest import grade_total, grade_ml, summarize
from sklearn.linear_model import LogisticRegression
warnings.filterwarnings('ignore')
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata')
ZONE = dict(BAL=0, BOS=0, NYA=0, NYN=0, PHI=0, PIT=0, TOR=0, WAS=0, ATL=0, MIA=0, FLO=0, TBA=0, CLE=0, DET=0, CIN=0, CHA=1, CHN=1, MIL=1, MIN=1, KCA=1, SLN=1, HOU=1, TEX=1,
            COL=2, ARI=3, LAN=3, SDN=3, SFN=3, SEA=3, OAK=3, ANA=3, ATH=3)   # 0 Eastern .. 3 Pacific (Arizona keeps Pacific time in season)

def extra(G, pit, bat, F):
    """New columns for F (one row per batting team per game). Everything uses strictly earlier days."""
    G = G.sort_values(['date', 'num']).reset_index(drop=True); G['day'] = day(G.date); pit = pit.reset_index(drop=True); pit['day'] = day(pit.date)
    Gi = G.set_index('gid'); X = pd.DataFrame(index=F.index); fd = day(F.date)
    # ---------- bullpen fatigue of the pitching side (F.opp): pitches thrown by relievers in the last 1 / 3 days, arms used on both of the last two days ----------
    rel = pit[pit.P_GS == 0]; rp = rel.groupby(['team', 'day']).P_PITCH.sum()
    get = lambda s, team, d: s.reindex(pd.MultiIndex.from_arrays([team, d])).fillna(0).values
    p1 = get(rp, F.opp, fd - 1); p2 = get(rp, F.opp, fd - 2); p3 = get(rp, F.opp, fd - 3)
    X['pen_pit1'] = p1; X['pen_pit3'] = p1 + p2 + p3
    a = rel[['team', 'pid', 'day']].drop_duplicates(); b = a.assign(day=a.day + 1)            # b: pitched the day before `day`
    b2b = a.merge(b, on=['team', 'pid', 'day']).groupby(['team', 'day']).size()               # arms that pitched on `day` and `day - 1`
    X['pen_b2b'] = get(b2b, F.opp, fd - 1)
    # how much of the pen's usual work those tired arms carry: share of the team's reliever outs (last ~30 days) thrown by arms used yesterday
    rel = rel.assign(o=rel.P_OUT.astype(float)); RO = dec_table(rel, 'pid', ['o'], 30.0); TO = dec_table(rel, 'team', ['o'], 30.0)
    y = rel[['team', 'pid', 'day']].drop_duplicates().reset_index(drop=True); y = y.assign(day=y.day + 1)             # query: arm's standing on the morning after he pitched
    w = asof(y, RO, 'pid', ['o'], 30.0).o.values; t = asof(y, TO, 'team', ['o'], 30.0).o.values
    y['share'] = np.where(t > 0, w / np.maximum(t, 1e-9), 0.0); used = y.groupby(['team', 'day']).share.sum()
    X['pen_used_share'] = get(used, F.opp, fd)                                                 # 0 = nobody pitched yesterday, 0.5 = arms carrying half the workload did
    # ---------- umpire strike zone: strikeouts and walks per batter in his earlier plate games, vs the league ----------
    gs = pit.groupby('gid')[['P_SO', 'P_BB', 'P_TBF']].sum(); u = G[['gid', 'ump', 'day']].merge(gs, left_on='gid', right_index=True); u = u[u.ump != '']; u['lg'] = 'MLB'
    UT = dec_table(u, 'ump', ['P_SO', 'P_BB', 'P_TBF'], 1000.0); LT = dec_table(u, 'lg', ['P_SO', 'P_BB', 'P_TBF'], 365.0)
    q = pd.DataFrame(dict(ump=F.ump.values, lg='MLB', day=fd.values), index=F.index); a_ = asof(q, UT, 'ump', ['P_SO', 'P_BB', 'P_TBF'], 1000.0); l_ = asof(q, LT, 'lg', ['P_SO', 'P_BB', 'P_TBF'], 365.0)
    lk = l_.P_SO / l_.P_TBF.clip(lower=1); lb = l_.P_BB / l_.P_TBF.clip(lower=1)
    X['ump_k'] = ((a_.P_SO + lk * 4000) / (a_.P_TBF + 4000) / lk).fillna(1.0); X['ump_bb'] = ((a_.P_BB + lb * 4000) / (a_.P_TBF + 4000) / lb).fillna(1.0)
    # ---------- lineup vs the team's usual lineup (decayed mean of its own earlier lineups, half-life 20 days) ----------
    lu = pd.DataFrame(dict(team=F.team.values, day=fd.values, w=F.lu_woba.values, n=1.0)); lu = lu[F.runs.notna().values]
    LU = dec_table(lu, 'team', ['w', 'n'], 20.0); a_ = asof(pd.DataFrame(dict(team=F.team.values, day=fd.values), index=F.index), LU, 'team', ['w', 'n'], 20.0)
    X['lu_delta'] = np.where(a_.n > 3, F.lu_woba - a_.w / a_.n.clip(lower=1e-9), 0.0)          # + = stronger lineup than usual
    # ---------- travel and rest (batting team) ----------
    tg = pd.concat([pd.DataFrame(dict(gid=G.gid, team=G.home, day=G.day, num=G.num, at=G.home, dn=G.dn)), pd.DataFrame(dict(gid=G.gid, team=G.away, day=G.day, num=G.num, at=G.home, dn=G.dn))]).sort_values(['team', 'day', 'num'])
    tg['season'] = (tg.day // 365.25).astype(int); g = tg.groupby(['team', 'season'])
    tg['pday'] = g.day.shift(1); tg['pat'] = g['at'].shift(1); tg['pdn'] = g.dn.shift(1)
    gap = (tg.day - tg.pday).fillna(3)
    tg['rest'] = (gap - 1).clip(0, 2)                                                          # 0 = played yesterday (or earlier today), 1 = one day off, 2 = two or more
    z = tg['at'].map(ZONE).fillna(1); pz = tg.pat.map(ZONE).fillna(z); mv = (z - pz) * (gap <= 2)
    tg['tz_east'] = (-mv).clip(lower=0); tg['tz_west'] = mv.clip(lower=0)                      # time zones crossed since the previous game (no more than one day off)
    tg['day_after_night'] = ((gap == 1) & (tg.dn == 'D') & (tg.pdn == 'N')).astype(float)
    tg['dh2'] = ((gap == 0) | (tg.num == 2)).astype(float)
    road = (tg.team != tg['at']).astype(int); blk = (road != road.groupby([tg.team, tg.season]).shift(1)).cumsum(); tg['trip_len'] = road.groupby([tg.team, tg.season, blk]).cumsum().clip(upper=10)
    T = tg.set_index(['gid', 'team'])[['rest', 'tz_east', 'tz_west', 'day_after_night', 'dh2', 'trip_len']]
    me = T.reindex(pd.MultiIndex.from_arrays([F.gid, F.team])); op = T.reindex(pd.MultiIndex.from_arrays([F.gid, F.opp]))
    for c in T.columns: X[c] = me[c].values; X['o_' + c] = op[c].values
    # ---------- wind by park: each park's own response to wind blowing out (+) / in (-), learned from its earlier games ----------
    Gd = G[G.hruns.notna()].copy(); h = F[F.is_home == 1].set_index('gid')
    Gd['sw'] = (h.wind_out - h.wind_in).reindex(Gd.gid).values; Gd['rr'] = ((Gd.hruns + Gd.aruns) / (2 * h.lg_rpg.reindex(Gd.gid).values * h.park.reindex(Gd.gid).values) - 1.0)
    Gd = Gd[Gd.sw.notna() & Gd.rr.notna()]; Gd['xy'] = Gd.sw * Gd.rr; Gd['xx'] = Gd.sw ** 2; Gd['lg'] = 'MLB'
    PW = dec_table(Gd, 'park', ['xy', 'xx'], 1500.0); LW = dec_table(Gd, 'lg', ['xy', 'xx'], 1500.0)
    q = pd.DataFrame(dict(park=F.park_id.values, lg='MLB', day=fd.values), index=F.index); a_ = asof(q, PW, 'park', ['xy', 'xx'], 1500.0); l_ = asof(q, LW, 'lg', ['xy', 'xx'], 1500.0)
    lg_slope = l_.xy / l_.xx.clip(lower=1.0); slope = (a_.xy + lg_slope * 6000.0) / (a_.xx + 6000.0)                 # shrunk to the league slope (6000 mph^2 ~ 60 windy games)
    sw = F.wind_out - F.wind_in
    X['park_wind'] = (slope - lg_slope) * sw                                                    # the park's EXTRA response beyond the league-wide wind terms already in the model
    X['park_wind_slope'] = slope
    return X.fillna(0.0)

GROUPS = {
    'wind by park': ['park_wind'],
    'bullpen fatigue': ['pen_pit1', 'pen_pit3', 'pen_b2b', 'pen_used_share'],
    'umpire zone': ['ump_k', 'ump_bb'],
    'lineup vs usual': ['lu_delta'],
    'travel / rest': ['rest', 'tz_east', 'tz_west', 'day_after_night', 'dh2', 'trip_len', 'o_rest', 'o_tz_east', 'o_tz_west', 'o_day_after_night', 'o_dh2', 'o_trip_len'],
}

def walk(F, feats, first=2013):
    P = []
    for S in sorted(s for s in F.season.unique() if s >= first):
        tr = F[F.season < S]; te = F[F.season == S]; sc_mu = tr[feats].values.mean(0); sc_sd = tr[feats].values.std(0) + 1e-9
        from sklearn.linear_model import PoissonRegressor
        m = PoissonRegressor(alpha=0.002, max_iter=500).fit((tr[feats].values - sc_mu) / sc_sd, tr.runs.values)
        P.append(pd.DataFrame(dict(gid=te.gid.values, season=S, is_home=te.is_home.values, lam=m.predict((te[feats].values - sc_mu) / sc_sd))))
    P = pd.concat(P); h = P[P.is_home == 1].set_index('gid'); a = P[P.is_home == 0].set_index('gid')
    return pd.DataFrame(dict(season=h.season, lam_h=h.lam, lam_a=a.lam)).reset_index()

def score(R):
    """Out-of-sample quality by season: Poisson deviance of team runs, total MAE, winner log loss (calibrated on earlier seasons)."""
    R = R.copy(); R['z'] = np.log(R.lam_h / R.lam_a); out = {}
    dev = lambda y, m: 2 * (np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / m), 0) - (y - m))
    for S in sorted(R.season.unique())[1:]:
        tr = R[R.season < S]; te = R[R.season == S]
        m = LogisticRegression(C=1e6).fit(tr[['z']].values, tr.home_win.values); p = m.predict_proba(te[['z']].values)[:, 1]; y = te.home_win.values
        out[int(S)] = dict(dev=float((dev(te.hruns.values, te.lam_h.values).mean() + dev(te.aruns.values, te.lam_a.values).mean()) / 2), mae=float((te.lam_h + te.lam_a - te.hruns - te.aruns).abs().mean()),
                           ll=float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()))
    return out

def zreg(x, y):
    """z of the slope of y on x (one variable, with intercept)."""
    x = np.asarray(x, float); y = np.asarray(y, float); ok = np.isfinite(x) & np.isfinite(y); x, y = x[ok], y[ok]
    if len(x) < 200 or x.std() == 0: return 0.0, 0.0, len(x)
    xc = x - x.mean(); b = (xc * (y - y.mean())).sum() / (xc ** 2).sum(); r = y - y.mean() - b * xc; se = np.sqrt((r ** 2).sum() / (len(x) - 2) / (xc ** 2).sum())
    return float(b), float(b / se), int(len(x))

if __name__ == '__main__':
    G = pd.read_parquet(f'{D}/games.parquet'); pit = pd.read_parquet(f'{D}/pit.parquet'); bat = pd.read_parquet(f'{D}/bat.parquet')
    F = pd.read_parquet(f'{D}/feat.parquet').reset_index(drop=True)
    X = extra(G, pit, bat, F); F = pd.concat([F, X], axis=1); F.to_parquet(f'{D}/feat_x.parquet')
    print(X.describe().T[['mean', 'std', 'min', 'max']].round(3).to_string())
    keep = G[['gid', 'hruns', 'aruns', 'ml_h', 'ml_a', 'tot', 'over', 'under', 'ml_suspect', 'tot_suspect']]
    res = dict(built=str(pd.Timestamp.now().date()), note='A = run model out of sample (each season fitted on earlier seasons). B = against the closing line.', groups={})
    def prep(feats):
        R = walk(F, feats).merge(keep, on='gid'); R['home_win'] = (R.hruns > R.aruns).astype(int); return R
    R0 = prep(FEATS); S0 = score(R0)
    print('\nA. RUN MODEL, out of sample (negative = better than the current model)')
    print(f"{'group':18s} {'deviance':>10s} {'total MAE':>10s} {'winner logloss':>15s} {'seasons better (dev / ll)':>26s}")
    allnew = []
    for name, cols in list(GROUPS.items()) + [('ALL of them', None)]:
        cols = cols or allnew; allnew += [c for c in cols if c not in allnew]
        S1 = score(prep(FEATS + cols)); ss = sorted(S1)
        d = {k: float(np.mean([S1[s][k] - S0[s][k] for s in ss])) for k in ('dev', 'mae', 'll')}
        better = (sum(S1[s]['dev'] < S0[s]['dev'] for s in ss), sum(S1[s]['ll'] < S0[s]['ll'] for s in ss))
        # paired t on the per-season log-loss differences
        dl = np.array([S1[s]['ll'] - S0[s]['ll'] for s in ss]); t = float(dl.mean() / (dl.std(ddof=1) / np.sqrt(len(dl)))) if dl.std() > 0 else 0.0
        print(f"{name:18s} {d['dev']:+10.5f} {d['mae']:+10.4f} {d['ll']:+15.5f} {better[0]:>12d}/{len(ss)} {better[1]:>5d}/{len(ss)}   t(ll) {t:+.1f}")
        res['groups'][name] = dict(cols=cols, d_deviance=round(d['dev'], 6), d_total_mae=round(d['mae'], 5), d_winner_logloss=round(d['ll'], 6), seasons=len(ss), seasons_better_dev=better[0], seasons_better_ll=better[1], t_ll=round(t, 2),
                                   keep=bool(d['dev'] < 0 and d['ll'] < 0 and better[0] >= 9 and better[1] >= 9))
    # ---------- B. what the closing line misses ----------
    h = F[F.is_home == 1].set_index('gid'); a = F[F.is_home == 0].set_index('gid')   # h row: home batters vs the AWAY pitching staff
    R = R0.merge(pd.DataFrame(dict(
        park_wind=h.park_wind, wind_signed=h.wind_out - h.wind_in, park_wind_slope=h.park_wind_slope,
        pen_pit3_sum=h.pen_pit3 + a.pen_pit3, pen_b2b_sum=h.pen_b2b + a.pen_b2b, pen_used_sum=h.pen_used_share + a.pen_used_share,
        pen_pit3_diff=h.pen_pit3 - a.pen_pit3, pen_used_diff=h.pen_used_share - a.pen_used_share, pen_b2b_diff=h.pen_b2b - a.pen_b2b,      # + = the AWAY pen is more tired (helps home)
        ump_k=h.ump_k, ump_bb=h.ump_bb, lu_delta_sum=h.lu_delta + a.lu_delta, lu_delta_diff=h.lu_delta - a.lu_delta,
        rest_diff=h.rest - a.rest, tz_east_away=a.tz_east, tz_west_away=a.tz_west, tz_east_home=h.tz_east, tz_west_home=h.tz_west, dan_sum=h.day_after_night + a.day_after_night, dan_diff=h.day_after_night - a.day_after_night,
        trip_len_away=a.trip_len, dh2=h.dh2)), left_on='gid', right_index=True)
    R['total'] = R.hruns + R.aruns; R['margin'] = R.hruns - R.aruns; ih, ia = imp(R.ml_h), imp(R.ml_a); R['mkt_p'] = ih / (ih + ia)
    R['over_y'] = np.where(R.total > R.tot, 1.0, np.where(R.total < R.tot, 0.0, np.nan)); R['side_y'] = R.home_win - R.mkt_p
    TOT = ['park_wind', 'wind_signed', 'pen_pit3_sum', 'pen_b2b_sum', 'pen_used_sum', 'ump_k', 'ump_bb', 'lu_delta_sum', 'dan_sum', 'dh2']
    SIDE = ['pen_pit3_diff', 'pen_used_diff', 'pen_b2b_diff', 'lu_delta_diff', 'rest_diff', 'tz_east_away', 'tz_west_away', 'tz_east_home', 'tz_west_home', 'dan_diff', 'trip_len_away']
    print('\nB. VS THE CLOSING LINE: slope z-scores (|z| >= 2 in BOTH windows with the same sign = the line misses it)')
    print(f"{'variable':16s} {'market':7s} {'2013-20 z':>10s} {'2021-25 z':>10s}   verdict"); res['vs_close'] = []
    for mk, cols, yv, ok in (('total', TOT, 'over_y', R.tot.notna() & ~R.tot_suspect), ('side', SIDE, 'side_y', R.mkt_p.notna() & ~R.ml_suspect)):
        for c in cols:
            d = R[ok & R.season.between(2013, 2020)]; hd = R[ok & R.season.between(2021, 2025)]
            b1, z1, n1 = zreg(d[c], d[yv]); b2, z2, n2 = zreg(hd[c], hd[yv])
            v = 'MISSED BY THE LINE' if abs(z1) >= 2 and abs(z2) >= 2 and np.sign(z1) == np.sign(z2) else 'priced in'
            print(f"{c:16s} {mk:7s} {z1:+10.2f} {z2:+10.2f}   {v}"); res['vs_close'].append(dict(var=c, market=mk, z_disc=round(z1, 2), z_hold=round(z2, 2), n_disc=n1, n_hold=n2, verdict=v))
    # ---------- bets at closing prices for the usual suspects ----------
    f = lambda s: f"{s.get('w', 0)}-{s.get('l', 0)} {100 * s.get('hit', 0):.1f}% (be {100 * s.get('be', 0):.1f}) roi {100 * s.get('roi', 0):+.1f}% z {s.get('z', 0):+.1f}" if s.get('n') else 'none'
    def bet(name, market, mask, side, note):
        d = R[mask].copy(); d = d[d.tot.notna() & ~d.tot_suspect] if market == 'total' else d[d.ml_h.notna() & ~d.ml_suspect]
        sd = side[d.index].values if isinstance(side, pd.Series) else np.full(len(d), side); pr, o = (grade_total if market == 'total' else grade_ml)(d, sd); d = d.assign(pr=pr, o=o)
        A, B = summarize(d[d.season.between(2013, 2020)].pr, d[d.season.between(2013, 2020)].o), summarize(d[d.season.between(2021, 2025)].pr, d[d.season.between(2021, 2025)].o)
        v = 'PASS' if B.get('n', 0) >= 150 and B['z'] >= 1.64 and B['roi'] > 0 and A.get('n', 0) >= 150 and A['roi'] > 0 else 'fail'
        print(f"{name:28s} {v:4s} | {f(A):46s} | {f(B)}"); res.setdefault('bets', []).append(dict(rule=name, market=market, note=note, disc=A, hold=B, verdict=v))
    S = lambda m: pd.Series(np.where(m, 'home', 'away'), index=R.index)
    print(f"\n{'bet at the closing price':28s} {'':4s} | {'discovery 2013-20':46s} | hold-out 2021-25")
    q = R.park_wind_slope.quantile(0.8)
    bet('windy_park_out10_over', 'total', (R.park_wind_slope >= q) & (R.wind_signed >= 10), 'over', 'parks in the top fifth for wind response, wind out 10+ mph')
    bet('windy_park_in10_under', 'total', (R.park_wind_slope >= q) & (R.wind_signed <= -10), 'under', 'same parks, wind in 10+ mph')
    bet('park_wind_plus_over', 'total', R.park_wind >= 0.03, 'over', "park's own wind response adds 3%+ runs"); bet('park_wind_minus_under', 'total', R.park_wind <= -0.03, 'under', "park's own wind response takes 3%+ runs off")
    bet('both_pens_gassed_over', 'total', R.pen_pit3_sum >= R.pen_pit3_sum.quantile(0.9), 'over', 'both bullpens: combined reliever pitches over 3 days in the top 10%')
    bet('pens_b2b_over', 'total', R.pen_b2b_sum >= 4, 'over', '4+ relievers (both teams) worked each of the last two days')
    bet('fresh_pen_side', 'ml', R.pen_used_diff.abs() >= 0.35, S(R.pen_used_diff > 0), "back the team whose opponent used far more of its bullpen yesterday")
    bet('tired_pen_pitches_fade', 'ml', R.pen_pit3_diff.abs() >= 90, S(R.pen_pit3_diff > 0), 'back the team whose bullpen threw 90+ fewer pitches over 3 days')
    bet('ump_k_under', 'total', R.ump_k >= 1.03, 'under', 'plate umpire strikeout rate 3%+ above league'); bet('ump_bb_over', 'total', R.ump_bb >= 1.04, 'over', 'plate umpire walk rate 4%+ above league')
    bet('ump_k_low_over', 'total', R.ump_k <= 0.97, 'over', 'plate umpire strikeout rate 3%+ below league')
    bet('lineup_short_fade', 'ml', R.lu_delta_diff.abs() >= 0.06, S(R.lu_delta_diff > 0), 'back the team whose lineup is stronger vs its usual one (other side resting regulars)')
    bet('lineups_short_under', 'total', R.lu_delta_sum <= -0.08, 'under', 'both lineups weaker than usual'); bet('lineups_full_over', 'total', R.lu_delta_sum >= 0.06, 'over', 'both lineups stronger than usual')
    bet('rested_vs_tired', 'ml', R.rest_diff.abs() >= 1, S(R.rest_diff > 0), 'back the team with more days off')
    bet('away_flew_east', 'ml', R.tz_east_away >= 2, 'home', 'road team crossed 2+ time zones eastward since its last game'); bet('away_flew_west', 'ml', R.tz_west_away >= 2, 'home', 'road team crossed 2+ time zones westward')
    bet('home_back_from_west', 'ml', R.tz_east_home >= 2, 'away', 'home team just flew 2+ zones east to get home'); bet('day_after_night_under', 'total', R.dan_sum >= 2, 'under', 'day game after a night game for both teams')
    bet('long_road_trip_fade', 'ml', R.trip_len_away >= 8, 'home', 'road team on its 8th+ straight road game'); bet('doubleheader_g2_under', 'total', R.dh2 == 1, 'under', 'second game of a doubleheader')
    json.dump(res, open(f'{HERE}/variables_2013_2025.json', 'w'), indent=1); print('\nsaved mlb/research/variables_2013_2025.json')
