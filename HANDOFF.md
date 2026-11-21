# Bet Tracker — handoff (Nov 21 2026)

Repo: Dan2Duper/betting-model (public). Site: https://dan2duper.github.io/betting-model/ (main branch, index.html).
Branches: main (published page + t.js + dfs-todo workflow) · package (all code; tracker_template.html is the page source) ·
sync (state.json, report.json written by the page; reports.json written by the relay) · data (DFS salaries, dfs_todo.json, dfs_nfl.json, dfs-build workflow).
Pushing to main/package/sync/data from a Claude session with git works.

## Working now
- NFL DFS: Rich's Android phone (Tasker, every morning) pulls DK draftables -> relay /salaries -> data branch -> GitHub Action builds dfs_nfl.json -> DFS tab (cash/GPP, classic + showdown, write-ups, shuffle, "my read" AI box).
- Relay = Cloudflare Worker "bet-tracker-relay" (code: dfs/worker.js; deploy by pasting into the dashboard editor in Rich's Chrome).
  Routes: / (DFS AI read), /salaries, /betslip (screenshot bet import), /report (?force=now|preview|update|final, ?dry=1).
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

## Next: MLB live (before Opening Day, late March 2027)
1. 2026 season: rerun `bash mlb/setup_data.sh <dir> 2026` once Retrosheet publishes 2026 (chadwickbureau/retrosheet seasons/2026 and
   retrosplits playing-2026.csv were not up yet on Nov 21). No closing odds source for 2026 yet.
2. Daily job (GitHub Action on main, like dfs): mlb/live.py pulls MLB Stats API (statsapi.mlb.com schedule with
   hydrate=probablePitcher,lineups,officials,weather + /game/{pk}/boxscore), converts to the games/pit/bat schema (MLBAM -> Retrosheet ids
   via chadwickbureau/register people-*.csv; unknown rookies keep "mlbam:<id>"), appends today's games and runs features.build() on
   history + season-to-date so live features are identical to the backtest. Output mlb.json on the data branch.
   Lineups post ~3 h before first pitch: until then project each team's last lineup against the same-handed starter.
3. Page: MLB in Week/Live/Tracker (snapshot ships base runs at 72F/no wind + the GLM temp/wind weights; the page applies the forecast,
   as it does for football), reports (add an MLB espnDay URL in dfs/worker.js runReports), moneyline + total + run line bet types.
4. MLB DFS: projections from the same features + dfs/optimizer.py (P,P,C,1B,2B,3B,SS,OF,OF,OF, $50K, 4-5 hitter stacks).

## Loose ends
- TE floor/ceiling calibration in dfs/nfl_model.py is off.
- package branch lacks cfb/, nfl/, paths.py, run_week.py, setup_data.sh (they live on Rich's PC) — needed to move the weekly refresh to GitHub Actions.
