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

## Next: MLB (build like football)
1. Data: Retrosheet game logs + event files (github chadwickbureau/retrosheet, seasons/YYYY/GLYYYY.TXT) for results, starters, park, weather, umpire.
   Odds/closing lines: ethanbell528-cmd/fda-project-1 (2010-2021) and ArnavSaraogi/mlb-odds-scraper dataset (2021-2025).
2. Model: walk-forward (no look-ahead) run/win model — starting pitcher form, bullpen, lineup, park, weather; backtest every season.
3. Rules: an AI-LOX rule only counts if it beats the CLOSING line out of sample (same bar as football; kill switch below 52.4%).
4. Page: MLB in Week/Live/Tracker tabs + reports (runReports handles leagues generically; add an MLB espnDay URL).
5. MLB DFS: projections + optimizer reuse dfs/optimizer.py (DK MLB classic: P,P,C,1B,2B,3B,SS,OF,OF,OF, $50K; stacks of 4-5 hitters);
   dk_slate.py / make_todo.py already list MLB slates; the phone already sends MLB salaries.
Season starts late March 2027, so there is time to backtest properly.

## Loose ends
- TE floor/ceiling calibration in dfs/nfl_model.py is off.
- package branch lacks cfb/, nfl/, paths.py, run_week.py, setup_data.sh (they live on Rich's PC) — needed to move the weekly refresh to GitHub Actions.
