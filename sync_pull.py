"""Pull the Bet Tracker page's synced state (state.json on the 'sync' branch of Dan2Duper/betting-model)
and write its changes to logs/page_changes.json so tracker.py merges them into the CSV logs.
Usage: python3 sync_pull.py [path to a clone of betting-model]   (default /home/claude/betting-model)"""
import os, sys, json, subprocess
PKG = os.path.dirname(os.path.abspath(__file__))
R = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/betting-model'
if not os.path.isdir(R):
    subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/Dan2Duper/betting-model', R], check=True)
f = subprocess.run(['git', '-C', R, 'fetch', '--depth', '1', 'origin', 'sync'], capture_output=True, text=True)
if f.returncode:
    print('SYNC: no sync branch yet (nothing saved from the page)'); sys.exit(0)
st = json.loads(subprocess.run(['git', '-C', R, 'show', 'FETCH_HEAD:state.json'], capture_output=True, text=True, check=True).stdout)
ch = st.get('changes') or {}
json.dump(ch, open(f'{PKG}/logs/page_changes.json', 'w'), indent=1)
print(f"SYNC: page state from {st.get('updated')} -> logs/page_changes.json "
      f"({len(ch.get('struck', []))} struck, {len(ch.get('overrides', {}))} edits, {len(ch.get('bets', []))} bets, {len(ch.get('mine', []))} own bets)")
