"""MLB DFS projections: mean (proj), floor (15th pct) and ceiling (85th pct) DraftKings classic points for every starting hitter and
starting pitcher, built only from games played on earlier dates (same rule as mlb/features.py).

  Hitters: decayed + shrunk per-PA skill (DK points, wOBA pieces, K, ISO, HR, walks, steals, runs, RBI), his DK-points history and spread,
           his split against the starter's hand, lineup slot, and the game from the run model: opposing starter + bullpen, park (runs and HR),
           weather, platoon, team implied runs (lam) and the other side's.
  Pitchers: his starts (DK points, outs, pitches, strikeouts per start, spread), his skill rates (mlb/features sp_*), rest, the opposing
           lineup (wOBA, K rate, ISO, platoon), runs the model expects him to allow (opponent lam) and his team's win odds (log lam ratio).
  Engine: gradient-boosted trees (squared error for proj, quantile 0.15 / 0.85 for floor / ceiling), like dfs/nfl_model.py.

Usage:
  python3 dfs/mlb_proj.py backtest [--cache DATA_BRANCH_MLB_DIR] [--test 2025 2026] [--out DIR]
      Walk-forward: each test season is projected by models fitted on earlier seasons only. 2026 needs the Stats API cache
      (data branch mlb/live_*.parquet written after the runs/RBI/wins columns were added).
  python3 dfs/mlb_proj.py lookahead [--date 2025-07-15]   rebuild with history cut at that date; projections must match to 0."""
import os, sys, json, argparse, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as HGB
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'mlb'))
from features import build, dec_table, asof, day, FEATS
warnings.filterwarnings('ignore')

# ---------------- DraftKings classic scoring ----------------
def dk_bat(b):
    s1 = b.B_H - b.B_2B - b.B_3B - b.B_HR
    return 3 * s1 + 5 * b.B_2B + 8 * b.B_3B + 10 * b.B_HR + 2 * b.B_RBI + 2 * b.B_R + 2 * b.B_BB + 2 * b.B_HP + 5 * b.B_SB
def dk_pit(p):
    cg = (p.P_CG > 0).astype(float); sho = (p.P_SHO > 0).astype(float) * cg; nh = cg * (p.P_H == 0)
    return 0.75 * p.P_OUT + 2 * p.P_SO + 4 * p.P_W - 2 * p.P_ER - 0.6 * (p.P_H + p.P_BB + p.P_HP) + 2.5 * cg + 2.5 * sho + 5 * nh
DK_POS = {'2': 'C', '3': '1B', '4': '2B', '5': '3B', '6': 'SS', '7': 'OF', '8': 'OF', '9': 'OF', '10': 'DH', 'D': 'DH', '1': 'P'}

# ---------------- data ----------------
def load(cache=None, hist_last=None, hist_dir=None):
    """mlb/hist (Retrosheet) + optionally the Stats API cache (later seasons), ids remapped the way mlb/live.py does."""
    H = {n: pd.read_parquet(f'{hist_dir or ROOT + "/mlb/hist"}/{n}.parquet') for n in ('games', 'pit', 'bat', 'hands')}
    for n in ('games', 'pit', 'bat'): H[n]['date'] = pd.to_datetime(H[n].date)
    if hist_last:
        for n in ('games', 'pit', 'bat'): H[n] = H[n][H[n].date < f'{hist_last + 1}-01-01']
    G, pit, bat, hands = H['games'], H['pit'], H['bat'], H['hands']
    if cache and os.path.exists(f'{cache}/live_games.parquet'):
        X = json.load(open(f'{cache}/xwalk.json'))
        T = {n: pd.read_parquet(f'{cache}/live_{n}.parquet') for n in ('games', 'pit', 'bat')}
        if 'B_RBI' not in T['bat'].columns: raise SystemExit(f'{cache}: Stats API cache has no runs/RBI/wins yet (the MLB model workflow refetches it)')
        last = int(G.season.max()); LG = T['games'].copy(); LG = LG[pd.to_datetime(LG.date).dt.year > last]
        rp = lambda i: X['players'].get(str(i)[1:], i) if str(i).startswith('m') else i
        LG['park'] = [X['venues'].get(str(p)[1:], p) if str(p).startswith('v') else p for p in LG.park]
        LG['ump'] = [X['umps'].get(str(u)[1:], u) if str(u).startswith('m') else u for u in LG.ump]
        for c in ('asp', 'hsp'): LG[c] = LG[c].map(rp)
        for c in ('a_lineup', 'h_lineup'): LG[c] = [','.join(rp(i) for i in str(v).split(',')) for v in LG[c]]
        tp = T['pit'].assign(pid=T['pit'].pid.map(rp)); tb = T['bat'].assign(pid=T['bat'].pid.map(rp))
        tp = tp[tp.gid.isin(LG.gid)]; tb = tb[tb.gid.isin(LG.gid)]
        G = pd.concat([G, LG.reindex(columns=G.columns)], ignore_index=True)
        for n, t in (('pit', tp), ('bat', tb)): t['date'] = pd.to_datetime(t.date)
        pit = pd.concat([pit, tp.reindex(columns=pit.columns)], ignore_index=True); bat = pd.concat([bat, tb.reindex(columns=bat.columns)], ignore_index=True)
        hands = pd.concat([hands, pd.DataFrame([(q, v[0], v[1]) for k, v in X.get('hands', {}).items() for q in {k, rp(k)}], columns=['pid', 'bats', 'throws'])]).drop_duplicates('pid')
    G = G.copy(); G['date'] = pd.to_datetime(G.date); G['num'] = G.num.astype(int); G['season'] = G.date.dt.year
    for c in ('ump', 'asp', 'hsp', 'sky', 'winddir', 'usedh', 'a_lineup', 'h_lineup', 'a_pos', 'h_pos'): G[c] = G[c].fillna('')
    for t in (pit, bat):
        for c in t.columns:
            if c.startswith(('P_', 'B_')): t[c] = t[c].fillna(0).astype(float)
    return G, pit, bat, hands.drop_duplicates('pid')

# ---------------- run model (walk-forward lam per team-game) ----------------
def run_lam(F, seasons_out, model_json=None):
    """lam for every F row in seasons_out. Each season is predicted by a Poisson GLM fitted on earlier seasons only;
    model_json (mlb/model.json, the live model) can be given for seasons after the ones it was trained on."""
    sys.path.insert(0, os.path.join(ROOT, 'mlb')); from model import fit_glm, pred_glm
    F = F.copy(); F['lam'] = np.nan
    for S in seasons_out:
        te = F.season == S
        if model_json and S > int(model_json['trained'].split('-')[1]): M = model_json
        else:
            tr = F[(F.season < S) & F.runs.notna() & np.isfinite(F[FEATS]).all(axis=1)]
            M = fit_glm(tr[FEATS].values, tr.runs.values)
        F.loc[te, 'lam'] = pred_glm(M, np.nan_to_num(F.loc[te, FEATS].values, nan=1.0))
    o = F[['gid', 'team', 'lam']].rename(columns={'team': 'opp', 'lam': 'opp_lam'})
    return F.merge(o, on=['gid', 'opp'], how='left')

# ---------------- player rows ----------------
def starters(G, hands):
    """One row per starting hitter (9 per side) and one per starting pitcher, from the lineup strings (works for an unplayed slate too)."""
    th = hands.set_index('pid').throws; bh = hands.set_index('pid').bats; H, P = [], []
    for side, opp in (('h', 'a'), ('a', 'h')):
        team = G.home if side == 'h' else G.away; oteam = G.away if side == 'h' else G.home
        ids = G[side + '_lineup'].str.split(',', expand=True).reindex(columns=range(9)); pos = G[side + '_pos'].str.split(',', expand=True).reindex(columns=range(9))
        for j in range(9):
            H.append(pd.DataFrame(dict(gid=G.gid, date=G.date, season=G.season, team=team, opp=oteam, pid=ids[j], slot=j + 1, pos=pos[j], osp=G[opp + 'sp'])))
        P.append(pd.DataFrame(dict(gid=G.gid, date=G.date, season=G.season, team=team, opp=oteam, pid=G[side + 'sp'], ospn=G[opp + 'sp'])))
    H = pd.concat(H, ignore_index=True); P = pd.concat(P, ignore_index=True)
    H = H[H.pid.notna() & (H.pid != '') & (H.pos != '1')]; P = P[P.pid.notna() & (P.pid != '')]
    H['osp_hand'] = H.osp.map(th).fillna('R'); H['bats'] = H.pid.map(bh).fillna('R')
    H['plat_adv'] = ((H.bats == 'B') | ((H.bats == 'L') & (H.osp_hand == 'R')) | ((H.bats == 'R') & (H.osp_hand == 'L'))).astype(float)
    H['bats_l'] = (H.bats == 'L').astype(float); H['bats_s'] = (H.bats == 'B').astype(float); H['vs_lhp'] = (H.osp_hand == 'L').astype(float)
    H['dkpos'] = H.pos.astype(str).map(DK_POS).fillna('DH'); H['posn'] = pd.to_numeric(H.pos, errors='coerce').fillna(10)
    P['throws_l'] = (P.pid.map(th) == 'L').astype(float)
    return H.reset_index(drop=True), P.reset_index(drop=True)

HB, HBS, HD, HPL, HPS, HPP = 500.0, 60.0, 365.0, 1000.0, 365.0, 45.0
def player_rows(G, pit, bat, hands, F):
    """Features + actual DK points (NaN for unplayed games) for every starter. Only days before the game feed its features."""
    Hs, Ps = starters(G, hands)
    played = set(G.gid[G.hruns.notna()])
    # ----- batter event table (all PAs, starters and subs) -----
    b = bat.copy(); b['day'] = day(b.date); b['lg'] = 'MLB'
    b['S1'] = b.B_H - b.B_2B - b.B_3B - b.B_HR; b['DK'] = dk_bat(b); b['OB'] = b.S1 + b.B_BB + b.B_HP
    b['XB'] = b.B_2B + 2 * b.B_3B + 3 * b.B_HR
    BC = ['B_PA', 'B_AB', 'S1', 'B_2B', 'B_3B', 'B_HR', 'B_BB', 'B_HP', 'B_SO', 'B_SB', 'B_CS', 'B_R', 'B_RBI', 'DK', 'OB', 'XB']
    BL = dec_table(b, 'pid', BC, HB); BS = dec_table(b, 'pid', ['B_PA', 'DK', 'OB', 'XB', 'B_SO'], HBS); LB = dec_table(b, 'lg', BC, 365.0)
    # ----- hitter starts: DK per start + spread, split vs the starter's hand -----
    hl = b.groupby(['gid', 'pid'], as_index=False)[['DK', 'B_PA']].sum()
    Hs = Hs.merge(hl, on=['gid', 'pid'], how='left'); Hs['day'] = day(Hs.date)
    Hs.loc[Hs.gid.isin(played), ['DK', 'B_PA']] = Hs.loc[Hs.gid.isin(played), ['DK', 'B_PA']].fillna(0)   # started, no PA (rare): 0 points
    st = Hs[Hs.DK.notna()].assign(dk2=lambda d: d.DK ** 2, g=1.0, key=lambda d: d.pid + '_' + d.osp_hand)
    SD = dec_table(st, 'pid', ['DK', 'dk2', 'g', 'B_PA'], HD); SPL = dec_table(st, 'key', ['DK', 'B_PA'], HPL)
    q = Hs[['pid', 'day']]; a = asof(q, BL, 'pid', BC, HB); s = asof(q, BS, 'pid', ['B_PA', 'DK', 'OB', 'XB', 'B_SO'], HBS)
    l = asof(pd.DataFrame(dict(lg='MLB', day=Hs.day)), LB, 'lg', BC, 365.0)
    def r(x, num, den, lgnum, lgden, K): return (x[num] + (l[lgnum] / l[lgden]).values * K) / (x[den] + K)
    lgdk = (l.DK / l.B_PA).values
    Hs['n_pa'] = np.log1p(a.B_PA.values)
    Hs['dk_pa'] = r(a, 'DK', 'B_PA', 'DK', 'B_PA', 200).values / lgdk
    Hs['ob_pa'] = r(a, 'OB', 'B_PA', 'OB', 'B_PA', 200).values / (l.OB / l.B_PA).values
    Hs['hr_pa'] = r(a, 'B_HR', 'B_PA', 'B_HR', 'B_PA', 400).values / (l.B_HR / l.B_PA).values
    Hs['iso'] = r(a, 'XB', 'B_AB', 'XB', 'B_AB', 300).values / (l.XB / l.B_AB).values
    Hs['k_pa'] = r(a, 'B_SO', 'B_PA', 'B_SO', 'B_PA', 120).values / (l.B_SO / l.B_PA).values
    Hs['bb_pa'] = r(a, 'B_BB', 'B_PA', 'B_BB', 'B_PA', 150).values / (l.B_BB / l.B_PA).values
    Hs['sb_ob'] = ((a.B_SB + (l.B_SB / l.OB).values * 60) / (a.OB + 60)).values / (l.B_SB / l.OB).values
    Hs['sba_ob'] = ((a.B_SB + a.B_CS + ((l.B_SB + l.B_CS) / l.OB).values * 60) / (a.OB + 60)).values
    Hs['r_ob'] = ((a.B_R + (l.B_R / l.OB).values * 100) / (a.OB + 100)).values / (l.B_R / l.OB).values
    Hs['rbi_pa'] = r(a, 'B_RBI', 'B_PA', 'B_RBI', 'B_PA', 300).values / (l.B_RBI / l.B_PA).values
    Hs['dk_pa_s'] = ((s.DK + Hs.dk_pa.values * lgdk * 60) / (s.B_PA + 60)).values / lgdk          # recent form, shrunk to his long-run rate
    Hs['n_pa_s'] = np.log1p(s.B_PA.values)
    a = asof(q, SD, 'pid', ['DK', 'dk2', 'g', 'B_PA'], HD)
    Hs['n_starts'] = np.log1p(a.g.values); Hs['dk_g'] = ((a.DK + 7.0 * 5) / (a.g + 5)).values      # shrunk to ~7 DK points a start
    Hs['pa_g'] = ((a.B_PA + 4.1 * 5) / (a.g + 5)).values
    m2 = (a.dk2 + 85.0 * 5) / (a.g + 5); Hs['dk_sd'] = np.sqrt(np.maximum(m2 - Hs.dk_g ** 2, 1)).values
    a = asof(Hs.assign(key=Hs.pid + '_' + Hs.osp_hand)[['key', 'day']], SPL, 'key', ['DK', 'B_PA'], HPL)
    Hs['dk_pa_split'] = ((a.DK + Hs.dk_pa.values * lgdk * 150) / (a.B_PA + 150)).values / lgdk
    Hs['naive'] = Hs.dk_g
    LBs = dec_table(b, 'lg', ['DK', 'B_PA'], 30.0); ls = asof(pd.DataFrame(dict(lg='MLB', day=Hs.day)), LBs, 'lg', ['DK', 'B_PA'], 30.0)
    Hs['lg_dk_pa_s'] = (ls.DK / ls.B_PA).values / lgdk                      # league scoring drift: last month vs last year
    # ----- HR park factor (HR per game at the park vs the home team's road games) -----
    hrg = b.groupby('gid').B_HR.sum(); Gd = G[G.gid.isin(played)]
    pk = pd.DataFrame(dict(park=Gd.park, day=day(Gd.date), hr=Gd.gid.map(hrg).fillna(0).values, g=1.0)); PK = dec_table(pk, 'park', ['hr', 'g'], 730.0)
    rd = pd.DataFrame(dict(team=Gd.away, day=day(Gd.date), hr=Gd.gid.map(hrg).fillna(0).values, g=1.0)); RD = dec_table(rd, 'team', ['hr', 'g'], 730.0)
    GG = G[['gid', 'park', 'home', 'date']].copy(); GG['day'] = day(GG.date); lhr = 2.2
    a1 = asof(GG[['park', 'day']], PK, 'park', ['hr', 'g'], 730.0); a2 = asof(GG.rename(columns={'home': 'team'})[['team', 'day']], RD, 'team', ['hr', 'g'], 730.0)
    GG['park_hr'] = ((a1.hr + lhr * 60) / (a1.g + 60)) / ((a2.hr + lhr * 60) / (a2.g + 60))
    # ----- game context from the run model rows (F: one row per batting team per game) -----
    ctx = ['sp_k', 'sp_bb', 'sp_hr', 'sp_ra', 'sp_h', 'sp_gb', 'sp_ogs', 'sp_exp', 'sp_k_s', 'sp_rest', 'pen_k', 'pen_bb', 'pen_hr', 'pen_ra', 'pen_out3',
           'off_rs', 'lu_woba', 'lu_k', 'lu_iso', 'lu_plat', 'park', 'temp', 'wind_out', 'wind_in', 'wind_x', 'dome', 'dh', 'sched7', 'day_game', 'ump_idx', 'is_home', 'lam', 'opp_lam']
    Fc = F[['gid', 'team'] + ctx]
    Hs = Hs.merge(Fc, on=['gid', 'team'], how='left').merge(GG[['gid', 'park_hr']], on='gid', how='left')
    Hs['game_tot'] = Hs.lam + Hs.opp_lam
    # ----- pitchers -----
    p = pit.copy(); p['day'] = day(p.date); p['DK'] = dk_pit(p); sp = p[p.P_GS > 0].assign(dk2=lambda d: d.DK ** 2, g=1.0)
    PCOLS = ['DK', 'dk2', 'g', 'P_OUT', 'P_SO', 'P_TBF', 'P_PITCH', 'P_ER', 'P_W']
    PL = dec_table(sp, 'pid', PCOLS, HPS); PSS = dec_table(sp, 'pid', ['DK', 'g', 'P_OUT', 'P_PITCH', 'P_SO'], HPP)
    sp['lg'] = 'MLB'; LP = dec_table(sp, 'lg', PCOLS, 365.0)
    pl = p.groupby(['gid', 'pid'], as_index=False)[['DK', 'P_OUT', 'P_SO', 'P_ER', 'P_W']].sum()
    Ps = Ps.merge(pl, on=['gid', 'pid'], how='left'); Ps['day'] = day(Ps.date)
    q = Ps[['pid', 'day']]; a = asof(q, PL, 'pid', PCOLS, HPS); s = asof(q, PSS, 'pid', ['DK', 'g', 'P_OUT', 'P_PITCH', 'P_SO'], HPP)
    l = asof(pd.DataFrame(dict(lg='MLB', day=Ps.day)), LP, 'lg', PCOLS, 365.0)
    lg = {c: (l[c] / l.g).values for c in PCOLS}
    Ps['n_gs'] = np.log1p(a.g.values)
    Ps['dk_gs'] = ((a.DK + lg['DK'] * 4) / (a.g + 4)).values; m2 = (a.dk2 + lg['dk2'] * 4) / (a.g + 4); Ps['dk_sd'] = np.sqrt(np.maximum(m2 - Ps.dk_gs ** 2, 1)).values
    Ps['out_gs'] = ((a.P_OUT + lg['P_OUT'] * 4) / (a.g + 4)).values; Ps['pit_gs'] = ((a.P_PITCH + lg['P_PITCH'] * 4) / (a.g + 4)).values
    Ps['so_gs'] = ((a.P_SO + lg['P_SO'] * 4) / (a.g + 4)).values; Ps['w_gs'] = ((a.P_W + lg['P_W'] * 6) / (a.g + 6)).values
    Ps['k_tbf'] = ((a.P_SO + l.P_SO.values / l.P_TBF.values * 100) / (a.P_TBF + 100)).values
    Ps['dk_gs_s'] = ((s.DK + Ps.dk_gs.values * 3) / (s.g + 3)).values; Ps['out_gs_s'] = ((s.P_OUT + Ps.out_gs.values * 3) / (s.g + 3)).values
    Ps['pit_gs_s'] = ((s.P_PITCH + Ps.pit_gs.values * 3) / (s.g + 3)).values
    Ps['lg_out_gs'] = lg['P_OUT']; Ps['naive'] = Ps.dk_gs
    LPs = dec_table(sp, 'lg', ['DK', 'g', 'P_OUT'], 30.0); ls = asof(pd.DataFrame(dict(lg='MLB', day=Ps.day)), LPs, 'lg', ['DK', 'g', 'P_OUT'], 30.0)
    Ps['lg_dk_gs'] = lg['DK']; Ps['lg_dk_gs_s'] = (ls.DK / ls.g).values; Ps['lg_out_gs_s'] = (ls.P_OUT / ls.g).values
    # the pitcher's own skill (sp_*) and the lineup he faces live on the OPPONENT's batting row
    pctx = ['sp_k', 'sp_bb', 'sp_hr', 'sp_ra', 'sp_h', 'sp_gb', 'sp_ogs', 'sp_exp', 'sp_k_s', 'sp_bb_s', 'sp_ra_s', 'sp_rest', 'off_rs', 'off_rs_s', 'lu_woba', 'lu_k', 'lu_iso', 'lu_plat', 'lu_exp',
            'park', 'temp', 'wind_out', 'wind_in', 'dome', 'dh', 'sched7', 'day_game', 'ump_idx', 'lam', 'opp_lam']
    Fo = F[['gid', 'team'] + pctx].rename(columns={'team': 'opp', 'lam': 'lam_against', 'opp_lam': 'lam_for'})
    Ps = Ps.merge(Fo, on=['gid', 'opp'], how='left').merge(GG[['gid', 'park_hr']], on='gid', how='left')
    Ps['is_home'] = Ps.merge(F[['gid', 'team', 'is_home']], on=['gid', 'team'], how='left').is_home.values
    Ps['win_z'] = np.log(Ps.lam_for / Ps.lam_against)
    Ps['exp_k'] = Ps.k_tbf * Ps.lu_k * (Ps.out_gs * 4.3 / 3)          # strikeouts vs this lineup over his usual length
    Ps.loc[~Ps.gid.isin(played), ['DK']] = np.nan
    Hs.loc[~Hs.gid.isin(played), ['DK']] = np.nan
    return Hs, Ps

HFEATS = ['slot', 'posn', 'is_home', 'n_pa', 'dk_pa', 'ob_pa', 'hr_pa', 'iso', 'k_pa', 'bb_pa', 'sb_ob', 'sba_ob', 'r_ob', 'rbi_pa', 'dk_pa_s', 'n_pa_s', 'n_starts', 'dk_g', 'pa_g', 'dk_sd',
          'dk_pa_split', 'plat_adv', 'bats_l', 'bats_s', 'vs_lhp', 'sp_k', 'sp_bb', 'sp_hr', 'sp_ra', 'sp_h', 'sp_gb', 'sp_ogs', 'sp_exp', 'sp_k_s', 'pen_k', 'pen_bb', 'pen_hr', 'pen_ra', 'pen_out3',
          'off_rs', 'lu_woba', 'lu_iso', 'park', 'park_hr', 'temp', 'wind_out', 'wind_in', 'wind_x', 'dome', 'dh', 'day_game', 'ump_idx', 'lam', 'opp_lam', 'game_tot', 'lg_dk_pa_s']
PFEATS = ['is_home', 'throws_l', 'n_gs', 'dk_gs', 'dk_sd', 'out_gs', 'pit_gs', 'so_gs', 'w_gs', 'k_tbf', 'dk_gs_s', 'out_gs_s', 'pit_gs_s', 'lg_out_gs', 'sp_k', 'sp_bb', 'sp_hr', 'sp_ra', 'sp_h',
          'sp_gb', 'sp_ogs', 'sp_exp', 'sp_k_s', 'sp_bb_s', 'sp_ra_s', 'sp_rest', 'off_rs', 'off_rs_s', 'lu_woba', 'lu_k', 'lu_iso', 'lu_plat', 'lu_exp', 'park', 'park_hr', 'temp', 'wind_out',
          'wind_in', 'dome', 'dh', 'sched7', 'day_game', 'ump_idx', 'lam_against', 'lam_for', 'win_z', 'exp_k', 'lg_dk_gs', 'lg_dk_gs_s', 'lg_out_gs_s']
QS = {'proj': None, 'floor': 0.15, 'ceil': 0.85}
def _fit(X, y, q, leaf):
    kw = dict(max_iter=300, learning_rate=0.04, max_leaf_nodes=12, min_samples_leaf=leaf, l2_regularization=1.0, random_state=7)   # picked on 2023-24, not on the test seasons
    return (HGB(loss='quantile', quantile=q, **kw) if q else HGB(loss='squared_error', **kw)).fit(X, y)
def train(Hs, Ps, seasons):
    h = Hs[Hs.season.isin(seasons) & Hs.DK.notna() & Hs.lam.notna()]; p = Ps[Ps.season.isin(seasons) & Ps.DK.notna() & Ps.lam_against.notna()]
    return dict(H={k: _fit(h[HFEATS], h.DK, q, 200) for k, q in QS.items()}, P={k: _fit(p[PFEATS], p.DK, q, 60) for k, q in QS.items()})
def predict(M, Hs, Ps):
    Hs = Hs.copy(); Ps = Ps.copy()
    for k in QS: Hs[k] = M['H'][k].predict(Hs[HFEATS]); Ps[k] = M['P'][k].predict(Ps[PFEATS])
    for d in (Hs, Ps): d['floor'] = np.minimum(d.floor, d.proj); d['ceil'] = np.maximum(d.ceil, d.proj)
    return Hs, Ps

def drift(R, HL=30.0, K=None):
    """League-level drift fix, walk-forward: add the decayed mean miss (actual - proj) of starters on EARLIER days of the frame
    (half-life HL days, shrunk toward 0 by K rows). The run model already carries the run level; this catches what DK scoring adds
    (starters' innings, win rates). R holds one season (or season to date) with actual DK where played."""
    K = K or (1500.0 if 'slot' in R else 150.0)
    d = R[R.DK.notna()].assign(res=lambda x: x.DK - x.proj, n=1.0).groupby('date')[['res', 'n']].sum()
    days = sorted(set(R.date)); out, sr, sn, last, j = {}, 0.0, 0.0, None, 0
    for t in days:
        if last is not None: f = 0.5 ** ((t - last).days / HL); sr *= f; sn *= f
        out[t] = sr / (sn + K)
        if t in d.index: sr += d.res[t]; sn += d.n[t]
        last = t
    c = R.date.map(out).values; R = R.copy()
    for k in (QS if 'slot' not in R else ('proj', 'ceil')): R[k] = R[k] + c     # a hitter's 15th-percentile game is a zero: his floor stays put
    R['drift'] = c; return R

def all_rows(cache=None, hist_last=None, model_json=None, hist_dir=None):
    G, pit, bat, hands = load(cache, hist_last, hist_dir)
    F = build(G, pit, bat, hands)
    F = run_lam(F, sorted(s for s in F.season.unique() if s >= int(F.season.min()) + 1), model_json)
    return player_rows(G, pit, bat, hands, F)

def project_day(G, pit, bat, hands, F, day0, model_json, model_cache=None):
    """What the daily job calls (step 3): G/pit/bat/hands/F as mlb/live.py builds them (F from features.build, no lam needed).
    Trains on every earlier season, projects the season to date (for the drift fix) and returns (hitters, pitchers) for day0's slate.
    model_cache: a file to keep the fitted trees in. They only depend on earlier seasons, so they are fitted once a season (and again
    when the features, the training rows or scikit-learn change) instead of on every build."""
    d0 = pd.Timestamp(day0); S = d0.year
    F = run_lam(F, sorted(s for s in F.season.unique() if s > int(F.season.min())), model_json)
    Hs, Ps = player_rows(G, pit, bat, hands, F)
    seasons = list(range(int(Hs.season.min()) + 2, S)); M = None
    if model_cache:
        import sklearn, joblib, hashlib
        sig = f"{sklearn.__version__}|{seasons}|{int((Hs.season.isin(seasons) & Hs.DK.notna()).sum())}|{int((Ps.season.isin(seasons) & Ps.DK.notna()).sum())}|{hashlib.md5(','.join(HFEATS + PFEATS + FEATS).encode()).hexdigest()[:10]}|{model_json.get('trained')}|{round(float(model_json.get('b0', 0)), 6)}"
        try:
            o = joblib.load(model_cache); M = o['M'] if o.get('sig') == sig else None
        except Exception: M = None
        if M is None:
            M = train(Hs, Ps, seasons)
            try: joblib.dump(dict(sig=sig, M=M), model_cache, compress=3); print('DFS trees fitted and cached:', model_cache)
            except Exception as e: print('could not cache the DFS trees:', e)
    if M is None: M = train(Hs, Ps, seasons)
    h, p = predict(M, Hs[(Hs.season == S) & (Hs.date <= d0)], Ps[(Ps.season == S) & (Ps.date <= d0)])
    h, p = drift(h), drift(p)
    return h[h.date >= d0], p[p.date >= d0]

# ---------------- proof ----------------
def spearman(a, b): return float(pd.Series(a).rank().corr(pd.Series(b).rank()))
def report(R, label):
    y = R.DK; e = dict(n=int(len(R)), mae_proj=round(float((R.proj - y).abs().mean()), 3), mae_naive=round(float((R.naive - y).abs().mean()), 3),
                       rmse_proj=round(float(np.sqrt(((R.proj - y) ** 2).mean())), 3), rmse_naive=round(float(np.sqrt(((R.naive - y) ** 2).mean())), 3),
                       spear_proj=round(spearman(R.proj, y), 3), spear_naive=round(spearman(R.naive, y), 3),
                       slate_spear_proj=round(float(R.groupby('date').apply(lambda d: spearman(d.proj, d.DK) if len(d) > 5 else np.nan).mean()), 3),
                       slate_spear_naive=round(float(R.groupby('date').apply(lambda d: spearman(d.naive, d.DK) if len(d) > 5 else np.nan).mean()), 3),
                       mean_proj=round(float(R.proj.mean()), 2), mean_actual=round(float(y.mean()), 2),
                       below_floor=round(float((y < R.floor).mean()), 3), at_or_below_floor=round(float((y <= R.floor).mean()), 3), above_ceil=round(float((y > R.ceil).mean()), 3))
    e['label'] = label; return e

def backtest(cache, tests, out, hist_dir=None, first=None):
    Hs, Ps = all_rows(cache, model_json=json.load(open(f'{ROOT}/mlb/model.json')), hist_dir=hist_dir)
    first = first or int(Hs.season.min()) + 2      # two warm-up seasons of history before a season is used for training
    rows, deciles, keep = [], [], []
    for S in tests:
        if not (Hs.season == S).any() or Hs[Hs.season == S].DK.notna().sum() == 0: print('no played games for', S); continue
        M = train(Hs, Ps, list(range(first, S)))
        h, p = predict(M, Hs[(Hs.season == S) & Hs.DK.notna()], Ps[(Ps.season == S) & Ps.DK.notna()]); h, p = drift(h), drift(p)
        keep += [h.assign(role='H'), p.assign(role='P')]
        rows.append(report(h, f'{S} hitters (all starters)'))
        for pos in ('C', '1B', '2B', '3B', 'SS', 'OF', 'DH'): rows.append(report(h[h.dkpos == pos], f'{S} {pos}'))
        rows.append(report(p, f'{S} SP (all starters)')); rows.append(report(p[p.n_gs >= np.log1p(3)], f'{S} SP with 3+ earlier starts'))
        for role, d in (('H', h), ('P', p)):       # floor/ceiling should hold for steady and volatile players alike
            w = d.assign(t=pd.qcut((d.ceil - d.floor).rank(method='first'), 3, labels=['narrow', 'mid', 'wide']))
            for t, x in w.groupby('t'): rows.append(report(x, f'{S} {"hitters" if role == "H" else "SP"} {t} floor-ceiling range'))
            d = d.assign(dec=pd.qcut(d.proj, 10, labels=False))
            deciles.append(d.groupby('dec').agg(proj=('proj', 'mean'), actual=('DK', 'mean'), n=('DK', 'size')).round(2).assign(season=S, role=role).reset_index())
        imp = pd.Series(np.abs(np.corrcoef(np.c_[h[HFEATS].fillna(0).values, h.proj.values].T)[-1, :-1]), index=HFEATS).sort_values(ascending=False)
        print(S, 'hitter projection tracks most closely with:', ', '.join(imp.index[:8]))
    T = pd.DataFrame(rows).set_index('label'); D = pd.concat(deciles)
    pd.set_option('display.width', 250); print(T.to_string()); print(D.pivot_table(index='dec', columns=['season', 'role'], values=['proj', 'actual']).round(2).to_string())
    if out:
        os.makedirs(out, exist_ok=True); T.to_csv(f'{out}/mlb_proj_backtest.csv'); D.to_csv(f'{out}/mlb_proj_deciles.csv')
        pd.concat(keep).to_parquet(f'{out}/mlb_proj_rows.parquet')
        json.dump(dict(built=str(pd.Timestamp.now().date()), table=T.reset_index().to_dict('records')), open(f'{ROOT}/dfs/mlb_proj_backtest.json', 'w'), indent=1)
    return T

def lookahead(date, cache=None):
    """Projections for `date` built from (a) everything and (b) history cut to games before `date` + that day's lineups only."""
    d0 = pd.Timestamp(date); MJ = json.load(open(f'{ROOT}/mlb/model.json'))
    G, pit, bat, hands = load(cache)
    def proj(G, pit, bat):
        F = build(G, pit, bat, hands); F = run_lam(F, [d0.year], MJ); Hs, Ps = player_rows(G, pit, bat, hands, F)
        return Hs[Hs.date == d0].set_index(['gid', 'pid'])[HFEATS].sort_index(), Ps[Ps.date == d0].set_index(['gid', 'pid'])[PFEATS].sort_index()
    G1 = G[G.date <= d0]; a = proj(G1, pit[pit.date <= d0], bat[bat.date <= d0])
    G2 = G1.copy(); G2.loc[G2.date == d0, ['hruns', 'aruns']] = np.nan
    b = proj(G2, pit[pit.date < d0], bat[bat.date < d0])
    for x, y, n in ((a[0], b[0], 'hitters'), (a[1], b[1], 'pitchers')):
        diff = float(np.nanmax(np.abs(x.values - y.values))); print(f'{date} {n}: {len(x)} rows, largest feature difference {diff:.2e}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['backtest', 'lookahead']); ap.add_argument('--cache'); ap.add_argument('--test', type=int, nargs='+', default=[2025, 2026])
    ap.add_argument('--out', default='/tmp/mlb_proj'); ap.add_argument('--date', default='2025-07-20'); ap.add_argument('--hist-dir'); ap.add_argument('--first', type=int); A = ap.parse_args()
    if A.cmd == 'backtest': backtest(A.cache, A.test, A.out, A.hist_dir, A.first)
    else: lookahead(A.date, A.cache)
