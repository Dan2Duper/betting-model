"""Walk-forward MLB features: every number on a game's row is built ONLY from games played on earlier dates (no look-ahead).
Skill estimates are exponentially decayed sums (half-life H days) shrunk toward the league rate, so a rookie starts at league
average and a veteran's last two seasons dominate. One output row per team per game: that team's runs are the target.
Usage: python3 mlb/features.py [DATA_DIR]  -> DATA_DIR/feat.parquet"""
import os, sys
import numpy as np, pandas as pd
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata')
T0 = pd.Timestamp('2008-01-01')
def day(s): return (pd.to_datetime(s) - T0).dt.days.astype(float)

def dec_table(ev, key, cols, H):
    """Per (key, day): decayed running sums INCLUDING that day, stored scaled by 2^(day/H) so any later day can read them back."""
    a = ev.groupby([key, 'day'], as_index=False)[cols].sum().sort_values([key, 'day'])
    w = np.power(2.0, a.day.values / H)
    for c in cols: a[c] = (a[c].values * w)
    a[cols] = a.groupby(key)[cols].cumsum()
    return a.sort_values('day')
def asof(q, tab, key, cols, H, pre=''):
    """State of `key` at the START of q.day (strictly earlier days only)."""
    qq = q[[key, 'day']].reset_index().sort_values('day')
    m = pd.merge_asof(qq, tab, on='day', by=key, allow_exact_matches=False).set_index('index').reindex(q.index)
    sc = np.power(2.0, -q.day.values / H)
    return pd.DataFrame({pre + c: np.nan_to_num(m[c].values) * sc for c in cols}, index=q.index)

def build(G, pit, bat, hands):
    G = G.sort_values(['date', 'num']).reset_index(drop=True); G['day'] = day(G.date)
    pit = pit.copy(); bat = bat.copy(); pit['day'] = day(pit.date); bat['day'] = day(bat.date)
    # ---------- event tables ----------
    pit['BBH'] = pit.P_BB - pit.P_IBB + pit.P_HP; pit['OUT_GS'] = pit.P_OUT * (pit.P_GS > 0)
    PCOL = ['P_TBF', 'P_SO', 'BBH', 'P_HR', 'P_R', 'P_OUT', 'P_GS', 'OUT_GS', 'P_GO', 'P_AO', 'P_H']
    bat['DEN'] = bat.B_AB + bat.B_BB - bat.B_IBB + bat.B_SF + bat.B_HP
    s1 = bat.B_H - bat.B_2B - bat.B_3B - bat.B_HR
    bat['WN'] = 0.69 * (bat.B_BB - bat.B_IBB) + 0.72 * bat.B_HP + 0.88 * s1 + 1.25 * bat.B_2B + 1.58 * bat.B_3B + 2.03 * bat.B_HR
    bat['XB'] = bat.B_2B + 2 * bat.B_3B + 3 * bat.B_HR
    BCOL = ['DEN', 'WN', 'B_SO', 'XB', 'B_AB']
    HL, HS, HB, HP_, HT, HPK, HU = 450.0, 40.0, 500.0, 200.0, 150.0, 730.0, 1000.0
    pit['lg'] = 'MLB'; bat['lg'] = 'MLB'
    P_L = dec_table(pit, 'pid', PCOL, HL); P_S = dec_table(pit, 'pid', PCOL, HS)
    LP = dec_table(pit, 'lg', PCOL, 365.0); LB = dec_table(bat, 'lg', BCOL, 365.0)
    B_L = dec_table(bat, 'pid', BCOL, HB)
    rel = pit[pit.P_GS == 0]; PEN = dec_table(rel, 'team', PCOL, HP_)
    # bullpen workload: reliever outs by team-day (plain sums over the previous 1-3 days)
    ro = rel.groupby(['team', 'day']).P_OUT.sum()
    # team runs for / against, park and road run totals
    Gd = G[G.hruns.notna()]   # games already played; rows without a score (today's slate) only receive features
    tg = pd.concat([pd.DataFrame(dict(team=Gd.home, day=Gd.day, rs=Gd.hruns, ra=Gd.aruns, g=1.0)), pd.DataFrame(dict(team=Gd.away, day=Gd.day, rs=Gd.aruns, ra=Gd.hruns, g=1.0))])
    TM = dec_table(tg, 'team', ['rs', 'ra', 'g'], HT); TS = dec_table(tg, 'team', ['rs', 'ra', 'g'], 25.0)
    tg['lg'] = 'MLB'; LG_S = dec_table(tg, 'lg', ['rs', 'g'], 45.0); LG_L = dec_table(tg, 'lg', ['rs', 'g'], 400.0)
    G['tot'] = G.hruns + G.aruns
    pk = pd.DataFrame(dict(park=Gd.park, day=Gd.day, pr=(Gd.hruns + Gd.aruns).astype(float), pg=1.0)); PK = dec_table(pk, 'park', ['pr', 'pg'], HPK)
    rd = pd.DataFrame(dict(team=Gd.away, day=Gd.day, rr=(Gd.hruns + Gd.aruns).astype(float), rg=1.0)); RD = dec_table(rd, 'team', ['rr', 'rg'], HPK)
    # ---------- league baselines at each game day ----------
    q = pd.DataFrame(dict(lg='MLB', day=G.day)); lp = asof(q, LP, 'lg', PCOL, 365.0); lb = asof(q, LB, 'lg', BCOL, 365.0)
    lgs = asof(q, LG_S, 'lg', ['rs', 'g'], 45.0); lgl = asof(q, LG_L, 'lg', ['rs', 'g'], 400.0)
    L = pd.DataFrame(index=G.index)
    L['k'] = lp.P_SO / lp.P_TBF; L['bb'] = lp.BBH / lp.P_TBF; L['hr'] = lp.P_HR / lp.P_TBF; L['r'] = lp.P_R / lp.P_TBF; L['h'] = lp.P_H / lp.P_TBF
    L['gb'] = lp.P_GO / (lp.P_GO + lp.P_AO); L['ogs'] = lp.OUT_GS / lp.P_GS; L['woba'] = lb.WN / lb.DEN; L['bk'] = lb.B_SO / lb.DEN; L['iso'] = lb.XB / lb.B_AB
    L['rpg'] = lgl.rs / lgl.g; L['rpg_s'] = (lgs.rs + 4.5 * 30) / (lgs.g + 30)
    out = []
    for side, opp in (('h', 'a'), ('a', 'h')):
        team = G.home if side == 'h' else G.away; oteam = G.away if side == 'h' else G.home
        F = pd.DataFrame(dict(gid=G.gid, season=G.season, date=G.date, team=team, opp=oteam, is_home=float(side == 'h'),
                              runs=(G.hruns if side == 'h' else G.aruns).astype(float), opp_runs=(G.aruns if side == 'h' else G.hruns).astype(float)))
        # ----- opposing starter -----
        sp = G[opp + 'sp']; q = pd.DataFrame(dict(pid=sp, day=G.day))
        a = asof(q, P_L, 'pid', PCOL, HL); s = asof(q, P_S, 'pid', PCOL, HS)
        def rate(x, num, lg, K): return (x[num] + lg * K) / (x.P_TBF + K)
        F['sp_k'] = rate(a, 'P_SO', L.k, 100); F['sp_bb'] = rate(a, 'BBH', L.bb, 200); F['sp_hr'] = rate(a, 'P_HR', L.hr, 900)
        F['sp_ra'] = rate(a, 'P_R', L.r, 500); F['sp_h'] = rate(a, 'P_H', L.h, 600)
        F['sp_gb'] = (a.P_GO + L.gb * 150) / (a.P_GO + a.P_AO + 150)
        F['sp_ogs'] = (a.OUT_GS + L.ogs * 4) / (a.P_GS + 4)
        F['sp_exp'] = np.log1p(a.P_TBF)
        F['sp_k_s'] = rate(s, 'P_SO', F.sp_k, 60); F['sp_bb_s'] = rate(s, 'BBH', F.sp_bb, 100); F['sp_ra_s'] = rate(s, 'P_R', F.sp_ra, 150)
        for c, l in (('sp_k', 'k'), ('sp_bb', 'bb'), ('sp_hr', 'hr'), ('sp_ra', 'r'), ('sp_h', 'h'), ('sp_k_s', 'k'), ('sp_bb_s', 'bb'), ('sp_ra_s', 'r')): F[c] = F[c] / L[l]   # 1.0 = league
        F['sp_gb'] = F.sp_gb / L.gb; F['sp_ogs'] = F.sp_ogs / L.ogs
        last = pit[['pid', 'day']].drop_duplicates().sort_values('day').rename(columns={'day': 'last_day'}); last['day'] = last.last_day
        m = pd.merge_asof(q.reset_index().sort_values('day'), last, on='day', by='pid', allow_exact_matches=False).set_index('index').reindex(q.index)
        F['sp_rest'] = (q.day - m.last_day).clip(upper=12).fillna(12)
        thr = sp.map(hands.set_index('pid').throws); F['sp_left'] = (thr == 'L').astype(float)
        # ----- opposing bullpen -----
        q = pd.DataFrame(dict(team=oteam, day=G.day)); a = asof(q, PEN, 'team', PCOL, HP_)
        F['pen_k'] = rate(a, 'P_SO', L.k, 300) / L.k; F['pen_bb'] = rate(a, 'BBH', L.bb, 300) / L.bb; F['pen_hr'] = rate(a, 'P_HR', L.hr, 900) / L.hr; F['pen_ra'] = rate(a, 'P_R', L.r, 600) / L.r
        idx = pd.MultiIndex.from_arrays([oteam, G.day - 1]); o1 = ro.reindex(idx).fillna(0).values
        o2 = ro.reindex(pd.MultiIndex.from_arrays([oteam, G.day - 2])).fillna(0).values; o3 = ro.reindex(pd.MultiIndex.from_arrays([oteam, G.day - 3])).fillna(0).values
        F['pen_out1'] = o1; F['pen_out3'] = o1 + o2 + o3
        # ----- opposing team run prevention, own offense (team level) -----
        a = asof(q, TM, 'team', ['rs', 'ra', 'g'], HT); F['opp_ra'] = (a.ra + L.rpg * 40) / (a.g + 40) / L.rpg
        q2 = pd.DataFrame(dict(team=team, day=G.day)); a = asof(q2, TM, 'team', ['rs', 'ra', 'g'], HT); F['off_rs'] = (a.rs + L.rpg * 40) / (a.g + 40) / L.rpg
        a = asof(q2, TS, 'team', ['rs', 'ra', 'g'], 25.0); F['off_rs_s'] = (a.rs + L.rpg * 25) / (a.g + 25) / L.rpg
        # ----- own lineup -----
        ids = G[side + '_lineup'].str.split(',', expand=True); pos = G[side + '_pos'].str.split(',', expand=True)
        SW = np.array([4.65, 4.55, 4.43, 4.33, 4.24, 4.13, 4.01, 3.90, 3.77]); SW = SW / SW.sum()
        bh = hands.set_index('pid').bats; othr = G[opp + 'sp'].map(hands.set_index('pid').throws)
        lw = np.zeros(len(G)); lk = np.zeros(len(G)); li = np.zeros(len(G)); lpl = np.zeros(len(G)); lpa = np.zeros(len(G))
        for j in range(9):
            q = pd.DataFrame(dict(pid=ids[j], day=G.day)); a = asof(q, B_L, 'pid', BCOL, HB)
            w = (a.WN + L.woba * 220) / (a.DEN + 220); k = (a.B_SO + L.bk * 120) / (a.DEN + 120); iso = (a.XB + L.iso * 300) / (a.B_AB + 300)
            isp = (pos[j] == '1').values
            w = np.where(isp, 0.150, w / L.woba); k = np.where(isp, 1.9, k / L.bk); iso = np.where(isp, 0.25, iso / L.iso)
            b = ids[j].map(bh); adv = ((b == 'B') | ((b == 'L') & (othr == 'R')) | ((b == 'R') & (othr == 'L'))).astype(float).values
            w = np.where(isp, 0.150 / L.woba, w)
            lw += SW[j] * w; lk += SW[j] * k; li += SW[j] * iso; lpl += SW[j] * adv; lpa += SW[j] * np.log1p(a.DEN.values)
        F['lu_woba'] = lw; F['lu_k'] = lk; F['lu_iso'] = li; F['lu_plat'] = lpl; F['lu_exp'] = lpa
        # ----- park, umpire, weather, context (same for both sides) -----
        a = asof(pd.DataFrame(dict(park=G.park, day=G.day)), PK, 'park', ['pr', 'pg'], HPK); r = asof(pd.DataFrame(dict(team=G.home, day=G.day)), RD, 'team', ['rr', 'rg'], HPK)
        F['park'] = ((a.pr + 2 * L.rpg * 60) / (a.pg + 60)) / ((r.rr + 2 * L.rpg * 60) / (r.rg + 60))
        F['lg_rpg'] = L.rpg; F['lg_rpg_s'] = L.rpg_s / L.rpg
        dome = (G.sky == 'dome').values
        F['dome'] = dome.astype(float); F['temp'] = np.where(dome, 72.0, G.temp.fillna(72.0)); ws = np.where(dome, 0.0, G.windspeed.fillna(0.0))
        wd = G.winddir.fillna('unknown')
        F['wind_out'] = ws * wd.isin(['tocf', 'tolf', 'torf']).values; F['wind_in'] = ws * wd.isin(['fromcf', 'fromlf', 'fromrf']).values; F['wind_x'] = ws * wd.isin(['ltor', 'rtol']).values
        usedh = G.usedh.map({'true': 1.0, 'false': 0.0}); usedh = usedh.fillna(((G.home_lg == 'AL') | (G.season >= 2022) | (G.season == 2020)).astype(float))
        F['dh'] = usedh.values; F['sched7'] = (G.season.isin([2020, 2021]) & (G.num > 0)).astype(float).values
        F['day_game'] = (G.dn == 'D').astype(float).values; F['ump'] = G.ump.values; F['park_id'] = G.park.values; F['winddir'] = wd.values; F['windspeed'] = ws
        out.append(F)
    F = pd.concat(out, ignore_index=True)
    # umpire: total runs vs the league in his earlier plate games
    ug = pd.DataFrame(dict(ump=G.ump, day=G.day, ur=G.tot / (2 * L.rpg) / 1.0, ug=1.0)); ug = ug[(ug.ump != '') & ug.ur.notna()]
    UT = dec_table(ug, 'ump', ['ur', 'ug'], HU)
    a = asof(pd.DataFrame(dict(ump=F.ump, day=day(F.date))), UT, 'ump', ['ur', 'ug'], HU); F['ump_idx'] = (a.ur + 150) / (a.ug + 150)
    return F

FEATS = ['is_home', 'sp_k', 'sp_bb', 'sp_hr', 'sp_ra', 'sp_h', 'sp_gb', 'sp_ogs', 'sp_exp', 'sp_k_s', 'sp_bb_s', 'sp_ra_s', 'sp_rest', 'sp_left',
         'pen_k', 'pen_bb', 'pen_hr', 'pen_ra', 'pen_out1', 'pen_out3', 'opp_ra', 'off_rs', 'off_rs_s', 'lu_woba', 'lu_k', 'lu_iso', 'lu_plat', 'lu_exp',
         'park', 'lg_rpg', 'lg_rpg_s', 'dome', 'temp', 'wind_out', 'wind_in', 'wind_x', 'dh', 'sched7', 'day_game', 'ump_idx']
if __name__ == '__main__':
    G = pd.read_parquet(f'{D}/games.parquet'); pit = pd.read_parquet(f'{D}/pit.parquet'); bat = pd.read_parquet(f'{D}/bat.parquet'); hands = pd.read_parquet(f'{D}/hands.parquet').drop_duplicates('pid')
    F = build(G, pit, bat, hands); F = F[F.season >= 2010]
    F.to_parquet(f'{D}/feat.parquet'); print(F.shape); print(F[FEATS].describe().T[['mean', 'std', 'min', 'max']].round(3).to_string())
