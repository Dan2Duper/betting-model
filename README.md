# Rich's Betting Models (CFB + NFL)

Saved, trained models. Nothing needs to be rebuilt week to week: you fill in two small slate files and run one command.

## Weekly run (about 1 minute of compute)
1. Unzip this package to `/home/claude/betting_models` and run `bash setup_data.sh 2026`. This fetches only the current season's play-by-play and schedules, about 40 MB in a few seconds.
2. Fill in the slate files, copying the previous week's files as templates:
   - `inputs/cfb_slate_<season>_w<week>.csv`: `away,home,spread,total,dk,qa,qh,note,temp,wind,gust,rain`
     - Use CFBD team names.
     - `spread` is the **home** line (negative = home favored).
     - `qa`/`qh` are the QB/injury value in points vs that team's season stats (backup QB about -3 to -7).
   - `inputs/nfl_slate_<season>_w<week>.csv`: `away,home,spread,total,wind,roof,qb_away,qb_home,note`
     - Lines default to the nflverse consensus if left blank.
     - QB names use the play-by-play format (`T.Huntley`); leave blank to use the team's last starter.
     - `wind` is the kickoff forecast for outdoor games.
3. Run `python3 run_week.py --season 2026 --cfb-week 9 --cfb-slate inputs/... --nfl-week 8 --nfl-slate inputs/...`
   - It prints only the qualifying picks.
   - It writes `Betting Model <date>.xlsx` (tabs: Picks, CFB, NFL, Rules) to `/home/claude/bm_out`.
   - It appends the picks to `logs/picks_log.csv`.
4. After the games, run `python3 grade.py` to fill in W/L/P and print the record by rule. Adjust `rules.json` only when a rule's record clearly says to.

## What qualifies as a pick (`rules.json`)
| Rule | Hit rate vs closing line |
|---|---|
| CFB model total off by 7+ points | 60% (55 games, 2025) |
| CFB east team traveling 2+ time zones west: bet home | 55% (129), 59% since 2021 |
| CFB wind 13+ mph: under | ~57% (published studies) |
| NFL wind 10+ mph: under | 59% at 10-14 mph (465), 55% at 15+ |
| NFL side gap 4+ | lean only, no proven edge |

## Models
- **CFB** (`cfb/model.json`): season-to-date, opponent-adjusted team stats (ppg, EPA, success, explosive, points per trip inside the 40, turnovers per play, plays, pass rate) + home + pregame Elo gap (0.0422 pts per Elo point). Ridge regression trained 2023-25.
- **NFL** (`nfl/model.json`): ridge opponent-adjusted ratings with a 4-week recency half-life and 0.2 prior-season weight, garbage time filtered out, play-by-play QB values, home / wind / division / travel. Trained 2014-25 and tested on 2023-25. History lives in `nfl/tg_hist.parquet`.

## Offseason refit (once a year)
- `bash setup_data.sh 2027 --train`
- `python3 nfl/model.py train 2026`
- The CFB refit uses the same features with `tg_hist.parquet` + the new season (see the training notes in `cfb/model.json`).

## Bets and bankroll (rules.json -> betting)
- A pick becomes a bet when its backtest hit rate is at least **55% (CFB)** or **53.5% (NFL)**.
- At most **5 bets per day**, ranked by hit rate. One bet per game.
- Time-zone spots where the model disagrees are excluded. `--skip "Away @ Home"` passes on a game (for example, stale QB data).
- **$130 per day**, split by fractional Kelly on a shrunk hit rate: the more proven the edge, the bigger the stake. Minimum stake is $5. Odds default to -110; edit `odds` in the log if DK differs.
- The bets go to `logs/bets_log.csv`.
- `python3 grade.py` fills in W/L/P and profit, then prints:
  - win % against the 52.4% break-even
  - $ won, $ **lost**, net and ROI
  - the worst drop from a high
  - the longest losing streak
  - results by day and by league

## Bet Tracker (self-updating HTML)
`Bet Tracker.html` sits next to the `betting_models` folder and opens in any browser.

**On open (or with Refresh) it pulls:**
- Final scores from ESPN, and grades every open bet and pick.
- DraftKings lines from The Odds API. Paste a free key in Settings. Without a key it uses the lines ESPN lists, and the nflverse consensus for NFL.
- Kickoff weather from Open-Meteo.
- QB status:
  - **NFL:** ESPN's official injury report (Out/Doubtful/Questionable/IR), plus each team's ESPN news ("X will start").
  - **College:** each team's ESPN news. It looks for the season starter's and last starter's names next to words like out, doubtful, questionable, will start, returns.
  - College adjustment: 5 points × (new QB is the season starter? 1 : 0 − the season starter's share of pass attempts).
  - NFL: switches to the right QB's play-by-play value.
  - Questionable QBs get a warning chip. Anything typed in by hand overrides the automatic call.

**Then it:**
- Runs the model on this week's games, flags picks, picks bets (CFB 55%+, NFL 53.5%+, top 5 a day) and splits each day's budget.

**Things you can change on the page:**
- Strike or undo any bet.
- Edit odds or stake (this locks it).
- Enter lines, QB changes or wind by hand.
- Add your own bets (any FBS or NFL game from ESPN's list): spread, total or moneyline, with odds and stake. They grade themselves and are tracked apart from the model; the Tracker has Model bets / My own bets / Every pick and a side-by-side comparison.
- **GitHub sync:** with a fine-grained GitHub key (betting-model only, Contents read/write) saved in Settings, every change is saved to `state.json` on the `sync` branch of Dan2Duper/betting-model. Every device reads it on open, so phone and PC match. `python3 sync_pull.py` writes that state to `logs/page_changes.json`; `tracker.py` merges it (own bets go to `logs/my_bets_log.csv`).
- Without a key, changes stay in that browser; Settings > Save changes file writes `page_changes.json` for `logs/`.

**Weekly refresh (scheduled, Tuesday morning):**
- `export_snapshot.py <season>` rebuilds team ratings and base predictions into `logs/snapshot.json`.
- `grade.py` then grades the logs and rebuilds the tracker. `tracker.py` merges `page_changes.json` first.

**Tabs:**
- This week: bets and games.
- Tracker: slicers by rule, conference, team, kickoff window, bet type, line size, wind, model gap and more.
- Model stats: backtests, factor weights, live accuracy, and each rule's live record.
