"""Build the self-updating 'Bet Tracker.html': embeds the weekly model snapshot, the bet/pick logs and the prediction log.
Also merges logs/page_changes.json (strikes, edited stakes/odds, bets the page made) into the CSV logs first.
Usage: python3 tracker.py [output.html]   (default: Bet Tracker.html next to the betting_models folder)"""
import os, sys, json, datetime as dt
import pandas as pd
PKG = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(PKG), 'Bet Tracker.html')
L = f'{PKG}/logs'
NAMES = {'cfb_total_gap7': 'CFB model total off by 7+', 'cfb_tz_home': 'CFB east team flying west (home)',
         'cfb_tz_home_late': 'CFB east team west, late kick (home)', 'cfb_wind_under': 'CFB wind 13+ mph under',
         'nfl_wind_under_10_14': 'NFL wind 10-14 mph under', 'nfl_wind_under_15plus': 'NFL wind 15+ mph under',
         'nfl_side_gap4': 'NFL side lean (model gap 4+)', 'nfl_key_side': 'NFL key-number side (lean)', 'nfl_teaser_dog': 'NFL underdog teaser (2-team, +1.5/+2.5 to +7.5/+8.5)', 'nfl_teaser_leg': 'NFL teaser leg (graded at teased line)', 'nfl_early_dog': 'NFL weeks 1-2 underdog (lean)', 'nfl_mnf_under': 'NFL Monday night under (lean)', 'cfb_total68_under': 'CFB total 68+ under', 'cfb_letdown': 'CFB letdown fade (after beating a top team)', 'cfb_road_bounce28': 'CFB road team off a 28+ point loss', 'cfb_late_under': 'CFB late-night under (lean)', 'cfb_secb1g_road_bounce': 'SEC/Big Ten road team off a 21+ loss or 3+ game skid'}
def read(name):
    f = f'{L}/{name}'
    return pd.read_csv(f, dtype=str).fillna('') if os.path.exists(f) else pd.DataFrame()

# ---- merge changes saved from the page ----
chg_f = f'{L}/page_changes.json'
if os.path.exists(chg_f):
    C = json.load(open(chg_f)); B = read('bets_log.csv'); P = read('picks_log.csv')
    if len(B):
        B['id'] = B.date + '|' + B.game_id + '|' + B.rule
        B = B[~B.id.isin(set(C.get('struck', [])))]
        for bid, o in (C.get('overrides') or {}).items():
            m = B.id == bid
            for k in ('stake', 'odds', 'line'):
                if o.get(k) not in (None, ''): B.loc[m, k] = str(o[k])
    new = pd.DataFrame([b for b in C.get('bets', []) if b.get('id') not in set(B.get('id', []))])
    if len(new):
        new = new.reindex(columns=[c for c in B.columns if c != 'id'] or new.columns).fillna('')
        new['id'] = new.date + '|' + new.game_id.astype(str) + '|' + new.rule
        B = pd.concat([B, new.astype(str)])
    if len(B): B.drop(columns='id').to_csv(f'{L}/bets_log.csv', index=False)
    pk = pd.DataFrame(C.get('picks', []))
    if len(pk) and len(P):
        pk = pk.reindex(columns=P.columns).fillna('').astype(str)
        P = pd.concat([P, pk]).drop_duplicates(['league', 'game_id', 'rule'], keep='first'); P.to_csv(f'{L}/picks_log.csv', index=False)
    if C.get('mine') is not None:   # my own bets: the page (synced state) is the full list
        M = pd.DataFrame(C['mine'])
        if len(M): M.astype(str).to_csv(f'{L}/my_bets_log.csv', index=False)
    os.replace(chg_f, f'{L}/page_changes_merged_{dt.date.today():%Y%m%d}.json')
    print('merged page changes')

bets, picks, pred = read('bets_log.csv').to_dict('records'), read('picks_log.csv').to_dict('records'), read('predictions_log.csv').to_dict('records')
mine = read('my_bets_log.csv').to_dict('records')
snap = json.load(open(f'{L}/snapshot.json'))
snap['rules'] = json.load(open(f'{PKG}/rules.json')); snap['keys'] = json.load(open(f'{PKG}/keys.json'))
names = dict(NAMES)
data = dict(snapshot=snap, bets=bets, picks=picks, predlog=pred, names=names, mine=mine)
html = open(f'{PKG}/tracker_template.html').read().replace('__DATA__', json.dumps(data).replace('</', '<\\/'))
html = html.replace('__PAGEVER__', dt.datetime.now().strftime('%Y%m%d%H%M%S'))
open(OUT, 'w').write(html); print('TRACKER', OUT, f'({len(bets)} bets, {len(picks)} picks, {len(mine)} own bets, {len(pred)} predictions)')
