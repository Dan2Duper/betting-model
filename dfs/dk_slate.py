"""DraftKings slates and salaries (public lobby + draftables JSON). Runs where DraftKings is reachable (GitHub Actions, a PC)."""
import json, re, urllib.request, datetime as dt
UA = {'User-Agent': 'Mozilla/5.0 (betting-model DFS)'}
TEAM_FIX = {'LAR': 'LA', 'JAC': 'JAX', 'WSH': 'WAS', 'LVR': 'LV'}   # DraftKings -> nflverse

def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r: return json.load(r)

def draft_groups(sport='NFL'):
    """Every open draft group: id, game type (Classic / Showdown Captain Mode), label, start, number of games."""
    L = _get(f'https://www.draftkings.com/lobby/getcontests?sport={sport}')
    types = {}
    for c in L.get('Contests', []):
        g = types.setdefault(c['dg'], dict(dg=c['dg'], game_type=c.get('gameType', ''), start=c.get('sd', ''), label=c.get('sdstring', ''), entries=0, prize=0, contests=0))
        g['entries'] += c.get('m', 0) or 0; g['prize'] += c.get('po', 0) or 0; g['contests'] += 1
    for x in L.get('DraftGroups', []):
        g = types.get(x.get('DraftGroupId'))
        if g: g.update(games=x.get('GameCount'), tag=x.get('DraftGroupTag', ''), suffix=x.get('ContestStartTimeSuffix') or '', start_est=x.get('StartDateEst', ''))
    return sorted(types.values(), key=lambda g: -g['prize'])

def pick_slates(groups, days=7):
    """Main Classic slate (the plain 'Classic' group with the most contests) and every single-game Showdown
    that hasn't started yet, within the next `days` days. Lobby times are US Eastern."""
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None)
    def upcoming(g):
        try: t = dt.datetime.fromisoformat((g.get('start_est') or '')[:19])
        except ValueError: return True
        return now < t < now + dt.timedelta(days=days)
    classic = [g for g in groups if g['game_type'] == 'Classic' and not (g.get('suffix') or '').strip() and upcoming(g)]
    main = max(classic, key=lambda g: g['contests']) if classic else None
    sd = [g for g in groups if g['game_type'] == 'Showdown Captain Mode' and g.get('games') in (None, 1) and upcoming(g)]
    for g in sd: g['label'] = f"{(g.get('suffix') or '').strip(' ()')} {g.get('label', '')}".strip()
    return main, sorted(sd, key=lambda g: g.get('start_est') or '')

def draftables_url(dg):
    return f'https://api.draftkings.com/draftgroups/v1/draftgroups/{dg}/draftables'

def players(dg):
    """Player pool for a draft group, fetched directly (works from a home connection, not from cloud servers)."""
    return parse(_get(draftables_url(dg)))

def parse(J):
    """DraftKings draftables JSON -> one row per player (showdown rows carry flex and captain salaries)."""
    rows = {}
    for p in J.get('draftables', []):
        if p.get('isDisabled'): continue
        comp = p.get('competition') or {}
        key = p['playerId']
        r = rows.setdefault(key, dict(dk_id=p['playerId'], name=p['displayName'], pos=p['position'], team=TEAM_FIX.get(p['teamAbbreviation'], p['teamAbbreviation']),
                                      game=comp.get('name', ''), start=comp.get('startTime', ''), status=p.get('status') or 'None', salary=None, cpt_salary=None,
                                      draftable_id=None, cpt_draftable_id=None))
        sal = p['salary']
        if r['salary'] is None or sal < r['salary']:
            if r['salary'] is not None: r['cpt_salary'], r['cpt_draftable_id'] = r['salary'], r['draftable_id']
            r['salary'], r['draftable_id'] = sal, p['draftableId']
        else:
            r['cpt_salary'], r['cpt_draftable_id'] = sal, p['draftableId']
    out = list(rows.values())
    for r in out:
        a, h = (r['game'].split(' @ ') + [''])[:2]
        a, h = TEAM_FIX.get(a, a), TEAM_FIX.get(h, h)
        r['opp'] = h if r['team'] == a else a
    return out
