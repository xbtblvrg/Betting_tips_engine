"""Offline NHL final-boxscore adapter for the experimental D01 history API.

No fetching, filesystem access, current participation inference or model approval.
Archive timestamps remain caller claims; hash verification authenticates bytes,
not the source or the time at which the caller obtained them.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from .player_history import build_player_history, _identifier, _json_object, _number, _time


@dataclass(frozen=True)
class PlayerArchive:
    body: bytes
    source_sha256: str
    source_url: str
    observed_at: str | datetime
    archive_reference: str
    evidence_kind: str  # fixture, unverified, archived_observation (caller claim)


# Same NHL fields/renames as player_data.parse_official_hockey. Deliberately
# exclude scores, prices, inferred role, and unsupported event-order targets.
_STATS = {
    "goals": "goals", "assists": "assists", "points": "points",
    "sog": "shots_on_goal", "hits": "hits", "blockedShots": "blocked_shots",
    "plusMinus": "plus_minus", "powerPlayGoals": "power_play_goals",
    "goalsAgainst": "goals_against", "shotsAgainst": "shots_against",
    "saves": "saves", "pim": "pim", "shifts": "shifts",
    "giveaways": "giveaways", "takeaways": "takeaways",
}


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("invalid_provider_id")
    return str(value)


def decode_nhl_archive(archive: PlayerArchive) -> list[dict]:
    """Decode one complete official-schema response; reject ambiguous bodies.

    Availability is ONLY the archive observation time of these final-result
    bytes. Publication time is unknown. A legacy fetched_at/hash pair without
    bytes and archive metadata is insufficient. This adapter is not live checked.
    """
    if not isinstance(archive, PlayerArchive):
        raise ValueError("missing_archive_document")
    if (type(archive.body) is not bytes or not isinstance(archive.source_sha256, str)
            or hashlib.sha256(archive.body).hexdigest() != archive.source_sha256):
        raise ValueError("source_hash_mismatch")
    if not _identifier(archive.archive_reference):
        raise ValueError("missing_archive_reference")
    if archive.evidence_kind not in ("fixture", "unverified", "archived_observation"):
        raise ValueError("invalid_evidence_kind")
    try:
        observed = _time(archive.observed_at)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid_observation_time") from exc
    try:
        payload = json.loads(archive.body, object_pairs_hook=_json_object)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid_source_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_source_schema")
    event = _positive_id(payload.get("id"))
    if archive.source_url != f"https://api-web.nhle.com/v1/gamecenter/{event}/boxscore":
        raise ValueError("source_event_url_mismatch")
    if payload.get("gameState") not in ("OFF", "FINAL"):
        raise ValueError("game_not_final")
    try:
        start = _time(payload.get("startTimeUTC"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid_game_start") from exc
    if observed <= start:
        raise ValueError("observation_not_after_start")
    teams = []
    for side in ("homeTeam", "awayTeam"):
        team = payload.get(side)
        if not isinstance(team, dict) or not _identifier(team.get("abbrev")):
            raise ValueError("invalid_team_identity")
        score = team.get("score")
        if type(score) is not int or score < 0:
            raise ValueError("missing_final_score")
        teams.append(team["abbrev"])
    if teams[0] == teams[1]:
        raise ValueError("same_opponent")
    box = payload.get("playerByGameStats")
    if not isinstance(box, dict):
        raise ValueError("missing_player_boxscore")
    rows, seen = [], set()
    for index, side in enumerate(("homeTeam", "awayTeam")):
        groups = box.get(side)
        if not isinstance(groups, dict):
            raise ValueError("missing_team_boxscore")
        # Unknown groups must be reviewed rather than silently interpreted.
        if set(groups) != {"forwards", "defense", "goalies"}:
            raise ValueError("unsupported_player_groups")
        for players in groups.values():
            if not isinstance(players, list):
                raise ValueError("invalid_player_group")
            for player in players:
                if not isinstance(player, dict):
                    raise ValueError("invalid_player")
                pid = _positive_id(player.get("playerId"))
                if pid in seen:
                    raise ValueError("duplicate_player_identity")
                seen.add(pid)
                name = player.get("name")
                if not isinstance(name, dict) or not _identifier(name.get("default")):
                    raise ValueError("invalid_player_name")
                stats = {}
                for source, target in _STATS.items():
                    if source not in player:
                        continue
                    value = player[source]
                    if value is not None:
                        if (not _number(value) or value != int(value)
                                or (source != "plusMinus" and value < 0)):
                            raise ValueError("invalid_player_statistic")
                    stats[target] = value
                if not stats:
                    raise ValueError("missing_player_statistics")
                minutes = None
                toi = player.get("toi")
                if toi is not None:
                    if not isinstance(toi, str) or not re.fullmatch(r"[0-9]{1,3}:[0-5][0-9]", toi):
                        raise ValueError("invalid_time_on_ice")
                    minute, second = map(int, toi.split(":"))
                    minutes = minute + second / 60
                starter = player.get("starter")
                if starter is not None and type(starter) is not bool:
                    raise ValueError("invalid_starter")
                rows.append({
                    "league": "NHL", "game_id": "NHL:" + event,
                    "start": start.isoformat(), "player_id": pid,
                    "player_name": name["default"], "team_id": teams[index],
                    "opponent_id": teams[1 - index], "home": index == 0,
                    "stat_group": "player", "stats": json.dumps(stats, sort_keys=True),
                    "minutes": minutes, "starter": starter,
                    # Zero/missing TOI does not prove a settlement DNP.
                    "played": True if minutes is not None and minutes > 0 else None,
                    "source_url": archive.source_url, "source_sha256": archive.source_sha256,
                    "source_fetched_at": observed.isoformat(), "source_published_at": None,
                    "available_at": observed.isoformat(),
                    "availability_basis": "archived_observation",
                    "availability_reference": archive.archive_reference,
                })
    if not rows:
        raise ValueError("empty_player_boxscore")
    return rows


def build_archived_player_history(archives, **query) -> dict:
    """Integrate source decoding with D01; retain source-level rejection audit.

    Duplicate eligible versions stay ambiguous under D01's existing rule. Future
    corrections are excluded by that builder before duplicate detection.
    """
    if query.get("league") != "NHL":
        raise ValueError("unsupported_archive_league")
    rows, reviews = [], []
    for index, archive in enumerate(archives):
        review = {"archive_index": index, "row_count": 0, "reason": None}
        if isinstance(archive, PlayerArchive):
            review.update(source_sha256=archive.source_sha256,
                          source_url=archive.source_url,
                          archive_reference=archive.archive_reference,
                          evidence_kind=archive.evidence_kind)
        try:
            decoded = decode_nhl_archive(archive)
            rows.extend(decoded)
            review["row_count"] = len(decoded)
        except ValueError as exc:
            review["reason"] = str(exc)
        reviews.append(review)
    return {
        "adapter_version": "experimental-nhl-player-archive-v1",
        "source_status": ("missing_source" if not reviews else
                          "rejected" if not rows else
                          "partial" if any(r["reason"] for r in reviews) else "decoded"),
        "features": build_player_history(rows, **query), "source_reviews": reviews,
        "source_rejection_counts": dict(Counter(r["reason"] for r in reviews if r["reason"])),
        "live_source_verified": False, "archive_timestamp_authenticated": False,
        "production_supported": False,
    }
