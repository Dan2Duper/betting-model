// Cloudflare Worker: the AI relay for the Bet Tracker's DFS tab.
// The page sends the slate and Rich's read on the game; Claude turns the read into projection adjustments
// (boosts, fades, must-haves, a captain); the page then rebuilds the lineup from its pool so it always stays legal.
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
Reply with JSON only, no other text:
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

export default {
  async fetch(req, env) {
    const origin = req.headers.get('Origin') || '';
    if (req.method === 'OPTIONS') return new Response(null, {headers: cors(origin)});
    if (req.method !== 'POST') return reply({error: 'POST only'}, 405, origin);
    if (origin && !ALLOWED.includes(origin)) return reply({error: 'not allowed from this site'}, 403, origin);
    if (!env.ACCESS_CODE || req.headers.get('X-Code') !== env.ACCESS_CODE) return reply({error: 'wrong or missing DFS AI code (Settings tab)'}, 401, origin);
    const url = new URL(req.url);
    if (url.pathname === '/salaries') return saveSalaries(req, env, url, origin);
    let body; try { body = await req.json(); } catch (e) { return reply({error: 'bad request'}, 400, origin); }
    const thoughts = String(body.thoughts || '').slice(0, 1500);
    const players = (body.players || []).slice().sort((a, b) => (b.proj || 0) - (a.proj || 0)).slice(0, 90);
    const table = players.map(p => `${p.n} | ${p.pos} | ${p.t} vs ${p.o} | $${p.sal} | proj ${p.proj} | ceil ${p.ceil} | own ~${Math.round(p.own || 0)}% | team total ${p.imp ?? ''}${p.st && p.st !== 'None' ? ' | ' + p.st : ''}`).join('\n');
    const user = `Contest: ${body.kind === 'showdown' ? 'Showdown (single game, captain 1.5x)' : 'Classic main slate'}, ${body.mode === 'cash' ? 'cash game' : 'tournament'}.\nSlate: ${body.label}\n\nPlayers (name | pos | game | salary | projection | ceiling | est. ownership | Vegas team total):\n${table}\n\nMy read: ${thoughts}`;
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
      body: JSON.stringify({model: env.MODEL || 'claude-sonnet-5-5', max_tokens: 900, system: SYSTEM, messages: [{role: 'user', content: user}]})
    });
    const j = await r.json();
    if (!r.ok) return reply({error: (j.error && j.error.message) || 'AI error'}, 502, origin);
    const text = (j.content || []).map(c => c.text || '').join('');
    let out; try { out = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1)); } catch (e) { return reply({error: 'AI reply was not readable'}, 502, origin); }
    const clamp = o => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, Math.max(-60, Math.min(60, +v || 0))]));
    return reply({summary: out.summary || '', boost: clamp(out.boost), team_boost: clamp(out.team_boost), lock: out.lock || [], ban: out.ban || [], captain: out.captain || null}, 200, origin);
  }
};
