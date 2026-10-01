"""Offline NHL player SOG reconciliation, never a sportsbook settlement adapter.

Native play-by-play counts are checked against same-response team totals and,
optionally, separately archived player box scores. Neither comparison proves
source authenticity/completeness, current participation or Stake metric rules.
No I/O, fitting, odds, wagers, source-time inference or production caller.
"""
from collections import Counter
from datetime import datetime
import hashlib
import re

from .player_archive import PlayerArchive, decode_nhl_archive
from .player_history import _time
from .player_prop_contracts import canonical_hash
from .player_prop_probability import _json

VERSION = "f0025-nhl-player-shot-reconciliation-v1"
SCOPE = "full_game_including_overtime_excluding_shootout"
_CODES = {
    "faceoff": 502, "hit": 503, "giveaway": 504, "goal": 505,
    "shot-on-goal": 506, "missed-shot": 507, "blocked-shot": 508,
    "penalty": 509, "stoppage": 516, "period-start": 520,
    "period-end": 521, "shootout-complete": 523, "game-end": 524,
    "takeaway": 525, "delayed-penalty": 535,
}
_BLOCKERS = ["source_adapter_not_live_verified", "archive_clock_not_authenticated",
             "sequence_completeness_not_independently_verified",
             "current_participation_not_verified", "stake_identity_and_metric_not_verified",
             "sportsbook_dnp_void_push_rules_not_verified", "production_integration_not_reviewed"]


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _document(archive, endpoint, now):
    _require(isinstance(archive, PlayerArchive), "missing_archive_document")
    _require(type(archive.body) is bytes and 0 < len(archive.body) <= 8 * 1024 * 1024,
             "invalid_body_size")
    _require(hashlib.sha256(archive.body).hexdigest() == archive.source_sha256,
             "source_hash_mismatch")
    _require(archive.evidence_kind in ("fixture", "unverified", "archived_observation"),
             "invalid_evidence_kind")
    _require(isinstance(archive.archive_reference, str) and bool(archive.archive_reference.strip()),
             "missing_archive_reference")
    observed = _time(archive.observed_at)
    _require(observed <= now, "not_observed_as_of")
    payload = _json(archive.body)
    _require(isinstance(payload, dict), "invalid_source_schema")
    event = payload.get("id")
    _require(_integer(event, 1), "invalid_game_id")
    _require(archive.source_url == f"https://api-web.nhle.com/v1/gamecenter/{event}/{endpoint}",
             "source_event_url_mismatch")
    start = _time(payload.get("startTimeUTC"))
    _require(start < observed, "observation_not_after_start")
    _require(payload.get("gameState") in ("OFF", "FINAL"), "game_not_final")
    source = dict(sha256=archive.source_sha256, url=archive.source_url,
                  observed_at=observed.isoformat(), available_at=observed.isoformat(),
                  source_published_at=None, availability_basis="archived_observation",
                  archive_reference=archive.archive_reference, evidence_kind=archive.evidence_kind)
    return payload, source, start


def _phase(descriptor, game_type):
    _require(isinstance(descriptor, dict), "missing_period_descriptor")
    number, phase = descriptor.get("number"), descriptor.get("periodType")
    _require(_integer(number, 1) and type(descriptor.get("maxRegulationPeriods")) is int
             and descriptor["maxRegulationPeriods"] == 3,
             "invalid_period_descriptor")
    _require((number <= 3 and phase == "REG") or
             (number > 3 and phase == "OT" and (game_type == 3 or number == 4)) or
             (number == 5 and phase == "SO" and game_type == 2), "invalid_period_scope")
    return number, phase


def _clock(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9]{2}:[0-5][0-9]", value),
             "invalid_event_clock")
    minute, second = map(int, value.split(":"))
    return minute * 60 + second


def _sequence(payload):
    game_type = payload.get("gameType")
    _require(type(game_type) is int and game_type in (2, 3), "unsupported_game_type")
    _require(payload.get("limitedScoring") is False, "limited_scoring_or_unknown_coverage")
    final_period = _phase(payload.get("periodDescriptor"), game_type)
    _require(final_period[0] >= 3 and payload.get("gameOutcome", {}).get("lastPeriodType") == final_period[1],
             "final_period_mismatch")
    teams = {}
    for side in ("homeTeam", "awayTeam"):
        team = payload.get(side)
        _require(isinstance(team, dict), "missing_team")
        tid = team.get("id")
        _require(_integer(tid, 1) and tid not in teams, "invalid_team_identity")
        _require(isinstance(team.get("abbrev"), str) and re.fullmatch(r"[A-Z]{2,3}", team["abbrev"]),
                 "invalid_team_abbreviation")
        _require(_integer(team.get("sog")) and _integer(team.get("score")), "missing_team_totals")
        teams[tid] = team
    _require(len({t["abbrev"] for t in teams.values()}) == 2, "ambiguous_team_abbreviation")
    roster = payload.get("rosterSpots")
    _require(isinstance(roster, list) and 0 < len(roster) <= 100, "missing_or_oversized_roster")
    players = {}
    for row in roster:
        _require(isinstance(row, dict), "invalid_roster_row")
        pid, tid = row.get("playerId"), row.get("teamId")
        _require(_integer(pid, 1) and pid not in players and _integer(tid, 1) and tid in teams,
                 "ambiguous_player_team_identity")
        _require(row.get("positionCode") in ("C", "L", "R", "D", "G"), "unknown_player_position")
        players[pid] = dict(player_id=str(pid), team_id=teams[tid]["abbrev"],
                            provider_team_id=str(tid), position=row["positionCode"],
                            observed_shots_on_goal=0, event_ids=[],
                            historical_played=None, boxscore_shots_on_goal=None,
                            reconciled_value=None)
    plays = payload.get("plays")
    _require(isinstance(plays, list) and 0 < len(plays) <= 10000, "missing_or_oversized_plays")
    ids, orders, periods = set(), set(), set()
    starts, ends, goals, shots = Counter(), Counter(), Counter(), Counter()
    excluded, game_ends, ordered = [], [], []
    for play in plays:
        _require(isinstance(play, dict), "invalid_play")
        eid, order, kind = play.get("eventId"), play.get("sortOrder"), play.get("typeDescKey")
        _require(_integer(eid, 1) and eid not in ids, "duplicate_or_invalid_event_id")
        _require(_integer(order) and order not in orders, "duplicate_or_invalid_event_order")
        ids.add(eid)
        orders.add(order)
        _require(isinstance(kind, str) and kind in _CODES and type(play.get("typeCode")) is int
                 and play["typeCode"] == _CODES[kind], "unknown_or_conflicting_event_type")
        number, phase = _phase(play.get("periodDescriptor"), game_type)
        _require(number <= final_period[0], "event_after_final_period")
        periods.add(number)
        elapsed, remaining = _clock(play.get("timeInPeriod")), _clock(play.get("timeRemaining"))
        duration = 0 if phase == "SO" else 300 if phase == "OT" and game_type == 2 else 1200
        _require(elapsed + remaining == duration, "event_clock_duration_mismatch")
        ordered.append((order, number, elapsed, kind))
        if kind == "period-start":
            _require(elapsed == 0, "invalid_period_start")
            starts[number] += 1
        elif kind == "period-end":
            _require(phase != "REG" or remaining == 0, "incomplete_regulation_period")
            ends[number] += 1
        elif kind == "game-end":
            _require((number, phase) == final_period, "game_end_period_mismatch")
            game_ends.append(order)
        if phase == "SO":
            excluded.append(eid)
            continue
        if kind not in ("goal", "shot-on-goal"):
            continue
        details = play.get("details")
        _require(isinstance(details, dict), "missing_shot_details")
        pid = details.get("scoringPlayerId" if kind == "goal" else "shootingPlayerId")
        tid = details.get("eventOwnerTeamId")
        _require(_integer(pid, 1) and pid in players and _integer(tid, 1) and tid in teams,
                 "missing_shooter_or_team")
        _require(players[pid]["provider_team_id"] == str(tid), "shooter_team_mismatch")
        players[pid]["observed_shots_on_goal"] += 1
        players[pid]["event_ids"].append(eid)
        players[pid]["historical_played"] = True
        shots[tid] += 1
        goals[tid] += int(kind == "goal")
    _require(len(periods) == final_period[0] and periods == set(range(1, len(periods) + 1)) and
             all(starts[p] == ends[p] == 1 for p in periods), "incomplete_period_markers")
    _require(len(game_ends) == 1 and game_ends[0] == max(orders), "missing_or_nonterminal_game_end")
    ordered.sort()
    _require(all(a[1:3] <= b[1:3] for a, b in zip(ordered, ordered[1:])), "nonmonotone_event_clock")
    for period in periods:
        group = [p for p in ordered if p[1] == period]
        _require(group[0][3] == "period-start" and group[-1][3] in ("period-end", "game-end"),
                 "events_outside_period_markers")
        _require(all(p[3] == "game-end" for p in group[next(i for i, p in enumerate(group) if p[3] == "period-end") + 1:]),
                 "events_after_period_end")
    _require(all(shots[t] == teams[t]["sog"] for t in teams), "team_shots_mismatch")
    if final_period[1] == "SO":
        _require(len(set(goals[t] for t in teams)) == 1 and
                 sorted(teams[t]["score"] - goals[t] for t in teams) == [0, 1],
                 "shootout_score_mismatch")
    else:
        _require(all(goals[t] == teams[t]["score"] for t in teams), "team_goals_mismatch")
    for row in players.values():
        row["event_ids"].sort()
    return players, teams, excluded


def reconcile_nhl_player_shots(play_by_play: PlayerArchive, *, as_of: str | datetime,
                               boxscore: PlayerArchive | None = None) -> dict:
    """Reconcile native final-result archives without creating settlement claims.

    A missing boxscore leaves counted observations but no reconciled_value.
    Roster membership/zero shots never imply played=False or a DNP void. Positive
    boxscore TOI is required even for a zero-valued reconciled observation. A
    mismatch anywhere rejects every reconciled_value; partial successes cannot
    hide an attribution conflict. Each invocation preserves its source version;
    revisions are not silently selected or merged. Caller retains old reports.
    """
    result = dict(version=VERSION, state="rejected", league="NHL", game_id=None,
                  identity_namespace="nhl", metric="shots_on_goal", stat_scope=SCOPE,
                  assessed_as_of=None, sources=[], players=[], reasons=[],
                  excluded_shootout_event_ids=[], activation_blockers=list(_BLOCKERS),
                  production_supported=False, settlement_eligible=False, publishable=False,
                  current_participation_verified=False, source_published_at=None)
    try:
        now = _time(as_of)
        result["assessed_as_of"] = now.isoformat()
        payload, source, start = _document(play_by_play, "play-by-play", now)
        result["sources"].append(source)
        result["game_id"] = "NHL:" + str(payload["id"])
        result["start"] = start.isoformat()
        players, teams, excluded = _sequence(payload)
        result["players"] = [players[p] for p in sorted(players)]
        result["excluded_shootout_event_ids"] = sorted(excluded)
        result["team_shots"] = {t["abbrev"]: t["sog"] for t in teams.values()}
        if boxscore is None:
            result["state"] = "team_totals_reconciled"
            result["reasons"].append("missing_independent_player_boxscore")
        else:
            box, box_source, box_start = _document(boxscore, "boxscore", now)
            result["sources"].append(box_source)
            _require(box["id"] == payload["id"] and box_start == start, "boxscore_event_mismatch")
            _require(type(box.get("gameType")) is int and box["gameType"] == payload["gameType"]
                     and _phase(box.get("periodDescriptor"), box["gameType"]) ==
                     _phase(payload["periodDescriptor"], payload["gameType"]), "boxscore_scope_mismatch")
            for side in ("homeTeam", "awayTeam"):
                _require(isinstance(box.get(side), dict) and all(
                    type(box[side].get(k)) is type(payload[side][k]) and box[side][k] == payload[side][k]
                    for k in ("id", "abbrev", "sog", "score")), "boxscore_team_or_totals_mismatch")
            decoded = decode_nhl_archive(boxscore)
            box_players = {row["player_id"]: row for row in decoded}
            # Goalies need not have an SOG field. Every skater must be present.
            _require(set(box_players) == {str(p) for p in players}, "boxscore_roster_mismatch")
            for pid, player in players.items():
                row = box_players[str(pid)]
                _require(row["team_id"] == player["team_id"], "boxscore_player_team_mismatch")
                stats = _json(row["stats"])
                value = stats.get("shots_on_goal")
                player["boxscore_shots_on_goal"] = value
                if player["position"] == "G" and value is None:
                    _require(player["observed_shots_on_goal"] == 0, "goalie_shots_require_review")
                    continue
                _require(type(value) in (int, float) and value == int(value) and value >= 0,
                         "missing_boxscore_player_shots")
                _require(value == player["observed_shots_on_goal"], "player_shots_mismatch")
                _require(not value or row["played"] is True, "positive_shots_without_boxscore_participation")
                if row["played"] is True:
                    player["historical_played"] = True
                    player["reconciled_value"] = int(value)
            result["state"] = "player_boxscore_reconciled"
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        result["reasons"].append(str(exc))
        for player in result["players"]:
            player["reconciled_value"] = None
    result["assessment_sha256"] = canonical_hash(result)
    return result
