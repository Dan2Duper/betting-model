"""Local end-to-end DFS test: pretend it is the morning of DAY (history cut there), export projections the way mlb/live.py does, build lineups with made-up prices."""
import sys, os, json, glob, csv, subprocess, pandas as pd, numpy as np
PKG='/home/claude/betting-model'; sys.path.insert(0, PKG+'/mlb'); sys.path.insert(0, PKG+'/dfs')
from features import build, FEATS
import picks, dfs_export
DAY=sys.argv[1] if len(sys.argv)>1 else '2025-07-19'; CACHE=sys.argv[2] if len(sys.argv)>2 else '/home/claude/work/dfs/cache'; os.makedirs(CACHE,exist_ok=True)
D='/home/claude/mlbdata'; d0=pd.Timestamp(DAY)
G=pd.read_parquet(f'{D}/games.parquet'); pit=pd.read_parquet(f'{D}/pit.parquet'); bat=pd.read_parquet(f'{D}/bat.parquet'); hands=pd.read_parquet(f'{D}/hands.parquet').drop_duplicates('pid')
G=G[(G.season>=2016)&(G.date<=d0)].copy(); odds=G[G.date==d0].set_index('gid')[['ml_h','ml_a','tot','over','under','rl','rl_h','rl_a','hruns','aruns']].copy()
G.loc[G.date==d0,['hruns','aruns']]=np.nan; pit=pit[(pit.date>='2016-01-01')&(pit.date<d0)]; bat=bat[(bat.date>='2016-01-01')&(bat.date<d0)]
for c in ('ump','asp','hsp','sky','winddir','usedh','a_lineup','h_lineup','a_pos','h_pos'): G[c]=G[c].fillna('')
names={}
for f in glob.glob(f'{D}/raw/ros/*202[3-5].ROS'):
    for r in csv.reader(open(f,encoding='latin-1')):
        if len(r)>=3: names[r[0]]=f'{r[2]} {r[1]}'
M=json.load(open(PKG+'/mlb/model.json')); RJ=json.load(open(PKG+'/rules.json'))['mlb']
F=build(G,pit,bat,hands); F['lam']=np.exp(M['b0']+((F[FEATS].values-np.array(M['mu']))/np.array(M['sd']))@np.array(M['beta']))
h=F[F.is_home==1].set_index('gid'); a=F[F.is_home==0].set_index('gid'); games=[]
nn=lambda v: None if v!=v else float(v)
for i,r in enumerate(G[G.date==d0].itertuples()):
    lh,la=float(h.lam[r.gid]),float(a.lam[r.gid]); o=odds.loc[r.gid]
    g=dict(id=r.gid,pk=800000+i,date=DAY,kick=f'{DAY}T{17+i%7}:10:00Z',away=r.away,home=r.home,park=r.park,pa=round(la,2),ph=round(lh,2),total=round(lh+la,2),p_home=float(1/(1+np.exp(-(M['win']['a']+M['win']['k']*np.log(lh/la))))),
           sp_away_known=True,sp_home_known=True,lineup_away='posted' if i%3 else 'last game',lineup_home='posted',dome=bool(h.dome[r.gid]),wind_out=float(h.wind_out[r.gid]),wind_in=float(h.wind_in[r.gid]),temp=float(h.temp[r.gid]),wx_more={'rain':75.0} if i==2 else {})
    g['p_h2'],g['p_a2']=picks.rl_probs(M,lh,la)
    g['line']=dict(mh=nn(o.ml_h),ma=nn(o.ml_a),total=nn(o.tot),over=nn(o.over),under=nn(o.under),rl=nn(o.rl),rl_h=nn(o.rl_h),rl_a=nn(o.rl_a)) if o.ml_h==o.ml_h else None
    picks.game_picks(g,M,RJ); games.append(g)
import time; t=time.time()
n=dfs_export.export(G,pit,bat,hands,F,DAY,M,CACHE,games,names,f'{CACHE}/dfs_proj.json'); print('exported',n,'rows in',round(time.time()-t),'s')
J=json.load(open(f'{CACHE}/dfs_proj.json')); R=pd.DataFrame(J['rows']); print(R.groupby('role')[['base','floor','ceil','veg_gap','veg_win','pen_tired','ump_k','ump_bb','park_hand']].mean().round(3).to_string()); print('no name:',int((R.name=='').sum()),'| actual days:',{k:len(v) for k,v in J['actual'].items()})
json.dump(dict(games=games),open(f'{CACHE}/games.json','w')); odds.reset_index().to_json(f'{CACHE}/truth_{DAY}.json',orient='records')
