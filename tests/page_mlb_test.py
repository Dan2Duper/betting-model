import json, sys, re, asyncio
from playwright.sync_api import sync_playwright
HTML = open('/home/claude/work/test_page.html').read()
MLBJ = open('mlb.json').read(); LOG = open('log_2027.json').read(); SB = json.load(open('espn_mlb.json'))
asof = sys.argv[1] if len(sys.argv) > 1 else '2027-06-15'; shots = sys.argv[2] if len(sys.argv) > 2 else 'mlb'
errs = []; reqs = []
def handle(route):
    u = route.request.url; reqs.append(u.split('?')[0][-70:])
    J = lambda o, st=200: route.fulfill(status=st, content_type='application/json', body=o if isinstance(o, str) else json.dumps(o), headers={'access-control-allow-origin': '*'})
    if u.startswith('http://localhost:8765/'): return route.fulfill(status=200, content_type='text/html', body=HTML)
    if 'baseball/mlb/scoreboard' in u: d = re.search(r'dates=(\d+)', u); return J(SB.get(d.group(1), {'events': []}) if d else {'events': []})
    if 'scoreboard' in u: return J({'events': []})
    if 'data/mlb.json' in u: return J(MLBJ)
    if 'data/mlb/log_' in u: return J(LOG)
    if 'data/dfs_mlb.json' in u:
        try: return J(open('dfs_mlb.json').read())
        except Exception: return J({}, 404)
    if 'raw.githubusercontent.com' in u or 'api.github.com' in u: return J({}, 404)
    if 'open-meteo' in u: return J({})
    return J({}, 404)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 400, 'height': 860}, device_scale_factor=2)
    pg = ctx.new_page(); pg.route('**/*', handle)
    pg.on('console', lambda m: errs.append('console.' + m.type + ': ' + m.text[:300]) if m.type in ('error',) else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)[:400]))
    pg.goto(f'http://localhost:8765/index.html?asof={asof}'); pg.wait_for_timeout(2500)
    tabs = pg.eval_on_selector_all('#tabs button', 'els => els.filter(b => !b.classList.contains("hide")).map(b => b.dataset.t)'); print('visible tabs:', tabs)
    print('TAB =', pg.evaluate('TAB'), '| MLB games:', pg.evaluate('MLB ? MLB.games.length : null'), '| log games:', pg.evaluate('MLBLOG ? Object.keys(MLBLOG.games).length : null'), '| MLBROWS', pg.evaluate('MLBROWS.length'), '| PICKS mlb', pg.evaluate('PICKS.filter(p => p.league === "MLB").length'))
    if 'mlb' in tabs:
        pg.click('#tabs button[data-t="mlb"]'); pg.wait_for_timeout(1500)
        print('record:', pg.inner_text('#mlb-record')[:500]); print('days:', pg.eval_on_selector_all('#mlb-day button', 'els => els.map(b => b.textContent + (b.classList.contains("on") ? "*" : ""))'))
        cards = pg.eval_on_selector_all('#mlb-games .mg', 'els => els.map(e => e.innerText.replace(/\\n+/g, " | "))'); print(len(cards), 'cards'); [print(' -', c[:420]) for c in cards[:3] + cards[5:7] + cards[9:10]]
        print('my bets card in MLB tab:', pg.evaluate('!!document.querySelector("#t-mlb #mineboard")'))
        pg.screenshot(path=f'{shots}_1.png', full_page=False); pg.screenshot(path=f'{shots}_full.png', full_page=True)
        days = pg.query_selector_all('#mlb-day button'); days[0].click(); pg.wait_for_timeout(800)
        cards = pg.eval_on_selector_all('#mlb-games .mg', 'els => els.map(e => e.innerText.replace(/\\n+/g, " | "))'); print('yesterday:', len(cards), 'cards'); [print(' -', c[:330]) for c in cards[:2]]
        days = pg.query_selector_all('#mlb-day button'); days[-1].click(); pg.wait_for_timeout(600)
        cards = pg.eval_on_selector_all('#mlb-games .mg', 'els => els.map(e => e.innerText.replace(/\\n+/g, " | "))'); print('tomorrow:', len(cards), 'cards'); [print(' -', c[:330]) for c in cards[:2]]
    pg.click('#tabs button[data-t="track"]'); pg.wait_for_timeout(800)
    print('tracker lg buttons:', pg.eval_on_selector_all('#tk-lg button', 'els => els.filter(b => !b.classList.contains("hide")).map(b => b.textContent)'))
    if pg.query_selector('#tk-lg button[data-v="MLB"]:not(.hide)'):
        pg.click('#tk-lg button[data-v="MLB"]'); pg.wait_for_timeout(500); print('scorecard MLB:', pg.inner_text('#scorecard').replace('\n', ' | ')[:700])
        pg.click('#mode button[data-m="model"]'); pg.wait_for_timeout(400); print('model list:', pg.inner_text('#list').replace('\n', ' | ')[:500]); pg.screenshot(path=f'{shots}_track.png', full_page=True)
        pg.click('#tk-lg button[data-v=""]'); pg.wait_for_timeout(300)
    pg.click('#tabs button[data-t="stats"]'); pg.wait_for_timeout(500); print('stats rules has MLB:', 'MLB model total' in pg.inner_text('#stat-rules'))
    if len(sys.argv) > 3: exec(open(sys.argv[3]).read())
    print('ERRORS:', errs[:12]); b.close()
