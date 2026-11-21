"""MLB history -> three tidy tables (games, pitcher lines, batter lines). Run once per season refresh.
Sources (all free): Retrosheet game logs + event-file info records + rosters (github chadwickbureau/retrosheet),
per-game player lines (chadwickbureau/retrosplits daybyday), closing odds 2010-21 (ethanbell528-cmd/fda-project-1, from
SportsbookReviewsOnline) and 2021-25 per-book open/close (ArnavSaraogi/mlb-odds-scraper release dataset, from SportsBookReview).
Usage: python3 mlb/build_data.py [DATA_DIR]      DATA_DIR holds raw/ (see mlb/setup_data.sh); writes DATA_DIR/*.parquet"""
import os, sys, csv, json, glob, statistics
import numpy as np, pandas as pd
D = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MLB_DATA', '/home/claude/mlbdata'); RAW = f'{D}/raw'

# ---------------- games (Retrosheet game logs) ----------------
OFF = ['ab', 'h', 'd', 't', 'hr', 'rbi', 'sh', 'sf', 'hbp', 'bb', 'ibb', 'k', 'sb', 'cs', 'gdp', 'ci', 'lob']
rows = []
for f in sorted(glob.glob(f'{RAW}/GL20*.TXT')):
    for r in csv.reader(open(f, encoding='latin-1')):
        if len(r) < 160 or r[9] == '' or r[10] == '': continue
        g = dict(date=r[0], num=int(r[1]), dow=r[2], away=r[3], away_lg=r[4], home=r[6], home_lg=r[7], ar=int(r[9]), hr_=int(r[10]),
                 outs=int(r[11] or 0), dn=r[12], park=r[16], ump=r[77], asp=r[101], hsp=r[103], asp_name=r[102], hsp_name=r[104])
        for i, k in enumerate(OFF):
            g['a_' + k] = int(r[21 + i] or 0); g['h_' + k] = int(r[49 + i] or 0)
        g['a_er'] = int(r[40] or 0); g['h_er'] = int(r[68] or 0); g['a_e'] = int(r[45] or 0); g['h_e'] = int(r[73] or 0)
        g['a_lineup'] = ','.join(r[105 + 3 * i] for i in range(9)); g['h_lineup'] = ','.join(r[132 + 3 * i] for i in range(9))
        g['a_pos'] = ','.join(r[107 + 3 * i] for i in range(9)); g['h_pos'] = ','.join(r[134 + 3 * i] for i in range(9))
        rows.append(g)
G = pd.DataFrame(rows).rename(columns={'hr_': 'hruns', 'ar': 'aruns'})
G['gid'] = G.home + G.date + G.num.astype(str); G['season'] = G.date.str[:4].astype(int); G['date'] = pd.to_datetime(G.date)
assert G.gid.is_unique

# ---------------- weather / start time (event-file info records) ----------------
info, cur = {}, None
for line in open(f'{RAW}/info_all.txt', encoding='latin-1'):
    p = line.rstrip('\n').split(',')
    if p[0] == 'id': cur = info.setdefault(p[1], {})
    elif cur is not None and len(p) >= 3 and p[1] in ('temp', 'winddir', 'windspeed', 'sky', 'precip', 'starttime', 'usedh', 'fieldcond'): cur[p[1]] = p[2]
W = pd.DataFrame.from_dict(info, orient='index').rename_axis('gid').reset_index()
W['temp'] = pd.to_numeric(W.temp, errors='coerce'); W.loc[W.temp <= 0, 'temp'] = np.nan
W['windspeed'] = pd.to_numeric(W.windspeed, errors='coerce'); W.loc[W.windspeed < 0, 'windspeed'] = np.nan
G = G.merge(W, on='gid', how='left')

# ---------------- handedness (rosters) ----------------
H = {}
for f in glob.glob(f'{RAW}/ros/*.ROS'):
    for r in csv.reader(open(f, encoding='latin-1')):
        if len(r) >= 5: H[r[0]] = (r[3], r[4])
pd.DataFrame([(k, b, t) for k, (b, t) in H.items()], columns=['pid', 'bats', 'throws']).to_parquet(f'{D}/hands.parquet')

# ---------------- player lines (retrosplits daybyday) ----------------
BC = ['B_PA', 'B_AB', 'B_H', 'B_2B', 'B_3B', 'B_HR', 'B_BB', 'B_IBB', 'B_SO', 'B_HP', 'B_SF', 'B_SB']
PC = ['P_G', 'P_GS', 'P_OUT', 'P_TBF', 'P_R', 'P_ER', 'P_H', 'P_HR', 'P_BB', 'P_IBB', 'P_SO', 'P_HP', 'P_GO', 'P_AO', 'P_PITCH']
bat, pit = [], []
for f in sorted(glob.glob(f'{RAW}/playing-20*.csv')):
    p = pd.read_csv(f, usecols=['game.key', 'game.date', 'season.phase', 'team.key', 'opponent.key', 'person.key', 'slot', 'seq'] + BC + PC, low_memory=False)
    p = p[p['season.phase'] == 'R'].rename(columns={'game.key': 'gid', 'game.date': 'date', 'team.key': 'team', 'opponent.key': 'opp', 'person.key': 'pid'}).drop(columns='season.phase')
    b = p[p.B_PA.fillna(0) > 0][['gid', 'date', 'team', 'opp', 'pid', 'slot', 'seq'] + BC]; bat.append(b)
    q = p[p.P_G.fillna(0) > 0][['gid', 'date', 'team', 'opp', 'pid'] + PC]; pit.append(q)
bat = pd.concat(bat).fillna(0); pit = pd.concat(pit).fillna(0)
for d_ in (bat, pit): d_['date'] = pd.to_datetime(d_.date)
bat.to_parquet(f'{D}/bat.parquet'); pit.to_parquet(f'{D}/pit.parquet')

# ---------------- odds 2010-21: one closing moneyline / total / run line per game ----------------
A = pd.read_csv(f'{D}/odds_a/data/mlb.csv', low_memory=False); A = A[A.moneyline.notna() & (A.game_type == 'regular')]
key = G.set_index('gid')[['hruns', 'aruns']]
A = A.join(key, on='game_id', how='inner')
Ah = A[(A.score_for == A.hruns) & (A.score_against == A.aruns)].drop_duplicates('game_id').set_index('game_id')
Aa = A[(A.score_for == A.aruns) & (A.score_against == A.hruns)].drop_duplicates('game_id').set_index('game_id')
OA = pd.DataFrame(dict(ml_h=Ah.moneyline, ml_a=Aa.moneyline, tot=Ah.total_line, rl_h=Ah.line)).dropna(subset=['ml_h', 'ml_a'])
OA['src'] = 'sbro'

# ---------------- odds 2021-25: DraftKings close (fallback: median book), open, consensus ----------------
T = {'CHC': 'CHN', 'CWS': 'CHA', 'CHW': 'CHA', 'KC': 'KCA', 'LAA': 'ANA', 'LAD': 'LAN', 'NYM': 'NYN', 'NYY': 'NYA', 'SD': 'SDN', 'SF': 'SFN', 'STL': 'SLN', 'TB': 'TBA', 'WSH': 'WAS', 'WSN': 'WAS', 'AZ': 'ARI'}
def imp(o): return -o / (-o + 100) if o < 0 else 100 / (o + 100)
def ok2(a, b):
    try: s = imp(a) + imp(b)
    except Exception: return False
    return a and b and abs(a) >= 100 and abs(b) >= 100 and 1.0 <= s <= 1.12
def lines(lst, fa, fb, extra, which):
    out = []
    for o in lst or []:
        L = o.get(which) or {}; a, b = L.get(fa), L.get(fb)
        if a is None or b is None or not ok2(a, b): continue
        x = L.get(extra) if extra else 0.0
        if extra and x is None: continue
        out.append(dict(book=o['sportsbook'], a=a, b=b, x=float(x), p=imp(a) / (imp(a) + imp(b))))
    return out
def pick(lst, fa, fb, extra=None):
    """One trustworthy closing line per game. SportsBookReview's 'currentLine' is sometimes a LIVE in-game line (DraftKings and FanDuel
    for nearly all of Sept-Oct 2021, a few percent of games elsewhere), so the reference is the cross-book median; the price is DraftKings'
    when DraftKings sits on the reference, otherwise the book nearest the median. Returns the line, the opening reference and a 'suspect'
    flag (reference moved implausibly far from the open: live line or a late scratch) so those games can be left out of bet grading."""
    C = lines(lst, fa, fb, extra, 'currentLine'); Op = lines(lst, fa, fb, extra, 'openingLine')
    if not C: return None
    ref_x = statistics.median_low([c['x'] for c in C]) if extra else 0.0
    Cx = [c for c in C if c['x'] == ref_x]; ref_p = float(np.median([c['p'] for c in Cx]))
    dk = [c for c in Cx if c['book'] == 'draftkings' and abs(c['p'] - ref_p) <= 0.025]
    c = dk[0] if dk else min(Cx, key=lambda c: abs(c['p'] - ref_p))
    o_x = statistics.median_low([o['x'] for o in Op]) if (extra and Op) else np.nan
    Ox = [o for o in Op if o['x'] == o_x] if extra else Op
    o_p = float(np.median([o['p'] for o in Ox])) if Ox else np.nan
    dko = [o for o in Op if o['book'] == 'draftkings']
    sus = (abs(ref_x - o_x) >= 1.5 if extra and Op else False) or (not extra and Op and abs(ref_p - o_p) > 0.10)
    return dict(a=c['a'], b=c['b'], x=c['x'], p=ref_p, book=c['book'], n=len(C), open_x=o_x, open_p=o_p,
                dk_open=(dko[0]['a'], dko[0]['b'], dko[0]['x']) if dko else (np.nan, np.nan, np.nan), suspect=bool(sus))
J = json.load(open(f'{RAW}/mlb_odds_dataset.json'))
gk = {}
for r in G[G.season >= 2021].itertuples(): gk.setdefault((str(r.date.date()), r.home, r.away), []).append(r)
ob, miss = {}, 0
for day, games in J.items():
    for g in sorted(games, key=lambda x: x['gameView'].get('startDate') or ''):
        v = g['gameView']
        if v.get('gameType') not in ('R', 'Unknown') or not str(v.get('gameStatusText', '')).startswith('Final'): continue
        h = v['homeTeam']['shortName']; a = v['awayTeam']['shortName']; h = T.get(h, h); a = T.get(a, a)
        if h == 'OAK' and day >= '2025': h = 'ATH'
        if a == 'OAK' and day >= '2025': a = 'ATH'
        cands = [r for r in gk.get((day, h, a), []) if r.gid not in ob and r.hruns == v.get('homeTeamScore') and r.aruns == v.get('awayTeamScore')]
        if not cands: miss += v.get('gameType') == 'R'; continue
        o = g['odds']; r = cands[0]
        M = pick(o.get('moneyline'), 'homeOdds', 'awayOdds'); Tt = pick(o.get('totals'), 'overOdds', 'underOdds', 'total')
        if M is None: continue
        d = dict(ml_h=M['a'], ml_a=M['b'], ml_book=M['book'], books=M['n'], cons_p_h=M['p'], open_p_h=M['open_p'], open_ml_h=M['dk_open'][0], open_ml_a=M['dk_open'][1],
                 ml_suspect=M['suspect'], start_utc=v.get('startDate'), src='sbr')
        if Tt is not None:
            d.update(tot=Tt['x'], over=Tt['a'], under=Tt['b'], tot_book=Tt['book'], open_tot=Tt['open_x'], open_over=Tt['dk_open'][0], open_under=Tt['dk_open'][1], tot_suspect=Tt['suspect'])
        ob[r.gid] = d
OB = pd.DataFrame.from_dict(ob, orient='index').dropna(subset=['ml_h', 'ml_a'])
print('2021-25 odds matched', len(OB), 'unmatched regular-season odds rows', miss)
# 2021 is in both sources. SportsbookReviewsOnline's archive has an explicit pregame "close" column, so it is the truth for 2021
# (DraftKings/FanDuel were live lines that fall); keep the per-book over/under prices only where the total agrees.
both = OA.index.intersection(OB.index)
pa = np.array([imp(h) / (imp(h) + imp(a)) for h, a in zip(OA.loc[both].ml_h, OA.loc[both].ml_a)])
print('overlap 2021:', len(both), 'games; cleaned close vs archive close within 3 pts of win prob on', round(float((np.abs(pa - OB.loc[both].cons_p_h.values) <= 0.03).mean()), 3),
      '| totals equal on', round(float((OA.loc[both].tot == OB.loc[both].tot).mean()), 3))
same_tot = OA.loc[both].tot == OB.loc[both].tot
for c in ('ml_h', 'ml_a', 'tot'): OB.loc[both, c] = OA.loc[both, c]
OB.loc[both, 'cons_p_h'] = pa; OB.loc[both, 'ml_suspect'] = False; OB.loc[both, 'ml_book'] = 'sbro'; OB.loc[both, 'src'] = 'sbro+sbr'
OB.loc[both[~same_tot.values], ['over', 'under']] = np.nan; OB.loc[both, 'tot_suspect'] = False
OB = OB[~((OB.src == 'sbr') & (OB.index.str[3:7] == '2021'))]          # 2021 games with no archive line: skip
O = pd.concat([OA[~OA.index.isin(OB.index)], OB]).rename_axis('gid').reset_index()
for c in ('ml_suspect', 'tot_suspect'): O[c] = O[c].fillna(False).astype(bool)
# sanity: a handful of archive rows carry typos (totals of 100+, half-cent moneylines); live totals far from any real line are flagged
bad = (O.tot > 20) | (O.tot < 5) | ((O.ml_h.abs() % 1) != 0)
O.loc[(O.src == 'sbro') & bad, ['ml_h', 'ml_a', 'tot']] = np.nan
O.loc[(O.src != 'sbro') & ((O.tot > 20) | (O.tot < 5.5)), 'tot_suspect'] = True
O = O.dropna(subset=['ml_h', 'ml_a'])
G = G.merge(O, on='gid', how='left')
for c in ('ml_suspect', 'tot_suspect'): G[c] = G[c].fillna(False).astype(bool)
G.to_parquet(f'{D}/games.parquet')
print(G.groupby('season').agg(games=('gid', 'size'), ml=('ml_h', 'count'), tot=('tot', 'count'), ou_odds=('over', 'count'), temp=('temp', 'count'), ml_suspect=('ml_suspect', 'sum'), tot_suspect=('tot_suspect', 'sum')).to_string())
