"""Experimental independent scoring-event targets; no pricing or production I/O.

The input is our normalized archive contract, NOT a purported vendor response.
Completeness attestations must be established by a source-specific verifier.
See docs/EVENT_SEQUENCE.md for the trust boundary and clock semantics.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping

SCHEMA = "independent-scoring-sequence-v1"
# Scoring types are explicit: NFL field goals are not basketball baskets.
RULES = {
    "NHL": (3, 1_200_000, {"goal": 1}),
    "MLS": (2, 2_700_000, {"goal": 1, "own_goal": 1}),
    "WNBA": (4, 600_000, {"basket_2": 2, "basket_3": 3, "free_throw": 1}),
    "NBA": (4, 720_000, {"basket_2": 2, "basket_3": 3, "free_throw": 1}),
    "NFL": (4, 900_000, {"touchdown": 6, "field_goal": 3, "extra_point": 1,
                          "two_point": 2, "safety": 2, "defensive_conversion": 2}),
    "MLB": (9, None, {"run": 1}),
}


class SequenceError(ValueError):
    """A stable rejection reason; a rejected sequence produces no targets."""


def _require(condition, reason):
    if not condition:
        raise SequenceError(reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _time(value):
    _require(isinstance(value, str), "invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SequenceError("invalid_timestamp") from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "naive_timestamp")
    return parsed.astimezone(timezone.utc)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _decode(body):
    _require(isinstance(body, bytes), "missing_sequence_body")
    try:
        value = json.loads(body, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(SequenceError("nonfinite_json")))
    except (ValueError, UnicodeDecodeError) as exc:
        if isinstance(exc, SequenceError):
            raise
        raise SequenceError("invalid_json") from exc
    _require(isinstance(value, dict) and value.get("schema") == SCHEMA, "unsupported_sequence_schema")
    return value


def _score(value):
    _require(isinstance(value, list) and len(value) == 2 and
             all(_integer(x) for x in value), "invalid_observed_score")
    return tuple(value)


def _verify(body, evidence, as_of):
    _require(isinstance(evidence, Mapping), "missing_verification")
    digest = hashlib.sha256(body).hexdigest()
    _require(evidence.get("snapshot_sha256") == digest, "snapshot_hash_mismatch")
    _require(evidence.get("complete") is True, "unverified_complete_sequence")
    _require(evidence.get("coverage") == "all_scoring_events", "incomplete_event_coverage")
    _require(evidence.get("source_type") == "independent_sports_events", "unverified_source_type")
    for field in ("source_url", "completeness_reference", "availability_reference"):
        _require(_text(evidence.get(field)), "missing_" + field)
    _require(evidence.get("availability_basis") in ("source_publication", "archived_observation"),
             "unknown_availability_basis")
    available = _time(evidence.get("available_at"))
    verified = _time(evidence.get("verified_at"))
    fetched = _time(evidence.get("fetched_at"))
    _require(available <= fetched <= verified <= as_of, "evidence_not_available_at_cutoff")
    if evidence["availability_basis"] == "archived_observation":
        _require(available == fetched, "archive_availability_mismatch")
    return digest, available


def _periods(data, league):
    regulation_count, duration, _ = RULES[league]
    rows = data.get("periods")
    _require(isinstance(rows, list) and len(rows) >= regulation_count, "incomplete_period_history")
    periods = {}
    for row in rows:
        _require(isinstance(row, dict), "invalid_period")
        number = row.get("number")
        _require(_integer(number, 1) and number not in periods, "duplicate_or_invalid_period")
        phase = "REG" if number <= regulation_count else "OT"
        _require(row.get("phase") == phase, "period_phase_mismatch")
        _require(row.get("complete") is True, "incomplete_period_history")
        length = row.get("duration_ms")
        if league == "MLB":
            _require(length is None, "baseball_has_no_game_clock")
        elif league == "MLS":
            # Duration includes independently observed stoppage time, never guessed.
            base = duration if phase == "REG" else 900_000
            _require(_integer(length, base), "invalid_period_duration")
        elif phase == "REG":
            _require(type(length) is int and length == duration, "invalid_period_duration")
        else:
            allowed = {"NHL": (300_000, 1_200_000), "NBA": (300_000,),
                       "WNBA": (300_000,), "NFL": (600_000, 900_000)}[league]
            _require(type(length) is int and length in allowed, "invalid_period_duration")
        _score(row.get("score"))
        periods[number] = row
    _require(set(periods) == set(range(1, len(periods) + 1)), "incomplete_period_history")
    if league == "MLS":
        _require(len(periods) in (2, 4), "incomplete_extra_time")
    if len(periods) > regulation_count and league in ("NBA", "WNBA", "NFL", "NHL"):
        regulation_score = tuple(sum(periods[p]["score"][i] for p in range(1, regulation_count + 1)) for i in (0, 1))
        _require(regulation_score[0] == regulation_score[1], "overtime_without_regulation_tie")
    if league in ("NBA", "WNBA"):
        # Every extra period after the first requires another tie. Counting
        # impossible later OT rows can otherwise create false first/last labels.
        cumulative = [0, 0]
        for number in sorted(periods):
            row = periods[number]
            if number > regulation_count:
                _require(cumulative[0] == cumulative[1], "overtime_without_previous_tie")
            cumulative = [cumulative[i] + row["score"][i] for i in (0, 1)]
    if league == "NHL" and len(periods) > regulation_count:
        ot = [periods[p] for p in sorted(periods) if p > regulation_count]
        _require(len({row["duration_ms"] for row in ot}) == 1, "mixed_nhl_overtime_rules")
        _require(len(ot) == 1 or ot[0]["duration_ms"] == 1_200_000, "multiple_regular_nhl_overtimes")
        for index, row in enumerate(ot):
            goals = sum(row["score"])
            _require(goals <= 1 and (goals == 0 or index == len(ot) - 1), "nhl_sudden_death_violation")
    return periods


def _events(data, periods, league, start, completed):
    events = data.get("events")
    _require(isinstance(events, list), "missing_event_history")
    _require(_integer(data.get("event_count")) and data["event_count"] == len(events),
             "event_count_mismatch")
    teams = (data.get("home_id"), data.get("away_id"))
    _require(all(_text(t) for t in teams) and teams[0] != teams[1], "invalid_teams")
    totals = {p: [0, 0] for p in periods}
    seen = set()
    normalized = []
    for event in events:
        _require(isinstance(event, dict), "invalid_event")
        eid = event.get("event_id")
        _require(_text(eid) and eid not in seen, "duplicate_or_missing_event_id")
        seen.add(eid)
        _require(event.get("game_id") == data["game_id"], "event_game_mismatch")
        _require(event.get("status") == "confirmed", "unresolved_event_correction")
        period = event.get("period")
        _require(_integer(period, 1) and period in periods, "invalid_event_period")
        _require(event.get("phase") == periods[period]["phase"], "event_phase_mismatch")
        kind = event.get("kind")
        _require(isinstance(kind, str) and kind in RULES[league][2], "unsupported_scoring_type")
        points = event.get("points")
        _require(type(points) is int and points == RULES[league][2][kind], "invalid_scoring_value")
        team = event.get("team_id")
        _require(team in teams, "event_team_mismatch")
        player = event.get("player_id")
        _require(player is None or _text(player), "invalid_player_id")
        order = event.get("order")
        _require(order is None or _integer(order), "invalid_event_order")
        elapsed = event.get("elapsed_ms")
        if league == "MLB":
            _require(elapsed is None, "baseball_has_no_game_clock")
            occurred = _time(event.get("occurred_at"))
            _require(start <= occurred <= completed, "event_timestamp_outside_game")
            clock = occurred
        else:
            _require(_integer(elapsed) and elapsed <= periods[period]["duration_ms"],
                     "invalid_event_clock")
            clock = elapsed
            if event.get("occurred_at") is not None:
                _require(start <= _time(event["occurred_at"]) <= completed,
                         "event_timestamp_outside_game")
        totals[period][teams.index(team)] += points
        normalized.append((period, clock, event))
    for period, total in totals.items():
        _require(tuple(total) == _score(periods[period]["score"]), "incomplete_scoring_sequence")
    # Inning clocks must not run backwards between innings.
    if league == "MLB":
        ordered = sorted(normalized, key=lambda item: (item[0], item[1]))
        _require(all(a[1] <= b[1] for a, b in zip(ordered, ordered[1:])), "nonmonotone_event_time")
    else:
        known_times = {}
        for period, clock, event in normalized:
            if event.get("occurred_at") is not None:
                known_times.setdefault((period, clock), []).append(_time(event["occurred_at"]))
        ordered_times = [known_times[key] for key in sorted(known_times)]
        _require(all(max(a) <= min(b) for a, b in zip(ordered_times, ordered_times[1:])),
                 "nonmonotone_event_time")
    final = tuple(sum(v[i] for v in totals.values()) for i in (0, 1))
    award = data.get("shootout_award")
    if award is not None:
        _require(league == "NHL" and award in teams and len(periods) > 3 and final[0] == final[1],
                 "invalid_shootout_award")
        _require(len(periods) == 4 and periods[4]["duration_ms"] == 300_000,
                 "shootout_outside_regular_nhl_overtime")
        final = tuple(v + int(teams[i] == award) for i, v in enumerate(final))
    _require(final == _score(data.get("final_score")), "final_score_mismatch")
    return normalized


def _boundary(group, subject, edge):
    """Same clock is unresolved unless every event has a unique source order."""
    orders = [e.get("order") for _, _, e in group]
    if all(o is not None for o in orders):
        _require(len(set(orders)) == len(orders), "conflicting_event_order")
        group = [min(group, key=lambda x: x[2]["order"]) if edge == "first" else
                 max(group, key=lambda x: x[2]["order"])]
    events = [e for _, _, e in group]
    field = "team_id" if subject == "team" else "player_id"
    missing = any(e.get(field) is None or (subject == "player" and e["kind"] == "own_goal") for e in events)
    subjects = sorted({e[field] for e in events if e.get(field) is not None}) if not missing else []
    return {"status": "missing_scorer" if missing else ("resolved" if len(subjects) == 1 else "tied"),
            "subjects": subjects, "event_ids": sorted(e["event_id"] for e in events),
            "period": events[0]["period"], "elapsed_ms": events[0].get("elapsed_ms"),
            "occurred_at": events[0].get("occurred_at") if len(events) == 1 else None}


def extract_event_targets(body: bytes, verification: Mapping, *, as_of: str,
                          league: str, game_id: str, scope: str = "regulation",
                          kind: str = "all_scoring", subject: str = "team") -> dict:
    """Return count and first/last scorer/time, or explicit missing evidence.

    Scopes: regulation, regulation_ot (extra innings/time included), overtime,
    or period_N. No shootout event is ever a goal target. A complete, verified
    scoreless history returns count=0 and no_event, never a zero timing target.
    Each correction is a new complete snapshot with its own hash/availability.
    """
    result = {"schema": SCHEMA, "league": league, "game_id": game_id, "scope": scope,
              "kind": kind, "subject": subject, "status": "rejected", "reason": None,
              "event_count": None, "first": None, "last": None, "provenance": None,
              "production_supported": False, "source_identity_verified": False,
              "completeness_basis": "caller_supplied_attestation"}
    try:
        _require(isinstance(league, str) and league in RULES, "unsupported_league")
        _require(_text(game_id) and game_id.startswith(league + ":") and
                 len(game_id) > len(league) + 1, "invalid_game_id")
        _require(subject in ("team", "player"), "unsupported_subject")
        _require(isinstance(kind, str) and (kind == "all_scoring" or kind in RULES[league][2]),
                 "unsupported_target_kind")
        data = _decode(body)
        _require(data.get("league") == league and data.get("game_id") == game_id, "game_identity_mismatch")
        digest, available = _verify(body, verification, _time(as_of))
        start, completed = _time(data.get("start")), _time(data.get("completed_at"))
        _require(start < completed <= available, "invalid_completion_chronology")
        _require(data.get("state") == "final", "game_not_final")
        _require(data.get("corrections_resolved") is True, "unresolved_event_correction")
        periods = _periods(data, league)
        events = _events(data, periods, league, start, completed)
        if scope == "regulation":
            selected = {p for p, row in periods.items() if row["phase"] == "REG"}
        elif scope == "regulation_ot":
            selected = set(periods)
        elif scope == "overtime":
            selected = {p for p, row in periods.items() if row["phase"] == "OT"}
            _require(bool(selected), "scope_not_played")
        elif isinstance(scope, str) and scope.startswith("period_") and scope[7:].isdigit():
            selected = {int(scope[7:])}
            _require(selected <= periods.keys(), "scope_not_played")
        else:
            raise SequenceError("unsupported_scope")
        # Validate ordering collisions before filtering kinds, which could hide them.
        groups = {}
        for p, clock, event in events:
            groups.setdefault((p, clock), []).append((p, clock, event))
        for group in groups.values():
            orders = [e["order"] for _, _, e in group if e.get("order") is not None]
            _require(len(set(orders)) == len(orders), "conflicting_event_order")
        chosen = [(p, clock, e) for p, clock, e in events if p in selected and
                  (kind == "all_scoring" or e["kind"] == kind)]
        chosen.sort(key=lambda row: (row[0], row[1]))
        result.update(status="ready" if chosen else "no_event", event_count=len(chosen),
                      provenance={"snapshot_sha256": digest, **dict(verification)})
        if chosen:
            first_key, last_key = chosen[0][:2], chosen[-1][:2]
            result["first"] = _boundary([r for r in chosen if r[:2] == first_key], subject, "first")
            result["last"] = _boundary([r for r in chosen if r[:2] == last_key], subject, "last")
    except SequenceError as exc:
        result.update(status="rejected", reason=str(exc), event_count=None, first=None, last=None,
                      provenance=None)
    return result
