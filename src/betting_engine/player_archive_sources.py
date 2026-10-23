"""Hash-bound, offline player-history refresh from the collector's raw archive.

Reads exact public response bytes, never normalized player statistics or odds.
Collector observation times are local evidence, not provider publication times.
This produces D01 inputs, not a player model, current lineup, or prop approval.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo
from itertools import zip_longest

import pyarrow as pa
import pyarrow.parquet as pq

from .io_utils import atomic_bytes, atomic_json
from .player_archive import PlayerArchive, decode_nhl_archive
from .player_data import BASKETBALL, BASEBALL, FOOTBALL, HOCKEY
from .player_history import _identifier, _json_object, _time, build_player_history

VERSION = "verified-player-archive-v1"
LEAGUES = ("NBA", "WNBA", "NHL", "NFL", "MLB")
NAMESPACES = {"NBA": "espn", "WNBA": "espn", "NHL": "nhl", "NFL": "nflverse-gsis", "MLB": "mlbam"}
STAT_SCOPES = {league: "full_game_including_overtime" for league in ("NBA", "WNBA", "NFL")} | {
    "NHL": "full_game_including_overtime_excluding_shootout", "MLB": "full_game_including_extra_innings"}
GAME_FIELDS = ("id", "league", "start", "season", "home_id", "away_id", "home_score", "away_score", "state", "source", "fetched_at", "game_type")
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_ROWS = 500_000
_HEX = re.compile(r"[0-9a-f]{64}")
_SD = re.compile(r"https://github\.com/sportsdataverse/sportsdataverse-data/releases/download/(espn_nba|espn_wnba|nhl)_player_boxscores/player_box_(20\d{2})\.parquet")
_NFL = re.compile(r"https://github\.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_(20\d{2})\.parquet")
_NHL = re.compile(r"https://api-web\.nhle\.com/v1/gamecenter/([1-9]\d*)/boxscore")
_MLB = re.compile(r"https://statsapi\.mlb\.com/api/v1/people\?personIds=([1-9]\d*(?:,[1-9]\d*)*)&hydrate=stats\(group=\[hitting,pitching\],type=\[gameLog\],season=(20\d{2})\)")


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _read(path: Path, parent: Path, limit: int):
    # No archive-supplied path can escape its dedicated directory, including symlinks.
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError("unsafe_archive_path")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("archive_missing_or_oversize")
    with path.open("rb") as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise ValueError("archive_oversize")
    return body


def _json(body):
    value = json.loads(body, object_pairs_hook=_json_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
    return value


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError("invalid_object_schema")
    return value


def _array(value):
    if not isinstance(value, list):
        raise ValueError("invalid_array_schema")
    return value


def source_kind(url):
    """Exact collector endpoints; URLs are evidence identifiers, never fetched here."""
    if not isinstance(url, str):
        return None
    match = _SD.fullmatch(url)
    if match:
        return {"league": {"espn_nba": "NBA", "espn_wnba": "WNBA", "nhl": "NHL"}[match[1]],
                "season": int(match[2]), "format": "parquet", "provider": "sportsdataverse"}
    match = _NFL.fullmatch(url)
    if match:
        return {"league": "NFL", "season": int(match[1]), "format": "parquet", "provider": "nflverse"}
    match = _NHL.fullmatch(url)
    if match:
        return {"league": "NHL", "game_id": "NHL:" + match[1], "format": "nhl_json", "provider": "nhl_official"}
    match = _MLB.fullmatch(url)
    if match:
        ids = match[1].split(",")
        if len(ids) != len(set(ids)) or len(ids) > 100:
            return None
        return {"league": "MLB", "season": int(match[2]), "format": "mlb_json", "provider": "mlb_official", "player_ids": ids}
    return None


def read_collector_archive(raw_dir, metadata_path, *, now):
    """Verify pointer, body, length, timestamp and URL identity without network I/O."""
    raw = Path(raw_dir)
    path = Path(metadata_path)
    metadata_bytes = _read(path, raw, 64 * 1024)
    meta = _json(metadata_bytes)
    if not isinstance(meta, dict) or source_kind(meta.get("url")) is None:
        raise ValueError("unsupported_source_url")
    if path.name != _sha(meta["url"].encode()) + ".json":
        raise ValueError("metadata_url_hash_mismatch")
    digest = meta.get("sha256")
    if not isinstance(digest, str) or not _HEX.fullmatch(digest) or meta.get("blob") != digest + ".data":
        raise ValueError("invalid_content_address")
    stamp = _time(meta.get("fetched_at"))
    if stamp > _time(now):
        raise ValueError("future_archive_observation")
    epoch = meta.get("epoch")
    if type(epoch) not in (int, float) or not math.isfinite(epoch) or abs(epoch - stamp.timestamp()) > 1:
        raise ValueError("archive_clock_mismatch")
    if type(meta.get("http_status")) is not int or meta["http_status"] != 200:
        raise ValueError("unsuccessful_archive_response")
    body = _read(raw / meta["blob"], raw, MAX_SOURCE_BYTES)
    if type(meta.get("bytes")) is not int or meta["bytes"] != len(body) or _sha(body) != digest:
        raise ValueError("archive_body_hash_or_length_mismatch")
    reference = "raw/" + path.name + "#sha256=" + _sha(metadata_bytes)
    return body, meta, {"reference": reference, "metadata_path": "raw/" + path.name,
                        "metadata_sha256": _sha(metadata_bytes), "metadata": meta, "metadata_json": metadata_bytes.decode("utf-8"),
                        "body_path": "raw/" + meta["blob"], "body_sha256": digest,
                        "collector_archive_verified": True, "provider_publication_at": None,
                        "timestamp_independently_authenticated": False}


def _id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("invalid_numeric_provider_id")
    return str(value)


def _exact(value):
    if not _identifier(value):
        raise ValueError("invalid_exact_identity")
    return value


def _bool(value):
    if value is not None and type(value) is not bool:
        raise ValueError("invalid_nullable_boolean")
    return value


def _numeric(value, *, negative=False, fractional=False):
    if value is None:
        return None
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (not negative and value < 0) or (not fractional and value != int(value))):
        raise ValueError("invalid_statistic")
    return value


def _stats(row, fields, league):
    stats = {}
    for name in fields:
        if name in row:
            stats[name] = _numeric(row[name], negative=name in ("plus_minus", "passing_yards", "rushing_yards", "receiving_yards"),
                                   fractional=league == "NFL" and name == "def_sacks")
    if not stats:
        raise ValueError("missing_statistics")
    return stats


def _minutes(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,3}:[0-5]\d", value):
        raise ValueError("invalid_time_on_ice")
    minute, second = map(int, value.split(":"))
    return minute + second / 60


def _game(games, league, event, meta, now):
    game = games.get(league + ":" + event)
    if game is None:
        raise ValueError("game_not_in_independent_schedule")
    if game.get("state") != "final" or game.get("league") != league:
        raise ValueError("game_not_final")
    start, fetched = _time(game.get("start")), _time(game.get("fetched_at"))
    if start >= _time(meta["fetched_at"]) or fetched <= start or fetched > now:
        raise ValueError("invalid_game_evidence_time")
    for key in ("home_id", "away_id"):
        _exact(game.get(key))
    if game["home_id"] == game["away_id"]:
        raise ValueError("same_opponent")
    for key in ("home_score", "away_score"):
        if _numeric(game.get(key)) is None:
            raise ValueError("missing_final_score")
    return game


def _observation(game, pid, name, team, stats, meta, evidence, *, group="player", minutes=None, starter=None, played=None):
    if team not in (game["home_id"], game["away_id"]):
        raise ValueError("team_mismatch")
    # Final schedule identity can itself be a later observed correction. Never
    # make joined evidence available earlier than either actual source observation.
    available = max(_time(meta["fetched_at"]), _time(game["fetched_at"]))
    identity = {key: game[key] for key in GAME_FIELDS}
    return {"league": game["league"], "game_id": game["id"], "season": game["season"],
            "start": _time(game["start"]).isoformat(), "player_id": _exact(pid), "player_name": _exact(name),
            "team_id": team, "opponent_id": game["away_id"] if team == game["home_id"] else game["home_id"],
            "home": team == game["home_id"], "identity_namespace": NAMESPACES[game["league"]],
            "stat_group": group, "scope": "player_full_game", "stat_scope": STAT_SCOPES[game["league"]],
            "minutes": minutes, "starter": starter, "played": played,
            "stats": json.dumps(stats, sort_keys=True, allow_nan=False), "source_url": meta["url"],
            "source_sha256": meta["sha256"], "source_fetched_at": _time(meta["fetched_at"]).isoformat(),
            "source_published_at": None, "available_at": available.isoformat(), "availability_basis": "archived_observation",
            "availability_reference": evidence["reference"], "game_identity_sha256": _sha(_bytes(identity)),
            "game_identity_fetched_at": _time(game["fetched_at"]).isoformat(), "target_participation": None}


def decode_parquet_archive(body, meta, evidence, games, *, now):
    kind = source_kind(meta["url"])
    league, year = kind["league"], kind["season"]
    file = pq.ParquetFile(pa.BufferReader(body))
    if file.metadata.num_rows > MAX_SOURCE_ROWS or file.metadata.num_columns > 256:
        raise ValueError("oversize_decoded_source")
    if "game_id" not in file.schema_arrow.names:
        raise ValueError("aggregate_without_game_identity")
    result, rejected = [], Counter()
    for batch in file.iter_batches(batch_size=4096):
        for row in batch.to_pylist():
            try:
                if league == "NFL":
                    event = _exact(row.get("game_id"))
                    if not re.fullmatch(r"20\d{2}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}", event):
                        raise ValueError("invalid_nfl_game_id")
                else:
                    event = _id(row.get("game_id"))
                game = _game(games, league, event, meta, now)
                if game["season"] != year:
                    raise ValueError("season_mismatch")
                minutes = starter = played = None
                if league in ("NBA", "WNBA"):
                    if row.get("season") != year:
                        raise ValueError("season_mismatch")
                    pid, name, team, opponent = _id(row.get("athlete_id")), row.get("athlete_display_name"), _id(row.get("team_id")), _id(row.get("opponent_team_id"))
                    if {team, opponent} != {game["home_id"], game["away_id"]} or team == opponent:
                        raise ValueError("team_or_opponent_mismatch")
                    home = team == game["home_id"]
                    if row.get("home_away") != ("home" if home else "away"):
                        raise ValueError("home_away_mismatch")
                    if (_numeric(row.get("team_score")), _numeric(row.get("opponent_team_score"))) != ((game["home_score"], game["away_score"]) if home else (game["away_score"], game["home_score"])):
                        raise ValueError("final_score_mismatch")
                    if abs((_time(row.get("game_date_time")) - _time(game["start"])).total_seconds()) > 300:
                        raise ValueError("game_start_mismatch")
                    stats = _stats(row, BASKETBALL, league)
                    minutes = _numeric(row.get("minutes"), fractional=True)
                    starter, dnp = _bool(row.get("starter")), _bool(row.get("did_not_play"))
                    if dnp is True and (minutes is not None and minutes > 0 or starter is True):
                        raise ValueError("contradictory_participation")
                    played = False if dnp is True else True if minutes is not None and minutes > 0 else None
                elif league == "NHL":
                    if row.get("season") != (year - 1) * 10000 + year:
                        raise ValueError("season_mismatch")
                    pid, name, team = _id(row.get("player_id")), row.get("player_name"), _exact(row.get("team_abbrev"))
                    if row.get("home_away") != ("home" if team == game["home_id"] else "away"):
                        raise ValueError("home_away_mismatch")
                    # NHL's schedule gameDate is the Eastern calendar day, not
                    # the UTC day of a late-night North American fixture.
                    if row.get("game_date") != _time(game["start"]).astimezone(ZoneInfo("America/New_York")).date().isoformat():
                        raise ValueError("game_date_mismatch")
                    stats = _stats(row, HOCKEY, league)
                    minutes, starter = _minutes(row.get("toi")), _bool(row.get("starter"))
                    played = True if minutes is not None and minutes > 0 else None
                else:
                    pid = _exact(row.get("player_id"))
                    if not re.fullmatch(r"00-\d{7}", pid):
                        raise ValueError("invalid_gsis_player_id")
                    if row.get("season") != year or int(event[:4]) != year or row.get("week") != int(event[5:7]):
                        raise ValueError("season_week_mismatch")
                    if row.get("season_type") != ("POST" if game.get("game_type") == "playoff" else "REG"):
                        raise ValueError("season_type_mismatch")
                    aliases = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}
                    team = aliases.get(row.get("team"), row.get("team"))
                    opponent = aliases.get(row.get("opponent_team"), row.get("opponent_team"))
                    if {team, opponent} != {game["home_id"], game["away_id"]} or team == opponent:
                        raise ValueError("team_or_opponent_mismatch")
                    name = row.get("player_display_name") or row.get("player_name")
                    stats = _stats(row, FOOTBALL, league)
                    # NFL weekly counting stats do not establish snaps or DNP.
                result.append(_observation(game, pid, name, team, stats, meta, evidence, minutes=minutes, starter=starter, played=played))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                rejected[str(exc)] += 1
    return result, rejected


def decode_mlb_archive(body, meta, evidence, games, *, now):
    kind = source_kind(meta["url"])
    payload = _json(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("people"), list):
        raise ValueError("invalid_mlb_people_schema")
    result, rejected, seen = [], Counter(), set()
    for person in payload["people"]:
        person = _mapping(person)
        pid = _id(person.get("id"))
        if pid not in kind["player_ids"] or pid in seen:
            raise ValueError("duplicate_or_unrequested_player")
        seen.add(pid)
        for group in _array(person.get("stats", [])):
            group = _mapping(group)
            stat_group = _mapping(group.get("group"))["displayName"]
            if stat_group not in ("hitting", "pitching") or _mapping(group.get("type")).get("displayName") != "gameLog":
                raise ValueError("unsupported_mlb_stat_scope")
            for split in _array(group.get("splits", [])):
                try:
                    split = _mapping(split)
                    if _mapping(split.get("sport")).get("id") != 1 or _id(_mapping(split.get("player")).get("id")) != pid:
                        raise ValueError("sport_or_player_mismatch")
                    game = _game(games, "MLB", _id(_mapping(split.get("game")).get("gamePk")), meta, now)
                    if split.get("season") != str(kind["season"]) or game["season"] != kind["season"]:
                        raise ValueError("season_mismatch")
                    team, opponent = _id(_mapping(split.get("team")).get("id")), _id(_mapping(split.get("opponent")).get("id"))
                    if {team, opponent} != {game["home_id"], game["away_id"]} or team == opponent:
                        raise ValueError("team_or_opponent_mismatch")
                    if type(split.get("isHome")) is not bool or split["isHome"] != (team == game["home_id"]):
                        raise ValueError("home_away_mismatch")
                    stats = _stats(_mapping(split.get("stat")), BASEBALL, "MLB")
                    # Baseball decimal notation is outs, never a floating inning duration.
                    innings = split["stat"].get("inningsPitched")
                    if stat_group == "pitching" and innings is not None:
                        if not isinstance(innings, str) or not re.fullmatch(r"\d+\.[0-2]", innings):
                            raise ValueError("invalid_baseball_innings")
                        whole, extra = map(int, innings.split("."))
                        if stats.get("outs") != whole * 3 + extra:
                            raise ValueError("innings_outs_mismatch")
                    started = stats.get("gamesStarted")
                    if started is not None and started not in (0, 1):
                        raise ValueError("invalid_games_started")
                    played_count = _numeric(split["stat"].get("gamesPlayed"))
                    if played_count is not None and played_count not in (0, 1):
                        raise ValueError("invalid_games_played")
                    # A zero-exposure line alone is not a prop settlement DNP.
                    played = True if played_count == 1 else None
                    result.append(_observation(game, pid, person.get("fullName"), team, stats, meta, evidence,
                                               group=stat_group, starter=None if started is None else bool(started), played=played))
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    rejected[str(exc)] += 1
    if set(kind["player_ids"]) - seen:
        rejected["requested_player_missing"] += len(set(kind["player_ids"]) - seen)
    return result, rejected


def decode_official_nhl(body, meta, evidence, games, *, now):
    decoded = decode_nhl_archive(PlayerArchive(body, meta["sha256"], meta["url"], meta["fetched_at"], evidence["reference"], "archived_observation"))
    event = source_kind(meta["url"])["game_id"].split(":", 1)[1]
    game = _game(games, "NHL", event, meta, now)
    payload = _json(body)
    if ((payload["homeTeam"]["abbrev"], payload["awayTeam"]["abbrev"], payload["homeTeam"]["score"], payload["awayTeam"]["score"])
            != (game["home_id"], game["away_id"], game["home_score"], game["away_score"])):
        raise ValueError("official_game_identity_mismatch")
    if abs((_time(payload["startTimeUTC"]) - _time(game["start"])).total_seconds()) > 300:
        raise ValueError("game_start_mismatch")
    rows = [_observation(game, row["player_id"], row["player_name"], row["team_id"], _json(row["stats"]), meta, evidence,
                         minutes=row["minutes"], starter=row["starter"], played=row["played"]) for row in decoded]
    return rows, Counter()


def _schema():
    bools = {"home", "starter", "played", "target_participation"}
    fields = ("league game_id season start player_id player_name team_id opponent_id home identity_namespace stat_group scope stat_scope minutes starter played stats source_url source_sha256 source_fetched_at source_published_at available_at availability_basis availability_reference game_identity_sha256 game_identity_fetched_at target_participation").split()
    return pa.schema([(name, pa.int64() if name == "season" else pa.float64() if name == "minutes" else pa.bool_() if name in bools else pa.string()) for name in fields])


def refresh_verified_player_history(data_dir, output_dir, *, leagues=LEAGUES, seasons=None, now=None):
    """Run a standalone local job; no production data writes, fetching or prop enable.

    Immutable run directory is published by one atomic latest pointer only after
    all source/row checks finish. Readers verify hashes. Partial coverage remains
    explicit and old runs remain available for audit; no truthy zero-row success.
    """
    now = _time(now or datetime.now(timezone.utc))
    leagues = tuple(leagues)
    if not leagues or len(set(leagues)) != len(leagues) or any(v not in LEAGUES for v in leagues):
        raise ValueError("unsupported_or_duplicate_league")
    seasons = None if seasons is None else set(seasons)
    if seasons is not None and (not seasons or any(type(y) is not int or not 2000 <= y <= 2099 for y in seasons)):
        raise ValueError("invalid_seasons")
    data, output = Path(data_dir).resolve(), Path(output_dir).resolve()
    # The caller may use a dedicated subdirectory of data, never a source directory.
    if output == data or output == data / "raw" or output == data / "players" or output in data.parents:
        raise ValueError("output_overlaps_source")
    raw = data / "raw"
    if raw.is_symlink():
        raise ValueError("unsafe_raw_directory")
    game_bytes = _read(data / "games.json", data, 256 * 1024 * 1024)
    decoded_games = _json(game_bytes)
    if not isinstance(decoded_games, list):
        raise ValueError("invalid_game_snapshot")
    games = {}
    for game in decoded_games:
        game = _mapping(game)
        if game.get("league") in leagues and (seasons is None or game.get("season") in seasons):
            if game.get("id") in games:
                raise ValueError("duplicate_game_identity")
            games[game["id"]] = game
    rows, reviews = [], []
    for path in sorted(raw.glob("*.json")):
        review = None
        try:
            # Discovery reads only small public metadata. Unsupported sources are
            # unrelated inputs, never advertised as player coverage.
            hint = _json(_read(path, raw, 64 * 1024))
            kind = source_kind(hint.get("url")) if isinstance(hint, dict) else None
            if kind is None or kind["league"] not in leagues:
                continue
            year = kind.get("season") or games.get(kind.get("game_id"), {}).get("season")
            if seasons is not None and year not in seasons:
                continue
            review = {"url": hint["url"], **kind, "accepted_rows": 0, "rejection_counts": {}, "state": "rejected"}
            reviews.append(review)
            body, meta, evidence = read_collector_archive(raw, path, now=now)
            review.update(evidence)
            decoder = {"parquet": decode_parquet_archive, "nhl_json": decode_official_nhl, "mlb_json": decode_mlb_archive}[kind["format"]]
            batch, rejected = decoder(body, meta, evidence, games, now=now)
            rows.extend(batch)
            review.update(accepted_rows=len(batch), rejection_counts=dict(rejected), state="partial" if rejected else "decoded" if batch else "empty")
        except (ValueError, TypeError, KeyError, OSError, OverflowError, pa.ArrowException) as exc:
            # Malformed metadata may be unclassifiable: record the exact pointer,
            # never silently claim every archive was interpreted.
            if review is None:
                review = {"metadata_path": "raw/" + path.name, "accepted_rows": 0, "rejection_counts": {}}
                reviews.append(review)
            review.update(state="rejected", error=str(exc)[:240])

    # All duplicate exact player/game/stat-group contracts are quarantined. A
    # corrected body or another provider is not an extra historical observation.
    by_identity = defaultdict(list)
    for row in rows:
        by_identity[(row["league"], row["game_id"], row["player_id"], row["stat_group"])].append(row)
    duplicates = [identity for identity, values in by_identity.items() if len(values) > 1]
    rows = [values[0] for values in by_identity.values() if len(values) == 1]
    rows.sort(key=lambda row: (row["league"], row["game_id"], row["player_id"], row["stat_group"]))
    used_games = {row["game_id"] for row in rows}
    snapshot_games = [{key: game[key] for key in GAME_FIELDS}
                      for game in games.values() if game["id"] in used_games]
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    folder = output / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    files = {}
    def write(name, body):
        atomic_bytes(folder / name, body)
        files[name] = {"sha256": _sha(body), "bytes": len(body)}
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=_schema()), sink, compression="zstd")
    write("observations.parquet", sink.getvalue().to_pybytes())
    write("sources.json", _bytes({"sources": reviews}))
    write("game-identities.json", _bytes(sorted(snapshot_games, key=lambda g: g["id"])))
    write("quarantine.json", _bytes({"duplicate_identities": duplicates}))
    coverage = {}
    for league in leagues:
        part = [row for row in rows if row["league"] == league]
        matched_sources = [review for review in reviews if review.get("league") == league]
        rejected_rows = sum(sum(review["rejection_counts"].values()) for review in matched_sources)
        coverage[league] = {"observations": len(part), "players": len({row["player_id"] for row in part}),
                            "games": len({row["game_id"] for row in part}), "sources": len(matched_sources),
                            "source_errors": sum(review["state"] == "rejected" for review in matched_sources),
                            "rejected_rows": rejected_rows, "identity_namespace": NAMESPACES[league],
                            "unknown_played": sum(row["played"] is None for row in part),
                            "unknown_starter": sum(row["starter"] is None for row in part),
                            "available_at_min": min((row["available_at"] for row in part), default=None),
                            "available_at_max": max((row["available_at"] for row in part), default=None)}
    incomplete = any(not entry["observations"] or entry["source_errors"] or entry["rejected_rows"] for entry in coverage.values()) or bool(duplicates) or any(r.get("state") == "rejected" for r in reviews)
    manifest = {"adapter_version": VERSION, "run_id": run_id, "created_at": now.isoformat(),
                "adapter_source_sha256": {name: _sha(Path(__file__).with_name(name).read_bytes())
                                          for name in ("player_archive_sources.py", "player_archive.py", "player_history.py")},
                "state": "partial" if incomplete else "archive_ready", "observation_count": len(rows),
                "game_snapshot_sha256": _sha(game_bytes), "leagues": coverage, "files": files,
                "duplicate_identity_count": len(duplicates), "source_count": len(reviews),
                "source_error_count": sum(r.get("state") == "rejected" for r in reviews),
                "collector_archives_verified": True, "archive_timestamps_independently_authenticated": False,
                "historical_point_in_time_backtest_ready": False, "current_participation": "unknown",
                "production_props_enabled": False, "odds_used": False,
                "unimplemented_leagues": ["NCAAF", "NCAAM", "NCAAW", "other NCAA sports"],
                "next_dependencies": ["Exact Stake-to-provider player identity registry", "Verified current-game role and participation", "Chronological player-model calibration and validation", "Exact prop settlement and independent evidence reproduction"]}
    manifest_bytes = _bytes(manifest)
    atomic_bytes(folder / "manifest.json", manifest_bytes)
    atomic_json(output / "latest.json", {"run_id": run_id, "manifest_sha256": _sha(manifest_bytes)})
    return manifest


def _load_history_artifacts(output_dir, snapshot=None):
    root = Path(output_dir).resolve()
    if snapshot is not None:
        if not isinstance(snapshot, dict):
            raise ValueError("invalid_snapshot_reference")
        pointer = snapshot
    else:
        pointer = _json(_read(root / "latest.json", root, 8192))
    run_id = pointer.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{12}", run_id) or ".." in run_id:
        raise ValueError("invalid_run_reference")
    manifest_sha = pointer.get("manifest_sha256")
    if not isinstance(manifest_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha):
        raise ValueError("invalid_snapshot_reference")
    folder = root / "runs" / run_id
    if (root / "runs").is_symlink() or folder.is_symlink():
        raise ValueError("unsafe_run_directory")
    manifest_bytes = _read(folder / "manifest.json", folder, 1024 * 1024)
    if _sha(manifest_bytes) != pointer.get("manifest_sha256"):
        raise ValueError("manifest_hash_mismatch")
    manifest = _json(manifest_bytes)
    if manifest.get("adapter_version") != VERSION or manifest.get("run_id") != run_id or manifest.get("production_props_enabled") is not False:
        raise ValueError("invalid_history_manifest")
    required = {"observations.parquet", "sources.json", "game-identities.json", "quarantine.json"}
    if set(manifest.get("files", {})) != required:
        raise ValueError("incomplete_history_artifacts")
    bodies = {}
    for name in required:
        body = _read(folder / name, folder, 256 * 1024 * 1024)
        receipt = manifest["files"][name]
        if len(body) != receipt.get("bytes") or _sha(body) != receipt.get("sha256"):
            raise ValueError("history_artifact_hash_mismatch")
        bodies[name] = body
    return bodies, manifest


def load_verified_player_history(output_dir, snapshot=None):
    """Verify the complete durable receipt before exposing any D01 input rows."""
    bodies, manifest = _load_history_artifacts(output_dir, snapshot=snapshot)
    table = pq.read_table(pa.BufferReader(bodies["observations.parquet"]))
    if len(table) != manifest["observation_count"] or table.schema != _schema():
        raise ValueError("history_row_count_or_schema_mismatch")
    return table.to_pylist(), manifest
    return table.to_pylist(), manifest


def export_verified_player_sources(data_dir, history_dir, destination, *, max_total_bytes=128 * 1024 * 1024, max_sources=256):
    """Bounded public evidence export for a fresh isolated development workspace.

    Output is an adapter input root: raw/<urlhash>.json, raw/<bodyhash>.data,
    games.json and manifest.json. Only exact source versions reviewed by the
    selected history run are exported, even if live metadata pointers advanced.
    Existing output must be empty, so old sources cannot masquerade as this run.
    """
    if type(max_total_bytes) is not int or max_total_bytes < 0 or type(max_sources) is not int or max_sources < 0:
        raise ValueError("invalid_export_bounds")
    bodies, history = _load_history_artifacts(history_dir)
    sources = _json(bodies["sources.json"])["sources"]
    target = Path(destination)
    if target.is_symlink() or (target.exists() and any(target.iterdir())):
        raise ValueError("evidence_destination_must_be_empty")
    target.mkdir(parents=True, exist_ok=True)
    (target / "raw").mkdir()
    raw = Path(data_dir).resolve() / "raw"
    if raw.is_symlink():
        raise ValueError("unsafe_raw_directory")
    groups = [[r for r in sources if r.get("league") == league] for league in LEAGUES]
    # Round robin prevents one prolific league from consuming the bounded export
    # before another league gets even its first source.
    ordered = [r for batch in zip_longest(*groups) for r in batch if r is not None]
    files, reviews, spent, copied = {}, [], 0, 0
    for source in ordered:
        report = {"url": source.get("url"), "league": source["league"], "body_sha256": source.get("body_sha256"), "state": "skipped"}
        reviews.append(report)
        try:
            if source.get("state") not in ("decoded", "partial") or not source.get("accepted_rows"):
                raise ValueError("source_not_accepted_by_history_run")
            metadata_bytes = source.get("metadata_json", "").encode()
            if _sha(metadata_bytes) != source.get("metadata_sha256"):
                raise ValueError("frozen_metadata_hash_mismatch")
            meta = _json(metadata_bytes)
            digest = meta.get("sha256", "")
            if source_kind(meta.get("url")) is None or not _HEX.fullmatch(digest) or meta.get("blob") != digest + ".data":
                raise ValueError("invalid_frozen_source_identity")
            if meta != source.get("metadata") or digest != source.get("body_sha256"):
                raise ValueError("frozen_source_receipt_mismatch")
            names = {"raw/" + _sha(meta["url"].encode()) + ".json": metadata_bytes}
            blob_name = "raw/" + digest + ".data"
            body = _read(raw / meta["blob"], raw, MAX_SOURCE_BYTES)
            if _sha(body) != digest or len(body) != meta.get("bytes"):
                raise ValueError("archive_body_hash_or_length_mismatch")
            names[blob_name] = body
            increment = sum(len(content) for name, content in names.items() if name not in files)
            # Reserve the full game-identity evidence before deciding source room.
            if spent + increment + len(bodies["game-identities.json"]) > max_total_bytes:
                raise ValueError("byte_budget_exhausted")
            if copied >= max_sources:
                raise ValueError("source_budget_exhausted")
            for name, content in names.items():
                if name not in files:
                    atomic_bytes(target / name, content)
                    files[name] = {"sha256": _sha(content), "bytes": len(content)}
            spent += increment
            copied += 1
            report.update(state="copied", bytes=increment, metadata_sha256=_sha(metadata_bytes))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            report["reason"] = str(exc)[:240]
    # The complete small identity snapshot preserves all joins, even when a
    # bounded raw subset is exported. It contains no betting or private data.
    game_body = bodies["game-identities.json"] if copied else b"[]"
    if len(game_body) + spent > max_total_bytes:
        game_body = b""
    if game_body:
        atomic_bytes(target / "games.json", game_body)
        files["games.json"] = {"sha256": _sha(game_body), "bytes": len(game_body)}
        spent += len(game_body)
    league_coverage = {league: {"copied_sources": sum(r["state"] == "copied" for r in reviews if r["league"] == league),
                                 "skipped_sources": sum(r["state"] != "copied" for r in reviews if r["league"] == league),
                                 "source_observations_available": history["leagues"].get(league, {}).get("observations", 0),
                                 "skip_reasons": dict(Counter(r.get("reason") for r in reviews if r["league"] == league and r["state"] != "copied"))}
                       for league in history["leagues"]}
    report = {"schema": "public-player-source-bundle-v1", "history_run_id": history["run_id"],
              "state": "complete" if copied and all(r["state"] == "copied" for r in reviews) else "partial" if copied else "missing",
              "copied_sources": copied, "skipped_sources": len(reviews) - copied, "copied_bytes": spent,
              "bounds": {"max_total_bytes": max_total_bytes, "max_sources": max_sources},
              "leagues": league_coverage, "files": files, "sources": reviews,
              "production_props_enabled": False, "current_participation": "unknown",
              "archive_timestamps_independently_authenticated": False}
    atomic_json(target / "manifest.json", report)
    return report


def build_verified_player_features(output_dir, *, identity_namespace, snapshot=None, **query):
    """D01 integration; future inputs are excluded by their actual availability."""
    if NAMESPACES.get(query.get("league")) != identity_namespace:
        raise ValueError("player_identity_namespace_mismatch")
    rows, manifest = load_verified_player_history(output_dir, snapshot=snapshot)
    selected = [row for row in rows if row["league"] == query.get("league") and row["player_id"] == query.get("player_id")]
    result = build_player_history(selected, **query)
    return {"features": result, "archive_run_id": manifest["run_id"], "archive_state": manifest["state"],
            "collector_archives_verified": True, "production_supported": False, "current_participation": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--leagues", nargs="+", choices=LEAGUES, default=LEAGUES)
    parser.add_argument("--seasons", nargs="+", type=int)
    args = parser.parse_args()
    print(json.dumps(refresh_verified_player_history(args.data_dir, args.output_dir, leagues=args.leagues, seasons=args.seasons), indent=2))


if __name__ == "__main__":
    main()
