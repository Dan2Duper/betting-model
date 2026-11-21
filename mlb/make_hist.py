"""Compact history for the daily job: the columns features.build() needs, recent seasons only (older games carry <2% weight).
Usage: python3 mlb/make_hist.py [DATA_DIR] [FIRST_SEASON]  -> mlb/hist/*.parquet"""
import os, sys, pandas as pd
D = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/mlbdata'; FIRST = int(sys.argv[2]) if len(sys.argv) > 2 else 2016
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hist'); os.makedirs(H, exist_ok=True)
GC = ['gid', 'season', 'date', 'num', 'away', 'home', 'aruns', 'hruns', 'park', 'ump', 'asp', 'hsp', 'asp_name', 'hsp_name', 'a_lineup', 'h_lineup', 'a_pos', 'h_pos', 'dn', 'home_lg', 'temp', 'windspeed', 'winddir', 'sky', 'usedh']
G = pd.read_parquet(f'{D}/games.parquet'); G = G[G.season >= FIRST][GC]; G.to_parquet(f'{H}/games.parquet', compression='zstd')
for n in ('pit', 'bat'):
    t = pd.read_parquet(f'{D}/{n}.parquet'); t = t[t.date >= f'{FIRST}-01-01']
    for c in t.columns:
        if c.startswith(('P_', 'B_')): t[c] = t[c].astype('int16')
    t.drop(columns=[c for c in ('opp', 'slot', 'seq') if c in t]).to_parquet(f'{H}/{n}.parquet', compression='zstd')
h = pd.read_parquet(f'{D}/hands.parquet').drop_duplicates('pid'); h.to_parquet(f'{H}/hands.parquet', compression='zstd')
print({f: os.path.getsize(f'{H}/{f}') for f in os.listdir(H)})
