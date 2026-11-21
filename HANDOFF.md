# Bet Tracker — handoff (Nov 21 2026, MLB DFS projections added)

Repo: Dan2Duper/betting-model (public). Site: https://dan2duper.github.io/betting-model/ (main branch, index.html).
Branches: main (published page + t.js + dfs-todo and mlb workflows) · package (all code; tracker_template.html is the page source) ·
sync (state.json, report.json written by the page; reports.json written by the relay) · data (DFS salaries, dfs_todo.json, dfs_nfl.json, dfs-build workflow).
Pushing to main/package/sync/data from a Claude session with git works.

## Working now
- NFL DFS: Rich's Android phone (Tasker, every morning) pulls DK draftables -> relay /salaries -> data branch -> GitHub Action builds dfs_nfl.json -> DFS tab (cash/GPP, classic + showdown, write-ups, shuffle, "my read" AI box).
- Relay = Cloudflare Worker "bet-tracker-relay" (code: dfs/worker.js; deploy by pasting into the dashboard editor in Rich's Chrome).
  Routes: / (DFS AI read), /salaries, /betslip (screenshot bet import: up to 8 screenshots in one read; a parlay split across
  screenshots is merged into one bet; page has Save all + an already-in-My-bets check; deployed Nov 21 2026 as b7695412), /report (?force=now|preview|update|final, ?dry=1).
  Cron every 15 min -> runReports(): reads report.json, refreshes scores from ESPN, computes records/$ in code, Claude writes the
  report in a random comic style (mean/vulgar allowed, numbers exact) -> reports.json. "🔥 Roast me now" button on the Reports tab.
  Secrets: ANTHROPIC_API_KEY, ACCESS_CODE (also saved in the phone app as "DFS AI code"), GH_TOKEN. Model claude-sonnet-5-5.
- Weekly CFB/NFL betting refresh: scheduled task "Betting model weekly refresh" (Tue 5:31 AM CT) runs on Rich's PC files.

## MLB - done Nov 21 2026 (package branch, mlb/)
- Data (`bash mlb/setup_data.sh` rebuilds all of it, ~900 MB, few minutes): Retrosheet game logs + event-file weather + rosters,
  retrosplits per-game player lines (2008-25), closing odds 2010-21 (SportsbookReviewsOnline via fda-project-1) and 2021-25
  (SportsBookReview per-book, release file of ArnavSaraogi/mlb-odds-scraper).
  TRAP found and handled in build_data.py: the 2021-25 file's "currentLine" is sometimes a LIVE in-game line (DraftKings + FanDuel for
  nearly all of Sept-Oct 2021, 1-5% of games elsewhere). Fix: cross-book median is the reference, DraftKings price only when it sits on
  it, 2021 uses the archive close, big open-to-close jumps are flagged (ml_suspect / tot_suspect) and left out of grading.
- Features (mlb/features.py): decayed + shrunk skill for starter, bullpen, each lineup batter (wOBA, K, ISO, platoon), team offense,
  park, umpire, league run level, temp/wind/dome. Verified no look-ahead (truncated-history rebuild matches to 0.0).
- Model (mlb/model.py -> mlb/model.json, mlb/stats.json): Poisson GLM, each season predicted by a fit on earlier seasons only.
  2013-25, 29,045 games: picks the winner 57.9% (closing line 58.1%), log loss 0.6754 (line 0.6730).
- Rules (mlb/backtest.py, 52 candidates; mlb/market_model.py, a model of what the line misses): NOTHING beats the closing line out of
  sample. Model moneyline edges lose the vig (-2% to -4%). Three leans seeded in rules.json / rules_seed.json, all lean_only:
  mlb_total_gap_over 909-773, mlb_total_gap_under 1206-1051 (model total 0.75+ runs off the line), mlb_wrigley_wind_out_over 73-46.

## MLB live - built Nov 21 2026
- Daily job: workflow "MLB model" (main: .github/workflows/mlb.yml; 8:23 am + 3:41 pm Central, March-November, or Run workflow).
  mlb/live.py pulls the MLB Stats API (schedule + box scores, each game cached once in data:mlb/live_*.parquet), maps MLBAM ids to
  Retrosheet ids (chadwickbureau/register; venues/umpires learned from the last 3 Retrosheet seasons; data:mlb/xwalk.json), appends the
  slate and runs features.build() on mlb/hist (2016-25) + season to date, so live features = backtest features. Writes data:mlb.json.
  Lineups: posted ones if up, else the team's last lineup. Weather: the Stats API's game-day weather once posted, else 72F and calm.
- Verified: workflow input parity=2025 replays 2025 from the Stats API with history cut at 2024: winner 56.09%, log loss 0.6781,
  avg total 8.84 vs the Retrosheet path's 56.01%, 0.6786, 8.85. 2026 season (Stats API, out of sample, no odds): 2,429 games,
  winner 55.6%, log loss 0.6821 (a rough year; 2025 was 56.0%, the 2013-25 average 57.9%).
- Page: MLB tab (tracker_template.html, mlbLoad/mlbRender): model score, win %, DraftKings moneyline + total from ESPN's MLB scoreboard,
  lean chips, warnings for unannounced starters / unposted lineups / missing weather. Not yet seen against a real in-season ESPN feed.
- Seasons: applySeasons() shows a league and its tabs only in season (SEASONS: MLB Mar 18-Nov 5, CFB Aug 20-Jan 25, NFL Sep 1-Feb 15).
  Football tabs = Week, Scores, DFS. Preview any day with ?asof=YYYY-MM-DD on the page address.

## MLB DFS projections - done Nov 21 2026 (package branch, dfs/mlb_proj.py)
- Data: runs, RBI, CS (hitters) and W, CG, SHO (pitchers) now kept in mlb/build_data.py, mlb/hist (rebuilt) and mlb/live.py box_rows.
  The Stats API cache on data:mlb/ was re-downloaded once with them (live.py refetches automatically if B_RBI is missing).
  Checks: one win per game in both sources; team runs = sum of batter runs on ~96% of team-games (the rest are runs by pinch
  runners with no PA, who never start).
- DK classic scoring in dk_bat / dk_pit. One row per starting hitter (from the lineup strings) and per starting pitcher.
- Hitter features: decayed + shrunk per-PA skill (DK pts, on-base, HR, ISO, K, BB, SB, runs, RBI), DK points per start + spread,
  split vs the starter's hand, slot, position, platoon, opposing SP + bullpen, park runs + HR park factor, weather, umpire,
  team lam and opponent lam (run model, walk-forward). Pitcher: DK per start + spread, outs/pitches/K per start (long and recent),
  sp_* skill, rest, opposing lineup wOBA/K/ISO/platoon, lam against, win odds (log lam ratio), expected K.
- Engine: sklearn HistGradientBoosting, squared error = proj, quantile 0.15 = floor, 0.85 = ceil. Tree settings picked on 2023-24.
  Drift fix: the decayed mean miss on earlier days of the season (half-life 30 days) is added (hitters: proj + ceil only).
- `python3 dfs/mlb_proj.py backtest --cache <data mlb dir> --test 2025 2026` (writes dfs/mlb_proj_backtest.json).
  `python3 dfs/mlb_proj.py lookahead --date 2025-07-20` -> 0.0 difference with that day's results removed (also 2025-04-02).
  project_day(G, pit, bat, hands, F, day0, model.json) = the daily path; on 2025-07-20 it equals the backtest to 0.0.
- Walk-forward results (each season fitted on 2018..season-1; "naive" = decayed DK per start):
    | season | role | MAE proj / naive | per-slate rank corr proj / naive | mean proj / actual | below floor | above ceil |
    | 2025 | hitters (43,740) | 5.43 / 5.50 | 0.186 / 0.155 | 6.98 / 6.93 | 0 (floor=0) | 14.9% |
    | 2025 | SP (4,860)       | 8.20 / 8.41 | 0.327 / 0.266 | 13.27 / 13.51 | 15.7% | 15.8% |
    | 2026 | hitters (43,713) | 5.37 / 5.47 | 0.176 / 0.140 | 6.89 / 6.87 | 0 (floor=0) | 15.0% |
    | 2026 | SP (4,857)       | 7.94 / 8.23 | 0.336 / 0.278 | 13.24 / 13.09 | 15.5% | 13.8% |
  Beats naive at every position in 2023-26; projection deciles track actual deciles (see the json). A hitter's 15th-percentile game
  is a zero (23% of starts score 0), so hitter floors are 0: cash builds should rank hitters on proj, pitchers on proj + floor.

## START HERE next session: MLB DFS lineups (steps 2-3)
2. Lineups: dfs/optimizer.py (PuLP) gets an MLB classic builder: P,P,C,1B,2B,3B,SS,OF,OF,OF, $50,000, max 5 hitters from one team,
   players from 2+ games; GPP stacks of 4-5 hitters from one lineup (consecutive slots), no hitters against your own pitcher.
   Showdown = Captain 1.5x + 5 UTIL (optimizer.showdown already exists for NFL). DK eligibility comes from the salary file
   (C/1B, 2B/SS etc.); proj rows join on name + team. Ownership model for MLB still to build (dfs/ownership.py is NFL).
   NO MLB salary files exist yet (data:salaries/ holds NFL only). dfs/make_todo.py already lists MLB slates and the relay /salaries
   accepts sport=MLB, so files arrive by themselves once DraftKings posts MLB slates (spring). Until then test with made-up salaries;
   a lineup backtest can use the 2025-26 projection rows (dfs/mlb_proj.py backtest --out writes mlb_proj_rows.parquet).
3. Build + page: in mlb/live.py (after F = build(...)) call mlb_proj.project_day(...) for the slate -> dfs/build_mlb.py ->
   data:dfs_mlb.json; the workflow needs scikit-learn added to its pip line (training takes ~1 min). DFS tab reads NFL or MLB by
   season (applySeasons / inSeason exist; the DFS tab is marked data-sport="fb" today and must show for MLB too).
4. Loose NFL DFS item worth fixing on the way: TE floor/ceiling calibration in dfs/nfl_model.py.

## Next for MLB
1. First week of the 2027 season: open the MLB tab on a game day and check the ESPN odds fields (mlbOdds reads both feed shapes) and
   team matching; fix whatever the real feed shows.
2. Log MLB leans as picks so they grade on the Tracker and in reports (frozenPicks / picks_log; grade.py needs MLB finals; worker.js
   runReports needs an MLB espnDay URL). Until then the MLB tab is display only.
3. Retrosheet 2026 (not published yet Nov 21): `bash mlb/setup_data.sh <dir> 2026`, then `python3 mlb/make_hist.py <dir>` and commit
   mlb/hist + model.json; the Stats API cache then only carries 2027. No closing-odds source for 2026 yet (backtest stays 2013-25).
4. Forecast weather before the Stats API posts it (Open-Meteo at the park + field azimuth from the Stats API venue record).
5. MLB DFS: projections done (dfs/mlb_proj.py); lineups + page next (see START HERE).

## Loose ends
- TE floor/ceiling calibration in dfs/nfl_model.py is off.
- package branch lacks cfb/, nfl/, paths.py, run_week.py, setup_data.sh (they live on Rich's PC) — needed to move the weekly refresh to GitHub Actions.
