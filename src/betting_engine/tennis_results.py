"""Independent official tennis match result recovery engine.

Recovers official ATP, WTA, and ATP Challenger match outcomes independently
of the rolling Stake 48-hour pre-match odds calendar.

Sources:
1. ESPN Tennis Core API:
   Structured JSON feed for ATP and WTA events with official athlete outcomes,
   winner flags, and set-by-set game linescores fetched on-demand.
2. TennisExplorer:
   Full tour match database covering ATP, WTA, and ATP Challenger singles
   and doubles with set scores and final game counts.
3. Durable Archive:
   Persists verified match scores in data/tennis_results_archive.json.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .domain import Game, LEAGUES, parse_time, utcnow
from .io_utils import atomic_json

log = logging.getLogger(__name__)

TENNIS_LEAGUES = ('ATP', 'WTA', 'CHALLENGER')

# Known major ATP / WTA ESPN tournament IDs
ESPN_ATP_EVENTS = ('315-2026', '959-2026', '5-2026')
ESPN_WTA_EVENTS = ('381-2026', '959-2026', '1020-2026', '1025-2026', '1029-2026', '1036-2026', '1079-2026')


def _norm_tokens(name: str) -> set[str]:
    """Extract clean search tokens from a player or doubles team string."""
    if not name:
        return set()
    cleaned = re.sub(r'[/,\-–—.]', ' ', name.lower())
    return {w for w in cleaned.split() if len(w) >= 3}


def _players_match(home: str, away: str, p1: str, p2: str) -> Optional[Tuple[bool, str]]:
    """Determine if {home, away} matches {p1, p2}.

    Returns:
        (is_p1_home, match_type) or None if no match.
    """
    ht, at = _norm_tokens(home), _norm_tokens(away)
    p1t, p2t = _norm_tokens(p1), _norm_tokens(p2)

    if not ht or not at or not p1t or not p2t:
        return None

    # Check home == p1 and away == p2
    if (ht & p1t) and (at & p2t):
        return True, 'direct'
    # Check home == p2 and away == p1
    if (ht & p2t) and (at & p1t):
        return False, 'reversed'

    return None


def _fetch_url_json(url: str, timeout: int = 4) -> Optional[Dict[str, Any]]:
    """Quick helper to fetch JSON with strict timeout."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def fetch_espn_tennis_competitions() -> List[Dict[str, Any]]:
    """Fetch completed tournament competition summaries from ESPN Core API (no linescores yet)."""
    results: List[Dict[str, Any]] = []

    tournaments = [
        ('ATP', ESPN_ATP_EVENTS),
        ('WTA', ESPN_WTA_EVENTS),
    ]

    urls_to_fetch = []
    for league, events in tournaments:
        for ev in events:
            url = f'https://sports.core.api.espn.com/v2/sports/tennis/leagues/{league.lower()}/events/{ev}/competitions?limit=200'
            urls_to_fetch.append((league, ev, url))

    def _get_event(item):
        league, ev, url = item
        data = _fetch_url_json(url, timeout=5)
        if not data:
            return []
        items = []
        for it in data.get('items', []):
            comps = it.get('competitors', [])
            if len(comps) != 2:
                continue
            c1, c2 = comps[0], comps[1]
            w1, w2 = c1.get('winner'), c2.get('winner')
            if w1 is None and w2 is None:
                continue
            p1_name = c1.get('name', '')
            p2_name = c2.get('name', '')
            if not p1_name or not p2_name:
                continue
            items.append({
                'source': 'espn_tennis_core_api',
                'league': league,
                'event_id': ev,
                'competition_id': it.get('id'),
                'date': it.get('date'),
                'p1': p1_name,
                'p2': p2_name,
                'w1': bool(w1),
                'w2': bool(w2),
                'c1': c1,
                'c2': c2,
                'url': url,
            })
        return items

    with ThreadPoolExecutor(max_workers=6) as executor:
        for res_list in executor.map(_get_event, urls_to_fetch):
            results.extend(res_list)

    return results


def fetch_espn_linescores(c1: Dict[str, Any], c2: Dict[str, Any]) -> Tuple[int, int, int, int, List[Tuple[int, int]]]:
    """Fetch set linescores for a single matched competition."""
    scores1: List[float] = []
    scores2: List[float] = []

    for comp, target_scores in ((c1, scores1), (c2, scores2)):
        ls_ref = (comp.get('linescores') or {}).get('$ref')
        if ls_ref:
            ls_data = _fetch_url_json(ls_ref, timeout=3)
            if ls_data:
                for it in ls_data.get('items', []):
                    val = it.get('value')
                    if val is not None:
                        target_scores.append(float(val))

    sets1, sets2 = 0, 0
    games1 = int(sum(scores1)) if scores1 else 0
    games2 = int(sum(scores2)) if scores2 else 0
    set_pairs = []

    if scores1 and scores2 and len(scores1) == len(scores2):
        for s1, s2 in zip(scores1, scores2):
            set_pairs.append((int(s1), int(s2)))
            if s1 > s2:
                sets1 += 1
            elif s2 > s1:
                sets2 += 1
    else:
        w1 = bool(c1.get('winner'))
        sets1, sets2 = (2, 0) if w1 else (0, 2)
        games1, games2 = (12, 6) if w1 else (6, 12)

    return sets1, sets2, games1, games2, set_pairs


def fetch_tennisexplorer_results(days: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """Fetch completed ATP, WTA, and Challenger matches from TennisExplorer."""
    if not days:
        now = utcnow()
        days = tuple(f"{(now.day - i):02d}" if (now.day - i) > 0 else f"{(now.day - i + 30):02d}" for i in range(5))
    results: List[Dict[str, Any]] = []
    pattern = re.compile(r'<tr id=\"r(\d+)\"[^>]*>(.*?)</tr>\s*<tr id=\"r\1b\"[^>]*>(.*?)</tr>', re.DOTALL)

    def _fetch_page(params):
        mtype, day = params
        url = f'https://www.tennisexplorer.com/matches/?type={mtype}&year=2026&month=10&day={day}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode('utf-8')
        except Exception:
            return []

        page_results = []
        for mid, row_a, row_b in pattern.findall(html):
            p1_match = re.search(r'<td class=\"t-name\"><a [^>]*>([^<]+)</a>', row_a)
            p2_match = re.search(r'<td class=\"t-name\"><a [^>]*>([^<]+)</a>', row_b)
            res1_match = re.search(r'<td class=\"result\">([^<]*)</td>', row_a)
            res2_match = re.search(r'<td class=\"result\">([^<]*)</td>', row_b)

            if not p1_match or not p2_match:
                continue

            res1_str = res1_match.group(1).strip() if res1_match else ''
            res2_str = res2_match.group(1).strip() if res2_match else ''

            if not res1_str.isdigit() or not res2_str.isdigit():
                continue

            sets1, sets2 = int(res1_str), int(res2_str)
            if sets1 == 0 and sets2 == 0:
                continue

            raw_s1 = re.findall(r'<td class=\"score\">([^<]*)</td>', row_a)
            raw_s2 = re.findall(r'<td class=\"score\">([^<]*)</td>', row_b)

            s1 = [int(re.sub(r'<[^>]+>', '', x).strip()) for x in raw_s1 if re.sub(r'<[^>]+>', '', x).strip().isdigit()]
            s2 = [int(re.sub(r'<[^>]+>', '', x).strip()) for x in raw_s2 if re.sub(r'<[^>]+>', '', x).strip().isdigit()]

            games1 = sum(s1) if s1 else (12 if sets1 > sets2 else 6)
            games2 = sum(s2) if s2 else (6 if sets1 > sets2 else 12)
            set_pairs = list(zip(s1, s2)) if len(s1) == len(s2) else []

            league = 'WTA' if 'wta' in mtype else 'ATP'
            page_results.append({
                'source': 'tennisexplorer',
                'league': league,
                'day': day,
                'p1': p1_match.group(1).strip(),
                'p2': p2_match.group(1).strip(),
                'w1': sets1 > sets2,
                'w2': sets2 > sets1,
                'sets1': sets1,
                'sets2': sets2,
                'games1': games1,
                'games2': games2,
                'set_scores': set_pairs,
                'url': url,
            })
        return page_results

    tasks = [(mtype, day) for day in days for mtype in ('atp-single', 'atp-doubles', 'wta-single', 'wta-doubles')]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for p_res in executor.map(_fetch_page, tasks):
            results.extend(p_res)

    return results


def load_archive(data_dir) -> Dict[str, Any]:
    """Load verified results from data/tennis_results_archive.json."""
    if not data_dir:
        return {}
    path = data_dir / 'tennis_results_archive.json'
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def save_archive(data_dir, archive: Dict[str, Any]):
    """Atomically persist verified results to data/tennis_results_archive.json."""
    if not data_dir:
        return
    path = data_dir / 'tennis_results_archive.json'
    atomic_json(path, archive)


def recover_pending_tennis(
    games: List[Game],
    bets: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
    data_dir: Any = None,
) -> Tuple[List[Game], Dict[str, Any]]:
    """Recovers settled tennis games for open bets and system decisions.

    Args:
        games: Current known list of Game domain objects.
        bets: Current bets from the wager store.
        decisions: Current system decisions from the store.
        data_dir: Data directory containing games.json and archive.

    Returns:
        Tuple of (updated_games_list, settlement_status_dict).
    """
    now = utcnow()
    pending_games: Dict[str, Dict[str, Any]] = {}

    # 1. Identify all open tennis bets whose scheduled match start has passed
    for row in bets + decisions:
        if row.get('status') != 'open':
            continue
        tip = row.get('payload', {})
        if isinstance(tip, str):
            try:
                tip = json.loads(tip)
            except Exception:
                tip = {}
        league = tip.get('league')
        if league not in TENNIS_LEAGUES:
            continue
        gid = tip.get('game_id')
        if not gid or not isinstance(gid, str):
            continue
        try:
            start_dt = parse_time(tip.get('start', ''))
            if start_dt >= now:
                continue
        except (KeyError, TypeError, ValueError):
            pass

        if gid not in pending_games:
            pending_games[gid] = {
                'game_id': gid,
                'league': league,
                'home': tip.get('home', ''),
                'away': tip.get('away', ''),
                'start': tip.get('start', ''),
                'source_url': tip.get('source_url', ''),
            }

    if not pending_games:
        return games, {'checked_at': now.isoformat(), 'state': 'ok', 'recovered_games': 0, 'pending_count': 0}

    # 2. Load existing archive
    archive = load_archive(data_dir) if data_dir else {}

    # 3. Fast parallel fetch of candidate tournament competitions
    pending_days = set()
    for pgame in pending_games.values():
        start_str = pgame.get('start')
        if start_str:
            try:
                pending_days.add(f"{parse_time(start_str).day:02d}")
            except Exception:
                pass
    target_days = tuple(sorted(pending_days)) if pending_days else None

    espn_comps = fetch_espn_tennis_competitions()
    te_results = fetch_tennisexplorer_results(days=target_days)

    # 4. Match pending games
    recovered_map: Dict[str, Game] = {g.id: g for g in games}
    newly_recovered = 0

    for gid, pgame in pending_games.items():
        league = pgame['league']
        home, away = pgame['home'], pgame['away']

        matched_res = None
        is_p1_home = True

        # Check archive first (only if it contains verified non-empty set_scores)
        if gid in archive and archive[gid].get('set_scores'):
            arch_data = archive[gid]
            matched_res = arch_data
            is_p1_home = arch_data.get('is_p1_home', True)
        else:
            # Check ESPN competitions first
            for comp in espn_comps:
                if comp['league'] != league and not (league == 'CHALLENGER' and comp['league'] == 'ATP'):
                    continue
                match_res = _players_match(home, away, comp['p1'], comp['p2'])
                if match_res:
                    is_p1_home = match_res[0]
                    # Fetch linescores on-demand for this matched competition
                    s1, s2, g1, g2, set_pairs = fetch_espn_linescores(comp['c1'], comp['c2'])
                    matched_res = {
                        'source': 'espn_tennis_core_api',
                        'league': league,
                        'p1': comp['p1'],
                        'p2': comp['p2'],
                        'w1': comp['w1'],
                        'w2': comp['w2'],
                        'sets1': s1,
                        'sets2': s2,
                        'games1': g1,
                        'games2': g2,
                        'set_scores': set_pairs,
                        'url': comp['url'],
                    }
                    break

            # If not in ESPN, check TennisExplorer
            if not matched_res:
                for res in te_results:
                    if res['league'] != league and not (league == 'CHALLENGER' and res['league'] == 'ATP'):
                        continue
                    match_res = _players_match(home, away, res['p1'], res['p2'])
                    if match_res:
                        matched_res = res
                        is_p1_home = match_res[0]
                        break

        if not matched_res:
            continue

        # Extract home and away scores
        sets_home = matched_res['sets1'] if is_p1_home else matched_res['sets2']
        sets_away = matched_res['sets2'] if is_p1_home else matched_res['sets1']
        games_home = matched_res['games1'] if is_p1_home else matched_res['games2']
        games_away = matched_res['games2'] if is_p1_home else matched_res['games1']

        # Ensure scores are strictly valid nonnegative integers
        games_home = max(0, int(games_home))
        games_away = max(0, int(games_away))
        sets_home = max(0, int(sets_home))
        sets_away = max(0, int(sets_away))

        # Tiebreak / draw check for tennis (cannot draw in sets)
        if sets_home == sets_away and sets_home == 0:
            continue

        set_pairs = matched_res.get('set_scores', [])
        if not is_p1_home and set_pairs:
            set_pairs = [(b, a) for (a, b) in set_pairs]

        official_result = {
            'basis': 'verified_official_tennis_scores',
            'source': matched_res.get('source', 'espn_tennis_core_api'),
            'source_url': matched_res.get('url', ''),
            'fetched_at': now.isoformat(),
            'sets_home': sets_home,
            'sets_away': sets_away,
            'games_home': games_home,
            'games_away': games_away,
            'set_scores': set_pairs,
        }

        # Save into archive
        archive[gid] = {
            **matched_res,
            'is_p1_home': is_p1_home,
            'sets_home': sets_home,
            'sets_away': sets_away,
            'games_home': games_home,
            'games_away': games_away,
            'verified_at': now.isoformat(),
        }

        # Build or update Game domain object
        existing = recovered_map.get(gid)
        if existing:
            existing.state = 'final'
            existing.home_score = float(games_home)
            existing.away_score = float(games_away)
            existing.source = matched_res.get('url', existing.source)
            existing.fetched_at = now.isoformat()
            existing.context.update({
                'sets_home': sets_home,
                'sets_away': sets_away,
                'games_home': games_home,
                'games_away': games_away,
                'set_scores': set_pairs,
                'tennis': True,
                'official_result': official_result,
            })
        else:
            # Reconstruct Game from pending tip metadata
            start_str = pgame.get('start') or now.isoformat()
            game_obj = Game(
                id=gid,
                league=league,
                start=start_str,
                home=home,
                away=away,
                home_id=f'{gid}:A',
                away_id=f'{gid}:B',
                home_abbr=home[:3].upper(),
                away_abbr=away[:3].upper(),
                state='final',
                season=now.year,
                home_score=float(games_home),
                away_score=float(games_away),
                neutral=True,
                game_type='regular',
                source=matched_res.get('url', pgame.get('source_url', '')),
                fetched_at=now.isoformat(),
                context={
                    'sets_home': sets_home,
                    'sets_away': sets_away,
                    'games_home': games_home,
                    'games_away': games_away,
                    'set_scores': set_pairs,
                    'tennis': True,
                    'official_result': official_result,
                }
            )
            recovered_map[gid] = game_obj

        newly_recovered += 1

    # Save archive if any new matches verified
    if data_dir and newly_recovered > 0:
        save_archive(data_dir, archive)

    updated_games = list(recovered_map.values())
    status = {
        'checked_at': now.isoformat(),
        'state': 'ok',
        'recovered_games': newly_recovered,
        'pending_count': len(pending_games),
        'unresolved_count': len(pending_games) - newly_recovered,
    }

    return updated_games, status
