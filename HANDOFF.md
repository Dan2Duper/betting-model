# Bet Tracker — handoff (Nov 22 2026, NFL DFS live statuses / weather / self-tuning added)

Repo: Dan2Duper/betting-model (public). Site: https://dan2duper.github.io/betting-model/ (main branch, index.html).
Branches: main (published page + t.js + dfs-todo and mlb workflows) · package (all code; tracker_template.html is the page source) ·
sync (state.json, report.json written by the page; reports.json written by the relay) · data (DFS salaries, dfs_todo.json, dfs_nfl.json, dfs-build workflow).
Pushing to main/package/sync/data from a Claude session with git works.

## Working now
- NFL DFS: Rich's Android phone (Tasker, 7am-9pm every 3h) pulls DK draftables -> relay /salaries -> data branch -> GitHub Action builds dfs_nfl.json -> DFS tab (cash/GPP, classic + showdown, write-ups, shuffle, "my read" AI box).
- Relay = Cloudflare Worker "bet-tracker-relay" (code: dfs/worker.js; deploy by pasting into the dashboard editor in Rich's Chrome).
  Routes: / (DFS AI read), /salaries, /betslip (screenshot dump, any book or prediction market: reader returns book, kind bet|trade, bet_ref,
  status open|live|won|lost|push|void|sold|partially_sold, stake, payout, sold_for, sell_offer, legs + game_id picked from the page's ESPN
  list, legs_expected/cut_off, ignore for non-bet screens; page shotPlan() reconciles each read vs saved bets by leg signature + bet_ref:
  new / dup (incl. mid-game) / up (trade position upped: stake+odds updated, ups[] history) / down / partial (sold piece split off) / sell;
  a sold bet carries `sold` = proceeds and profit = sold - stake in the Tracker, report.json (pl), relay money() and grade.py;
  reader model claude-opus-5-5 (env SLIP_MODEL); deployed Nov 21 2026 as ca1f840d; game reports answer through the game_report tool (tool_choice auto: the model refuses a forced tool) + looseReport() text fallback, relay c4e8c5ba), /report (?force=now|preview|update|final, ?dry=1).
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

## NFL DFS upgrade - done Nov 22 2026 (package branch, dfs/)
- Research: dfs/research/matchup_*.py + matchup_results_2021_2025.json (2021-25, ~20k player-games, residuals after Vegas + own form).
  Unit-vs-unit matchups add nothing out of sample once Vegas is in (offense explains ~2/3, defense ~1/3). Lockdown corner on WR1 -0.4 (+/-0.4, not significant);
  weak top corner +1.1 WR1. Archetype vs elite CB: separators -0.9 (borderline), big/slot -0.75, speed -0.2, deep threats 0. Man/zone fit +0.25 best vs worst.
  Biggest: vacated roles (lead RB out -> next RB +2.2), wind (Vegas under-adjusts), dome +0.7 WR. Shadow assignments are paid-only (PFF) -> corner tests are proxies.
- dfs/nfl_live.py: statuses() ESPN injuries + Sleeper (gsis ids) override DK's stale tag (worst tag wins; absent from both feeds = cleared);
  forecasts() Open-Meteo kickoff wind/gust/temp/rain per stadium_id (retractable roof assumed closed if <55F, rain>=40% or wind>=15). Both fail soft.
- dfs/nfl_tune.py: adjustment layer on top of the tree model. Vars per position (teammate out targets/carries, wind, dome, new QB, division, byes, elite/weak top corner).
  Starting weights = walk-forward fit on base-model residuals 2022-25 (nfl_tune_init.json); only RB starts on (only position where it beat out-of-sample RMSE);
  the rest start at 0. Every build remembers base proj + vars (state lives in dfs_nfl.json "tune"); next week's build grades each variable
  (log: helped = MAE gain), refits decayed stats pulled to the long-run fit, moves weights 30% of the way. Players carry adj/why/wind in dfs_nfl.json.
- Fixed: starting-QB pick used groupby max of season and week separately (matched nothing) -> fell back to highest salary.
- Game-day rebuilds are automatic in the relay cron (deployed f7065a59, Nov 22): on any CT day with NFL games (ESPN scoreboard) it saves
  salaries/_refresh.json at 9:00 am CT and ~75 min before each kickoff -> the salaries/** push trigger rebuilds (GH_TOKEN is Contents-only, so no
  workflow_dispatch). Hourly 6am-8pm it also tries missing DK salaries from Cloudflare, but GET /dkcheck showed DraftKings returns 403 to Cloudflare (Nov 22) -> the phone (Tasker) stays the salary source.
  Verified: 9:00 refresh -> build 15:07 UTC with ESPN + Sleeper statuses and 12 forecasts. Tasker: the old profile had no task linked (never fired); replaced Nov 22 with 7:00 AM every 3h till 9:00 PM -> DFS salaries; test run saved all 13 slates.
- Next: page chips for adj/why + wind on DFS player rows; PROE / pass-funnel and routes-per-dropback variables (need pbp/participation download);
  kicker wind.

## START HERE next session: MLB complete for Opening Day 2027 (betting lines, totals, DFS)
Goal (Rich, Nov 22 2026): MLB fully finished in the app - moneyline + run line + over/under picks that log, grade and report like
football, and MLB DFS lineups with the same freshness / weather / self-tuning treatment NFL DFS got on Nov 22. Opening Day is late
March 2027; spring training (late Feb) is the live dry run. Rules as always: do everything you can yourself, one step at a time when it
has to be Rich, prove every variable walk-forward before it goes live, nothing gets "bet" stars unless it beats the closing line out of sample.

Phase 0 - setup
- `bash mlb/setup_data.sh <dir>` rebuilds the history (~900 MB). Retrosheet 2026 may be out by now: if so `bash mlb/setup_data.sh <dir> 2026`,
  `python3 mlb/make_hist.py <dir>`, commit mlb/hist + model.json (the Stats API cache then only carries 2027).

Phase 1 - betting lines and totals (mlb/)
1. Run line: derive P(cover -1.5/+1.5) from the Poisson run model (margin distribution, plus extra-innings rule). Check whether the
   2021-25 SBR odds file carries run lines / first-five lines; if yes, backtest them like mlb/backtest.py did moneyline + totals.
2. Fresher inputs before first pitch (same idea as dfs/nfl_live.py): Open-Meteo forecast at the park + field azimuth from the Stats API
   venue record (wind out to CF vs in, temp, humidity, rain/postponement risk, roof); confirmed lineups + probable pitchers + scratches;
   home-plate umpire. Today live.py falls back to 72F/calm and last lineup until the Stats API posts them.
3. Re-test candidate variables walk-forward 2013-25 against the closing line: wind out/in by park, bullpen fatigue (pitches last 3 days),
   umpire zone, lineup strength vs the projected one, travel/rest. Current truth: nothing beats the close; 3 leans only
   (mlb_total_gap_over/under, mlb_wrigley_wind_out_over). Keep that honesty on the page.
4. Picks that count: log MLB leans/picks to frozenPicks / picks_log, grade.py with MLB finals, scorecard rows, and the relay's runReports
   (needs an MLB espnDay URL) so MLB shows in Reports. Daily self-tuning like the football weekly refresh (rules_learn.py).
5. More game-day runs: workflow "MLB model" (main: .github/workflows/mlb.yml) runs 8:23 am + 3:41 pm CT. Add rebuilds ~90 min before
   each first-pitch window. Easiest: give mlb.yml a push trigger on a data-branch path (e.g. mlb/_refresh.json) and have the relay cron
   write it, like salaries/_refresh.json for NFL. Editing a workflow on main was refused once from a Claude session - if refused again,
   ask Rich to approve it rather than working around it.
6. Page: MLB tab (mlbRender) gets run line + total picks with stars/lean chips, weather (wind arrow out/in), lineup-confirmed and
   starter-confirmed badges. First game day: check ESPN odds fields (mlbOdds reads both feed shapes) and team matching on the real feed.

Phase 2 - MLB DFS (dfs/)
1. Lineups: dfs/optimizer.py MLB classic builder - P,P,C,1B,2B,3B,SS,OF,OF,OF, $50,000, max 5 hitters per team, 2+ games; GPP stacks
   of 4-5 consecutive hitters from one lineup; no hitters against your own pitcher. Showdown = CPT 1.5x + 5 UTIL (optimizer.showdown
   exists). DK eligibility from the salary file (C/1B, 2B/SS...); proj rows join on name + team. MLB ownership model (dfs/ownership.py is NFL).
   No MLB salary files yet; dfs/make_todo.py lists MLB slates and the relay /salaries accepts sport=MLB, so Tasker brings them in spring
   (confirm the slate-list workflow includes MLB in season). Test meanwhile with made-up salaries + `dfs/mlb_proj.py backtest --out` rows.
2. Build + page: in mlb/live.py after F = build(...) call mlb_proj.project_day(...) -> dfs/build_mlb.py -> data:dfs_mlb.json (add
   scikit-learn to that workflow's pip line). DFS tab shows NFL or MLB by season (it is data-sport="fb" today).
3. Freshness (copy the NFL pattern): confirmed batting orders / scratches / probables from the Stats API at build time override DK's
   tags; park weather with wind direction; relay rebuilds ~60-90 min before each slate lock and again when lineups post (MLB has several
   locks a day - read lock times from dfs_todo.json or the Stats API schedule).
4. Research + self-tuning (copy dfs/research + dfs/nfl_tune.py): backtest batting-order slot (PA count), platoon, park factor by hand,
   wind out/in, umpire, bullpen fatigue, pitch mix vs batter (Statcast, check reachability from Actions) on 2021-26 projection residuals;
   start only what beats out-of-sample error; grade daily and nudge weights.

DFS entry tracking - BUILT Nov 22 (page eb99f21 on main, relay 1abeb538): "✅ I entered this lineup" under a lineup (type, fee, entries,
contest) -> ST.dfsMine (synced); "📷 Add DFS screenshots" -> relay /dfsshot (DFS_SHOT prompt + dfs_screens tool; a lineup split over two
screenshots is merged) -> matched to saved entries by player overlap (first initial + last name, need max(4, n-2)) -> new / result update /
already saved; "My DFS results" card: settled fees, won, net, ROI, cashed, split cash vs tournament. MLB: works as soon as MLB lineups exist.
  Added same day: contest choices (dfs/dk_slate.lobby_contests -> dfs_todo.json slates[].contests: top prize pools <= $100 fee + biggest cash
  games) fill contest/fee/type; screenshot fee from the Submit button / DETAILS tab / same contest before / lobby name / last fee (editable);
  contest-only screens (Standings) attach to saved entries; $ button edits a fee. AUTO SCORING: page dfsScore() every 60 s scores entered NFL
  lineups from ESPN summaries with DK rules (CPT x1.5, DST incl. points allowed; kickers approximate) - matched official stats exactly on
  IND@HOU Nov 19; final totals saved (autoFinal). report.json carries dfs[]; relay factsText has a MY DFS block (relay 23715ff4, which
  also carries the MLB-session report code; its tests/relay_mlb_test.mjs passed). Not yet: DFS rows in the Tracker/Stats tabs.

Phase 3 - spring training dry run (late Feb 2027): run every piece on real spring games/slates, fix feed surprises, then go live.

## Loose ends
- TE floor/ceiling calibration in dfs/nfl_model.py is off.
- package branch lacks cfb/, nfl/, paths.py, run_week.py, setup_data.sh (they live on Rich's PC) — needed to move the weekly refresh to GitHub Actions.
