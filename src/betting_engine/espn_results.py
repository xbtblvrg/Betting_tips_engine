"""Recover exact outstanding ESPN event results without accessing the ledger.

Only completed full-game scores are supported. Period scores are deliberately
absent, including when a stale schedule Game contains old period enrichment.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import re

from .contracts import split_market
from .domain import Game, parse_time, utcnow
from .io_utils import atomic_bytes, atomic_json
from .providers import DATA, Fetcher

VERSION = 'espn-official-settlement-v1'
LEAGUE_PATHS = {
    'NBA': 'basketball/nba', 'WNBA': 'basketball/wnba',
    'NCAAF': 'football/college-football',
    'NCAAM': 'basketball/mens-college-basketball',
    'NCAAW': 'basketball/womens-college-basketball',
}
MAX_BODY_BYTES = 4 * 1024 * 1024


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[1-9]\d{0,11}', str(value)):
        raise ValueError('invalid_result_identifier')
    return str(value)


def _integer(value):
    # ESPN encodes scores as decimal strings. Do not accept whitespace,
    # fractions, booleans, NaN, negatives or a missing score as zero.
    if isinstance(value, str):
        if not re.fullmatch(r'\d{1,6}', value):
            raise ValueError('invalid_nonnegative_integer')
        return int(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value or value > 999999:
        raise ValueError('invalid_nonnegative_integer')
    return int(value)


def _identity(tip):
    league = tip.get('league')
    gid = tip.get('game_id', '')
    if league not in LEAGUE_PATHS or not isinstance(gid, str) or not re.fullmatch(re.escape(league) + r':[1-9]\d{0,11}', gid):
        raise ValueError('invalid_espn_game_identity')
    home, away = tip.get('home'), tip.get('away')
    if any(not isinstance(name, str) or not name or name != name.strip() or len(name) > 200 for name in (home, away)) or home == away:
        raise ValueError('invalid_original_team_identity')
    return gid, home, away, parse_time(tip['start'])


def _url(tip):
    return f'https://site.api.espn.com/apis/site/v2/sports/{LEAGUE_PATHS[tip["league"]]}/summary?event={tip["game_id"].split(":")[1]}'


def parse_summary(raw, meta, tip, now=None):
    """Validate source bytes, immutable event identity and final score only."""
    now = now or utcnow()
    gid, home, away, start = _identity(tip)
    url = _url(tip)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BODY_BYTES:
        raise ValueError('invalid_result_body')
    if meta.get('url') != url or hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('result_source_hash_or_url_mismatch')
    fetched = parse_time(meta['fetched_at'])
    if fetched > now + timedelta(seconds=5) or fetched < start or start >= now:
        raise ValueError('invalid_result_source_time')
    try:
        body = json.loads(raw)
        header = body['header']
        event = gid.split(':')[1]
        if _identifier(header['id']) != event:
            raise ValueError('result_event_identity_mismatch')
        league = header['league']
        if league.get('slug') != LEAGUE_PATHS[tip['league']].split('/')[1]:
            raise ValueError('result_league_identity_mismatch')
        competitions = header['competitions']
        if not isinstance(competitions, list) or len(competitions) != 1:
            raise ValueError('ambiguous_result_competition')
        competition = competitions[0]
        if _identifier(competition['id']) != event:
            raise ValueError('result_competition_identity_mismatch')
        actual_start = parse_time(competition['date'])
        if actual_start != start:
            raise ValueError('result_start_mismatch')
        status = competition['status']['type']
        if status.get('completed') is not True or status.get('state') != 'post' or status.get('name') not in ('STATUS_FINAL', 'STATUS_FINAL_OVERTIME'):
            raise ValueError('official_game_not_final')
        competitors = competition['competitors']
        if not isinstance(competitors, list) or len(competitors) != 2 or {row.get('homeAway') for row in competitors} != {'home', 'away'}:
            raise ValueError('ambiguous_result_teams')
        teams = {row['homeAway']: row for row in competitors}
        ids, scores = {}, {}
        original_ids_checked = []
        for side, original_name in (('home', home), ('away', away)):
            row = teams[side]
            team_id = _identifier(row['id'])
            if team_id != _identifier(row['team']['id']) or row['team']['displayName'] != original_name:
                raise ValueError('result_team_identity_mismatch')
            original_id = tip.get(side + '_id')
            if original_id is not None:
                if _identifier(original_id) != team_id:
                    raise ValueError('result_original_team_id_mismatch')
                original_ids_checked.append(side)
            ids[side] = team_id
            scores[side] = _integer(row['score'])
        if ids['home'] == ids['away']:
            raise ValueError('result_same_team_identity')
        if scores['home'] == scores['away']:
            raise ValueError('final_winner_missing')
        for side in ('home', 'away'):
            expected = scores[side] > scores['away' if side == 'home' else 'home']
            if teams[side].get('winner') is not expected:
                raise ValueError('result_winner_score_conflict')
        season = _integer(header['season']['year'])
        if not start.year - 1 <= season <= start.year + 1 or header['season'].get('type') not in (2, 3):
            raise ValueError('unsupported_result_season')
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeDecodeError) as exc:
        raise ValueError('malformed_result_body') from exc
    source = {
        'version': VERSION, 'url': url, 'sha256': meta['sha256'], 'fetched_at': meta['fetched_at'],
        'game_id': gid, 'start': actual_start.isoformat(), 'home_id': ids['home'], 'away_id': ids['away'],
        'home_score': scores['home'], 'away_score': scores['away'],
        'team_identity_basis': 'exact_original_names_and_consistent_espn_ids',
        'expected_team_ids_checked': original_ids_checked,
        'scoped_scores_state': 'not_supported',
        'scoped_scores_reason': 'This recovery validates full-game scores only; period contracts require separate verified scope evidence.',
        'claim': 'Official final score evidence; sportsbook account settlement is not verified.',
    }
    return Game(gid, tip['league'], actual_start.isoformat(), home, away, ids['home'], ids['away'],
                teams['home']['team'].get('abbreviation', ''), teams['away']['team'].get('abbreviation', ''),
                'final', season, scores['home'], scores['away'], neutral=competition.get('neutralSite') is True,
                game_type='regular' if header['season']['type'] == 2 else 'playoff', source=url,
                fetched_at=meta['fetched_at'], context={'official_result': source})


def _original(tips, existing):
    """Combine compatible original snapshots without guessing absent team IDs."""
    if len({_identity(tip) for tip in tips}) != 1:
        raise ValueError('conflicting_original_identities')
    tip = dict(tips[0])
    if existing is not None:
        if (existing.id, existing.home, existing.away, parse_time(existing.start)) != _identity(tip) or existing.league != tip['league']:
            raise ValueError('conflicting_schedule_identity')
    for side in ('home', 'away'):
        ids = {_identifier(t[side + '_id']) for t in tips if t.get(side + '_id') is not None}
        if existing is not None:
            ids.add(_identifier(getattr(existing, side + '_id')))
        if len(ids) > 1:
            raise ValueError('conflicting_original_team_ids')
        if ids:
            tip[side + '_id'] = ids.pop()
    return tip


def _scope_report(tips):
    scopes = set()
    for tip in tips:
        try:
            scope, _ = split_market(tip['market'])
        except (KeyError, ValueError, TypeError):
            scope = 'unsupported_contract'
        if scope != 'full_game':
            scopes.add(scope)
    return sorted(scopes)


def recover_pending_espn(games, bets, decisions, *, data_dir=DATA, fetcher=None, now=None, max_games=12):
    """Return recovered sports records/status; never read or modify wager storage.

    Twelve exact events and four concurrent requests maximum per cycle. Source
    failures back off for 300 seconds; archived finals replay before backoff.
    """
    fixed_now = now is not None
    now = now or utcnow()
    data = Path(data_dir)
    pending, problems = defaultdict(list), []
    quarantined = set()
    mapping = {game.id: game for game in games}
    for source, rows in (('bet', bets), ('decision', decisions)):
        for row in rows:
            tip = row.get('payload', {})
            if not isinstance(tip, dict) or row.get('status') != 'open':
                continue
            gid = tip.get('game_id')
            prefix = gid.split(':', 1)[0] if isinstance(gid, str) else None
            # A malformed league must not hide a conflicting original from
            # the valid record for this same event. Settlement matches by ID.
            if tip.get('league') not in LEAGUE_PATHS and prefix not in LEAGUE_PATHS:
                continue
            if source == 'bet' and (tip.get('ledger_contract') != 'sportsbook' or 'polymarket' in row.get('platform', '').lower()):
                continue
            try:
                identity = _identity(tip)
                # Include future originals until all same-event records have
                # been checked for conflicts with one another and the Game.
                pending[identity[0]].append(tip)
            except (ValueError, KeyError, TypeError, AttributeError):
                if isinstance(gid, str):
                    quarantined.add(gid)
                problems.append({'game_id': gid, 'state': 'invalid_original_identity'})
    for gid in quarantined:
        if gid in mapping:
            mapping[gid] = deepcopy(mapping[gid])
            mapping[gid].context['espn_settlement_pending'] = True
    folder = data / 'espn_settlement'
    state_path = folder / 'recovery.json'
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    attempts = previous.get('attempts', {}) if isinstance(previous, dict) else {}
    if not isinstance(attempts, dict):
        attempts = {}
    tasks, cached_recovered = [], 0
    for gid, tips in sorted(pending.items(), key=lambda pair: _identity(pair[1][0])[3]):
        existing = mapping.get(gid)
        if existing is not None:
            existing = deepcopy(existing)
            existing.context['espn_settlement_pending'] = True
            mapping[gid] = existing
        if gid in quarantined:
            continue
        try:
            tip = _original(tips, existing)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            problems.append({'game_id': gid, 'state': 'conflicting_original_identities', 'error': str(exc)[:200]})
            continue
        if _identity(tip)[3] >= now:
            if existing is not None and existing.state == 'final':
                problems.append({'game_id': gid, 'state': 'original_game_not_started'})
            elif existing is not None:
                existing.context.pop('espn_settlement_pending', None)
            continue
        prior = attempts.get(gid, {})
        if not isinstance(prior, dict):
            prior = {}
        source = prior.get('source') or (existing.context.get('official_result') if existing else None) or {}
        try:
            sha = source['sha256']
            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
                raise ValueError('invalid_archive_hash')
            relative = 'espn_settlement/sources/' + sha + '.json'
            if source.get('archive') != relative:
                raise ValueError('invalid_archive_path')
            path = data / relative
            if path.stat().st_size > MAX_BODY_BYTES:
                raise ValueError('invalid_result_body')
            archived = parse_summary(path.read_bytes(), source, tip, now)
            archived.context['official_result']['archive'] = relative
            mapping[gid] = archived
            cached_recovered += 1
            missing = _scope_report(tips)
            problems.append({'game_id': gid, 'state': 'partial_result_recovered' if missing else 'official_final_recovered',
                             'reused_archived_evidence': True, 'source': archived.context['official_result'], 'missing_scopes': missing})
            continue
        except (OSError, KeyError, ValueError, TypeError):
            pass
        try:
            backed_off = parse_time(prior['next_attempt_at']) > now
        except (KeyError, ValueError, TypeError, AttributeError):
            backed_off = False
        if backed_off:
            problems.append({'game_id': gid, 'state': 'retry_backoff', 'next_attempt_at': prior['next_attempt_at'], 'last_error': prior.get('error')})
            continue
        if len(tasks) >= max(1, min(int(max_games), 12)):
            problems.append({'game_id': gid, 'state': 'deferred_batch_limit'})
            continue
        tasks.append((gid, tip))
    owned_fetcher = fetcher is None and bool(tasks)
    if owned_fetcher:
        fetcher = Fetcher(data)
        fetcher.client.timeout = 5.0

    def fetch(task):
        gid, tip = task
        url = _url(tip)
        try:
            raw, meta = fetcher.get(url, ttl=300)
            # Network batches can complete more than five seconds after the
            # cycle began; an explicitly supplied test clock stays fixed.
            game = parse_summary(raw, meta, tip, now if fixed_now else utcnow())
            archive = folder / 'sources' / (meta['sha256'] + '.json')
            if archive.exists():
                if archive.stat().st_size > MAX_BODY_BYTES or hashlib.sha256(archive.read_bytes()).hexdigest() != meta['sha256']:
                    raise ValueError('immutable_result_archive_corrupt')
            else:
                archive.parent.mkdir(parents=True, exist_ok=True)
                atomic_bytes(archive, raw)
            game.context['official_result']['archive'] = str(archive.relative_to(data))
            missing = _scope_report(pending[gid])
            return gid, game, {'state': 'partial_result_recovered' if missing else 'official_final_recovered',
                               'source': game.context['official_result'], 'missing_scopes': missing}
        except Exception as exc:
            return gid, None, {'state': 'result_source_unresolved', 'error': str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__, 'url': url}

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(fetch, tasks))
    finally:
        if owned_fetcher:
            fetcher.client.close()
    for gid, game, result in results:
        old = attempts.get(gid, {})
        count = old.get('count', 0) if isinstance(old, dict) else 0
        count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0
        attempts[gid] = {**result, 'count': count + 1, 'last_attempt_at': now.isoformat(),
                         'next_attempt_at': (now + timedelta(seconds=300)).isoformat()}
        history = folder / 'attempts' / (gid.replace(':', '-') + '-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        history.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(history, {'game_id': gid, **attempts[gid]})
        attempts[gid]['attempt_evidence'] = str(history.relative_to(data))
        if game is not None:
            mapping[gid] = game
        problems.append({'game_id': gid, **result})
    unresolved = sum(row['state'] != 'official_final_recovered' for row in problems)
    report = {'state': 'degraded' if unresolved else 'healthy', 'unresolved_count': unresolved,
              'version': VERSION, 'checked_at': now.isoformat(),
              'pending_started_games': sum(any(_identity(tip)[3] < now for tip in tips) for tips in pending.values()),
              'requested_games': len(tasks), 'recovered_games': cached_recovered + sum(game is not None for _, game, _ in results),
              'cached_recovered_games': cached_recovered, 'rows': problems, 'attempts': attempts,
              'claim': 'Official full-game result recovery only; original tickets unchanged. Period settlement remains independently scoped.'}
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(state_path, report)
    return list(mapping.values()), report
