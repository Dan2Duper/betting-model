"""Save a scheduled game report to reports.json on the sync branch (the Bet Tracker page's Reports tab reads it).
Usage: python3 save_report.py LEAGUE TITLE_FILE TEXT_FILE
TEXT is light markdown: **bold**, '- ' bullets, blank lines between paragraphs. Keeps the newest 40 reports."""
import sys, json, os, subprocess, tempfile, datetime as dt
URL = os.environ.get('BM_URL', 'https://github.com/Dan2Duper/betting-model.git')
league, title_f, text_f = sys.argv[1].upper(), sys.argv[2], sys.argv[3]
title = open(title_f).read().strip(); text = open(text_f).read().strip()
now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
entry = {'id': f'{now}-{league}', 'time': now, 'league': league, 'title': title, 'text': text}
d = tempfile.mkdtemp(prefix='bm-rep-')
def git(*a, check=True): return subprocess.run(['git', '-C', d, *a], check=check, capture_output=True, text=True)
git('init', '-q'); git('config', 'user.name', 'Claude'); git('config', 'user.email', 'noreply@anthropic.com')
for attempt in range(4):
    git('fetch', '-q', '--depth', '1', URL, 'sync'); git('checkout', '-q', '-f', '-B', 'sync', 'FETCH_HEAD')
    p = os.path.join(d, 'reports.json')
    R = json.load(open(p)) if os.path.exists(p) else {}
    R['_note'] = "Written by Claude's scheduled game reports; newest first; the Bet Tracker Reports tab reads this."
    reps = [r for r in R.get('reports', []) if r.get('id') != entry['id']]
    R['reports'] = ([entry] + reps)[:40]
    json.dump(R, open(p, 'w'), indent=1, ensure_ascii=False)
    git('add', 'reports.json'); git('commit', '-q', '-m', f'{league} game report {now}')
    r = git('push', '-q', URL, 'HEAD:sync', check=False)
    if r.returncode == 0: print('SAVED', entry['id'], f"({len(R['reports'])} reports on file)"); sys.exit(0)
    err = r.stderr.strip()
print('SAVE FAILED:', err); sys.exit(1)
