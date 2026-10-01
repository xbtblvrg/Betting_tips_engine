"""Offline NFL rushing-target consistency check; no betting/model activation.

Two nflverse representations are not independent ground truth. Hashes bind
caller-supplied archive bytes, not source authenticity or historical availability.
No historical row, including a zero, establishes current participation or DNP.
"""
from collections import Counter
from datetime import date
import hashlib
import math
import re

import pyarrow as pa
import pyarrow.parquet as pq

from .player_archive import PlayerArchive
from .player_history import _identifier, _time
from .player_prop_contracts import canonical_hash

VERSION = "f0027-nfl-rushing-reconciliation-v1"
_ROOT = "https://github.com/nflverse/nflverse-data/releases/download/"
_COMMON = ("game_id", "season", "week", "season_type")
_PBP = _COMMON + (
    "play_id", "home_team", "away_team", "posteam", "game_date", "qtr", "time",
    "desc", "play_type", "rush_attempt", "qb_kneel", "qb_scramble", "sack",
    "two_point_attempt", "rusher_player_id", "rushing_yards", "lateral_rush",
    "lateral_rusher_player_id", "lateral_rushing_yards", "home_score", "away_score",
    "total_home_score", "total_away_score", "game_seconds_remaining",
)
_WEEKLY = _COMMON + ("player_id", "team", "opponent_team", "carries", "rushing_yards")
_TYPES = {None, "run", "qb_kneel", "pass", "qb_spike", "no_play", "kickoff",
          "punt", "extra_point", "field_goal"}
_BLOCKERS = ["source_adapter_not_live_verified", "sequence_completeness_not_independently_verified",
             "shared_provider_not_independent_ground_truth", "historical_availability_not_authenticated",
             "current_participation_and_role_not_verified", "validated_prop_model_missing",
             "exact_stake_contract_and_settlement_rules_not_verified", "production_integration_not_reviewed"]


def _integer(value, *, minimum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value)
            or (minimum is not None and value < minimum)):
        raise ValueError("invalid_integer_stat_or_identity")
    return int(value)


def _player(value):
    if not isinstance(value, str) or not re.fullmatch(r"00-\d{7}", value):
        raise ValueError("invalid_gsis_player_id")
    return value


def _archive_rows(archive, *, kind, year, game_id, now):
    if not isinstance(archive, PlayerArchive):
        raise ValueError("missing_archive_document")
    if (type(archive.body) is not bytes or len(archive.body) > 64 * 1024 * 1024
            or hashlib.sha256(archive.body).hexdigest() != archive.source_sha256):
        raise ValueError("archive_bytes_missing_oversize_or_hash_mismatch")
    suffix = f"pbp/play_by_play_{year}.parquet" if kind == "plays" else f"stats_player/stats_player_week_{year}.parquet"
    if archive.source_url != _ROOT + suffix:
        raise ValueError("source_url_or_season_mismatch")
    if (archive.evidence_kind not in ("fixture", "unverified", "archived_observation")
            or not _identifier(archive.archive_reference)):
        raise ValueError("invalid_archive_metadata")
    observed = _time(archive.observed_at)
    if observed > now:
        raise ValueError("source_not_observed_as_of")
    file = pq.ParquetFile(pa.BufferReader(archive.body))
    columns = _PBP if kind == "plays" else _WEEKLY
    if (file.metadata.num_rows > 500_000 or file.metadata.num_columns > 512
            or len(set(file.schema_arrow.names)) != len(file.schema_arrow.names)):
        raise ValueError("oversize_or_ambiguous_parquet_schema")
    if not set(columns) <= set(file.schema_arrow.names):
        raise ValueError("missing_native_source_columns")
    # Bound decoding and select only native fields needed by this calculation.
    rows = []
    for batch in file.iter_batches(batch_size=4096, columns=list(columns)):
        rows.extend(r for r in batch.to_pylist() if r["game_id"] == game_id)
    if not rows:
        raise ValueError("game_absent_from_source")
    evidence = dict(sha256=archive.source_sha256, url=archive.source_url,
                    observed_at=observed.isoformat(), archive_reference=archive.archive_reference,
                    evidence_kind=archive.evidence_kind, source_published_at=None,
                    archive_timestamp_independently_authenticated=False)
    return rows, evidence


def reconcile_nfl_rushing(play_by_play, *, game_id, as_of, weekly=None):
    """Reconstruct one game's signed rushing yards and compare weekly totals.

    Includes credited runs/scrambles/kneels and overtime. Excludes two-point
    tries and no-play rows; conflicting credit flags and lateral credits require
    review. Weekly zero-carry rows remain unknown participation, never DNP.
    A mismatch clears all reconciled values, retaining both observed totals.
    Successful output is only same-provider historical consistency evidence.
    """
    out = dict(version=VERSION, state="rejected", game_id=game_id, assessed_as_of=None,
               sources={}, players=[], excluded_plays=[], reasons=[], available_at=None,
               source_published_at=None, identity_namespace="nflverse-gsis", league="NFL",
               metric="rushing_yards", stat_scope="full_game_including_overtime",
               production_props_enabled=False, publishable=False, execution_eligible=False,
               current_participation_verified=False, settlement_ready=False,
               activation_blockers=list(_BLOCKERS))

    def finish():
        out["reasons"] = list(dict.fromkeys(out["reasons"]))
        if out["reasons"]:
            for player in out["players"]:
                player["reconciled_value"] = None
        out["assessment_sha256"] = canonical_hash(out)
        return out

    try:
        now = _time(as_of)
        out["assessed_as_of"] = now.isoformat()
        match = re.fullmatch(r"(20\d{2})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})", game_id)
        if not match:
            raise ValueError("invalid_nfl_game_id")
        year, week = int(match[1]), int(match[2])
        away, home = match[3], match[4]
        if away == home or not 1 <= week <= 22:
            raise ValueError("invalid_event_teams_or_week")
        rows, evidence = _archive_rows(play_by_play, kind="plays", year=year, game_id=game_id, now=now)
        out["sources"]["plays"] = evidence
        observed = _time(evidence["observed_at"])
        season_type = rows[0]["season_type"]
        game_date = rows[0]["game_date"]
        if season_type not in ("REG", "POST") or date.fromisoformat(game_date) > observed.date():
            raise ValueError("invalid_season_type_or_future_game_date")
        out.update(home_team=home, away_team=away, game_date=game_date, season_type=season_type)
        seen_plays, previous_period = set(), 0
        players = {}
        terminal_count = 0
        for row in rows:
            if (_integer(row["season"]) != year or _integer(row["week"]) != week
                    or row["season_type"] != season_type or row["game_date"] != game_date
                    or row["home_team"] != home or row["away_team"] != away):
                raise ValueError("event_identity_conflict")
            play_id, period = _integer(row["play_id"], minimum=1), _integer(row["qtr"], minimum=1)
            # Native timeout rows can precede a smaller play_id at the same
            # clock. IDs identify plays; they are not a chronological counter.
            if play_id in seen_plays or period < previous_period or period > 10:
                raise ValueError("duplicate_or_unordered_play_or_period")
            seen_plays.add(play_id)
            previous_period = period
            if row["play_type"] not in _TYPES:
                raise ValueError("unknown_play_type")
            if not isinstance(row["time"], str) or not re.fullmatch(r"(?:0\d|1[0-5]):[0-5]\d", row["time"]):
                raise ValueError("invalid_period_clock")
            for flag in ("rush_attempt", "qb_kneel", "qb_scramble", "sack", "two_point_attempt", "lateral_rush"):
                if row[flag] is not None and _integer(row[flag]) not in (0, 1):
                    raise ValueError("invalid_play_flag")
            if row["desc"] == "END GAME":
                terminal_count += 1
            # Lateral player yard attribution is not implemented by this adapter.
            if (row["lateral_rush"] == 1 or row["lateral_rusher_player_id"] is not None
                    or row["lateral_rushing_yards"] is not None):
                raise ValueError("lateral_rushing_requires_review")
            rush = row["rush_attempt"] == 1
            if row["two_point_attempt"] == 1:
                out["excluded_plays"].append(dict(play_id=play_id, reason="two_point_attempt"))
                continue
            if row["play_type"] == "no_play":
                if rush or row["rushing_yards"] is not None or row["rusher_player_id"] is not None:
                    raise ValueError("no_play_with_rushing_credit")
                out["excluded_plays"].append(dict(play_id=play_id, reason="no_play"))
                continue
            if not rush:
                if (row["play_type"] in ("run", "qb_kneel") or row["rushing_yards"] is not None
                        or row["rusher_player_id"] is not None or row["qb_kneel"] == 1 or row["qb_scramble"] == 1):
                    raise ValueError("rushing_credit_without_attempt")
                continue
            if (row["play_type"] not in ("run", "qb_kneel") or row["sack"] != 0
                    or row["two_point_attempt"] != 0 or row["qb_kneel"] is None
                    or row["qb_scramble"] is None or row["lateral_rush"] != 0
                    or (row["play_type"] == "qb_kneel") != (row["qb_kneel"] == 1)
                    or row["qb_kneel"] == row["qb_scramble"] == 1):
                raise ValueError("ambiguous_rushing_play")
            pid, team = _player(row["rusher_player_id"]), row["posteam"]
            if team not in (home, away):
                raise ValueError("invalid_rushing_team")
            value = _integer(row["rushing_yards"])
            if pid in players and players[pid]["team_id"] != team:
                raise ValueError("player_on_both_teams")
            player = players.setdefault(pid, dict(player_id=pid, team_id=team, play_ids=[],
                observed_carries=0, observed_rushing_yards=0, kneels=0, scrambles=0,
                overtime_carries=0, weekly_carries=None, weekly_rushing_yards=None,
                historical_rush_observed=True, current_participation_verified=False,
                reconciled_value=None))
            player["play_ids"].append(play_id)
            player["observed_carries"] += 1
            player["observed_rushing_yards"] += value
            player["kneels"] += int(row["qb_kneel"])
            player["scrambles"] += int(row["qb_scramble"])
            player["overtime_carries"] += period > 4
        out["players"] = sorted(players.values(), key=lambda r: (r["team_id"], r["player_id"]))
        last = rows[-1]
        minute, second = map(int, last["time"].split(":"))
        # NFL overtime can end with time remaining. Native game_seconds_remaining
        # then retains that OT clock; END GAME plus final scores defines the end.
        remaining = _integer(last["game_seconds_remaining"], minimum=0)
        if (terminal_count != 1 or last["desc"] != "END GAME" or last["qtr"] < 4
                or remaining != minute * 60 + second or (last["qtr"] == 4 and remaining != 0)
                or last["rush_attempt"] == 1 or last["rusher_player_id"] is not None):
            raise ValueError("missing_or_ambiguous_final_marker")
        scores = tuple(_integer(last[k], minimum=0) for k in ("home_score", "away_score"))
        if scores != tuple(_integer(last[k], minimum=0) for k in ("total_home_score", "total_away_score")):
            raise ValueError("terminal_score_mismatch")
        if any(tuple(_integer(r[k], minimum=0) for k in ("home_score", "away_score")) != scores for r in rows):
            raise ValueError("conflicting_final_scores")
        out.update(final_scores=dict(zip((home, away), scores)), play_count=len(rows),
                   available_at=evidence["observed_at"], state="plays_reconstructed")
        if weekly is None:
            out["reasons"].append("missing_weekly_player_totals")
            return finish()
        totals, evidence = _archive_rows(weekly, kind="weekly", year=year, game_id=game_id, now=now)
        out["sources"]["weekly"] = evidence
        if date.fromisoformat(game_date) > _time(evidence["observed_at"]).date():
            raise ValueError("weekly_capture_before_game_date")
        out["available_at"] = max(observed, _time(evidence["observed_at"])).isoformat()
        seen = set()
        for row in totals:
            pid, team = _player(row["player_id"]), row["team"]
            if pid in seen:
                raise ValueError("duplicate_weekly_player")
            seen.add(pid)
            if (_integer(row["season"]) != year or _integer(row["week"]) != week
                    or row["season_type"] != season_type or team not in (home, away)
                    or row["opponent_team"] != (away if team == home else home)):
                raise ValueError("weekly_event_identity_conflict")
            carries, yards = _integer(row["carries"], minimum=0), _integer(row["rushing_yards"])
            if pid not in players:
                # Missing play evidence is NOT zero, even if the weekly row is zero.
                players[pid] = dict(player_id=pid, team_id=team, play_ids=[], observed_carries=None,
                    observed_rushing_yards=None, kneels=None, scrambles=None, overtime_carries=None,
                    historical_rush_observed=None, current_participation_verified=False, reconciled_value=None)
                if carries != 0 or yards != 0:
                    out["reasons"].append("weekly_credit_missing_from_plays:" + pid)
            else:
                p = players[pid]
                if (p["team_id"] != team or p["observed_carries"] != carries or p["observed_rushing_yards"] != yards):
                    out["reasons"].append("weekly_player_total_mismatch:" + pid)
                else:
                    p["reconciled_value"] = yards
            players[pid].update(weekly_carries=carries, weekly_rushing_yards=yards)
        # Set iteration changes across process hash seeds. Reasons are part of
        # the evidence checksum, so emit missing IDs in a stable order.
        for pid in sorted(players.keys() - seen):
            out["reasons"].append("rusher_missing_from_weekly:" + pid)
        out["players"] = sorted(players.values(), key=lambda r: (r["team_id"], r["player_id"]))
        out["state"] = "source_disagreement" if out["reasons"] else "historical_totals_reconciled"
        out["player_counts"] = dict(Counter("observed_rusher" if p["historical_rush_observed"] else "no_rush_evidence"
                                            for p in out["players"]))
    except (ValueError, TypeError, KeyError, OverflowError, OSError, pa.ArrowException) as exc:
        out["state"] = "rejected"
        out["reasons"].append(str(exc))
    return finish()
