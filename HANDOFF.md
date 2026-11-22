# Bet Tracker — handoff (Nov 22 2026: NFL DFS live statuses / weather / self-tuning; MLB lines, run line, totals and DFS built for 2027)

Repo: Dan2Duper/betting-model (public). Site: https://dan2duper.github.io/betting-model/ (main branch, index.html).
Branches: main (published page + t.js + dfs-todo and mlb workflows) · package (all code; tracker_template.html is the page source) ·
sync (state.json, report.json written by the page; reports.json written by the relay) · data (DFS salaries, dfs_todo.json, dfs_nfl.json, dfs_mlb.json, mlb.json, mlb/ cache + log + lines; workflows dfs-build, dfs-mlb, mlb-build).
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

## MLB for Opening Day 2027 - BUILT Nov 22 2026 (second session; all on package unless a branch is named)
Rich's goal: moneyline + run line + over/under picks that log, grade and report like football, and MLB DFS lineups with the NFL
freshness / weather / self-tuning treatment. Rules kept: every variable proven walk-forward; nothing gets stars or stakes unless it
beats the closing line out of sample. Result of that rule: MLB has NO AI-LOX and NO stars; 3 leans; every pick is logged with CLV.

Phase 0 (data): Retrosheet 2026 is NOT published yet (checked Nov 22: seasons/2026 404). `bash mlb/setup_data.sh` now also pulls closing
run lines. When 2026 appears: `bash mlb/setup_data.sh <dir> 2026`, `python3 mlb/make_hist.py <dir>`, commit mlb/hist + model.json + stats.json.

Phase 1 (lines and totals), done:
- Run line data: 2014-21 closing +/-1.5 with both prices from flancast90/sportsbookreview-scraper (TRAP: that file pairs the home row of
  one game with the visitor row of the next; build_data.py takes the team rows apart and re-matches them by team/date/runs, moneylines
  then equal the other archive on 99.9%), 2021-25 per-book "pointspread" from the existing odds file (live-line filter as for moneylines).
  26,026 clean games. No first-five-innings lines exist in either source (inning scores do), so F5 was not tested.
- Run line model (mlb/model.py, model.json "rl"): P(home by 2+) and P(away by 2+) = logistic(ln(lam ratio), model total), fitted
  walk-forward. Beats plain Poisson (which misses walk-offs / extra innings), loses to the closing price (log loss 0.6773 vs 0.6749).
  15 run-line rules at real closing prices (mlb/backtest.py): all fail in both windows.
- Variables re-tested walk-forward (mlb/research/variables.py -> variables_2013_2025.json): wind by park, bullpen fatigue (pitches,
  back-to-back arms, share of the pen used yesterday), umpire zone (K and BB rates), lineup vs the team's usual one, travel/rest/time zones.
  Only the umpire zone made the run model better out of sample (9 of 12 seasons on deviance and on winner log loss) -> ump_k, ump_bb are
  now in FEATS / model.json. Against the closing line nothing cleared |z|>=2 in both windows; 21 bets at closing prices, 0 pass.
  A within-season drift correction for totals was also tested: no gain, not used. Model now: winner 57.9% (line 58.1%), log loss 0.6754 (0.6730).
- Fresh inputs (mlb/inputs.py, in live.py): Stats API venue records give lat/lon, field azimuth and roof; Open-Meteo forecast at first
  pitch (one call per park; a 30-park call was rejected from Actions) -> temp + wind turned into out / in / across for that field; roof
  closed by a per-park rule from what each club did in 2026; plate umpire from schedule officials; posted lineups / probables as before.
  PROOF (mlb/research/wx_check.py on Actions, 2,429 games of 2026, wx_check_2026.json): forecast vs posted temp MAE 2.4F, wind MAE 2.2 mph,
  same out/in/across 70%, opposite 2%; run-model deviance neutral 2.3014 -> forecast 2.2846 -> posted 2.2806 (81% of the gain), bias
  -0.43 -> -0.16 runs; total MAE flat (3.507 vs 3.508). Roof rule right on 94% (cut-offs were set on the same season). Order used:
  posted game-day weather > forecast > 72F calm.
- Picks that count (mlb/picks.py, called by live.py): for every game a winner, a run line (side whose cover chance beats its no-vig price
  by more) and an over/under pick against the DraftKings line ESPN lists at build time; rule leans on top. Season log
  data:mlb/log_<season>.json: a game is rewritten on every build until first pitch, then frozen; graded from finals (Stats API);
  units at the logged prices; closing-line value against data:mlb/lines.json (the relay's last pregame look, keyed by ESPN id).
  Self-tuning = rule hit rates recomputed from the log every build (backtest prior + live, 50-pick promote / retire, same formula as
  rules_learn.py) -> mlb.json rule_stats; the page trusts those for MLB (the PC's rules.json does not carry them).
  mlb.json also carries record / record_7 / record_30 and each game's `logged` snapshot.
- Game-day runs: data-branch workflow "MLB build" (data:.github/workflows/mlb-build.yml) starts when mlb/_refresh.json is saved and runs
  mlb/run_refresh.py (live.py + dfs/build_mlb.py). The relay cron saves that file ~90 and ~30 min before each first-pitch window (25 min
  apart at least) and at 9 am CT, and saves closing lines for games inside their last 16 minutes. main's mlb.yml (8:23 am / 3:41 pm CT)
  still works as a backup for picks; a Claude session was REFUSED again when editing it (Nov 22) - the improved file is
  mlb/proposed_main_mlb.yml; ask Rich before copying it to main:.github/workflows/mlb.yml.
  _refresh.json test requests (nothing saved): {"test": {"dates": [...], "days": 1, "dfs": true, "fake_salaries": true}},
  {"feeds_test": true}, {"wx_check": 2026}, {"parity": 2025}. Read the run log with gh api .../actions/jobs/<id>/logs.
- Relay (dfs/worker.js; deployed by the other Nov 22 session as 23715ff4 with the MLB code in it): espnDay('MLB'), mlbOdds (both ESPN
  odds shapes), reports read data:mlb.json so MLB model picks need no open page (THE MLB MODEL block: winners, run line, totals, units,
  leans, closest / worst, live, still to start, season record), baseball wording in the prompt, /mlbtick?dry=1 to see what the cron
  would do. All of it is inert outside Mar 18 - Nov 5. Not in the deployed copy yet: the baseball lines added to the DFS "my read"
  prompt (SYSTEM) later on Nov 22 - they go out with the next relay deploy.
- Page (tracker_template.html; the MLB tab work is on main as of 0db8a21): MLB tab = record strip (season, last 7 days, leans, CLV),
  day buttons (yesterday from the log), per game: model score, the three picks with W/L (or ahead/behind live), prices, weather with
  wind out/in/across, forecast vs posted, umpire, starters-set / lineups-confirmed badges, line-moved note. Tracker: MLB button, MLB in
  the Model column (ATS = run line) and model list, leans in the pick rows, no stars for MLB (betStars returns 0). My bets: MLB by hand or
  screenshot (league MLB, ESPN ids, finals from ESPN); the My bets card moves into the MLB tab when football is out of season. A
  promoted MLB rule would appear as an AI-LOX automatically (tested by flipping a rule in the test harness).

Phase 2 (MLB DFS), done:
- dfs/mlb_opt.py: classic P,P,C,1B,2B,3B,SS,OF,OF,OF, $50,000, multi-position eligibility, <=5 hitters a team, 2+ games, no hitter
  against your own pitcher; GPP = a stack of 4 consecutive batting-order spots from a POSTED lineup (any team's when none is posted);
  cash ranks hitters on proj, pitchers on proj nudged to the floor. Showdown reuses optimizer.showdown. dfs/mlb_own.py = ownership estimate.
- Projections for the slate come out of the daily build (mlb/dfs_export.py -> data:mlb/dfs_proj.json; trees cached in
  data:mlb/dfs_models.joblib, refitted when seasons / features / scikit-learn change). dfs/build_mlb.py joins them to DraftKings salaries
  by name + team (names from the chadwick register + the Stats API), writes data:dfs_mlb.json. Unposted lineup: hitter kept at the odds
  his spot starts again (2021-25: 87-90% spots 1-4 ... 49% spot 9). DraftKings' out tag counts only when the lineup is not posted.
  Rain 50%+ flagged; 70%+ kept out of cash. Also built when MLB salary files arrive (data:.github/workflows/dfs-mlb.yml); the NFL
  workflow now ignores MLB salary files.
- Research + self-tuning (dfs/research/mlb_vars.py -> mlb_vars_2021_2026.json, dfs/mlb_tune.py, mlb_tune_init.json), on walk-forward
  projection misses 2021-25 (216k hitter rows, 24k starts): Vegas implied runs vs the run model is the big one (hitters t=+14.9,
  +0.70 DK pts per run; pitchers t=-5.6, and Vegas win chance t=+5.0), umpire walk rate (H t=+4.5, P t=-3.0), umpire K rate (H t=-2.4),
  park by batter side weak (half weight), tired bullpen nothing (weight 0, still graded). Out-of-sample error lower in every season for
  both roles (H RMSE 6.9966 -> 6.9930, slate rank corr 0.180 -> 0.183; P 10.204 -> 10.184), so both start switched on. Already inside
  the base model and flat: batting spot, platoon, wind, heat. Graded daily (pending rows keyed by day), weights move 30% toward the
  decayed fit. Not done: pitch mix vs batter (needs Statcast; reachability from Actions untested).
- Page: DFS tab shows NFL or MLB by season (switch when both), MLB lineups in roster order with batting spot / lineup-not-posted /
  rain / wind flags. PUBLISHED? Only if main:index.html is newer than package ff1c18b - check and republish if not.
- Tests (tests/README.md): picks_log_test.py, relay_mlb_test.mjs, make_mlb_fixtures.py + page_mlb_test.py (Chrome, ?asof=2027-06-15,
  mocked feeds), page_football_check.py (old vs new page in football season: identical), dfs_mlb_sim.py (a 2025 day end to end).
  Verified on Actions: replays of Sept 25-27 2026 (picks, log, DFS lineups on made-up prices), feeds test, forecast check.

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

## START HERE next session: spring-training dry run (late Feb 2027), then Opening Day
Nothing below could be checked in November because the feeds are empty out of season. Do them on the first days with real games:
1. ESPN's MLB odds on a pregame game: the build log line "lines: N of M games have a pregame DraftKings line" must be > 0 and the
   moneyline / total / run line with prices must be right (mlb/inputs.parse_odds, worker mlbOdds, page mlbOdds read both feed shapes;
   only the NFL shape could be seen live in November). If ESPN lists no MLB run-line prices, picks.game_picks falls back to the side
   the model has above 50% and units are not counted for it.
2. Relay in season: POST /mlbtick?dry=1 (X-Code) on a game day; then check that mlb/_refresh.json and mlb/lines.json get written and
   "MLB build" runs follow. Check a nightly report has the THE MLB MODEL block.
3. MLB salaries: confirm Tasker sends MLB slates (dfs_todo.json lists them once DraftKings posts MLB contests) and that
   "MLB DFS lineups" builds; check the name match count in its log ("N matched to projected starters") - it should be close to
   20 per game on the slate. Team codes DraftKings uses are mapped in build_mlb.DK_TEAM.
4. The page on a real day: MLB tab cards, line-moved note, My bets card under the MLB tab (football is out of season then), an MLB
   screenshot bet, DFS tab MLB lineups, "I entered this lineup" on an MLB lineup (auto scoring of entered MLB lineups is NOT built:
   dfsScore() is NFL-only).
5. After ~2 weeks: closing-line value in mlb.json record.clv - beating the close on more than half of picks is the first sign of an edge.
6. Ask Rich about mlb/proposed_main_mlb.yml (main workflow edit was refused for a Claude session).
7. Still open from the plan: optimizer weights are the NFL starting values (no real MLB contest results yet); pitch mix vs batter.

## Loose ends
- TE floor/ceiling calibration in dfs/nfl_model.py is off.
- package branch lacks cfb/, nfl/, paths.py, run_week.py, setup_data.sh (they live on Rich's PC) — needed to move the weekly refresh to GitHub Actions.
