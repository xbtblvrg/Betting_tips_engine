"""Offline ESPN injury context from the supplied public Parquet archives.

This is an experimental source adapter, not current-event participation approval.
Report dates and injury dates are NOT publication/availability timestamps. A
collector receipt bounds replay availability but does not authenticate its clock.
No network, model fitting, betting-ledger writes or production caller.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

import pyarrow as pa
import pyarrow.parquet as pq

from .participation import ParticipationIdentity
from .player_history import _time

VERSION = "f0016-injury-context-v1"
_URL = re.compile(r"https://github\.com/sportsdataverse/sportsdataverse-data/releases/download/espn_(nba|wnba|nhl|nfl|mlb)_injuries/injuries_(20\d{2})\.parquet")
_HEX = re.compile(r"[a-f0-9]{64}")
_REQUIRED = {"as_of_date", "league", "season", "team_id", "athlete_id", "injury_id",
             "status", "type_id", "type_name"}
_MAX_BODY = 64 * 1024 * 1024
# Exact structured type AND compatible display text are required. No prose NLP,
# fantasy-status inference, injury severity probability or role inference.
_TYPES = {
    0: ("ACTIVE", "active"), 2: ("QUESTIONABLE", "questionable"),
    3: ("DOUBTFUL", "doubtful"), 4: ("OUT", "out"),
    5: ("IR", "injured_reserve"), 6: ("DAYTODAY", "day_to_day"),
    10: ("15DAYIL", "15_day_il"), 11: ("60DAYIL", "60_day_il"),
    13: ("7DAYIL", "7_day_il"), 14: ("10DAYIL", "10_day_il"),
    15: ("BEREAVEMENT", "bereavement"), 16: ("PATERNITY", "paternity"),
    17: ("SUSPENSION", "suspension"),
}
_BASEBALL_ONLY = {10, 11, 13, 14, 15, 16}


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _json(body):
    return json.loads(body, object_pairs_hook=_pairs)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class InjuryRow:
    index: int
    player_id: str | None
    team_id: str | None
    report_date: date | None
    status: str | None
    reasons: tuple[str, ...]
    raw_json: str


@dataclass(frozen=True)
class InjurySnapshot:
    league: str
    season: int
    source_url: str
    source_sha256: str
    metadata_sha256: str
    observed_at: datetime
    verification: str
    rows: tuple[InjuryRow, ...]


@dataclass(frozen=True)
class InjuryBundle:
    manifest_sha256: str
    snapshots: tuple[InjurySnapshot, ...]
    # JSON strings keep source failures immutable and serializable.
    source_issues: tuple[str, ...]


def decode_injury_archive(body: bytes, metadata_body: bytes, *, verification="unverified") -> InjurySnapshot:
    """Decode native bytes with the collector's existing seven-field receipt.

    All rows survive decoding, including malformed identities and status fields.
    Such rows cannot resolve a status; their reasons remain in the replay audit.
    """
    if verification not in ("fixture", "unverified", "archived_observation"):
        raise ValueError("invalid_verification_label")
    if not isinstance(body, bytes) or not 0 < len(body) <= _MAX_BODY:
        raise ValueError("invalid_body_size")
    if not isinstance(metadata_body, bytes) or len(metadata_body) > 64 * 1024:
        raise ValueError("invalid_metadata_size")
    meta = _json(metadata_body)
    if not isinstance(meta, dict) or set(meta) != {"url", "fetched_at", "epoch", "sha256", "blob", "bytes", "http_status"}:
        raise ValueError("invalid_collector_receipt")
    match = _URL.fullmatch(meta["url"]) if isinstance(meta["url"], str) else None
    if match is None:
        raise ValueError("unsupported_injury_source")
    if (meta["sha256"] != _sha(body) or meta["blob"] != _sha(body) + ".data"
            or type(meta["bytes"]) is not int or meta["bytes"] != len(body)):
        raise ValueError("source_hash_or_length_mismatch")
    if type(meta["http_status"]) is not int or meta["http_status"] != 200:
        raise ValueError("unsuccessful_source_response")
    observed = _time(meta["fetched_at"])
    if (type(meta["epoch"]) not in (int, float) or not math.isfinite(meta["epoch"])
            or abs(meta["epoch"] - observed.timestamp()) > 1):
        raise ValueError("collector_clock_mismatch")
    league, season = match[1].upper(), int(match[2])
    parquet = pq.ParquetFile(pa.BufferReader(body))
    if (parquet.metadata.num_rows > 500_000 or parquet.metadata.num_columns > 64
            or sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.num_row_groups)) > 256 * 1024 * 1024):
        raise ValueError("decoded_source_too_large")
    names = parquet.schema_arrow.names
    if len(set(names)) != len(names) or not _REQUIRED <= set(names):
        raise ValueError("unsupported_injury_schema")
    rows = []
    for index, row in enumerate(parquet.read().to_pylist()):
        reasons = []
        if row["league"] != league.lower() or type(row["season"]) is not int or row["season"] != season:
            reasons.append("league_or_season_mismatch")
        ids = {}
        for field in ("athlete_id", "team_id"):
            value = row[field]
            ids[field] = str(value) if type(value) is int and value > 0 else None
            if ids[field] is None:
                reasons.append("invalid_" + field)
        # Real MLB observations include negative injury IDs. They are report
        # identifiers, never player identifiers or globally unique event IDs.
        if type(row["injury_id"]) is not int:
            reasons.append("invalid_injury_id")
        report_date = row["as_of_date"] if type(row["as_of_date"]) is date else None
        if report_date is None:
            reasons.append("invalid_report_date")
        elif report_date > observed.date():
            # Conservative date consistency check, not an invented time of day.
            reasons.append("report_date_after_observation_date")
        status = None
        type_id = row["type_id"]
        spec = _TYPES.get(type_id) if type(type_id) is int else None
        if spec is None:
            reasons.append("unknown_status_type")
        elif type_id in _BASEBALL_ONLY and league != "MLB":
            reasons.append("status_not_valid_for_league")
        else:
            expected_name, normalized = spec
            display = row["status"]
            display = display.lower().replace("-", "_").replace(" ", "_") if isinstance(display, str) else None
            if row["type_name"] != "INJURY_STATUS_" + expected_name or display != normalized:
                reasons.append("conflicting_status_fields")
            else:
                status = normalized
        # Arrow date values are retained as dates; no midnight/publication time
        # is invented. Unrecognized fields and source prose are inert evidence.
        raw = {k: v.isoformat() if isinstance(v, (date, datetime)) else v for k, v in row.items()}
        rows.append(InjuryRow(index, ids["athlete_id"], ids["team_id"], report_date,
                              status, tuple(reasons), _canonical(raw)))
    return InjurySnapshot(league, season, meta["url"], _sha(body), _sha(metadata_body),
                          observed, verification, tuple(rows))


def _read(root, relative, limit):
    path = root / relative
    if (root.is_symlink() or any(p.is_symlink() for p in path.parents)
            or path.is_symlink() or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError("unsafe_archive_path")
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("archive_file_too_large")
    return body


def load_injury_bundle(root: str | Path, *, manifest_sha256: str) -> InjuryBundle:
    """Read a pinned copied bundle only; one damaged league cannot hide others.

    Pinning checks byte identity, not source authority or authenticated capture
    time. Source failures remain failures, not an empty valid injury report.
    """
    root = Path(root)
    body = _read(root, "manifest.json", 2 * 1024 * 1024)
    if not isinstance(manifest_sha256, str) or _HEX.fullmatch(manifest_sha256) is None or _sha(body) != manifest_sha256:
        raise ValueError("manifest_hash_mismatch")
    manifest = _json(body)
    if manifest.get("schema") != "public-development-sports-evidence-v1":
        raise ValueError("unsupported_bundle_schema")
    snapshots, issues = [], []
    for source in manifest["sources"]:
        if source.get("kind") != "injuries":
            continue
        try:
            if source.get("state") != "copied":
                raise ValueError("source_not_copied")
            contents = []
            for field, suffix, limit in (("blob_path", "data", _MAX_BODY), ("metadata_path", "json", 64 * 1024)):
                relative = source[field]
                if not isinstance(relative, str) or re.fullmatch(r"raw/[a-f0-9]{64}\." + suffix, relative) is None:
                    raise ValueError("invalid_archive_reference")
                content = _read(root, relative, limit)
                receipt = manifest["files"][relative]
                if receipt != {"sha256": _sha(content), "bytes": len(content)}:
                    raise ValueError("bundle_file_changed")
                contents.append(content)
            snapshot = decode_injury_archive(*contents, verification="archived_observation")
            expected = {"league": snapshot.league, "season": snapshot.season, "url": snapshot.source_url,
                        "source_sha256": snapshot.source_sha256, "metadata_sha256": snapshot.metadata_sha256,
                        "blob_path": "raw/" + snapshot.source_sha256 + ".data",
                        "metadata_path": "raw/" + _sha(snapshot.source_url.encode()) + ".json"}
            if any(source.get(k) != v for k, v in expected.items()):
                raise ValueError("bundle_source_identity_mismatch")
            if any(_time(source[k]) != snapshot.observed_at for k in ("fetched_at", "available_at")):
                raise ValueError("bundle_source_clock_mismatch")
            snapshots.append(snapshot)
        except (OSError, ValueError, TypeError, KeyError, pa.ArrowException) as exc:
            issues.append(_canonical({"league": source.get("league"), "url": source.get("url"),
                                      "reason": str(exc)[:240]}))
    return InjuryBundle(manifest_sha256, tuple(snapshots), tuple(issues))


def assess_injury_context(bundle: InjuryBundle, identity: ParticipationIdentity, *,
                          as_of: str | datetime, max_age: timedelta) -> dict:
    """Replay injury context for an exact ESPN athlete/team; never approve play.

    max_age is an explicit experimental context policy, not quote freshness.
    Latest *reported date* wins over older dates within eligible observations;
    conflicting latest rows are quarantined, regardless of source/input order.
    Invalid latest rows cannot expose an older optimistic status. Unknown dates
    or player IDs conservatively prevent resolution for the queried league.
    """
    if not isinstance(bundle, InjuryBundle) or not isinstance(identity, ParticipationIdentity):
        raise ValueError("typed_bundle_and_identity_required")
    cutoff = _time(as_of)
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise ValueError("positive_max_age_required")
    reasons, reviews, candidates = [], [], []
    league_issues = [json.loads(x) for x in sorted(bundle.source_issues)
                     if json.loads(x).get("league") in (identity.league, None)]
    supported = identity.league in ("NBA", "WNBA", "NHL", "NFL", "MLB")
    if not supported:
        reasons.append("unsupported_league")
    if identity.identity_namespace != "espn":
        reasons.append("provider_identity_bridge_required")
    if cutoff >= identity.start:
        reasons.append("event_started")
    if league_issues:
        reasons.append("source_failure")
    seen_source = False
    for snapshot in bundle.snapshots:
        if snapshot.league != identity.league:
            continue
        seen_source = True
        source_reasons = []
        if snapshot.observed_at > cutoff:
            source_reasons.append("not_observed_as_of")
        elif cutoff - snapshot.observed_at >= max_age:
            source_reasons.append("stale_source_observation")
        for row in snapshot.rows:
            if row.player_id not in (None, identity.player_id):
                continue
            excluded = list(source_reasons)
            if not excluded:
                if row.player_id is None:
                    reasons.append("unresolved_source_player_identity")
                elif row.report_date is None:
                    reasons.append("unorderable_report_date")
                else:
                    candidates.append((snapshot, row))
            reviews.append({"source_sha256": snapshot.source_sha256,
                            "metadata_sha256": snapshot.metadata_sha256, "row_index": row.index,
                            "observed_at": snapshot.observed_at.isoformat(),
                            "report_date": row.report_date.isoformat() if row.report_date else None,
                            "status": row.status, "team_id": row.team_id,
                            "source_url": snapshot.source_url, "verification": snapshot.verification,
                            "row_reasons": list(row.reasons), "excluded": excluded,
                            "raw": json.loads(row.raw_json)})
    if not seen_source and not league_issues:
        reasons.append("missing_injury_source")
    latest = max((row.report_date for _, row in candidates), default=None)
    chosen = [(s, r) for s, r in candidates if r.report_date == latest]
    if chosen:
        if any(row.reasons for _, row in chosen):
            reasons.append("invalid_latest_report")
        if any(row.team_id != identity.team_id for _, row in chosen):
            reasons.append("latest_team_mismatch_or_conflict")
        if len({row.status for _, row in chosen}) != 1:
            reasons.append("conflicting_latest_status")
        # A date-only report cannot be given a made-up midnight or timezone.
        # Its age cannot be certified as current for this game's start.
    else:
        reasons.append("no_eligible_player_report")
    status = chosen[0][1].status if chosen and not reasons else None
    result = {"version": VERSION, "bundle_sha256": bundle.manifest_sha256,
              "identity": {k: v.isoformat() if isinstance(v, datetime) else v
                           for k, v in vars(identity).items()},
              "as_of": cutoff.isoformat(), "max_age_seconds": max_age.total_seconds(),
              "reported_status": status, "latest_report_date": latest.isoformat() if latest else None,
              "state": "context_only" if status else "unresolved",
              "reasons": sorted(set(reasons)), "source_issues": league_issues,
              "reviews": sorted(reviews, key=lambda r: (r["source_sha256"], r["metadata_sha256"], r["row_index"])),
              "source_published_at": None, "valid_until": None, "current_participation": None,
              "role": None, "production_supported": False, "current_participation_verified": False,
              "archive_timestamps_independently_authenticated": False,
              "activation_blockers": ["event_specific_participation_missing", "role_evidence_missing",
                                      "source_publication_and_validity_unknown", "live_adapter_unverified"]}
    result["assessment_sha256"] = _sha(_canonical(result).encode())
    return result


def summarize_injury_bundle(bundle: InjuryBundle) -> dict:
    """Count decoded/rejected rows without calling them players or decisions."""
    return {"version": VERSION, "manifest_sha256": bundle.manifest_sha256,
            "sources": [{"league": s.league, "season": s.season, "source_sha256": s.source_sha256,
                         "metadata_sha256": s.metadata_sha256, "observed_at": s.observed_at.isoformat(),
                         "rows": len(s.rows), "valid_rows": sum(not r.reasons for r in s.rows),
                         "row_reasons": dict(Counter(reason for r in s.rows for reason in r.reasons)),
                         "statuses": dict(Counter(r.status for r in s.rows if not r.reasons))}
                        for s in bundle.snapshots],
            "source_issues": [json.loads(x) for x in bundle.source_issues],
            "production_supported": False, "current_participation_verified": False}
