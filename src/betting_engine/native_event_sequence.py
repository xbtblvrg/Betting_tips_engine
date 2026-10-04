"""Read-only native archive adapters. Experimental targets, never betting approval.

Only load_archive() establishes exact-body provenance. Native validators are
internal and do not accept a caller's completeness flag. No network or writes.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
from pathlib import Path

from .event_sequence import (SCHEMA, RULES, SequenceError, _boundary, _events,
                             _pairs, _periods, _require, _time)

VERSION = "native-event-sequence-v20"
# Observed code/name pairs in the supplied gamecenter bodies. An unfamiliar
# event might be a correction or new scoring type; do not silently ignore it.
NHL_EVENT_CODES = {
    'faceoff': 502, 'hit': 503, 'giveaway': 504, 'goal': 505,
    'shot-on-goal': 506, 'missed-shot': 507, 'blocked-shot': 508,
    'penalty': 509, 'stoppage': 516, 'period-start': 520,
    'period-end': 521, 'shootout-complete': 523, 'game-end': 524,
    'takeaway': 525, 'delayed-penalty': 535,
}
# Native gamecenter ownership: winner, hitter, and SHOOTER respectively.
# A blocked shot belongs to the shooting team, not the blocking team. These
# pairs require distinct opposing participants from this game's own roster.
NHL_OPPOSING_ROLES = {
    'faceoff': ('winningPlayerId', 'losingPlayerId'),
    'hit': ('hittingPlayerId', 'hitteePlayerId'),
    'blocked-shot': ('shootingPlayerId', 'blockingPlayerId'),
}
# Only penalty schemas observed in the supplied native archives. Other codes
# or infractions need source evidence before their participant rules are used.
NHL_PAIRED_PENALTIES = {
    **{('MIN', desc): 2 for desc in (
        'interference', 'unsportsmanlike-conduct', 'cross-checking', 'hooking',
        'tripping', 'interference-goalkeeper', 'holding',
        'roughing-removing-opponents-helmet', 'slashing', 'elbowing',
        'roughing', 'boarding',
    )},
    ('MAJ', 'fighting'): 5,
    ('MAT', 'match-penalty'): 5,
}
NFL_TEAMS = frozenset('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC '
                      'LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split())
# Preserve explicit role teams: a tackler on a return need not be on defteam.
# These names (including forced_fumble_player_N_player_id) are the supplied
# nflverse schema, not a synthesized roster or a name-based identity join.
NFL_EXPLICIT_ROLE_TEAMS = {
    **{f'{role}_{n}': f'{role}_{n}_team'
       for role, count in (('solo_tackle', 2), ('assist_tackle', 4),
                           ('tackle_with_assist', 2), ('forced_fumble_player', 2),
                           ('fumbled', 2)) for n in range(1, count + 1)},
    'penalty': 'penalty_team',
}
NFL_DEFENSIVE_ROLES = (
    'tackle_for_loss_1', 'tackle_for_loss_2', 'qb_hit_1', 'qb_hit_2',
    'pass_defense_1', 'pass_defense_2', 'sack', 'half_sack_1', 'half_sack_2',
    'blocked',
)
NFL_ROLE_TEAMS = {**NFL_EXPLICIT_ROLE_TEAMS,
                  # nflverse records punts under punter, not kicker. Retain
                  # this evidence even on non-scoring plays, so it can reject
                  # a contradictory scorer elsewhere in the same game.
                  'punter': 'posteam',
                  **{role: 'defteam' for role in NFL_DEFENSIVE_ROLES}}
NFL_TEAM_COLUMNS = tuple(dict.fromkeys((
    'posteam', 'defteam', 'td_team', 'return_team',
    'fumble_recovery_1_team', 'fumble_recovery_2_team',
    *NFL_EXPLICIT_ROLE_TEAMS.values(),
)))
NFL_SCORING_FLAGS = (
    'touchdown', 'pass_touchdown', 'rush_touchdown', 'return_touchdown',
    'safety', 'defensive_two_point_conv', 'defensive_extra_point_conv',
    'own_kickoff_recovery_td',
)
NFL_POSSESSION_FLAGS = ('kickoff_attempt', 'punt_attempt', 'interception', 'fumble')
NFL_SCORING_RESULTS = {
    'field_goal_result': ('made', 'missed', 'blocked'),
    'extra_point_result': ('good', 'failed', 'blocked'),
    'two_point_conv_result': ('success', 'failure'),
}
NFL_COLUMNS = list(dict.fromkeys([
    'game_id', 'play_id', 'order_sequence', 'home_team', 'away_team', 'season',
    'season_type', 'qtr', 'time', 'quarter_seconds_remaining', 'desc',
    'play_deleted', 'replay_or_challenge', 'replay_or_challenge_result',
    'home_score', 'away_score', 'total_home_score', 'total_away_score',
    'posteam', 'defteam', 'td_team', 'touchdown', 'field_goal_result',
    'extra_point_result', 'two_point_conv_result', 'safety',
    'defensive_two_point_conv', 'defensive_extra_point_conv', 'time_of_day',
    'game_date', 'start_time',
    'pass_touchdown', 'rush_touchdown', 'return_touchdown', 'return_team',
    'kickoff_attempt', 'punt_attempt', 'interception', 'fumble',
    'own_kickoff_recovery_td',
    'fumble_recovery_1_team', 'fumble_recovery_2_team',
] + [f'{role}_player_{suffix}' for role in (
    'td', 'kicker', 'passer', 'rusher', 'receiver', 'interception', 'punt_returner',
    'kickoff_returner', 'fumble_recovery_1', 'fumble_recovery_2',
    'own_kickoff_recovery', 'safety', 'lateral_receiver', 'lateral_rusher',
    'lateral_interception', 'lateral_punt_returner', 'lateral_kickoff_returner',
) for suffix in ('id', 'name')] + list(NFL_TEAM_COLUMNS) + [
    f'{role}_player_{suffix}' for role in NFL_ROLE_TEAMS for suffix in ('id', 'name')]))


def _json(body):
    return json.loads(body, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(SequenceError('nonfinite_json')))


def _int(value, minimum=0):
    _require(not isinstance(value, bool) and isinstance(value, (int, float)) and
             math.isfinite(value) and int(value) == value and value >= minimum,
             'invalid_native_integer')
    return int(value)


def _clock(value):
    _require(isinstance(value, str) and re.fullmatch(r'\d{2}:\d{2}', value) is not None,
             'invalid_native_clock')
    minutes, seconds = map(int, value.split(':'))
    _require(seconds < 60, 'invalid_native_clock')
    return (minutes * 60 + seconds) * 1000


def _file(root, manifest, relative):
    _require(isinstance(relative, str), 'missing_archive_path')
    path = (root / relative).resolve()
    _require(path.is_relative_to(root) and path != root, 'archive_path_escape')
    entry = manifest['files'].get(relative)
    _require(isinstance(entry, dict), 'unmanifested_archive_file')
    body = path.read_bytes()
    _require(len(body) == entry['bytes'] and hashlib.sha256(body).hexdigest() == entry['sha256'],
             'archive_hash_mismatch')
    return body


class NativeArchive:
    """A verified local observation; use normalize() for a single provider game."""

    def __init__(self, source, provenance, games):
        self._source = source
        self._provenance = provenance
        self._games = games

    @property
    def game_ids(self):
        return tuple(sorted(self._games))

    def normalize(self, game_id: str, *, as_of: str) -> dict:
        result = {'adapter': VERSION, 'game_id': game_id, 'status': 'rejected',
                  'reason': None, 'sequence': None, 'provenance': dict(self._provenance),
                  'production_supported': False, 'current_participation_verified': False,
                  'historical_point_in_time_backtest_ready': False}
        try:
            _require(_time(self._source['available_at']) <= _time(as_of),
                     'evidence_not_available_at_cutoff')
            _require(game_id in self._games, 'game_identity_mismatch')
            league = self._source['league']
            data = (_nhl(self._games[game_id], game_id) if league == 'NHL'
                    else _nfl(self._games[game_id], game_id))
            available = _time(self._source['available_at'])
            if data['start'] is not None:
                _require(_time(data['start']) < available, 'game_after_observation')
            native_times = []
            for identity in data['source_event_identities']:
                wall_time = identity.get('native_time_of_day')
                if wall_time is not None:
                    observed = _time(wall_time)
                    _require(observed <= available, 'event_after_observation')
                    native_times.append(observed)
            # Validate every supplied timestamp in verified source order,
            # including non-scoring plays and terminal markers. Missing times
            # stay unknown and do not reset comparison with earlier evidence.
            _require(all(a <= b for a, b in zip(native_times, native_times[1:])),
                     'nonmonotone_event_time')
            # Preserve real scoring wall times so D09 checks their order against
            # game clocks. These are conservative observation bounds, NOT an
            # assertion of a known game start/completion time.
            wall_times = [_time(e['occurred_at']) for e in data['events']
                          if e['occurred_at'] is not None]
            lower_bound = _time(data['start']) if data['start'] else min(wall_times, default=available)
            periods = _periods(data, league)
            _events(data, periods, league, lower_bound, available)
            # Native source order also disambiguates plays sharing one clock.
            _require(all(a <= b for a, b in zip(wall_times, wall_times[1:])),
                     'nonmonotone_event_time')
            result.update(status='validated', sequence=data,
                          normalized_sha256=hashlib.sha256(json.dumps(
                              data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                          completeness_basis='native_terminal_and_score_reconciliation',
                          source_identity_verified=True)
        except SequenceError as exc:
            result['reason'] = str(exc)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            result['reason'] = 'invalid_native_schema:' + type(exc).__name__
        return result

    def targets(self, game_id: str, *, as_of: str, scope='regulation',
                kind='all_scoring', subject='team') -> dict:
        """D09 scoring targets through native validation, with unknown wall times."""
        result = self.normalize(game_id, as_of=as_of)
        result.update(scope=scope, kind=kind, subject=subject, event_count=None, first=None, last=None)
        if result['status'] != 'validated':
            return result
        try:
            data = result['sequence']
            league = data['league']
            _require(subject in ('team', 'player'), 'unsupported_subject')
            _require(kind == 'all_scoring' or kind in RULES[league][2], 'unsupported_target_kind')
            periods = {p['number']: p for p in data['periods']}
            if scope == 'regulation':
                selected = {n for n, p in periods.items() if p['phase'] == 'REG'}
            elif scope == 'regulation_ot':
                selected = set(periods)
            elif scope == 'overtime':
                selected = {n for n, p in periods.items() if p['phase'] == 'OT'}
            elif isinstance(scope, str) and re.fullmatch(r'period_[1-9]\d*', scope):
                selected = {int(scope[7:])} & periods.keys()
            else:
                raise SequenceError('unsupported_scope')
            _require(bool(selected), 'scope_not_played')
            events = [(e['period'], e['elapsed_ms'], e) for e in data['events']
                      if e['period'] in selected and (kind == 'all_scoring' or e['kind'] == kind)]
            events.sort(key=lambda e: (e[0], e[1], e[2]['order']))
            result.update(status='ready' if events else 'no_event', event_count=len(events))
            if events:
                for edge, row in [('first', events[0]), ('last', events[-1])]:
                    result[edge] = _boundary([e for e in events if e[:2] == row[:2]], subject, edge)
        except (SequenceError, TypeError) as exc:
            result.update(status='rejected', reason=str(exc), sequence=None)
        return result


def load_archive(root: str | Path, source_sha256: str) -> NativeArchive:
    """Verify manifest, collector sidecar and exact response bytes before parsing.

    Hashes provide integrity within this supplied bundle, not independent
    authentication of the collector clock or of historical publication time.
    """
    root = Path(root).resolve()
    # The manifest is source evidence too. Apply the same confinement as the
    # response/sidecar paths before reading it, including symlink resolution.
    manifest_path = (root / 'manifest.json').resolve()
    _require(manifest_path.is_relative_to(root) and manifest_path != root,
             'archive_path_escape')
    manifest_body = manifest_path.read_bytes()
    manifest = _json(manifest_body)
    _require(manifest.get('schema') == 'public-development-sports-evidence-v1', 'unsupported_manifest')
    sources = [s for s in manifest['sources'] if s.get('source_sha256') == source_sha256]
    _require(len(sources) == 1, 'ambiguous_or_missing_source')
    source = sources[0]
    _require(source.get('state') == 'copied' and source.get('kind') == 'native_events' and
             source.get('family') in ('NHL/events', 'NFL/events'), 'unsupported_source_scope')
    league = source['league']
    _require(source['family'] == league + '/events' and
             source['format'] == {'NHL': 'nhl_json', 'NFL': 'nfl_parquet'}.get(league),
             'source_schema_mismatch')
    body = _file(root, manifest, source['blob_path'])
    metadata_body = _file(root, manifest, source['metadata_path'])
    metadata = _json(metadata_body)
    _require(hashlib.sha256(body).hexdigest() == source_sha256 and
             hashlib.sha256(metadata_body).hexdigest() == source['metadata_sha256'], 'source_hash_mismatch')
    _require(metadata['sha256'] == source_sha256 and metadata['bytes'] == len(body) and
             metadata['blob'] == Path(source['blob_path']).name and metadata['http_status'] == 200 and
             metadata['url'] == source['url'] and metadata['fetched_at'] == source['fetched_at'],
             'collector_metadata_mismatch')
    _require(source['availability_basis'] == 'archived_observation' and
             source['available_at'] == source['fetched_at'], 'unverified_availability')
    # These native response schemas and collector sidecars establish only an
    # observation time. A manifest-only publication assertion must not become
    # verified provenance. Supporting publication times requires its own
    # source-bound verifier; neither export time nor a game clock supplies it.
    _require(source.get('source_published_at') is None,
             'unsupported_source_publication_time')
    _time(source['available_at'])
    if league == 'NHL':
        gid = str(source['provider_game_id'])
        _require(source['url'] == f'https://api-web.nhle.com/v1/gamecenter/{gid}/play-by-play',
                 'unsupported_source_url')
        data = _json(body)
        _require(str(data['id']) == gid, 'game_identity_mismatch')
        _require(len(data['plays']) == source['row_count'], 'source_row_count_mismatch')
        games = {'NHL:' + gid: data}
    else:
        season = _int(source['season'])
        _require(source['url'] == 'https://github.com/nflverse/nflverse-data/releases/download/'
                 f'pbp/play_by_play_{season}.parquet', 'unsupported_source_url')
        import pyarrow.parquet as pq
        # Identity columns are mandatory even when only scoring targets are
        # requested. Silently projecting the available subset could hide a
        # contradictory passer on a later non-scoring play.
        schema = pq.ParquetFile(io.BytesIO(body)).schema_arrow
        _require(set(NFL_COLUMNS) <= set(schema.names), 'missing_native_columns')
        table = pq.read_table(io.BytesIO(body), columns=NFL_COLUMNS)
        _require(table.num_rows == source['row_count'], 'source_row_count_mismatch')
        games = {}
        for row in table.to_pylist():
            _require(_int(row['season']) == season, 'season_identity_mismatch')
            games.setdefault('NFL:' + row['game_id'], []).append(row)
    provenance = {k: source.get(k) for k in ('url', 'source_sha256', 'metadata_sha256',
                  'blob_path', 'metadata_path', 'available_at', 'fetched_at', 'source_published_at')}
    provenance.update(manifest_sha256=hashlib.sha256(manifest_body).hexdigest(),
                      availability_basis='archived_observation',
                      archive_timestamp_independently_authenticated=False,
                      correction_identity=source_sha256, raw_bytes=len(body))
    return NativeArchive(source, provenance, games)


def _base(league, gid, teams, final):
    return dict(schema=SCHEMA, league=league, game_id=gid, home_id=teams[0], away_id=teams[1],
                state='final', start=None, completed_at=None, completion_time_basis='unknown',
                corrections_resolved=True, correction_scope='this_snapshot_only',
                periods=[], events=[], final_score=final, shootout_award=None,
                source_event_identities=[], terminal_event_ids=[])


def _event(data, eid, order, period, elapsed, kind, team, player):
    return dict(event_id=data['game_id'] + ':' + str(eid), game_id=data['game_id'],
                status='confirmed', period=period, phase='REG' if period <= RULES[data['league']][0] else 'OT',
                elapsed_ms=elapsed, occurred_at=None, order=order, kind=kind,
                points=RULES[data['league']][2][kind], team_id=team, player_id=player)


def _nhl_penalty_identity(detail, team, roster, positions):
    """Validate native penalized/drawing/serving roles, including observed exceptions."""
    schema = (detail.get('typeCode'), detail.get('descKey'))
    committed = detail.get('committedByPlayerId')
    drawn = detail.get('drawnByPlayerId')
    served = detail.get('servedByPlayerId')
    if schema == ('BEN', 'too-many-men-on-the-ice'):
        # A team bench minor has a serving skater, not an invented offender
        # or victim. This exact exception occurs in game 2022020939, event 477.
        _require('committedByPlayerId' not in detail and 'drawnByPlayerId' not in detail,
                 'unsupported_penalty_participant_schema')
        _require(served in roster, 'missing_native_participant_identity')
        duration = 2
    elif schema == ('MIS', 'misconduct'):
        # The supplied misconduct entries identify only the penalized player.
        _require('drawnByPlayerId' not in detail and 'servedByPlayerId' not in detail,
                 'unsupported_penalty_participant_schema')
        _require(committed in roster, 'missing_native_participant_identity')
        duration = 10
    else:
        _require(schema in NHL_PAIRED_PENALTIES, 'unsupported_penalty_identity_scope')
        duration = NHL_PAIRED_PENALTIES[schema]
        _require(committed in roster and drawn in roster,
                 'missing_native_participant_identity')
        _require(committed != drawn, 'overlapping_native_participant_identity')
        _require(team is not None and roster[drawn] != team,
                 'native_participant_team_mismatch')
    _require(_int(detail.get('duration'), 1) == duration, 'penalty_duration_mismatch')
    _require(team is not None and all(roster[pid] == team for pid in (committed, served)
                                     if pid is not None), 'native_participant_team_mismatch')
    if served is not None:
        _require(served not in (committed, drawn), 'overlapping_native_participant_identity')
        _require(positions[served] != 'G', 'ineligible_penalty_server')


def _nhl(raw, gid):
    _require(gid == 'NHL:' + str(_int(raw['id'], 1)), 'game_identity_mismatch')
    _require(raw['gameState'] in ('OFF', 'FINAL') and raw['gameScheduleState'] == 'OK',
             'game_not_final_or_exceptional')
    _require(raw['gameType'] in (2, 3) and raw['regPeriods'] == 3 and
             raw.get('limitedScoring') is False, 'unsupported_nhl_rules')
    season = _int(raw['season'])
    year, following = divmod(season, 10000)
    _require(2015 <= year <= 2026 and following == year + 1, 'unsupported_nhl_season_rules')
    _require(gid[4:10] == f'{year}{raw["gameType"]:02d}', 'season_identity_mismatch')
    teams = ['NHL:' + str(_int(raw[k]['id'], 1)) for k in ('homeTeam', 'awayTeam')]
    _require(teams[0] != teams[1], 'invalid_teams')
    data = _base('NHL', gid, teams, [_int(raw[k]['score']) for k in ('homeTeam', 'awayTeam')])
    data['start'] = raw['startTimeUTC']
    _time(data['start'])
    roster, positions = {}, {}
    for r in raw['rosterSpots']:
        pid = _int(r['playerId'], 1)
        team = 'NHL:' + str(_int(r['teamId'], 1))
        _require(pid not in roster and team in teams, 'invalid_roster_identity')
        roster[pid] = team
        _require(r.get('positionCode') in ('C', 'L', 'R', 'D', 'G'), 'unknown_player_position')
        positions[pid] = r['positionCode']
    plays = raw['plays']
    _require(bool(plays), 'missing_event_history')
    ids, orders, periods, totals, so_goals = set(), set(), {}, [0, 0], [0, 0]
    last_key = (0, 0)
    attempts, attempt_scores, shootout_done = [], [0, 0], False
    shooters = set()
    for e in plays:
        eid, order = _int(e['eventId'], 1), _int(e['sortOrder'], 1)
        _require(eid not in ids and order not in orders, 'duplicate_native_event')
        _require(not orders or order > max(orders), 'nonmonotone_native_order')
        ids.add(eid); orders.add(order)
        pd = e['periodDescriptor']; n = _int(pd['number'], 1); phase = pd['periodType']
        expected = 'REG' if n <= 3 else ('SO' if n == 5 and raw['gameType'] == 2 else 'OT')
        _require(phase == expected and pd['maxRegulationPeriods'] == 3, 'period_phase_mismatch')
        length = 1200000 if phase == 'REG' or raw['gameType'] == 3 else (300000 if phase == 'OT' else 0)
        elapsed = _clock(e['timeInPeriod'])
        _require(elapsed + _clock(e['timeRemaining']) == length and
                 (n, elapsed) >= last_key, 'invalid_native_clock')
        last_key = (n, elapsed)
        # A later start marker at the same clock cannot attest that earlier
        # plays belong to a complete period. Verify native marker order too.
        if n not in periods:
            _require(e['typeDescKey'] == 'period-start', 'incomplete_period_history')
            if phase in ('OT', 'SO'):
                _require(totals[0] == totals[1], 'untied_nhl_extra_period')
        p = periods.setdefault(n, dict(number=n, phase=phase, duration_ms=length,
                                       complete=False, score=[0, 0], starts=[], ends=[]))
        kind = e['typeDescKey']; detail = e.get('details', {})
        _require(phase != 'SO' or kind in ('period-start', 'period-end', 'game-end',
                 'shootout-complete', 'goal', 'shot-on-goal', 'missed-shot'),
                 'unsupported_shootout_event')
        if phase == 'OT' and sum(p['score']):
            _require(kind in ('period-end', 'game-end') and
                     elapsed == data['events'][-1]['elapsed_ms'],
                     'event_after_overtime_winner')
        _require(kind in NHL_EVENT_CODES, 'unsupported_nhl_event_type')
        _require(_int(e['typeCode']) == NHL_EVENT_CODES[kind], 'native_event_type_mismatch')
        _require(not p['complete'] or kind == 'game-end', 'event_after_period_terminal')
        for k, value in detail.items():
            if k.endswith('PlayerId') or k in ('playerId', 'goalieInNetId'):
                _require(_int(value, 1) in roster, 'unknown_player_identity')
        owner = detail.get('eventOwnerTeamId')
        team = None if owner is None else 'NHL:' + str(_int(owner, 1))
        _require(team is None or team in teams, 'event_team_mismatch')
        if kind in ('giveaway', 'takeaway'):
            participant = detail.get('playerId')
            _require(participant in roster, 'missing_native_participant_identity')
            _require(team is not None and roster[participant] == team,
                     'native_participant_team_mismatch')
        if kind == 'penalty':
            _nhl_penalty_identity(detail, team, roster, positions)
        if kind in NHL_OPPOSING_ROLES:
            owner_role, opposing_role = NHL_OPPOSING_ROLES[kind]
            participant, opponent = detail.get(owner_role), detail.get(opposing_role)
            _require(participant in roster and opponent in roster,
                     'missing_native_participant_identity')
            _require(participant != opponent, 'overlapping_native_participant_identity')
            _require(team is not None and roster[participant] == team and
                     roster[opponent] != team, 'native_participant_team_mismatch')
        if kind in ('goal', 'shot-on-goal', 'missed-shot'):
            shooter = detail.get('scoringPlayerId') if kind == 'goal' else detail.get('shootingPlayerId')
            _require(shooter in roster and roster[shooter] == team, 'scorer_team_mismatch')
            goalie = detail.get('goalieInNetId')
            # Empty-net shots may omit a goalie; a shootout attempt may not.
            _require(phase != 'SO' or goalie is not None, 'missing_shootout_goalie')
            if goalie is not None:
                _require(positions[goalie] == 'G', 'goalie_position_mismatch')
                _require(roster[goalie] != team, 'goalie_team_mismatch')
        if phase == 'SO' and kind in ('goal', 'shot-on-goal', 'missed-shot'):
            _require(not shootout_done and team in teams and
                     (not attempts or attempts[-1] != team), 'invalid_shootout_attempt_order')
            shooter = detail.get('scoringPlayerId') if kind == 'goal' else detail.get('shootingPlayerId')
            _require(shooter in roster and roster[shooter] == team, 'scorer_team_mismatch')
            _require(positions[shooter] != 'G', 'ineligible_shootout_shooter')
            # A repeat requires all eligible skaters to have attempted. The
            # roster alone cannot establish injury/penalty eligibility, so even
            # a late repeat is unsupported without that additional evidence.
            _require(shooter not in shooters, 'unsupported_repeated_shootout_shooter')
            shooters.add(shooter)
            attempts.append(team)
            attempt_scores[teams.index(team)] += int(kind == 'goal')
            counts = [attempts.count(t) for t in teams]
            shootout_done = (any(attempt_scores[i] > attempt_scores[1-i] + max(0, 3-counts[1-i])
                                for i in (0, 1)) if min(counts) < 3 else
                             counts[0] == counts[1] and attempt_scores[0] != attempt_scores[1])
        if kind == 'shootout-complete':
            _require(phase == 'SO' and shootout_done, 'incomplete_shootout')
        if kind == 'period-start':
            _require(elapsed == 0, 'invalid_period_start')
            p['starts'].append(eid)
        if kind == 'period-end':
            _require(elapsed == length or (phase == 'OT' and sum(p['score']) == 1), 'incomplete_period_history')
            p['ends'].append(eid); p['complete'] = True
        if kind == 'game-end':
            data['terminal_event_ids'].append(eid)
            _require(e is plays[-1] and pd == raw['periodDescriptor'], 'invalid_terminal_event')
        if kind == 'goal':
            player = _int(detail['scoringPlayerId'], 1)
            _require(team in teams and roster[player] == team, 'scorer_team_mismatch')
            _require('goalieInNetId' not in detail or roster[detail['goalieInNetId']] != team,
                     'goalie_team_mismatch')
            for k in ('assist1PlayerId', 'assist2PlayerId'):
                _require(k not in detail or roster[detail[k]] == team, 'assist_team_mismatch')
            credited = [player] + [detail[k] for k in ('assist1PlayerId', 'assist2PlayerId')
                                   if k in detail]
            _require(len(credited) == len(set(credited)), 'overlapping_goal_credit_identity')
            _require('assist2PlayerId' not in detail or 'assist1PlayerId' in detail,
                     'incomplete_assist_identity')
            if phase == 'SO':
                _require(len(credited) == 1, 'shootout_assist_not_allowed')
                so_goals[teams.index(team)] += 1
            else:
                totals[teams.index(team)] += 1
                p['score'][teams.index(team)] += 1
                data['events'].append(_event(data, eid, order, n, elapsed, 'goal', team, 'NHL:' + str(player)))
            _require(totals == [_int(detail['homeScore']), _int(detail['awayScore'])],
                     'native_score_reconciliation_failed')
        data['source_event_identities'].append(dict(event_id=eid, order=order, period=n, kind=kind))
    _require(len(data['terminal_event_ids']) == 1, 'missing_terminal_event')
    _require(set(periods) == set(range(1, max(periods) + 1)), 'incomplete_period_history')
    _require(raw['gameOutcome']['lastPeriodType'] == raw['periodDescriptor']['periodType'], 'terminal_phase_mismatch')
    for p in periods.values():
        _require(len(p['starts']) == len(p['ends']) == 1, 'incomplete_period_history')
    if raw['periodDescriptor']['periodType'] == 'SO':
        _require(raw['gameType'] == 2 and max(periods) == 5 and totals[0] == totals[1] and
                 sum(e['typeDescKey'] == 'shootout-complete' for e in plays) == 1 and
                 so_goals[0] != so_goals[1], 'incomplete_shootout')
        winner = int(so_goals[1] > so_goals[0])
        data['shootout_award'] = teams[winner]
        totals[winner] += 1
    else:
        _require(not any(so_goals) and totals[0] != totals[1], 'incomplete_nhl_final')
    _require(totals == data['final_score'], 'final_score_mismatch')
    data['periods'] = [{k: p[k] for k in ('number', 'phase', 'duration_ms', 'complete', 'score')}
                       for p in periods.values() if p['phase'] != 'SO']
    data['event_count'] = len(data['events'])
    return data


def _touchdown_role(row):
    """Corroborate TD identity with the scoring role AND that role's team.

    A passer, tackler or opposing kicker on the same play is not a scorer.
    Native role/team fields describe this historical play, never a current
    roster. Blocked-kick recoveries lacking structured attribution stay missing.
    """
    pid = row['td_player_id']
    candidates = []

    def add(role, team):
        if row.get(role + '_player_id') == pid and pid is not None:
            candidates.append((role, team))

    def recipient(role, team):
        # A known lateral recipient supersedes the initial ball carrier.
        lateral = 'lateral_' + role
        add(lateral if row.get(lateral + '_player_id') is not None else role, team)

    if row['pass_touchdown'] == 1:
        recipient('receiver', row['posteam'])
    if row['rush_touchdown'] == 1:
        recipient('rusher', row['posteam'])
    if row['return_touchdown'] == 1:
        if row['interception'] == 1:
            recipient('interception', row['defteam'])
        if row['kickoff_attempt'] == 1:
            recipient('kickoff_returner', row['return_team'])
        if row['punt_attempt'] == 1:
            recipient('punt_returner', row['return_team'])
    if row['fumble'] == 1:
        # The later recovery is the final possession, when present.
        role = ('fumble_recovery_2' if row['fumble_recovery_2_player_id'] is not None
                else 'fumble_recovery_1')
        add(role, row[role + '_team'])
    if row['kickoff_attempt'] == 1 and row['own_kickoff_recovery_td'] == 1:
        # The native TD-specific flag and recovery identity establish a kicking
        # team recovery; nflverse posteam still designates the receiving team.
        add('own_kickoff_recovery', row['defteam'])
    _require(bool(candidates), 'unverified_touchdown_player')
    _require(all(team == row['td_team'] and team is not None for _, team in candidates),
             'touchdown_player_team_mismatch')
    _require(all(row[role + '_player_name'] == row['td_player_name'] for role, _ in candidates),
             'touchdown_player_name_mismatch')
    # nflverse kickoff possession is the receiving team; the kicker is on the
    # other team. Also reject copying that kicker into a returner role.
    if row['kickoff_attempt'] == 1 and any('kickoff_returner' in role for role, _ in candidates):
        _require(row['return_team'] == row['posteam'] and pid != row['kicker_player_id'],
                 'touchdown_player_team_mismatch')
    if row['punt_attempt'] == 1 and any('punt_returner' in role for role, _ in candidates):
        _require(row['return_team'] == row['defteam'], 'touchdown_player_team_mismatch')
    return pid, [role for role, _ in candidates]


def _nfl(rows, gid):
    first = rows[0]
    home, away = first['home_team'], first['away_team']
    _require(isinstance(home, str) and isinstance(away, str) and home != away and
             re.fullmatch(r'\d{4}_\d{2}_' + re.escape(away) + '_' + re.escape(home), gid[4:]) is not None,
             'game_team_identity_mismatch')
    _require(gid[4:8] == str(_int(first['season'])), 'season_identity_mismatch')
    _require(first['season'] in (2024, 2025, 2026), 'unsupported_nfl_season_rules')
    _require(home in NFL_TEAMS and away in NFL_TEAMS, 'unsupported_nfl_team_identity')
    teams = ['NFL:' + home, 'NFL:' + away]
    final = [_int(first['home_score']), _int(first['away_score'])]
    data = _base('NFL', gid, teams, final)
    data['native_start_time'] = first['start_time']  # Unzoned: not converted to UTC.
    _require(first['desc'] == 'GAME' and first['qtr'] == 1 and
             first['quarter_seconds_remaining'] == 900, 'missing_game_start')
    _require(rows[-1]['desc'] == 'END GAME', 'missing_terminal_event')
    # END GAME is a game terminal, never a substitute for an earlier quarter
    # boundary. Check the whole native sequence before extracting any targets.
    _require(sum(r['desc'] == 'END GAME' for r in rows) == 1,
             'duplicate_game_terminal')
    _require(rows[-1]['qtr'] >= 4, 'premature_game_terminal')
    _require(first['season_type'] in ('REG', 'POST'), 'unsupported_nfl_rules')
    ids, orders, periods, totals = set(), set(), {}, [0, 0]
    last_key = (0, 0)
    for r in rows:
        _require(r['game_id'] == gid[4:] and r['home_team'] == home and r['away_team'] == away and
                 r['season'] == first['season'] and
                 r['season_type'] == first['season_type'] and
                 [_int(r['home_score']), _int(r['away_score'])] == final, 'game_team_identity_mismatch')
        eid, order = _int(r['play_id'], 1), _int(r['order_sequence'], 1)
        _require(eid not in ids and order not in orders, 'duplicate_native_event')
        _require(not orders or order > max(orders), 'nonmonotone_native_order')
        ids.add(eid); orders.add(order)
        # Parquet supplies numeric correction indicators. Python's bool/int
        # equality must not turn a malformed correction field into approval.
        for field in ('play_deleted', 'replay_or_challenge'):
            _require(type(r[field]) in (int, float) and r[field] in (0, 1),
                     'invalid_native_correction_indicator')
        _require(r['play_deleted'] == 0, 'deleted_event_requires_review')
        correction = r['replay_or_challenge_result']
        _require((r['replay_or_challenge'] == 0 and correction is None) or
                 (r['replay_or_challenge'] == 1 and correction in ('upheld', 'reversed')),
                 'unresolved_event_correction')
        # Unknown scoring/correction values must not silently fall through the
        # positive checks below and masquerade as scoreless native events.
        # Nulls occur in the actual archive (e.g. GAME/quarter markers); retain
        # them as missing, with running-score reconciliation still mandatory.
        for field in NFL_SCORING_FLAGS:
            value = r[field]
            _require(value is None or (type(value) in (int, float) and value in (0, 1)),
                     'invalid_native_scoring_indicator')
        # These flags select scorer/role teams, even on non-scoring plays.
        # Unknown values must not silently bypass that identity evidence.
        # Preserve native nulls; they are not observed zeroes.
        for field in NFL_POSSESSION_FLAGS:
            value = r[field]
            _require(value is None or (type(value) in (int, float) and value in (0, 1)),
                     'invalid_native_possession_indicator')
        for field, allowed in NFL_SCORING_RESULTS.items():
            _require(r[field] is None or r[field] in allowed,
                     'unsupported_native_scoring_result')
        n = _int(r['qtr'], 1)
        _require(n <= 5 or first['season_type'] == 'POST', 'unsupported_nfl_overtime')
        length = 900000 if n <= 4 or first['season_type'] == 'POST' else 600000
        remaining = _int(r['quarter_seconds_remaining']) * 1000
        _require(_clock(r['time']) == remaining and remaining <= length, 'invalid_native_clock')
        elapsed = length - remaining
        _require((n, elapsed) >= last_key, 'nonmonotone_native_clock')
        last_key = (n, elapsed)
        p = periods.setdefault(n, dict(number=n, phase='REG' if n <= 4 else 'OT',
                                       duration_ms=length, complete=False, score=[0, 0]))
        _require(not p['complete'], 'event_after_period_terminal')
        for field in NFL_TEAM_COLUMNS:
            _require(r[field] is None or r[field] in (home, away), 'event_team_mismatch')
        _require(r['posteam'] is None or r['defteam'] is None or r['posteam'] != r['defteam'],
                 'event_team_mismatch')
        for field in NFL_COLUMNS:
            if field.endswith('_player_id') and r[field] is not None:
                _require(isinstance(r[field], str) and re.fullmatch(r'00-\d{7}', r[field]) is not None and
                         isinstance(r[field[:-2] + 'name'], str) and bool(r[field[:-2] + 'name']),
                         'invalid_player_identity')
        scoring = []
        scoring_roles = []
        if r['touchdown'] == 1:
            pid, scoring_roles = _touchdown_role(r)
            scoring.append(('touchdown', r['td_team'], pid))
        for field, kind in [('field_goal_result', 'field_goal'), ('extra_point_result', 'extra_point')]:
            if r[field] in ('made', 'good'):
                _require(r['kicker_player_id'] is not None, 'missing_kicker_identity')
                scoring.append((kind, r['posteam'], r['kicker_player_id']))
        if r['two_point_conv_result'] == 'success':
            # NFL touchdown IDs do not identify conversion scorers. Do not guess.
            scoring.append(('two_point', r['posteam'], None))
        if r['safety'] == 1:
            scoring.append(('safety', r['defteam'], r['safety_player_id']))
        if r['defensive_two_point_conv'] == 1 or r['defensive_extra_point_conv'] == 1:
            scoring.append(('defensive_conversion', r['defteam'], None))
        _require(len(scoring) <= 1, 'ambiguous_native_scoring_event')
        for kind, team, player in scoring:
            _require(team in (home, away), 'event_team_mismatch')
            idx = (home, away).index(team); points = RULES['NFL'][2][kind]
            totals[idx] += points; p['score'][idx] += points
            data['events'].append(_event(data, eid, order, n, elapsed, kind,
                                         'NFL:' + team, None if player is None else 'NFL:' + player))
            data['events'][-1]['occurred_at'] = r['time_of_day']
        _require(totals == [_int(r['total_home_score']), _int(r['total_away_score'])],
                 'native_score_reconciliation_failed')
        description = r['desc'] or ''
        if description == f'END QUARTER {n}' or description == 'END GAME':
            _require(not p['complete'], 'duplicate_period_terminal')
            _require(remaining == 0 or (n > 4 and description == 'END GAME'), 'exceptional_early_finish')
            p['complete'] = True
            data['terminal_event_ids'].append(eid)
        data['source_event_identities'].append(dict(event_id=eid, order=order, period=n,
            terminal_kind=('game_end' if description == 'END GAME' else
                           'period_end' if description == f'END QUARTER {n}' else None),
            replay_or_challenge_result=correction, native_time_of_day=r['time_of_day'],
            touchdown_attribution_roles=scoring_roles))
    _require(totals == final, 'final_score_mismatch')
    data['periods'] = list(periods.values()); data['event_count'] = len(data['events'])
    _nfl_terminal(data, rows)
    # A scoreless/reversed play cannot retain a positive TD subtype.
    # Reconciled totals alone would ignore that conflicting native credit.
    # Run after terminal checks to preserve the earlier exceptional-game reason.
    # Do not require the converse: recovery TDs can lack these subtypes,
    # and their scorer still needs the structured recovery-role check.
    for row in rows:
        _require(row['touchdown'] == 1 or not any(row[field] == 1 for field in (
            'pass_touchdown', 'rush_touchdown', 'return_touchdown',
            'own_kickoff_recovery_td')), 'contradictory_native_touchdown_markers')
    _nfl_player_teams(data, rows)
    return data


def _nfl_player_teams(data, rows):
    """Reject contradictory in-game team evidence across native player roles.

    This is historical attribution, not a roster/current-participation claim.
    Absent players supply no evidence; supplied defensive/explicit identities
    must have their native team. The index spans the entire
    game, so a later role can contradict an earlier scoring attribution.
    Kickoff possession denotes the receiving team in these nflverse archives.
    """
    teams_by_player = {}

    def record(player, team):
        if player is not None and team is not None:
            teams_by_player.setdefault(player, set()).add(team)

    for row, identity in zip(rows, data['source_event_identities']):
        roles = {
            # Passing supplies team evidence even without a score or reception.
            # It does not identify the touchdown scorer (the receiver does).
            'passer': row['posteam'], 'rusher': row['posteam'], 'receiver': row['posteam'],
            'lateral_rusher': row['posteam'], 'lateral_receiver': row['posteam'],
            'interception': row['defteam'], 'lateral_interception': row['defteam'],
            'punt_returner': row['return_team'], 'kickoff_returner': row['return_team'],
            'lateral_punt_returner': row['return_team'],
            'lateral_kickoff_returner': row['return_team'],
            'fumble_recovery_1': row['fumble_recovery_1_team'],
            'fumble_recovery_2': row['fumble_recovery_2_team'],
        }
        if row['kickoff_attempt'] == 1:
            roles['kicker'] = row['defteam']
            roles['own_kickoff_recovery'] = row['defteam']
        elif row['field_goal_result'] is not None or row['extra_point_result'] is not None:
            roles['kicker'] = row['posteam']
        if row['touchdown'] == 1:
            roles['td'] = row['td_team']
        if row['safety'] == 1:
            roles['safety'] = row['defteam']
        evidence = []
        for role, team_field in NFL_ROLE_TEAMS.items():
            player = row[role + '_player_id']
            if player is not None:
                team = row[team_field]
                _require(team is not None, 'missing_player_team_identity')
                roles[role] = team
                evidence.append(dict(role=role, player_id=player,
                                     player_name=row[role + '_player_name'],
                                     team_field=team_field, team=team))
        identity['native_role_team_evidence'] = evidence
        for role, team in roles.items():
            # Every supplied role requires its native team, including return
            # and recovery roles. Missing attribution cannot erase a possible
            # opposing-player contradiction elsewhere in this same game.
            _require(row[role + '_player_id'] is None or team is not None,
                     'missing_player_team_identity')
            record(row[role + '_player_id'], team)
    for event in data['events']:
        if event['player_id'] is not None:
            record(event['player_id'][4:], event['team_id'][4:])
    _require(all(len(teams) == 1 for teams in teams_by_player.values()),
             'conflicting_player_team_identity')


def _nfl_terminal(data, rows):
    """Conservative, versioned subset of NFL terminal rules.

    2024 regular-season OT ends immediately on a touchdown (no try). A field
    goal needs possession evidence; 2025+ and postseason guarantee different
    opportunities. Those cases are explicitly unsupported here, never approved
    from an END GAME string or final margin alone.
    """
    if len(data['periods']) == 4:
        _require(data['final_score'][0] != data['final_score'][1], 'tied_regulation_without_overtime')
        return
    _require(rows[0]['season'] == 2024 and rows[0]['season_type'] == 'REG',
             'unsupported_nfl_overtime_rules')
    ot = [e for e in data['events'] if e['period'] > 4]
    terminal = rows[-1]
    if not ot:
        _require(terminal['quarter_seconds_remaining'] == 0 and
                 data['final_score'][0] == data['final_score'][1], 'incomplete_nfl_overtime')
        return
    # Only a single decisive touchdown from a tied score is accepted. More
    # complex OT (including FG possession sequences) needs a separate verifier.
    _require(len(ot) == 1 and ot[0]['kind'] == 'touchdown',
             'unsupported_nfl_overtime_scoring')
    winner = (data['home_id'], data['away_id']).index(ot[0]['team_id'])
    _require(data['final_score'][winner] - data['final_score'][1-winner] == 6,
             'invalid_nfl_overtime_outcome')
    # nflverse's play clock is the START of the scoring play; END GAME can
    # have less time remaining (LA/DET: TD at 05:24, terminal at 05:19).
    _require(ot[0]['elapsed_ms'] <= 600000 - terminal['quarter_seconds_remaining'] * 1000,
             'event_after_overtime_winner')
    after = [r for r in rows if r['order_sequence'] > ot[0]['order']]
    _require(len(after) == 1 and after[0]['desc'] == 'END GAME',
             'event_after_overtime_winner')
