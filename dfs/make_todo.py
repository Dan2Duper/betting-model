"""Morning job: list today's DraftKings slates (main Classic + each Showdown) in dfs_todo.json.
The phone (Tasker) reads this list, fetches each slate's salaries from DraftKings and sends them to the relay."""
import sys, os, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dk_slate
out = sys.argv[1] if len(sys.argv) > 1 else 'dfs_todo.json'
todo = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(), slates=[])
for sport in ('NFL', 'MLB'):
    try:
        groups = dk_slate.draft_groups(sport)
        main, sds = dk_slate.pick_slates(groups)
    except Exception as e: print(sport, 'lobby error', e); continue
    if main: todo['slates'].append(dict(sport=sport, kind='classic', dg=main['dg'], label=f"Main slate {main.get('label', '')}".strip(), url=dk_slate.draftables_url(main['dg'])))
    for g in sds: todo['slates'].append(dict(sport=sport, kind='showdown', dg=g['dg'], label=g.get('label', ''), url=dk_slate.draftables_url(g['dg'])))
json.dump(todo, open(out, 'w'), indent=1)
print(json.dumps(todo, indent=1))
