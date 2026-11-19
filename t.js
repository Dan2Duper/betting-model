// Loaded by Tasker as a JavaScriptlet "Library". The JavaScriptlet's own code is one line:  runDfs('your relay password');
// Each morning it reads today's DraftKings slate list from GitHub, fetches each slate's salaries from DraftKings
// on this phone's own connection, and hands them to the Bet Tracker relay, which saves them and starts the lineup build.
var RELAY = 'https://bet-tracker-relay.simplewindowscreens.workers.dev';
function runDfs(code) {
  function get(u) { var x = new XMLHttpRequest(); x.open('GET', u, false); x.send(); if (x.status != 200) throw 'GET ' + x.status; return x.responseText; }
  function post(u, body) { var x = new XMLHttpRequest(); x.open('POST', u, false); x.setRequestHeader('X-Code', code); x.setRequestHeader('Content-Type', 'application/json'); x.send(body); return x.status; }
  var todo = JSON.parse(get('https://raw.githubusercontent.com/Dan2Duper/betting-model/data/dfs_todo.json?t=' + new Date().getTime()));
  var done = 0, bad = [];
  for (var i = 0; i < todo.slates.length; i++) {
    var s = todo.slates[i];
    try { var st = post(RELAY + '/salaries?sport=' + s.sport + '&dg=' + s.dg, get(s.url)); if (st == 200) done++; else bad.push(s.label + ' (' + st + ')'); }
    catch (e) { bad.push(s.label + ' (' + e + ')'); }
  }
  flash('DFS salaries sent: ' + done + ' of ' + todo.slates.length + (bad.length ? '. Problems: ' + bad.join(', ') : ''));
}
