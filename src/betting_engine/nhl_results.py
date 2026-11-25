"""Exact official NHL result recovery for outstanding original betting records.

This is score evidence only. It does not publish, resize, rewrite or settle a
bet, and never substitutes a full-game score for a period/regulation contract.
"""
from collections import defaultdict
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import hashlib
import json
import math
import re

from .domain import Game, parse_time, utcnow
from .providers import DATA, Fetcher, NHL_NAMES
from .contracts import settlement_scores, split_market
from .io_utils import atomic_json, atomic_bytes

VERSION = 'nhl-official-settlement-v1'
RULES_URL = 'https://stake.com/policies/sportsbook#ice-hockey'


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
        raise ValueError('invalid_nonnegative_integer')
    return int(value)


def _identity(tip):
    game_id = tip.get('game_id', '')
    if tip.get('league') != 'NHL' or not re.fullmatch(r'NHL:\d{10}', game_id):
        raise ValueError('invalid_nhl_game_identity')
    if not tip.get('home') or not tip.get('away') or tip['home'] == tip['away']:
        raise ValueError('invalid_nhl_team_identity')
    return game_id, tip['home'], tip['away'], parse_time(tip['start'])


def parse_landing(raw, meta, tip, now=None):
    """Verify raw official final + exact original identity; partial scopes stay absent."""
    now = now or utcnow()
    gid, home, away, start = _identity(tip)
    url = f'https://api-web.nhle.com/v1/gamecenter/{gid.split(":")[1]}/landing'
    if not isinstance(raw, bytes) or len(raw) > 4 * 1024 * 1024:
        raise ValueError('invalid_result_body')
    if meta.get('url') != url or hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('result_source_hash_or_url_mismatch')
    fetched = parse_time(meta['fetched_at'])
    if fetched > now + timedelta(seconds=5) or fetched < start or start >= now:
        raise ValueError('invalid_result_source_time')
    body = json.loads(raw)
    if str(body.get('id')) != gid.split(':')[1] or body.get('gameType') not in (2, 3):
        raise ValueError('result_event_identity_mismatch')
    actual_start = parse_time(body['startTimeUTC'])
    if abs((actual_start-start).total_seconds()) > 300:
        raise ValueError('result_start_mismatch')
    h, a = body['homeTeam'], body['awayTeam']
    ha, aa = h['abbrev'], a['abbrev']
    if NHL_NAMES.get(ha) != home or NHL_NAMES.get(aa) != away or ha == aa:
        raise ValueError('result_team_identity_mismatch')
    if body.get('gameState') not in ('OFF', 'FINAL'):
        raise ValueError('official_game_not_final')
    hs, aws = _integer(h.get('score')), _integer(a.get('score'))
    if hs == aws:
        raise ValueError('nhl_final_winner_missing')
    descriptor = body.get('periodDescriptor', {})
    last = _integer(descriptor.get('number'))
    last_type = descriptor.get('periodType')
    if descriptor.get('maxRegulationPeriods', 3) != 3 or last < 3 or last_type not in ('REG', 'OT', 'SO'):
        raise ValueError('unsupported_final_period')
    if (last_type == 'REG' and last != 3) or (last_type != 'REG' and last < 4):
        raise ValueError('conflicting_final_period')
    if last_type in ('OT', 'SO') and abs(hs-aws) != 1:
        raise ValueError('invalid_extra_period_final_margin')
    source = {'version': VERSION, 'url': url, 'sha256': meta['sha256'], 'fetched_at': meta['fetched_at'],
              'game_id': gid, 'start': actual_start.isoformat(), 'home_id': ha, 'away_id': aa,
              'home_score': hs, 'away_score': aws, 'last_period_type': last_type,
              'rules_url': RULES_URL, 'claim': 'Official final score evidence; sportsbook account settlement is not verified.'}
    game = Game(gid, 'NHL', actual_start.isoformat(), home, away, ha, aa, ha, aa,
                'final', int(str(body['season'])[-4:]), hs, aws,
                game_type='regular' if body['gameType'] == 2 else 'playoff', source=url,
                fetched_at=meta['fetched_at'], context={'official_result': source})
    try:
        sections = body['summary']['scoring']
        if not isinstance(sections, list):
            raise ValueError('missing_scoring_sections')
        by_period = {}
        seen_events = set()
        prior = (0, 0)
        # Missing empty periods are not inferred to be zero.
        for section in sections:
            desc = section['periodDescriptor']; number = _integer(desc['number']); kind = desc['periodType']
            if number in by_period or number < 1 or number > last:
                raise ValueError('duplicate_or_unexpected_scoring_period')
            if (number <= 3 and kind != 'REG') or (number > 3 and kind not in ('OT', 'SO')):
                raise ValueError('scoring_period_type_mismatch')
            if kind == 'SO' and last_type != 'SO':
                raise ValueError('unexpected_shootout_section')
            goals = section.get('goals')
            if not isinstance(goals, list):
                raise ValueError('missing_period_goals')
            by_period[number] = (kind, goals)
        if set(range(1, 4)) - by_period.keys():
            raise ValueError('incomplete_regulation_periods')
        non_so = [n for n,(kind,_) in by_period.items() if kind != 'SO']
        expected_last = last - 1 if last_type == 'SO' else last
        if set(non_so) != set(range(1, expected_last+1)):
            raise ValueError('incomplete_overtime_periods')
        scores = {}
        for number in sorted(non_so):
            home_goals = away_goals = 0
            goals = by_period[number][1]
            def event_key(goal):
                clock = str(goal['timeInPeriod'])
                if not re.fullmatch(r'\d{2}:\d{2}', clock) or int(clock[-2:]) >= 60:
                    raise ValueError('invalid_goal_time')
                return clock, _integer(goal['eventId'])
            for goal in sorted(goals, key=event_key):
                event = _integer(goal['eventId'])
                if event in seen_events:
                    raise ValueError('duplicate_goal_event')
                seen_events.add(event)
                current = (_integer(goal['homeScore']), _integer(goal['awayScore']))
                delta = (current[0]-prior[0], current[1]-prior[1])
                if delta not in ((1, 0), (0, 1)):
                    raise ValueError('incomplete_goal_sequence')
                team = goal.get('teamAbbrev', {}).get('default')
                if team != (ha if delta[0] else aa):
                    raise ValueError('goal_team_mismatch')
                home_goals += delta[0]; away_goals += delta[1]; prior = current
            scores[number] = (home_goals, away_goals)
        expected = (hs, aws)
        if last_type == 'SO':
            expected = (hs-int(hs>aws), aws-int(aws>hs))
            if expected[0] != expected[1] or any(sum(scores[n]) for n in scores if n > 3):
                raise ValueError('shootout_requires_tied_score_and_scoreless_overtime')
        if prior != expected:
            raise ValueError('scoring_sequence_final_mismatch')
        scopes = {f'period_{n}': scores[n] for n in (1, 2, 3)}
        scopes['regulation'] = (sum(scores[n][0] for n in (1, 2, 3)), sum(scores[n][1] for n in (1, 2, 3)))
        game.context['segment_scores'] = {scope: {'home': value[0], 'away': value[1], 'complete': True,
            'source': url, 'source_sha256': meta['sha256'], 'fetched_at': meta['fetched_at'],
            'final_summary_source': source, 'result_parser': VERSION} for scope,value in scopes.items()}
        source['scoped_scores_state'] = 'verified'
    except (KeyError, TypeError, ValueError) as exc:
        # Official final can settle full-game tickets; an absent/contradictory
        # goal log must never silently settle period/regulation tickets.
        source['scoped_scores_state'] = 'incomplete'
        source['scoped_scores_reason'] = str(exc) if isinstance(exc, ValueError) else 'malformed_scoring_sections'
    return game


def recover_pending_nhl(games, bets, decisions, *, data_dir=DATA, fetcher=None, now=None, max_games=12):
    """Return enriched games and durable diagnostics; no ledger access or writes.

    At most twelve exact games and four concurrent official reads per run.
    Failed/unfinished games back off for five minutes, allowing unrelated work.
    """
    now = now or utcnow(); data = Path(data_dir)
    pending = defaultdict(list); problems = []
    for source, rows in (('bet', bets), ('decision', decisions)):
        for row in rows:
            tip = row.get('payload', {})
            if row.get('status') != 'open' or tip.get('league') != 'NHL':continue
            if source == 'bet' and (tip.get('ledger_contract') != 'sportsbook' or 'polymarket' in row.get('platform', '').lower()):continue
            try:
                identity = _identity(tip)
                if identity[3] >= now:continue
                pending[identity[0]].append(tip)
            except (ValueError, KeyError, TypeError):
                problems.append({'game_id': tip.get('game_id'), 'state': 'invalid_original_identity'})
    mapping = {g.id: g for g in games}
    folder = data / 'nhl_settlement';state_path = folder / 'recovery.json'
    try: previous = json.loads(state_path.read_text())
    except FileNotFoundError: previous = {}
    except (ValueError, OSError): previous = {'state_read_error': True}
    attempts = previous.get('attempts', {})
    if not isinstance(attempts, dict):attempts = {}
    tasks = []; cached_recovered = 0
    for gid, tips in sorted(pending.items(), key=lambda pair: min(t['start'] for t in pair[1])):
        existing = mapping.get(gid)
        if existing is not None:
            # Keep sports-form data, but a stale schedule final must not leak
            # through settlement while its exact result recovery is pending.
            existing = deepcopy(existing)
            existing.context['nhl_settlement_pending'] = True
            mapping[gid] = existing
        if len({_identity(t) for t in tips}) != 1:
            problems.append({'game_id': gid, 'state': 'conflicting_original_identities'});continue
        # A plain final from a season/schedule feed is useful for detection,
        # but pending tickets still need a retained exact official result body.
        prior = attempts.get(gid, {})
        # A crash after source archival but before settlement/games persistence
        # must not strand a verified final behind the network retry backoff.
        source = prior.get('source') or (existing.context.get('official_result') if existing else None) or {}
        try:
            sha = source['sha256']
            if not re.fullmatch(r'[0-9a-f]{64}', sha):raise ValueError('invalid_archive_hash')
            relative = 'nhl_settlement/sources/' + sha + '.json'
            if source.get('archive') != relative:raise ValueError('invalid_archive_path')
            archived = parse_landing((data / relative).read_bytes(), source, tips[0], now)
            archived.context['official_result']['archive'] = relative
            for tip in tips:settlement_scores(tip, archived)
            mapping[gid] = archived; cached_recovered += 1
            problems.append({'game_id': gid, 'state': 'official_final_recovered',
                             'reused_archived_evidence': True, 'source': archived.context['official_result']})
            continue
        except (OSError, KeyError, ValueError, TypeError):pass
        try: backed_off = parse_time(prior['next_attempt_at']) > now
        except (KeyError, ValueError, TypeError):backed_off = False
        if backed_off:
            problems.append({'game_id': gid, 'state': 'retry_backoff', 'next_attempt_at': prior['next_attempt_at'], 'last_error': prior.get('error')});continue
        if len(tasks) >= max(1, min(int(max_games), 12)):
            problems.append({'game_id': gid, 'state': 'deferred_batch_limit'});continue
        tasks.append((gid, tips[0]))
    owned_fetcher = fetcher is None and bool(tasks)
    if owned_fetcher:
        fetcher = Fetcher(data)
        # Result recovery must not monopolize the market loop on a dead host.
        fetcher.client.timeout = 5.0
    def fetch(task):
        gid, tip = task; url = f'https://api-web.nhle.com/v1/gamecenter/{gid.split(":")[1]}/landing'
        try:
            raw, meta = fetcher.get(url, ttl=300)
            game = parse_landing(raw, meta, tip, now)
            archive = folder / 'sources' / (meta['sha256'] + '.json')
            if archive.exists():
                if hashlib.sha256(archive.read_bytes()).hexdigest() != meta['sha256']:
                    raise ValueError('immutable_result_archive_corrupt')
            else:
                archive.parent.mkdir(parents=True, exist_ok=True)
                atomic_bytes(archive, raw)
            # Raw response is content-addressed and retained separately from
            # the URL cache; exact fetch metadata survives in every attempt.
            game.context['official_result']['archive'] = str(archive.relative_to(data))
            return gid, game, {'state': 'official_final_recovered', 'source': game.context['official_result']}
        except Exception as exc:
            return gid, None, {'state': 'result_source_unresolved', 'error': str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__, 'url': url}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(fetch, tasks))
    finally:
        if owned_fetcher:fetcher.client.close()
    for gid, game, result in results:
        old = attempts.get(gid, {})
        count = old.get('count', 0) if isinstance(old.get('count', 0), int) else 0
        attempts[gid] = {**result, 'count': count+1, 'last_attempt_at': now.isoformat(),
                        'next_attempt_at': (now+timedelta(seconds=300)).isoformat()}
        history_path = folder / 'attempts' / (gid.replace(':', '-') + '-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        history_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(history_path, {'game_id': gid, **attempts[gid]})
        attempts[gid]['attempt_evidence'] = str(history_path.relative_to(data))
        if game is not None:
            mapping[gid] = game
            scopes = {split_market(t['market'])[0] for t in pending[gid]}
            missing = sorted(scopes - {'full_game'} - game.context.get('segment_scores', {}).keys())
            result = {**result, 'missing_scopes': missing, 'state': 'partial_result_recovered' if missing else result['state']}
        problems.append({'game_id': gid, **result})
    unresolved = sum(r['state'] != 'official_final_recovered' for r in problems)
    report = {'state': 'degraded' if unresolved else 'healthy', 'unresolved_count': unresolved, 'version': VERSION, 'checked_at': now.isoformat(), 'pending_started_games': len(pending),
              'requested_games': len(tasks), 'recovered_games': cached_recovered + sum(g is not None for _,g,_ in results),
              'cached_recovered_games': cached_recovered,
              'rows': problems, 'attempts': attempts,
              'claim': 'Official result recovery only; original tickets unchanged. Settlement is performed separately against each exact contract.'}
    folder.mkdir(parents=True, exist_ok=True);atomic_json(state_path, report)
    return list(mapping.values()), report
