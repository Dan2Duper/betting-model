import json, sys, re
from playwright.sync_api import sync_playwright
def run(html_path, asof=None):
    HTML = open(html_path).read(); errs = []; out = {}
    def handle(route):
        u = route.request.url
        J = lambda o, st=200: route.fulfill(status=st, content_type='application/json', body=json.dumps(o), headers={'access-control-allow-origin': '*'})
        if u.startswith('http://localhost:8765/'): return route.fulfill(status=200, content_type='text/html', body=HTML)
        if 'scoreboard' in u: return J({'events': []})
        return J({}, 404)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox']); pg = b.new_context(viewport={'width': 400, 'height': 860}).new_page(); pg.route('**/*', handle)
        pg.on('pageerror', lambda e: errs.append(str(e)[:300]))
        pg.goto('http://localhost:8765/index.html' + (f'?asof={asof}' if asof else '')); pg.wait_for_timeout(2500)
        out['tabs'] = pg.eval_on_selector_all('#tabs button', 'els => els.filter(b => !b.classList.contains("hide")).map(b => b.dataset.t)')
        out['bets'] = pg.evaluate('BETS.length'); out['picks'] = pg.evaluate('PICKS.length'); out['mine'] = pg.evaluate('MINE.length'); out['board'] = pg.evaluate('BOARD.length')
        out['betboard'] = pg.inner_text('#betboard')[:3000]; out['games'] = pg.inner_text('#games')[:3000]
        pg.click('#tabs button[data-t="track"]'); pg.wait_for_timeout(600); out['scorecard'] = pg.inner_text('#scorecard'); out['starcard'] = pg.inner_text('#starcard'); out['list'] = pg.inner_text('#list')[:2000]
        out['tk'] = pg.eval_on_selector_all('#tk-lg button', 'els => els.filter(b => !b.classList.contains("hide")).map(b => b.textContent)')
        pg.click('#mode button[data-m="model"]'); pg.wait_for_timeout(300); out['modellist'] = pg.inner_text('#list')[:1500]
        pg.click('#tabs button[data-t="stats"]'); pg.wait_for_timeout(400); out['stats'] = pg.inner_text('#t-stats')[:4000]
        pg.click('#tabs button[data-t="week"]'); pg.wait_for_timeout(300); pg.click('#m-open'); pg.wait_for_timeout(500); out['mlg'] = pg.eval_on_selector_all('#m-lg option', 'els => els.filter(o => !o.hidden).map(o => o.value)')
        out['report'] = pg.evaluate('JSON.stringify(reportBuild()).length'); out['errs'] = errs; b.close()
    return out
A = run('/home/claude/work/test_page_old.html'); B = run('/home/claude/work/test_page.html')
for k in A:
    same = A[k] == B[k]; print(f'{k:10s}', 'SAME' if same else 'DIFF', '' if same else (str(A[k])[:160] + '  ->  ' + str(B[k])[:160]))
