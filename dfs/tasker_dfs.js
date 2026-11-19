// Tasker > Task "DFS salaries" > one action: Code > JavaScriptlet. Paste everything below into it.
// Each morning it reads today's DraftKings slate list from GitHub, fetches each slate's salaries from DraftKings
// on this phone's own connection, and hands them to the Bet Tracker relay, which saves them and starts the lineup build.
var RELAY = 'https://bet-tracker-relay.simplewindowscreens.workers.dev';
var CODE = 'CODE_HERE';
function get(u) { var x = new XMLHttpRequest(); x.open('GET', u, false); x.send(); if (x.status != 200) throw 'GET ' + x.status + ' ' + u; return x.responseText; }
function post(u, body) { var x = new XMLHttpRequest(); x.open('POST', u, false); x.setRequestHeader('X-Code', CODE); x.setRequestHeader('Content-Type', 'application/json'); x.send(body); return x.status + ' ' + x.responseText; }
var todo = JSON.parse(get('https://raw.githubusercontent.com/Dan2Duper/betting-model/data/dfs_todo.json?t=' + new Date().getTime()));
var done = 0, msgs = [];
for (var i = 0; i < todo.slates.length; i++) {
  var s = todo.slates[i];
  try { var r = post(RELAY + '/salaries?sport=' + s.sport + '&dg=' + s.dg, get(s.url)); if (r.indexOf('200') === 0) done++; else msgs.push(s.label + ': ' + r); }
  catch (e) { msgs.push(s.label + ': ' + e); }
}
var dfsresult = 'DFS salaries sent for ' + done + ' of ' + todo.slates.length + ' slates' + (msgs.length ? ' | ' + msgs.join(' | ') : '');
flash(dfsresult);
