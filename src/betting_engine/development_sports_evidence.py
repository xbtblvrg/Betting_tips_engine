"""Bounded public native-event/injury archive export for isolated development.

No fetching, credentials, ledger, pricing, participation inference or model
approval. Collector observation times are preserved, never backdated to games
or historical dates inside a newly observed snapshot.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
import json
import math
from pathlib import Path
import re

import pyarrow as pa
import pyarrow.parquet as pq

from .io_utils import atomic_bytes
from .workspace_copy import copy_verified

VERSION = "public-development-sports-evidence-v1"
MAX_BYTES = 256 * 1024 * 1024
MAX_SOURCES = 64
MAX_BODY = 64 * 1024 * 1024
META_FIELDS = {"url", "fetched_at", "epoch", "sha256", "blob", "bytes", "http_status"}
LEAGUES = ("NBA", "WNBA", "NHL", "NFL", "MLB")
NFL = re.compile(r"https://github\.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_(20\d{2})\.parquet")
NHL = re.compile(r"https://api-web\.nhle\.com/v1/gamecenter/([1-9]\d*)/play-by-play")
INJURY = re.compile(r"https://github\.com/sportsdataverse/sportsdataverse-data/releases/download/espn_(nba|wnba|nhl|nfl|mlb)_injuries/injuries_(20\d{2})\.parquet")
HEX = re.compile(r"[0-9a-f]{64}")


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_observation_time_required")
    return value.astimezone(timezone.utc)


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate_json_key")
        result[name] = value
    return result


def _json(body):
    return json.loads(body, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def _read(path, parent, limit):
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError("unsafe_source_path")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("source_missing_or_oversize")
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("source_oversize")
    return body


def classify_public_source(url):
    if not isinstance(url, str):
        return None
    match = NFL.fullmatch(url)
    if match:
        return {"league": "NFL", "kind": "native_events", "family": "NFL/events", "season": int(match[1]), "format": "nfl_parquet"}
    match = NHL.fullmatch(url)
    if match:
        return {"league": "NHL", "kind": "native_events", "family": "NHL/events", "provider_game_id": match[1], "format": "nhl_json"}
    match = INJURY.fullmatch(url)
    if match:
        league = match[1].upper()
        return {"league": league, "kind": "injuries", "family": league + "/injuries", "season": int(match[2]), "format": "injury_parquet"}
    return None


def _season_anchor(league, now):
    # End-year convention for the NHL/NBA season beginning in autumn. NFL,
    # WNBA and MLB use the calendar season; future record dates are not evidence.
    return now.year + int(league in ("NBA", "NHL") and now.month >= 7)


def _native_schema(body, kind):
    """Recognize native source type, not completeness or model readiness."""
    if kind["format"] == "nhl_json":
        payload = _json(body)
        if not isinstance(payload, dict) or type(payload.get("id")) is not int or str(payload["id"]) != kind["provider_game_id"]:
            raise ValueError("native_provider_game_identity_mismatch")
        plays = payload.get("plays")
        if not isinstance(plays, list) or not all(isinstance(row, dict) for row in plays):
            raise ValueError("missing_native_event_array")
        home, away = payload.get("homeTeam"), payload.get("awayTeam")
        if (not isinstance(home, dict) or not isinstance(away, dict)
                or any(type(t.get("id")) is not int or t["id"] <= 0 for t in (home, away)) or home["id"] == away["id"]):
            raise ValueError("native_team_identity_invalid")
        return {"row_count": len(plays), "game_state": payload.get("gameState"),
                "provider_game_id": str(payload["id"]), "source_schema_recognized": True,
                "sequence_completeness_verified": False}
    parquet = pq.ParquetFile(pa.BufferReader(body))
    if parquet.metadata.num_rows > 2_000_000 or parquet.metadata.num_columns > 512:
        raise ValueError("native_source_decoded_size_exceeded")
    required = ({"game_id", "play_id", "qtr", "desc", "home_team", "away_team", "home_score", "away_score"}
                if kind["format"] == "nfl_parquet" else
                {"as_of_date", "league", "season", "team_id", "athlete_id", "status"})
    if not required <= set(parquet.schema_arrow.names):
        raise ValueError("native_source_schema_mismatch")
    # Bound league/season metadata to the injury endpoint. Unknown/null IDs and
    # statuses are retained for D21 to reject or interpret, never zero-filled.
    if kind["format"] == "injury_parquet":
        values = parquet.read(columns=["league", "season"]).to_pylist()
        if any(row["league"] != kind["league"].lower() or type(row["season"]) is not int or row["season"] != kind["season"] for row in values):
            raise ValueError("native_injury_league_season_mismatch")
    return {"row_count": parquet.metadata.num_rows, "column_count": parquet.metadata.num_columns,
            "source_schema_recognized": True, "sequence_completeness_verified": False,
            "current_participation_verified": False}


def _verified_source(path, raw, *, now):
    metadata_bytes = _read(path, raw, 64 * 1024)
    meta = _json(metadata_bytes)
    if not isinstance(meta, dict) or set(meta) != META_FIELDS:
        raise ValueError("unexpected_metadata_fields")
    kind = classify_public_source(meta["url"])
    if kind is None:
        raise ValueError("unsupported_public_source_url")
    if path.name != _sha(meta["url"].encode()) + ".json":
        raise ValueError("metadata_url_hash_mismatch")
    digest = meta["sha256"]
    if not isinstance(digest, str) or HEX.fullmatch(digest) is None or meta["blob"] != digest + ".data":
        raise ValueError("invalid_content_address")
    observed = _time(meta["fetched_at"])
    if observed > now:
        raise ValueError("future_source_observation")
    epoch = meta["epoch"]
    if type(epoch) not in (int, float) or not math.isfinite(epoch) or abs(epoch - observed.timestamp()) > 1:
        raise ValueError("observation_clock_mismatch")
    if type(meta["http_status"]) is not int or meta["http_status"] != 200:
        raise ValueError("unsuccessful_public_response")
    if type(meta["bytes"]) is not int or not 0 < meta["bytes"] <= MAX_BODY:
        raise ValueError("invalid_or_oversize_body_length")
    body = _read(raw / meta["blob"], raw, MAX_BODY)
    if len(body) != meta["bytes"] or _sha(body) != digest:
        raise ValueError("body_hash_or_length_mismatch")
    schema = _native_schema(body, kind)
    return meta, kind, schema, metadata_bytes, body


def export_development_sports_evidence(data_dir, destination, *, now=None,
                                       max_total_bytes=MAX_BYTES, max_sources=MAX_SOURCES,
                                       max_nhl_sources=24):
    """Export allowlisted public bytes into a fresh isolated evidence directory.

    Bounds include payloads plus a reserved 2 MiB for the audit manifest. Complete
    source families may still be unusable for modeling: native adapters must
    establish identity, current participation, corrections and sequence coverage.
    """
    now = _time(now or datetime.now(timezone.utc))
    if (type(max_total_bytes) is not int or not 2 * 1024 * 1024 <= max_total_bytes <= MAX_BYTES
            or type(max_sources) is not int or not 1 <= max_sources <= MAX_SOURCES
            or type(max_nhl_sources) is not int or not 1 <= max_nhl_sources <= MAX_SOURCES):
        raise ValueError("invalid_export_bounds")
    source = Path(data_dir).resolve()
    raw = source / "raw"
    target = Path(destination)
    resolved = target.resolve()
    if raw.is_symlink() or target.is_symlink() or resolved == source or resolved == raw or raw in resolved.parents or resolved in source.parents:
        raise ValueError("unsafe_evidence_directory")
    if target.exists() and any(target.iterdir()):
        raise ValueError("evidence_destination_must_be_empty")
    target.mkdir(parents=True, exist_ok=True)
    (target / "raw").mkdir()
    windows = {"NFL/events": list(range(now.year, now.year - 3, -1))}
    windows.update({league + "/injuries": list(range(_season_anchor(league, now), _season_anchor(league, now) - 3, -1)) for league in LEAGUES})
    groups = {family: [] for family in ["NFL/events", "NHL/events", *(league + "/injuries" for league in LEAGUES)]}
    policy_excluded, unreadable, unrelated = [], 0, 0
    for path in sorted(raw.glob("*.json")):
        try:
            hint = _json(_read(path, raw, 64 * 1024))
            kind = classify_public_source(hint.get("url")) if isinstance(hint, dict) else None
            if kind is None:
                unrelated += 1
                continue
            if "season" in kind and kind["season"] not in windows[kind["family"]]:
                policy_excluded.append({"url": hint["url"], "family": kind["family"], "reason": "outside_current_three_seasons"})
                continue
            groups[kind["family"]].append((path, hint, kind))
        except (OSError, ValueError, TypeError, OverflowError):
            unreadable += 1
    for family, items in groups.items():
        # Latest NHL means newest actual observed archive; deterministic ties.
        def order(item):
            try:
                observed = _time(item[1].get("fetched_at"))
                stamp = observed.timestamp() if observed <= now else float("-inf")
            except (ValueError, TypeError, OverflowError):
                stamp = float("-inf")
            return stamp, item[2].get("season", 0), item[0].name
        items.sort(key=order, reverse=True)
        if family == "NHL/events":
            for _, hint, _ in items[max_nhl_sources:]:
                policy_excluded.append({"url": hint["url"], "family": family, "reason": "nhl_latest_source_limit"})
            del items[max_nhl_sources:]
    ordered = [item for batch in zip_longest(*groups.values()) for item in batch if item is not None]
    reviews, files, spent, copied = [], {}, 0, 0
    payload_limit = max_total_bytes - 2 * 1024 * 1024
    for path, hint, original_kind in ordered:
        review = {"url": hint["url"], **original_kind, "state": "excluded"}
        reviews.append(review)
        try:
            if copied >= max_sources:
                raise ValueError("source_budget_exhausted")
            meta, kind, schema, metadata_bytes, body = _verified_source(path, raw, now=now)
            if kind != original_kind:
                raise ValueError("source_identity_changed_during_export")
            paths = {"raw/" + path.name: metadata_bytes, "raw/" + meta["blob"]: body}
            increment = sum(len(content) for name, content in paths.items() if name not in files)
            if spent + increment > payload_limit:
                raise ValueError("byte_budget_exhausted")
            for name, content in paths.items():
                if name not in files:
                    copy_verified(raw / Path(name).name, target / name, content)
                    files[name] = {"sha256": _sha(content), "bytes": len(content)}
                    spent += len(content)
            copied += 1
            review.update(state="copied", source_sha256=meta["sha256"], metadata_sha256=_sha(metadata_bytes),
                          metadata_path="raw/" + path.name, blob_path="raw/" + meta["blob"],
                          fetched_at=meta["fetched_at"], available_at=meta["fetched_at"],
                          availability_basis="archived_observation", source_published_at=None,
                          archive_timestamp_independently_authenticated=False, bytes=increment, **schema)
        except (OSError, ValueError, TypeError, KeyError, OverflowError, pa.ArrowException) as exc:
            review["reason"] = str(exc)[:240]
    coverage = {}
    for family in groups:
        items = [r for r in reviews if r["family"] == family]
        good = [r for r in items if r["state"] == "copied"]
        missing = sorted(set(windows.get(family, [])) - {r["season"] for r in good if "season" in r}, reverse=True)
        coverage[family] = {"state": "missing" if not good else "partial" if missing or len(good) != len(items) else "exported",
                            "observed_sources": len(items), "copied_sources": len(good),
                            "expected_seasons": windows.get(family), "missing_seasons": missing,
                            "exclusion_reasons": dict(Counter(r["reason"] for r in items if r["state"] != "copied"))}
    report = {"schema": VERSION, "created_at": now.isoformat(),
              "state": "missing" if not copied else "partial" if any(row["state"] != "exported" for row in coverage.values()) else "exported",
              "copied_sources": copied, "copied_payload_bytes": spent, "files": files,
              "sources": reviews, "coverage": coverage, "policy_excluded": policy_excluded,
              "unrelated_metadata_ignored": unrelated, "unreadable_metadata_count": unreadable,
              "bounds": {"max_total_bytes": max_total_bytes, "max_sources": max_sources, "max_nhl_sources": max_nhl_sources,
                         "manifest_reserved_bytes": 2 * 1024 * 1024},
              "production_supported": False, "sequence_completeness_verified": False,
              "current_participation_verified": False, "historical_point_in_time_backtest_ready": False,
              "odds_used": False, "source_version": "exact_collector_observation",
              "next_dependencies": ["D20 native event normalization and completeness/identity/correction validation",
                                    "D21 exact provider/Stake identity bridge and current-game participation evidence"]}
    manifest_bytes = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(manifest_bytes) > 2 * 1024 * 1024:
        raise ValueError("audit_manifest_size_exceeded")
    atomic_bytes(target / "manifest.json", manifest_bytes)
    return report
