# Tests (run in a Claude session; nothing here runs on GitHub)
- `relay_mlb_test.mjs` (node): loads dfs/worker.js with mocked ESPN / GitHub / Anthropic and checks the MLB report facts, the rebuild
  kicks and the closing-line snapshot. `node tests/relay_mlb_test.mjs`
- `make_mlb_fixtures.py`: builds a realistic mlb.json, season log and ESPN scoreboard from the 2025 backtest data (needs the data
  folder from `bash mlb/setup_data.sh`), dated June 2027.
- `page_mlb_test.py` (playwright + Chrome): opens the page at ?asof=2027-06-15 with those fixtures; prints the MLB tab, Tracker and
  errors, saves screenshots. `page_football_check.py`: compares an old and a new build of the page in football season (same output = no regression).
Paths inside the scripts point at /home/claude/work; adjust when reusing.
