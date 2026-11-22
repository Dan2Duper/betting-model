"""MLB DFS: which extra variables explain what the base projection misses? (the MLB twin of dfs/research/matchup_*.py)
Works on walk-forward projection rows (python3 dfs/mlb_proj.py backtest --test 2021 ... 2026 --out DIR -> DIR/mlb_proj_rows.parquet):
residual = actual DraftKings points - projection made from earlier seasons only. Candidates (dfs/mlb_tune.py VARS):
  veg_gap / veg_win (closing moneyline + total vs the run model), pen_tired (opposing bullpen pitches, last 3 days),
  ump_k / ump_bb (plate umpire zone), park_hand (home-run park factor for the batter's side).
Also reported, already inside the base model (their residuals should be flat): batting-order spot, platoon, wind out / in.
Each role's weights are fitted season-forward (season S from seasons before it) and leave-one-season-out; a role starts switched on
only if both lower its out-of-sample error.
Usage: python3 dfs/research/mlb_vars.py DATA_DIR ROWS.parquet   -> dfs/mlb_tune_init.json, dfs/research/mlb_vars_2021_2026.json"""
import os, sys, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); DFS = os.path.dirname(HERE); ROOT = os.path.dirname(DFS)
sys.path.insert(0, DFS); sys.path.insert(0, os.path.join(ROOT, 'mlb')); sys.path.insert(0, os.path.join(ROOT, 'mlb', 'research'))
import mlb_tune as T
from features import dec_table, asof, day
D, ROWS = sys.argv[1], sys.argv[2]

park_hand, add_vars = T.park_hand, T.add_vars

if __name__ == '__main__':
    R = pd.read_parquet(ROWS); R = R[R.DK.notna()].copy(); R['res'] = R.DK - R.proj
    G = pd.read_parquet(f'{D}/games.parquet'); bat = pd.read_parquet(f'{D}/bat.parquet'); hands = pd.read_parquet(f'{D}/hands.parquet').drop_duplicates('pid')
    FX = pd.read_parquet(f'{D}/feat_x.parquet'); F = pd.read_parquet(f'{D}/feat.parquet'); FX = FX.drop(columns=[c for c in ('ump_k', 'ump_bb') if c in FX]).merge(F[['gid', 'team', 'ump_k', 'ump_bb']], on=['gid', 'team'])
    PH = park_hand(bat, G, hands)
    R = R[R.season <= int(G.season.max())]             # Retrosheet seasons carry the closing lines and the bullpen / umpire tables
    R = add_vars(R, G, FX, PH)
    res = dict(built=str(pd.Timestamp.now().date()), seasons=sorted(int(s) for s in R.season.unique()), rows=dict(H=int((R.role == 'H').sum()), P=int((R.role == 'P').sum())), roles={}, flat_checks={})
    out = dict(w={}, sd={}, report={})
    rm = lambda e: round(float(np.sqrt(np.mean(e ** 2))), 4); ma = lambda e: round(float(np.abs(e).mean()), 4)
    for role, V in T.VARS.items():
        x = R[R.role == role]; X = x[V].fillna(0).values.astype(float); y = x.res.values; S = sorted(x.season.unique())
        w, t = T.fit(X, y)
        wf = np.full(len(x), np.nan); lo = np.zeros(len(x))
        for s in S[1:]:
            tr, te = (x.season < s).values, (x.season == s).values; wf[te] = X[te] @ T.fit(X[tr], y[tr])[0]
        for s in S:
            tr, te = (x.season != s).values, (x.season == s).values; lo[te] = X[te] @ T.fit(X[tr], y[tr])[0]
        ok = ~np.isnan(wf); has = (x.veg_gap != 0).values
        rep = dict(n=int(len(x)), with_closing_line=int(has.sum()), rmse_base=rm(y[ok]), rmse_adj_season_forward=rm((y - wf)[ok]), mae_base=ma(y[ok]), mae_adj_season_forward=ma((y - wf)[ok]),
                   rmse_base_all=rm(y), rmse_adj_leave_one_season_out=rm(y - lo), t={v: round(float(a), 1) for v, a in zip(V, t)},
                   by_season={int(s): [rm(y[(x.season == s).values & ok]), rm((y - wf)[(x.season == s).values & ok])] for s in S[1:]},
                   slate_rank_corr=[round(float(x.assign(p=x.proj).groupby('date').apply(lambda d: d.p.rank().corr(d.DK.rank()) if len(d) > 5 else np.nan).mean()), 4),
                                    round(float(x[ok].assign(p=(x.proj + np.nan_to_num(wf))[ok]).groupby('date').apply(lambda d: d.p.rank().corr(d.DK.rank()) if len(d) > 5 else np.nan).mean()), 4)])
        rep['starts_on'] = bool(rep['rmse_adj_season_forward'] < rep['rmse_base'] and rep['rmse_adj_leave_one_season_out'] < rep['rmse_base_all'])
        out['fit_w'] = out.get('fit_w', {}); out['fit_w'][role] = {v: round(float(b), 4) for v, b in zip(V, w)}
        out['w'][role] = dict(out['fit_w'][role]) if rep['starts_on'] else {v: 0.0 for v in V}
        out['sd'][role] = {v: round(float(s_), 4) for v, s_ in zip(V, X.std(0))}; out['report'][role] = rep
        print(f"\n{role}: {len(x)} rows | RMSE base {rep['rmse_base']} -> season-forward {rep['rmse_adj_season_forward']} | leave-one-season-out {rep['rmse_base_all']} -> {rep['rmse_adj_leave_one_season_out']} | MAE {rep['mae_base']} -> {rep['mae_adj_season_forward']} | per-slate rank corr {rep['slate_rank_corr']} | starts on: {rep['starts_on']}")
        print('   t-values:', rep['t']); print('   weights (DK points per unit):', out['fit_w'][role]); print('   by season [base, adjusted]:', rep['by_season'])
    # ---- things already inside the base model: their residuals should be flat ----
    h = R[R.role == 'H']
    res['flat_checks']['batting_order_spot'] = {int(k): round(float(v), 3) for k, v in h.groupby('slot').res.mean().items()}
    res['flat_checks']['platoon_edge'] = {str(int(k)): round(float(v), 3) for k, v in h.groupby('plat_adv').res.mean().items()}
    res['flat_checks']['wind_out_10plus'] = round(float(h[h.wind_out >= 10].res.mean()), 3); res['flat_checks']['wind_in_10plus'] = round(float(h[h.wind_in >= 10].res.mean()), 3)
    res['flat_checks']['hot_85plus'] = round(float(h[h.temp >= 85].res.mean()), 3); res['flat_checks']['cold_55minus'] = round(float(h[(h.temp <= 55) & (h.dome == 0)].res.mean()), 3)
    print('\nAlready in the base model (average miss in DK points; near 0 = nothing left to add):', json.dumps(res['flat_checks']))
    res['roles'] = out['report']; res['fit_w'] = out['fit_w']; res['start_w'] = out['w']
    out['_note'] = 'Starting weights for dfs/mlb_tune.py, from dfs/research/mlb_vars.py (walk-forward projection misses, Retrosheet seasons with closing lines).'
    json.dump(out, open(f'{DFS}/mlb_tune_init.json', 'w'), indent=1); json.dump(res, open(f'{HERE}/mlb_vars_2021_2026.json', 'w'), indent=1); print('\nsaved dfs/mlb_tune_init.json, dfs/research/mlb_vars_2021_2026.json')
