import fs from 'fs'; import os from 'os'; import path from 'path'; import {fileURLToPath, pathToFileURL} from 'url';
const here = path.dirname(fileURLToPath(import.meta.url)), tmp = path.join(os.tmpdir(), 'relay_under_test.mjs');
fs.writeFileSync(tmp, fs.readFileSync(path.join(here, '..', 'dfs', 'worker.js'), 'utf8') + '\nexport {buildFacts, factsText, runReports, mlbGameDay, mlbOdds, mlbGrade, mlbSeason, espnDay};\n');
const W = await import(pathToFileURL(tmp).href);
const day = '2027-06-15', now = new Date('2027-06-16T00:20:00Z');   // 7:20 pm CT June 15
const ev = (id, a, h, as, hs, state, done, detail, kick, odds) => ({id, date: kick, status: {type: {state, completed: done, shortDetail: detail}},
  competitions: [{competitors: [{homeAway: 'home', score: String(hs), team: {abbreviation: h, location: h}}, {homeAway: 'away', score: String(as), team: {abbreviation: a, location: a}}], odds: odds ? [odds] : []}]});
const oddsA = {provider: {name: 'DraftKings'}, overUnder: 8.5, spread: -1.5, overOdds: -112, underOdds: -108, homeTeamOdds: {favorite: true, moneyLine: -150, spreadOdds: 140}, awayTeamOdds: {favorite: false, moneyLine: 130, spreadOdds: -165}};
const oddsB = {provider: {name: 'DraftKings'}, moneyline: {home: {close: {odds: '+120'}}, away: {close: {odds: '-142'}}}, pointSpread: {home: {close: {line: '+1.5', odds: '-170'}}, away: {close: {line: '-1.5', odds: '+145'}}}, total: {over: {close: {line: 'o9.5', odds: 'EVEN'}}, under: {close: {line: 'u9.5', odds: '-120'}}}};
console.log('odds A', JSON.stringify(W.mlbOdds(oddsA))); console.log('odds B', JSON.stringify(W.mlbOdds(oddsB)));
const SB = {events: [
  ev('1', 'NYY', 'BOS', 3, 5, 'post', true, 'Final', '2027-06-15T17:10Z', null),
  ev('2', 'CHC', 'STL', 7, 2, 'post', true, 'Final', '2027-06-15T18:15Z', null),
  ev('3', 'LAD', 'SF', 1, 1, 'in', false, 'Top 5th', '2027-06-15T23:10Z', null),
  ev('4', 'SEA', 'HOU', 0, 0, 'pre', false, '6/15 - 7:30 PM CDT', '2027-06-16T00:30Z', oddsA),
  ev('5', 'SD', 'ARI', 0, 0, 'pre', false, '6/15 - 8:40 PM CDT', '2027-06-16T01:45Z', oddsB),
  ev('6', 'DET', 'CLE', 0, 0, 'post', false, 'Postponed', '2027-06-15T22:40Z', null)]};
const g = (pk, espn, a, h, pa, ph, picks, rules, kick) => ({pk, espn, away: a, home: h, date: day, kick, pa, ph, p_home: 0.5, picks, rules, logged: {picks, rules}});
const MJ = {games: [
  g(11, '1', 'NYA', 'BOS', 4.6, 4.1, {ml: {side: 'away', p: 0.54, odds: -120}, rl: {side: 'home', line: 1.5, p: 0.62, odds: -160}, tot: {side: 'under', line: 9, p: 0.52, odds: -110}}, [], '2027-06-15T17:10:00Z'),
  g(12, '2', 'CHN', 'SLN', 5.2, 3.9, {ml: {side: 'away', p: 0.6, odds: -135}, rl: {side: 'away', line: 1.5, p: 0.44, odds: 125}, tot: {side: 'over', line: 8, p: 0.53, odds: -105}}, [{rule: 'mlb_total_gap_over', market: 'total', side: 'over', line: 8, odds: -105}], '2027-06-15T18:15:00Z'),
  g(13, '3', 'LAN', 'SFN', 4.9, 3.4, {ml: {side: 'away', p: 0.64, odds: -170}, rl: {side: 'away', line: 1.5, p: 0.47, odds: -105}, tot: {side: 'under', line: 8, p: 0.51, odds: -110}}, [], '2027-06-15T23:10:00Z'),
  g(14, '4', 'SEA', 'HOU', 3.9, 4.4, {ml: {side: 'home', p: 0.56, odds: -150}, rl: {side: 'away', line: -1.5, p: 0.66, odds: -165}, tot: {side: 'under', line: 8.5, p: 0.52, odds: -108}}, [], '2027-06-16T00:30:00Z'),
  g(15, '5', 'SDN', 'ARI', 4.8, 4.7, {ml: {side: 'home', p: 0.51, odds: 120}}, [], '2027-06-16T01:45:00Z'),
  g(16, '6', 'DET', 'CLE', 4.0, 4.2, {ml: {side: 'home', p: 0.55, odds: -125}}, [], '2027-06-15T22:40:00Z')],
  record: {model: {su: {W: 500, L: 400, P: 0, u: -12.3, n_u: 900}, rl: {W: 450, L: 440, P: 0, u: -20.1, n_u: 890}, tot: {W: 430, L: 420, P: 40, u: -8.4, n_u: 890}}}, rule_stats: {}};
const REPORT = {mine: [{who: 'mine', id: 'm1', league: 'MLB', date: day, kick: '2027-06-15T23:10:00Z', bet: 'Take LAD to win (moneyline)', game: 'LAD @ SF', market: 'ml', side: 'away', line: '', odds: -170, stake: 17, to_win: 10, result: '', rule: '', score: {id: '3', away: 'LAD', home: 'SF'}, margin_vs_line: null, ai_lox: false}], ai_lox: [], games: []};
const saved = [];
globalThis.fetch = async (url, opt = {}) => {
  url = String(url); const J = (o, status = 200) => new Response(JSON.stringify(o), {status, headers: {'content-type': 'application/json'}});
  if (url.includes('baseball/mlb/scoreboard')) return J(SB);
  if (url.includes('scoreboard')) return J({events: []});
  if (url.includes('raw.githubusercontent.com') && url.includes('mlb.json')) return J(MJ);
  if (url.includes('api.github.com')) {
    if ((opt.method || 'GET') === 'PUT') { saved.push([url.split('/contents/')[1], JSON.parse(Buffer.from(JSON.parse(opt.body).content, 'base64').toString())]); return J({content: {sha: 'x'}}); }
    if (url.includes('report.json')) return J({sha: 'a', encoding: 'base64', content: Buffer.from(JSON.stringify(REPORT)).toString('base64')});
    if (url.includes('mlb/_refresh.json')) return J({sha: 'r', content: Buffer.from(JSON.stringify({at: '2027-06-15T22:00:00Z'})).toString('base64')});
    return J({}, 404);
  }
  if (url.includes('anthropic.com')) { const b = JSON.parse(opt.body); globalThis.LAST_PROMPT = b; return J({content: [{type: 'tool_use', input: {title: 'Test headline', text: '**THE MODEL:** test'}}]}); }
  throw new Error('unexpected fetch ' + url);
};
const env = {GH_TOKEN: 'x', ANTHROPIC_API_KEY: 'y'};
console.log('in season?', W.mlbSeason(now), W.mlbSeason(new Date('2026-11-22T16:00:00Z')));
const F = await W.buildFacts(env, now); console.log('\n' + W.factsText(F));
console.log('\nrunReports dry:', JSON.stringify(await W.runReports(env, now, {dry: true, force: 'now'})).slice(0, 300));
console.log('system prompt mentions baseball:', /baseball/.test(globalThis.LAST_PROMPT.system));
console.log('\nmlbGameDay @7:20pm (SEA@HOU 10 min out -> closing; SD@ARI 85 min out -> kick):', JSON.stringify(await W.mlbGameDay(env, now)));
console.log('saved:', JSON.stringify(saved).slice(0, 700));
console.log('off-season tick:', JSON.stringify(await W.mlbGameDay(env, new Date('2026-11-22T16:00:00Z'))));
// football-only facts are unchanged out of season
const F2 = await W.buildFacts(env, new Date('2026-11-22T18:00:00Z')); console.log('off-season facts mlb games:', F2.mlb.length);
