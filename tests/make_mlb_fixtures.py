import sys, json, pandas as pd, numpy as np, datetime as dt
sys.path.insert(0,'/home/claude/betting-model/mlb'); import picks
D='/home/claude/mlbdata'; PKG='/home/claude/betting-model'
R=pd.read_parquet(f'{D}/preds.parquet'); F=pd.read_parquet(f'{D}/feat.parquet'); G=pd.read_parquet(f'{D}/games.parquet').set_index('gid')
h=F[F.is_home==1].set_index('gid'); M=json.load(open(f'{PKG}/mlb/model.json')); RJ=json.load(open(f'{PKG}/rules.json'))['mlb']; BR={k:v for k,v in json.load(open(f'{PKG}/rules.json'))['betting']['rules'].items() if k.startswith('mlb_')}
R=R[(R.season==2025)&(R.date<='2025-06-16')].merge(h[['park_id','wind_out','wind_in','wind_x','dome','temp']],left_on='gid',right_index=True).sort_values(['date','gid']).reset_index(drop=True)
sh=lambda d: str(pd.Timestamp(d).date()).replace('2025','2027')
nn=lambda v: None if v!=v else float(v)
games=[]; finals={}
for i,r in enumerate(R.itertuples()):
    g0=G.loc[r.gid]; hour=17+(i%8)
    g=dict(id=r.gid,pk=700000+i,espn=str(900000+i),date=sh(r.date),kick=f"{sh(r.date)}T{hour:02d}:10:00Z" if hour<24 else f"{sh(r.date)}T23:40:00Z",away=r.away,home=r.home,venue='Ballpark',park=r.park_id,num=0,sched7=False,
           sp_away=g0.asp_name,sp_home=g0.hsp_name,sp_away_known=True,sp_home_known=True,lineup_away='posted',lineup_home='posted' if i%5 else 'last game',pa=round(r.lam_a,2),ph=round(r.lam_h,2),
           p_home=round(float(1/(1+np.exp(-(M['win']['a']+M['win']['k']*np.log(r.lam_h/r.lam_a))))),4),total=round(r.lam_h+r.lam_a,2),wx='posted' if i%3 else 'forecast',temp=round(float(r.temp)),wind_out=round(float(r.wind_out)),wind_in=round(float(r.wind_in)),wind_x=round(float(r.wind_x)),dome=bool(r.dome),
           ump='Test Ump' if i%2 else '',wx_more={'rain':45.0} if i%7==0 else {})
    g['p_h2'],g['p_a2']=[round(x,4) for x in picks.rl_probs(M,r.lam_h,r.lam_a)]
    L=dict(mh=nn(r.ml_h),ma=nn(r.ml_a),total=nn(r.tot),over=nn(r.over),under=nn(r.under),rl=nn(r.rl),rl_h=nn(r.rl_h),rl_a=nn(r.rl_a),src='DraftKings',at=sh(r.date)+'T15:00+00:00')
    g['line']=L if L['mh'] is not None else None
    picks.game_picks(g,M,RJ); games.append(g); finals[str(g['pk'])]=(int(r.aruns),int(r.hruns))
TODAY='2027-06-15'; TOM='2027-06-16'
log=dict(season=2027,games={}); now=dt.datetime(2027,1,1,tzinfo=dt.timezone.utc)
past=[g for g in games if g['date']<TODAY]; today=[g for g in games if g['date']==TODAY]; tom=[g for g in games if g['date']==TOM]
picks.update_log(log,past+today,{},{},now)
# closing lines for a few past games (move the total half a run)
closes={}
for g in past[-40:]:
    if g.get('line') and g['line'].get('total'): c=dict(g['line']); c['total']=g['line']['total']+(0.5 if g['pk']%2 else -0.5); closes[g['espn']]=c
picks.update_log(log,[],{k:v for k,v in finals.items() if int(k)<today[0]['pk']},closes,now)
# today: first 5 final, next 3 live (no final), rest pregame
fin_today={str(g['pk']):finals[str(g['pk'])] for g in today[:5]}
picks.update_log(log,[],fin_today,{},now)
log['rule_stats']=picks.learn(log,BR); log['record']=picks.record(log); log['record_7']=picks.record(log,since='2027-06-08'); log['record_30']=picks.record(log,since='2027-05-16'); log['updated']=now.isoformat()
for g in tom[::2]: g['line']=None; picks.game_picks(g,M,RJ)   # some of tomorrow's games have no line yet
out=dict(built='2027-06-15T20:41:00+00:00',date=TODAY,model=dict(trained=M['trained'],over=M['over'],win=M['win'],rl=M['rl']),rules=RJ,wx={},games=today+tom,rule_stats=log['rule_stats'],record=log['record'],record_7=log['record_7'],record_30=log['record_30'],log='mlb/log_2027.json',inputs=dict(weather=['forecast','posted'],lines=len(today)))
for g in out['games']:
    rec=log['games'].get(str(g['pk']))
    if rec: g['logged']={k:rec[k] for k in ('line','picks','rules','final','res','units','close','clv','built','status') if k in rec}
json.dump(out,open('mlb.json','w')); json.dump(log,open('log_2027.json','w'),separators=(',',':'))
# ESPN scoreboard fixtures
ESPN={'ANA':'LAA','CHN':'CHC','CHA':'CHW','KCA':'KC','LAN':'LAD','NYN':'NYM','NYA':'NYY','SDN':'SD','SFN':'SF','SLN':'STL','TBA':'TB','WAS':'WSH'}
NAME={'NYA':('New York','Yankees'),'NYN':('New York','Mets'),'BOS':('Boston','Red Sox'),'CHN':('Chicago','Cubs'),'CHA':('Chicago','White Sox')}
def ev(g,state,done,detail,sc,odds):
    tm=lambda c:{'abbreviation':ESPN.get(c,c),'location':NAME.get(c,(c,c))[0],'name':NAME.get(c,(c,c+' nick'))[1],'id':c}
    L=g.get('line') or {}
    o=[{'provider':{'name':'DraftKings'},'overUnder':L.get('total'),'spread':abs(L['rl']) if L.get('rl') else None,'overOdds':L.get('over'),'underOdds':L.get('under'),
        'homeTeamOdds':{'favorite':(L.get('rl') or 0)<0,'moneyLine':(L.get('mh') or 0)-(8 if g['pk']%3==0 else 0),'spreadOdds':L.get('rl_h')},'awayTeamOdds':{'favorite':(L.get('rl') or 0)>0,'moneyLine':L.get('ma'),'spreadOdds':L.get('rl_a')}}] if odds and L.get('mh') else []
    return {'id':g['espn'],'date':g['kick'][:16]+'Z','status':{'type':{'state':state,'completed':done,'shortDetail':detail}},'competitions':[{'competitors':[{'homeAway':'home','score':str(sc[1]),'team':tm(g['home'])},{'homeAway':'away','score':str(sc[0]),'team':tm(g['away'])}],'odds':o}]}
SB={}
for d,gs in ((TODAY,today),(TOM,tom),('2027-06-14',[g for g in past if g['date']=='2027-06-14'])):
    E=[]
    for j,g in enumerate(gs):
        f=finals[str(g['pk'])]
        if d<TODAY or (d==TODAY and j<5): E.append(ev(g,'post',True,'Final',f,False))
        elif d==TODAY and j<8: E.append(ev(g,'in',False,'Top 5th',(min(f[0],2),min(f[1],3)),False))
        else: E.append(ev(g,'pre',False,'7:10 PM',(0,0),True))
    SB[d.replace('-','')]={'events':E}
json.dump(SB,open('espn_mlb.json','w'))
print(len(past),len(today),len(tom), json.dumps(log['record']['model']), list(log['rule_stats'].items())[0][1]['live_n'], len(closes), json.dumps(log['record'].get('clv')))
