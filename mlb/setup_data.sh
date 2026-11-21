#!/bin/bash
# Fetch everything the MLB backtest needs (about 900 MB, a few minutes) and build the tables.
# Usage: bash mlb/setup_data.sh [DATA_DIR] [LAST_SEASON]     default /home/claude/mlbdata 2025
set -e
D=${1:-/home/claude/mlbdata}; LAST=${2:-2025}; PKG=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$D/raw/ev" "$D/raw/ros"; cd "$D/raw"
B=https://raw.githubusercontent.com/chadwickbureau
for y in $(seq 2008 $LAST); do
  [ -s GL$y.TXT ] || curl -sS -f -o GL$y.TXT $B/retrosheet/master/seasons/$y/GL$y.TXT || curl -sS -f -o GL$y.TXT $B/retrosheet/master/seasons/$y/gl$y.txt
  [ -s playing-$y.csv ] || curl -sS -f -o playing-$y.csv $B/retrosplits/master/daybyday/playing-$y.csv
  [ -s ev/TEAM$y ] || curl -sS -f -o ev/TEAM$y $B/retrosheet/master/seasons/$y/TEAM$y
done
for y in $(seq 2008 $LAST); do awk -F, -v y=$y '{print y"/"$1 y".ROS"}' ev/TEAM$y; done | xargs -P 8 -I{} sh -c '[ -s ros/$(basename {}) ] || curl -sS -f -o ros/$(basename {}) '$B'/retrosheet/master/seasons/{} || true'
for y in $(seq 2009 $LAST); do awk -F, -v y=$y '$1!="ALS" && $1!="NLS"{print y"/"y$1".EV"$2}' ev/TEAM$y; done | xargs -P 8 -I{} sh -c '[ -s ev/$(basename {}) ] || curl -sS -f -o ev/$(basename {}) '$B'/retrosheet/master/seasons/{} || echo MISSING {}'
grep -h -E "^(id|info)," ev/*.EV? > info_all.txt
[ -s mlb_odds_dataset.json ] || curl -sSL -f -o mlb_odds_dataset.json https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/download/dataset/mlb_odds_dataset.json
[ -d "$D/odds_a" ] || git clone --depth 1 https://github.com/ethanbell528-cmd/fda-project-1 "$D/odds_a"
cd "$PKG"
python3 mlb/build_data.py "$D" && python3 mlb/features.py "$D" > /dev/null && python3 mlb/model.py "$D" > /dev/null && python3 mlb/backtest.py "$D" && python3 mlb/model.py "$D" | tail -1
