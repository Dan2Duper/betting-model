// Cloudflare Worker: the AI relay for the Bet Tracker's DFS tab.
// The page sends the slate and Rich's read on the game; Claude turns the read into projection adjustments
// (boosts, fades, must-haves, a captain); the page then rebuilds the lineup from its pool so it always stays legal.
// It reads bet-slip screenshots for "My bets" (/betslip), and on a cron trigger writes the game reports (/report runs it by hand).
// It also receives the DraftKings salaries Rich's phone (Tasker) fetches each morning and saves them to the repo's
// "data" branch, which starts the lineup build on GitHub.
// Secrets (Cloudflare dashboard > Worker > Settings > Variables and Secrets): ANTHROPIC_API_KEY, ACCESS_CODE, GH_TOKEN
// (a GitHub key limited to Dan2Duper/betting-model with Contents: Read and write). Optional MODEL.
const ALLOWED = ['https://dan2duper.github.io', 'http://localhost:8765'];
const SYSTEM = `You are a sharp DraftKings NFL/MLB DFS player. The user describes how they think a game or slate will play out.
Translate that read into adjustments to the player projections you are given. Think like a winning DFS player:
- Game script: a team expected to lead runs more (RB up, its QB/WRs slightly down late); a team chasing throws more (QB/WR/TE up, RB down).
- Shootouts lift both offenses' QBs and pass catchers; slow games lift defenses and kickers.
- Usage and opportunity matter more than names; injuries move volume to backups.
- Correlation: a QB's big game comes with his pass catchers'. In showdown, the captain should match the script.
- Cash games want safe, high-floor players; tournaments want upside and lower-owned players.
Only adjust what the read supports. Keep boosts between -60 and +60 percent. Use exact player names and team abbreviations from the list.
Answer only by calling the lineup_adjustments tool, in this shape:
{"summary": "<two plain sentences on how you applied the read>", "boost": {"<player name>": <percent>}, "team_boost": {"<TEAM>": <percent>}, "lock": ["<player who must be in>"], "ban": ["<player to leave out>"], "captain": "<showdown captain name or null>"}`;

function cors(origin) {
  return {'Access-Control-Allow-Origin': ALLOWED.includes(origin) ? origin : ALLOWED[0], 'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, X-Code', 'Content-Type': 'application/json'};
}
const reply = (obj, status, origin) => new Response(JSON.stringify(obj), {status, headers: cors(origin)});
const REPO = 'Dan2Duper/betting-model', BRANCH = 'data';

function b64(str) {   // UTF-8 safe base64 for the GitHub contents API
  const bytes = new TextEncoder().encode(str); let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
async function saveSalaries(req, env, url, origin) {
  const sport = (url.searchParams.get('sport') || '').toUpperCase(), dg = url.searchParams.get('dg') || '';
  if (!/^(NFL|MLB)$/.test(sport) || !/^\d+$/.test(dg)) return reply({error: 'need sport and dg'}, 400, origin);
  const text = await req.text();
  let J; try { J = JSON.parse(text); } catch (e) { return reply({error: 'salaries were not JSON'}, 400, origin); }
  if (!Array.isArray(J.draftables) || !J.draftables.length) return reply({error: 'no players in that file'}, 400, origin);
  const path = `salaries/${sport}_${dg}.json`, api = `https://api.github.com/repos/${REPO}/contents/${path}`;
  const gh = {'Authorization': `Bearer ${env.GH_TOKEN}`, 'Accept': 'application/vnd.github+json', 'User-Agent': 'bet-tracker-relay'};
  const old = await fetch(`${api}?ref=${BRANCH}`, {headers: gh});
  const sha = old.ok ? (await old.json()).sha : undefined;
  const put = await fetch(api, {method: 'PUT', headers: {...gh, 'Content-Type': 'application/json'},
    body: JSON.stringify({message: `Salaries ${sport} ${dg}`, content: b64(text), branch: BRANCH, ...(sha ? {sha} : {})})});
  if (!put.ok) return reply({error: `GitHub said ${put.status}`}, 502, origin);
  return reply({saved: path, players: J.draftables.length}, 200, origin);
}

const SLIP = `You read sportsbook bet slips (mostly DraftKings) from phone screenshots. List every bet you can see.
For each bet give: league (NFL, College Football, MLB, NBA ...), type (Single, Parlay, Teaser, Same Game Parlay),
odds (American, e.g. -110 or +450, for the whole bet), stake (dollars wagered), placed_date (YYYY-MM-DD if shown, else blank),
and legs. Each leg: team (the team the bet is on, as written; for a total, either team in the game), opponent (the other team if shown),
market: spread | total | moneyline | prop, pick (for a total: Over or Under; otherwise the team), line (the number for that leg as shown:
spread from the picked team's side like -3.5 or +7; total like 47.5; blank for moneyline), game_date (YYYY-MM-DD if the slip shows the game day),
description (the leg as written). Use "prop" for player props and anything else that isn't a spread, total or moneyline.
Never guess a number you can't read. Answer only by calling the bet_slip tool.`;
const SLIP_TOOL = {name: 'bet_slip', description: 'The bets read from the screenshot.', input_schema: {type: 'object', required: ['bets'], properties: {bets: {type: 'array', items: {type: 'object', properties: {
  league: {type: 'string'}, type: {type: 'string'}, odds: {type: 'number'}, stake: {type: 'number'}, placed_date: {type: 'string'},
  legs: {type: 'array', items: {type: 'object', properties: {team: {type: 'string'}, opponent: {type: 'string'}, market: {type: 'string', enum: ['spread', 'total', 'moneyline', 'prop']},
    pick: {type: 'string'}, line: {type: ['number', 'string']}, game_date: {type: 'string'}, description: {type: 'string'}}}}}}}}}};
async function readSlip(req, env, origin) {
  let body; try { body = await req.json(); } catch (e) { return reply({error: 'bad request'}, 400, origin); }
  if (!body.image || body.image.length > 4e6) return reply({error: 'no image, or it is too large'}, 400, origin);
  const r = await fetch('https://api.anthropic.com/v1/messages', {method: 'POST',
    headers: {'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
    body: JSON.stringify({model: env.MODEL || 'claude-sonnet-5-5', max_tokens: 2000, system: SLIP, tools: [SLIP_TOOL], tool_choice: {type: 'auto'},
      messages: [{role: 'user', content: [{type: 'image', source: {type: 'base64', media_type: 'image/jpeg', data: body.image}},
                                           {type: 'text', text: `Today is ${body.today || ''}. Read every bet on this slip.`}]}]})});
  const j = await r.json();
  if (!r.ok) return reply({error: (j.error && j.error.message) || 'AI error'}, 502, origin);
  const use = (j.content || []).find(c => c.type === 'tool_use');
  let out = use && use.input;
  if (!out) { const text = (j.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
    try { out = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1)); } catch (e) { return reply({error: 'could not read the slip: ' + text.slice(0, 160)}, 502, origin); } }
  return reply({bets: out.bets || []}, 200, origin);
}

// ---------- Automatic game reports (cron) ----------
// Every 15 minutes: read report.json (the page's scorecard on the sync branch), refresh the scores from ESPN itself so it
// works with the phone closed, work out the numbers in code, and when a game window closes have Claude write the report
// in a different comic style each time. Saved to reports.json on the sync branch (the Reports tab reads it).
const SYNC = 'sync', CT = 'America/Chicago';
const ctParts = d => Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: CT, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23'}).formatToParts(d).map(p => [p.type, p.value]));
const ctDay = d => { const p = ctParts(d); return `${p.year}-${p.month}-${p.day}`; };
const ctHour = d => +ctParts(d).hour;
const ctTime = d => new Intl.DateTimeFormat('en-US', {timeZone: CT, hour: 'numeric', minute: '2-digit'}).format(d);
const addDays = (day, n) => new Date(Date.parse(day + 'T12:00:00Z') + n * 864e5).toISOString().slice(0, 10);
const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const ghHead = env => ({'Authorization': `Bearer ${env.GH_TOKEN}`, 'Accept': 'application/vnd.github+json', 'User-Agent': 'bet-tracker-relay'});
async function ghGet(env, path) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/contents/${path}?ref=${SYNC}&t=${Date.now()}`, {headers: ghHead(env)});
  if (r.status === 404) return {json: null, sha: undefined};
  if (!r.ok) throw new Error(`GitHub ${path} ${r.status}`);
  const j = await r.json();
  let text;
  if (j.content && j.encoding === 'base64') text = new TextDecoder().decode(Uint8Array.from(atob(j.content.replace(/\n/g, '')), c => c.charCodeAt(0)));
  else { const raw = await fetch(j.download_url + '?t=' + Date.now()); text = await raw.text(); }   // files over 1 MB
  return {json: JSON.parse(text), sha: j.sha};
}
async function ghPut(env, path, obj, sha, msg) {
  return fetch(`https://api.github.com/repos/${REPO}/contents/${path}`, {method: 'PUT', headers: {...ghHead(env), 'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg, branch: SYNC, content: b64(JSON.stringify(obj, null, 1)), ...(sha ? {sha} : {})})});
}
async function espnDay(lg, day) {
  const base = lg === 'CFB' ? 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&limit=400&dates='
                            : 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=';
  const r = await fetch(base + day.replace(/-/g, '')); if (!r.ok) return [];
  const j = await r.json();
  return (j.events || []).map(ev => { const c = ev.competitions?.[0]; if (!c) return null;
    const H = c.competitors.find(x => x.homeAway === 'home'), A = c.competitors.find(x => x.homeAway === 'away');
    return {id: String(ev.id), away: A.team.location, home: H.team.location, aab: A.team.abbreviation, hab: H.team.abbreviation, as: +A.score || 0, hs: +H.score || 0,
            state: ev.status?.type?.state || 'pre', detail: ev.status?.type?.shortDetail || '', date: ctDay(new Date(ev.date))}; }).filter(Boolean);
}
function findGame(EV, lg, sc) {
  if (!sc) return null; const L = EV[lg] || [];
  return (sc.id && L.find(e => e.id === String(sc.id))) || L.find(e => norm(e.away) === norm(sc.away) && norm(e.home) === norm(sc.home)) || null;
}
function margin(b, g) {   // >0 winning; same math as the page's standing()
  const line = +b.line, side = String(b.side || '').toLowerCase();
  if (b.market === 'total') { const t = g.hs + g.as; return side === 'under' ? line - t : t - line; }
  if (b.market === 'ml') { const d = g.hs - g.as; return side === 'away' ? -d : d; }
  const d = g.hs - g.as + line; return side === 'away' ? -d : d;
}
function legFromText(l) {   // older report.json legs carry only the text
  const t = l.bet || '', sc = l.score || {};
  let m = t.match(/^(Over|Under) ([\d.]+)/i); if (m) return {market: 'total', side: m[1].toLowerCase(), line: +m[2]};
  const side = who => norm(sc.home).startsWith(norm(who)) || norm(who).startsWith(norm(sc.home)) ? 'home' : 'away';
  m = t.match(/^(.+) to win$/); if (m) return {market: 'ml', side: side(m[1])};
  m = t.match(/^(.+?) ([+-]?[\d.]+|PK|pk)$/); if (m) { const s = side(m[1]), v = /pk/i.test(m[2]) ? 0 : +m[2]; return {market: 'spread', side: s, line: s === 'home' ? v : -v}; }
  return null;
}
const sc = g => g ? {away: g.away, home: g.home, as: g.as, hs: g.hs, state: g.state, detail: g.detail} : null;
function statusOf(b) {
  if (b.result) return b.result;
  if (b.legs && b.legs.length) {
    if (b.legs.some(l => l.score && l.score.state === 'post' && l.m != null && l.m < 0)) return 'L';
    if (b.legs.every(l => l.score && l.score.state === 'post' && l.m != null && l.m > 0)) return 'W';
    if (b.legs.some(l => l.m != null && l.m < 0)) return 'losing';
    if (b.legs.every(l => l.m != null && l.m > 0)) return 'winning';
    return b.legs.some(l => l.score && l.score.state === 'in') ? 'live' : 'pending';
  }
  if (b.m == null) return 'pending';
  if (b.score && b.score.state === 'post') return b.m > 0 ? 'W' : b.m < 0 ? 'L' : 'P';
  return b.m > 0 ? 'winning' : b.m < 0 ? 'losing' : 'even';
}
const money = (b, s) => s === 'W' ? b.to_win : s === 'L' ? -b.stake : 0;
const fmt$ = v => (v < 0 ? '-$' : '+$') + Math.abs(v).toFixed(2);
function elapsed(detail) {
  const d = String(detail || '').toLowerCase(); if (d.includes('half')) return 0.5; if (d.includes('final')) return 1;
  const m = d.match(/(\d+):(\d+) - (\d)/); if (!m) return null; const q = +m[3], left = +m[1] + +m[2] / 60;
  return q <= 4 ? Math.min(((q - 1) * 15 + (15 - left)) / 60, 1) : 1;
}
async function buildFacts(env, now) {
  const {json: R} = await ghGet(env, 'report.json'); if (!R) throw new Error('no report.json yet');
  const day = ctHour(now) < 6 ? addDays(ctDay(now), -1) : ctDay(now);   // a late game still counts for the day it kicked
  const soon = [day, addDays(day, 1), addDays(day, 2)];
  const items = [...(R.mine || []), ...(R.ai_lox || [])].filter(b => soon.includes(b.date));
  const games = (R.games || []).filter(g => g.model && g.date === day);
  const need = new Set(); items.filter(b => b.date === day).forEach(b => need.add(b.league)); games.forEach(g => need.add(g.league));
  const EV = {}; for (const lg of need) { EV[lg] = (await espnDay(lg, day)).concat(day !== ctDay(now) ? await espnDay(lg, ctDay(now)) : []); }
  const refresh = b => {
    const o = {...b};
    if (b.legs && b.legs.length) o.legs = b.legs.map(l => { const g = findGame(EV, b.league, l.score) || null; const st = l.market ? l : (legFromText(l) || {});
      const s = g ? sc(g) : l.score; return {...l, score: s, m: g && g.state !== 'pre' && st.market ? margin(st, g) : l.margin_vs_line}; });
    else { const g = findGame(EV, b.league, b.score); o.score = g ? sc(g) : b.score; o.m = g && g.state !== 'pre' ? margin(b, g) : b.margin_vs_line; }
    o.status = statusOf(o); return o; };
  const R2 = items.map(refresh);
  const G = games.map(g => { const e = findGame(EV, g.league, g); return e ? {...g, as: e.as, hs: e.hs, state: e.state, detail: e.detail} : g; });
  return {R, day, now, items: R2, games: G};
}
function factsText(F) {
  const {day, items, games, now} = F, L = [];
  const line = b => { const s = b.score || {}; let where = s.state && s.state !== 'pre' ? `${s.away} ${s.as}-${s.hs} ${s.home} (${s.detail})` : (s.detail ? `kicks ${s.detail}` : 'not started');
    if (b.legs && b.legs.length) where = b.legs.map(l => `${l.bet}: ` + (l.score && l.score.state !== 'pre' ? `${l.score.away} ${l.score.as}-${l.score.hs} ${l.score.home} ${l.score.detail}` : 'not started') + (l.m != null ? ` (${l.m > 0 ? 'covering' : l.m < 0 ? 'missing' : 'even'} by ${Math.abs(l.m)})` : '')).join(' | ');
    else if (b.m != null) { where += ` — ${b.m > 0 ? 'beating' : b.m < 0 ? 'short of' : 'right on'} the number by ${Math.abs(b.m)}`;
      const e = elapsed(s.detail); if (b.market === 'total' && s.state === 'in' && e && e >= 0.15 && e < 1) where += `, scoring pace ${((s.as + s.hs) / e).toFixed(0)} vs ${b.line}`; }
    const st = {W: 'WON', L: 'LOST', P: 'PUSH'}[b.status] || b.status.toUpperCase();
    return `- [${st}${['W', 'L'].includes(b.status) ? ' ' + fmt$(money(b, b.status)) : ''}] ${b.bet} — ${b.game} — $${b.stake.toFixed(2)} to win $${b.to_win.toFixed(2)} — ${where}`; };
  const block = (name, list, note) => {
    const t = list.filter(b => b.date === day);
    if (!t.length) { L.push(`${name}: none today.`); return; }
    const S = t.map(b => b.status), w = S.filter(s => s === 'W').length, l = S.filter(s => s === 'L').length, p = S.filter(s => s === 'P').length;
    const net = t.reduce((a, b) => a + money(b, b.status), 0), staked = t.reduce((a, b) => a + b.stake, 0);
    L.push(`${name}${note ? ' (' + note + ')' : ''}: settled ${w}-${l}-${p}, net ${fmt$(net)} on $${staked.toFixed(2)} staked; ${S.filter(s => s === 'winning').length} live and winning, ${S.filter(s => s === 'losing').length} live and losing, ${S.filter(s => ['pending', 'even', 'live'].includes(s)).length} not started or even.`);
    t.sort((a, b) => String(a.kick).localeCompare(String(b.kick))).forEach(b => L.push(line(b)));
  };
  L.push(`REPORT DAY: ${day}. Scores as of ${ctTime(now)} Central (pulled live from ESPN just now).`);
  block('MY BETS (Rich\'s real money)', items.filter(b => b.who === 'mine'));
  const both = items.filter(b => b.who === 'mine' && b.ai_lox && b.date === day);
  if (both.length) L.push(`Of Rich's bets today, ${both.length} matched an AI-LOX pick.`);
  block('AI-LOX (the model\'s picks at suggested stakes, bet or not)', items.filter(b => b.who === 'ai-lox'), 'paper money unless Rich bet them');
  const fin = games.filter(g => g.state === 'post');
  if (games.length) {
    const su = fin.filter(g => g.hs !== g.as && ((g.model.home - g.model.away > 0) === (g.hs - g.as > 0))).length;
    const ats = fin.filter(g => g.model.line_spread != null).map(g => [g.model.home_spread - g.model.line_spread, g.hs - g.as + g.model.line_spread]).filter(([a, r]) => a && r);
    const ou = fin.filter(g => g.model.line_total != null).map(g => [g.model.total - g.model.line_total, g.as + g.hs - g.model.line_total]).filter(([a, r]) => a && r);
    const atsw = ats.filter(([a, r]) => (a < 0) === (r > 0)).length, ouw = ou.filter(([a, r]) => (a > 0) === (r > 0)).length;
    const miss = g => Math.abs((g.hs - g.as) - (g.model.home - g.model.away));
    L.push(`THE MODEL: ${fin.length} of ${games.length} modeled games final; picked the winner in ${su} of ${fin.length}; ${atsw}-${ats.length - atsw} against the spread; ${ouw}-${ou.length - ouw} on totals; average miss on the final margin ${(fin.reduce((a, g) => a + miss(g), 0) / Math.max(fin.length, 1)).toFixed(1)} points.`);
    const srt = fin.slice().sort((a, b) => miss(a) - miss(b));
    if (srt.length) { const g = srt[0]; L.push(`- Closest call: ${g.away} ${g.as}-${g.hs} ${g.home} (model had ${g.model.away.toFixed(0)}-${g.model.home.toFixed(0)}).`); }
    if (srt.length > 1) { const g = srt[srt.length - 1]; L.push(`- Worst miss: ${g.away} ${g.as}-${g.hs} ${g.home} (model had ${g.model.away.toFixed(0)}-${g.model.home.toFixed(0)}).`); }
    const live = games.filter(g => g.state === 'in');
    if (live.length) L.push('- Live now: ' + live.slice(0, 10).map(g => `${g.away} ${g.as}-${g.hs} ${g.home} ${g.detail} (model ${g.model.away.toFixed(0)}-${g.model.home.toFixed(0)})`).join('; '));
  }
  const later = items.filter(b => b.date > day || (b.date === day && ['pending'].includes(b.status) && !(b.score && b.score.state === 'in')));
  if (later.length) { L.push('STILL TO COME:'); later.sort((a, b) => String(a.kick).localeCompare(String(b.kick))).slice(0, 14).forEach(b => L.push(`- ${b.who === 'mine' ? 'MY BET' : 'AI-LOX'}: ${b.bet} — ${b.game} — ${b.date}${b.score && b.score.detail ? ' ' + b.score.detail : ''} — $${b.stake.toFixed(2)} to win $${b.to_win.toFixed(2)}`)); }
  return L.join('\n');
}
const STYLES = [
  'a nature documentary narrator watching Rich\'s bankroll get hunted on the savanna',
  'a hard-boiled 1940s noir detective working the case of the missing money',
  'a drunk uncle at Thanksgiving who will not shut up about these bets',
  'a roast-battle comic tearing Rich apart in front of a rowdy crowd',
  'a Shakespearean tragedy in five very short acts',
  'a war correspondent filing from the trenches',
  'a true-crime podcast host walking through the evidence',
  'a local TV weatherman giving the forecast',
  'a courtroom drama ending with a sentencing',
  'a furious sports-talk radio caller who has been on hold for an hour',
  'a drill sergeant screaming at a recruit',
  'a breakup letter to the sportsbook (or from it)',
  'a one-star (or five-star) online review of the day',
  'mission control during a launch that may or may not explode',
  'a horoscope for every bet',
  'a medieval town crier with terrible news',
  'a pirate captain\'s log',
  'a late-night infomercial host selling the model',
  'an HR annual performance review of the model and of Rich',
  'a therapist gently (then not gently) unpacking the day',
  'a funeral eulogy for the money',
  'an unhinged TV cooking-competition judge',
  'a fast-talking auctioneer',
  'an epic fantasy saga with prophecies and doom',
  'a telenovela full of betrayal',
  'an old-west saloon brawl',
  'a nature-park ranger giving a safety briefing',
  'a cheesy 1980s action-movie trailer voice',
  'a stuffy British cricket commentator who does not understand American football',
  'a conspiracy theorist with a corkboard and red string',
  'a used-car salesman trying to sell Rich on tomorrow\'s picks',
  'a hostage negotiator talking Rich off the ledge',
];
const SYS_REPORT = (style, kind) => `You write the game reports in Rich's Bet Tracker app. Rich is a grown man who bets college football and the NFL; he loves these reports funny, clever, mean and vulgar.
This report's style: written as ${style}. Commit to the bit all the way, but keep every fact readable.
Tone rules: be as mean and vulgar as is funny. Swearing is welcome (fuck, shit, damn, ass, whatever lands). Roast Rich, his bets, the model (it is called AI-LOX for picks, "the model" for score predictions), the teams, the coaches and the fan bases.
Hard limits: no slurs; nothing about race, religion, sex, sexuality, disability or anyone's family; no threats; no made-up quotes from real people; don't imitate a specific real person.
Numbers: use ONLY the numbers in the facts. Never invent a score, line, dollar amount, record or player. Every dollar figure and record you state must match the facts exactly. If something is not in the facts, leave it out.
${kind === 'preview' ? 'This is the PRE-GAME report: nothing has kicked off yet. Preview today\'s bets and AI-LOX picks with trash talk, and say what each one needs.' : kind === 'final' ? 'This is the FINAL report for the day: every game is done. Give the verdict.' : 'This is an UPDATE: some games are done, some are still to come.'}
Format: light markdown. Sections in this order, each starting with its label in bold: **MY BETS:**, **AI-LOX:**, **THE MODEL:**, **STILL TO COME:** (skip any section with nothing in it). Under a section, one short "- " line per bet. Short paragraphs, 150-320 words total.
Answer with only a JSON object: {"title": "<a funny headline under 110 characters>", "text": "<the report>"}`;
async function writeReport(env, F, kind, recent) {
  const used = new Set(recent.map(x => x.style));
  const pool = STYLES.filter(s => !used.has(s)), style = pool[Math.floor(Math.random() * pool.length)] || STYLES[0];
  const user = `FACTS (the only source of truth):\n${factsText(F)}\n\nRecent report headlines (don't repeat their jokes): ${recent.slice(0, 4).map(x => x.title).join(' | ') || 'none'}`;
  const r = await fetch('https://api.anthropic.com/v1/messages', {method: 'POST',
    headers: {'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
    body: JSON.stringify({model: env.MODEL || 'claude-sonnet-5-5', max_tokens: 1800, system: SYS_REPORT(style, kind), messages: [{role: 'user', content: user}]})});
  const j = await r.json(); if (!r.ok) throw new Error((j.error && j.error.message) || 'AI error');
  const text = (j.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  let out; try { out = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1)); } catch (e) { throw new Error('report not readable: ' + text.slice(0, 120)); }
  if (!out.title || !out.text) throw new Error('report missing title or text');
  return {title: String(out.title).slice(0, 160), text: String(out.text), style};
}
async function runReports(env, now, opts = {}) {
  const F = await buildFacts(env, now);
  const day = F.day, mineDay = F.items.filter(b => b.date === day);
  if (!mineDay.length && F.games.length < (opts.force ? 1 : 4)) return {skip: 'nothing worth a report today'};
  const settled = mineDay.filter(b => ['W', 'L', 'P'].includes(b.status)), live = mineDay.filter(b => ['winning', 'losing', 'even', 'live'].includes(b.status) || (b.score && b.score.state === 'in'));
  const allBetsDone = mineDay.every(b => ['W', 'L', 'P'].includes(b.status)), allGamesDone = F.games.every(g => g.state === 'post');
  const fp = settled.map(b => b.id + b.status).sort().join(',') + '|' + F.games.filter(g => g.state === 'post').length;
  const {json: RJ, sha} = await ghGet(env, 'reports.json');
  const RP = RJ || {}; RP.auto = RP.auto || {}; const A = RP.auto[day] || {n: 0};
  const lastT = A.last ? Date.parse(A.last) : 0, hrs = (now - lastT) / 36e5;
  const anyStarted = mineDay.some(b => b.score && b.score.state !== 'pre') || F.games.some(g => g.state !== 'pre');
  let kind = opts.force === 'now' ? (!anyStarted ? 'preview' : allGamesDone && allBetsDone ? 'final' : 'update') : (opts.force || null);
  if (!kind) {
    if (A.n >= 7) return {skip: 'daily cap'};
    if (!A.n && !anyStarted && mineDay.length && ctHour(now) >= 9) kind = 'preview';
    else if (fp !== A.fp && allGamesDone && allBetsDone && anyStarted) kind = 'final';
    else if (fp !== A.fp && settled.length && !live.length && A.fpBets !== settled.map(b => b.id + b.status).sort().join(',')) kind = 'update';
    else if (fp !== A.fp && settled.length && hrs >= 3) kind = 'update';
  }
  if (!kind) return {skip: 'nothing new', fp};
  const recent = (RP.reports || []).filter(x => x.style).slice(0, 8);
  const rep = await writeReport(env, F, kind, recent);
  const iso = now.toISOString().replace(/\.\d+Z$/, 'Z');
  const lg = [...new Set([...mineDay.map(b => b.league), ...F.games.map(g => g.league)])].join('+') || 'ALL';
  const entry = {id: `${iso}-${lg}`, time: iso, league: lg, kind, title: rep.title, text: rep.text, style: rep.style};
  if (opts.dry) return {dry: true, entry, facts: factsText(F)};
  for (let i = 0; i < 3; i++) {
    const cur = i ? await ghGet(env, 'reports.json') : {json: RP, sha};
    const D = cur.json || {}; D._note = "Written automatically by the relay (and by Claude); newest first; the Bet Tracker Reports tab reads this.";
    D.reports = [entry, ...(D.reports || []).filter(x => x.id !== entry.id)].slice(0, 60);
    D.auto = D.auto || {}; const prev = D.auto[day] || {n: 0};
    D.auto[day] = {n: prev.n + 1, last: iso, fp, fpBets: settled.map(b => b.id + b.status).sort().join(','), kinds: [...(prev.kinds || []), kind]};
    Object.keys(D.auto).filter(k => k < addDays(day, -10)).forEach(k => delete D.auto[k]);
    const p = await ghPut(env, 'reports.json', D, cur.sha, `${lg} ${kind} report ${iso}`);
    if (p.ok) return {saved: entry.id, kind, title: entry.title, style: entry.style};
    if (p.status !== 409 && p.status !== 422) throw new Error('GitHub save ' + p.status);
  }
  throw new Error('could not save the report');
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get('Origin') || '';
    if (req.method === 'OPTIONS') return new Response(null, {headers: cors(origin)});
    if (req.method !== 'POST') return reply({error: 'POST only'}, 405, origin);
    if (origin && !ALLOWED.includes(origin)) return reply({error: 'not allowed from this site'}, 403, origin);
    if (!env.ACCESS_CODE || req.headers.get('X-Code') !== env.ACCESS_CODE) return reply({error: 'wrong or missing DFS AI code (Settings tab)'}, 401, origin);
    const url = new URL(req.url);
    if (url.pathname === '/salaries') return saveSalaries(req, env, url, origin);
    if (url.pathname === '/betslip') return readSlip(req, env, origin);
    if (url.pathname === '/report') {   // run the report check now: ?dry=1 shows it without saving; ?force=preview|update|final
      try { return reply(await runReports(env, new Date(), {dry: url.searchParams.get('dry') === '1', force: url.searchParams.get('force') || null}), 200, origin); }
      catch (e) { return reply({error: String(e.message || e)}, 502, origin); } }
    let body; try { body = await req.json(); } catch (e) { return reply({error: 'bad request'}, 400, origin); }
    const thoughts = String(body.thoughts || '').slice(0, 1500);
    const players = (body.players || []).slice().sort((a, b) => (b.proj || 0) - (a.proj || 0)).slice(0, 90);
    const table = players.map(p => `${p.n} | ${p.pos} | ${p.t} vs ${p.o} | $${p.sal} | proj ${p.proj} | ceil ${p.ceil} | own ~${Math.round(p.own || 0)}% | team total ${p.imp ?? ''}${p.st && p.st !== 'None' ? ' | ' + p.st : ''}`).join('\n');
    const user = `Contest: ${body.kind === 'showdown' ? 'Showdown (single game, captain 1.5x)' : 'Classic main slate'}, ${body.mode === 'cash' ? 'cash game' : 'tournament'}.\nSlate: ${body.label}\n\nPlayers (name | pos | game | salary | projection | ceiling | est. ownership | Vegas team total):\n${table}\n\nMy read: ${thoughts}`;
    const TOOL = {name: 'lineup_adjustments', description: 'Projection adjustments that apply the user\'s read on the game.',
      input_schema: {type: 'object', required: ['summary'], properties: {
        summary: {type: 'string', description: 'Two plain sentences on how the read was applied.'},
        boost: {type: 'object', description: 'Player name -> percent change to projection (-60..60).', additionalProperties: {type: 'number'}},
        team_boost: {type: 'object', description: 'Team abbreviation -> percent change for all its players (-60..60).', additionalProperties: {type: 'number'}},
        lock: {type: 'array', items: {type: 'string'}, description: 'Players who must be in the lineup.'},
        ban: {type: 'array', items: {type: 'string'}, description: 'Players to leave out.'},
        captain: {type: ['string', 'null'], description: 'Showdown captain, or null.'}}}};
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
      body: JSON.stringify({model: env.MODEL || 'claude-sonnet-5-5', max_tokens: 2000, system: SYSTEM, tools: [TOOL], tool_choice: {type: 'auto'},
                            messages: [{role: 'user', content: user}]})
    });
    const j = await r.json();
    if (!r.ok) return reply({error: (j.error && j.error.message) || 'AI error'}, 502, origin);
    const use = (j.content || []).find(c => c.type === 'tool_use');
    let out = use && use.input;
    if (!out) {   // fallback: the first JSON object in the text that parses
      const text = (j.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
      for (let i = text.indexOf('{'); i >= 0 && !out; i = text.indexOf('{', i + 1)) {
        for (let k = text.lastIndexOf('}'); k > i && !out; k = text.lastIndexOf('}', k - 1)) {
          try { const o = JSON.parse(text.slice(i, k + 1)); if (o && o.summary !== undefined) out = o; } catch (e) {}
        }
      }
      if (!out) return reply({error: 'AI reply was not readable: ' + text.slice(0, 160)}, 502, origin);
    }
    const clamp = o => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, Math.max(-60, Math.min(60, +v || 0))]));
    return reply({summary: out.summary || '', boost: clamp(out.boost), team_boost: clamp(out.team_boost), lock: out.lock || [], ban: out.ban || [], captain: out.captain || null}, 200, origin);
  },
  async scheduled(event, env, ctx) {   // Cloudflare cron trigger (every 15 minutes)
    ctx.waitUntil(runReports(env, new Date(event.scheduledTime)).catch(e => console.log('report:', e.message)));
  }
};
