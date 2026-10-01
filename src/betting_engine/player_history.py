"""Experimental, pure point-in-time features; no model or publication integration.

See docs/PLAYER_HISTORY.md for the evidence contract. Availability claims are
caller-supplied evidence, not verified by this module against a remote source.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import re


def _time(value: object) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError("expected an aware ISO timestamp or datetime")
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return stamp.astimezone(timezone.utc)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate statistic")
        result[key] = value
    return result


def build_player_history(
    rows: Iterable[Mapping], *, as_of: str | datetime,
    target_game_id: str, target_start: str | datetime,
    league: str, player_id: str, team_id: str, stat_group: str,
    stat_fields: Sequence[str],
) -> dict:
    """Summarize eligible exact-identity observations with rejection diagnostics.

    Invalid query arguments raise ValueError. Invalid or unavailable input rows
    are excluded with one reason per row. This function has no I/O or clock use.
    Historical participation remains tri-state; target participation is unknown.
    """
    cutoff, target = _time(as_of), _time(target_start)
    identity = dict(league=league, player_id=player_id, team_id=team_id,
                    stat_group=stat_group)
    if not all(_identifier(v) for v in (*identity.values(), target_game_id)):
        raise ValueError("query identities must be nonempty exact strings")
    if not target_game_id.startswith(league + ":") or target_game_id == league + ":":
        raise ValueError("target_game_id must be league-prefixed")
    if cutoff > target:
        raise ValueError("as_of cannot follow the target start for pregame features")
    if not isinstance(stat_fields, Sequence) or isinstance(stat_fields, (str, bytes)):
        raise ValueError("stat_fields must be a sequence of unique names")
    fields = tuple(stat_fields)
    if not fields or not all(_identifier(k) for k in fields) or len(set(fields)) != len(fields):
        raise ValueError("stat_fields must contain unique nonempty names")

    rejected = []
    candidates = defaultdict(list)
    input_count = 0

    def reject(index, row, reason):
        rejected.append({"row_index": index,
                         "game_id": row.get("game_id") if isinstance(row, Mapping) else None,
                         "reason": reason})

    for index, row in enumerate(rows):
        input_count += 1
        if not isinstance(row, Mapping):
            reject(index, row, "invalid_row")
            continue
        mismatch = next((k for k, v in identity.items() if row.get(k) != v), None)
        if mismatch:
            reject(index, row, mismatch + "_mismatch")
            continue
        game_id = row.get("game_id")
        if not _identifier(game_id) or not game_id.startswith(league + ":") or game_id == league + ":":
            reject(index, row, "invalid_game_id")
            continue
        if game_id == target_game_id:
            reject(index, row, "target_game")
            continue
        try:
            start = _time(row.get("start"))
        except (ValueError, TypeError, OverflowError):
            reject(index, row, "invalid_start")
            continue
        if start >= cutoff or start >= target:
            reject(index, row, "current_or_future_game")
            continue
        if row.get("available_at") is None:
            reject(index, row, "missing_availability")
            continue
        try:
            available = _time(row["available_at"])
        except (ValueError, TypeError, OverflowError):
            reject(index, row, "invalid_availability")
            continue
        if available <= start:
            reject(index, row, "availability_not_after_start")
            continue
        if available > cutoff:
            reject(index, row, "unavailable_as_of")
            continue
        # Never substitute start, fetch time, or a fixed post-game delay.
        if (row.get("availability_basis") not in ("source_publication", "archived_observation")
                or not _identifier(row.get("availability_reference"))):
            reject(index, row, "missing_availability_evidence")
            continue
        if (not _identifier(row.get("source_url"))
                or not isinstance(row.get("source_sha256"), str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", row["source_sha256"]) is None):
            reject(index, row, "invalid_source_provenance")
            continue
        if any(row.get(k) is not None and type(row[k]) is not bool for k in ("played", "starter")):
            reject(index, row, "invalid_participation")
            continue
        minutes = row.get("minutes")
        if minutes is not None and (not _number(minutes) or minutes < 0):
            reject(index, row, "invalid_minutes")
            continue
        raw_stats = row.get("stats")
        try:
            stats = (json.loads(raw_stats, object_pairs_hook=_json_object)
                     if isinstance(raw_stats, str) else raw_stats)
            if (not isinstance(stats, Mapping)
                    or any(not _identifier(k) or (v is not None and not _number(v))
                           for k, v in stats.items())):
                raise ValueError("invalid statistics")
        except (ValueError, TypeError):
            reject(index, row, "invalid_stats")
            continue
        observation = {
            **identity, "game_id": game_id, "start": start.isoformat(),
            "available_at": available.isoformat(),
            "availability_basis": row["availability_basis"],
            "availability_reference": row["availability_reference"],
            "source_url": row["source_url"], "source_sha256": row["source_sha256"],
            "source_fetched_at": row.get("source_fetched_at"),
            "played": row.get("played"), "starter": row.get("starter"),
            "minutes": minutes, "stats": {k: stats.get(k) for k in fields},
        }
        candidates[game_id].append((index, observation))

    history = []
    for versions in candidates.values():
        # Fail closed on duplicate or conflicting eligible versions. Future
        # revisions were already excluded and cannot erase an eligible version.
        if len(versions) > 1:
            for index, row in versions:
                reject(index, row, "duplicate_game_observation")
        else:
            history.append(versions[0][1])
    history.sort(key=lambda row: (_time(row["start"]), row["game_id"]))
    summaries = {}
    for field in fields:
        values = [r["stats"][field] for r in history if r["stats"][field] is not None]
        summaries[field] = {
            "count": len(values), "missing_count": len(history) - len(values),
            "mean": math.fsum(v / len(values) for v in values) if values else None,
        }
    rejected.sort(key=lambda row: row["row_index"])
    return {
        "contract_version": "experimental-player-history-v1", **identity,
        "as_of": cutoff.isoformat(), "target_game_id": target_game_id,
        "target_start": target.isoformat(), "target_participation": None,
        "input_count": input_count, "history_count": len(history),
        "history": history, "statistics": summaries,
        "historical_participation": {
            "played": sum(r["played"] is True for r in history),
            "did_not_play": sum(r["played"] is False for r in history),
            "unknown": sum(r["played"] is None for r in history),
        },
        "rejected": rejected,
        "rejection_counts": dict(sorted(Counter(r["reason"] for r in rejected).items())),
        "production_supported": False,
    }
