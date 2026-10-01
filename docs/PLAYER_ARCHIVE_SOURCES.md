# Verified player archive input

`betting_engine.player_archive_sources` turns the existing collector's public raw
responses into D01-compatible player histories. It reads response bodies and the
independent schedule, not the normalized `players/*.parquet` statistics or prices.
The accepted D01 history and D03 participation validators are included unchanged.
The NHL official boxscore decoder comes from the reviewed development candidate
`20260929T183245395747-EA6FF4D60DB`; this layer adds actual archive verification,
schedule binding, durable receipts and the other four professional leagues.

## Runtime integration

Run after the existing `refresh_players` job has refreshed public source archives:

```python
from betting_engine.player_archive_sources import refresh_verified_player_history

result = refresh_verified_player_history(
    data_dir,
    data_dir / "players" / "verified_history",
    seasons=[2026],  # Or omit for all existing archived seasons.
)
```

The result exposes per-league observations, players, games, decoded sources,
rejected rows and source errors. `state="partial"` retains usable validated data
while explicitly reporting missing or rejected input. A successful source refresh
or nonempty league does not claim that all source rows were accepted. No network,
ledger, model promotion, recommendation, or production prop configuration is
changed by this module. NCAA remains explicitly unimplemented here.

Equivalent standalone job:

```sh
PYTHONPATH=src .venv/bin/python -m betting_engine.player_archive_sources \
  --data-dir /path/to/data --output-dir /path/to/verified_history \
  --seasons 2026
```

`output_dir/runs/<run_id>` contains observations.parquet, exact frozen metadata
in sources.json, game-identities.json, duplicate quarantine and manifest.json.
The manifest records all artifact and adapter-source hashes. `latest.json` points
atomically to a completed run; old runs are not overwritten. The reader verifies
all artifact hashes and the schema before returning observations.

Use `build_verified_player_features(output_dir, identity_namespace="espn", **query)`
for D01 queries. `query` is the unchanged D01 query contract: league, exact player
and team ID, stat_group, target game and start, as_of, and stat_fields. Namespaces
are NBA/WNBA `espn`, NHL `nhl`, NFL `nflverse-gsis`, MLB `mlbam`. These are not Stake
player IDs; a verified cross-provider identity registry is still necessary.

## Evidence and time semantics

The pointer filename must match SHA256(source URL), the URL must match a supported
public endpoint, and the raw blob name, byte count and body SHA256 must agree.
HTTP status must be 200; aware fetched_at must agree with epoch and cannot be in
the future. Symlinks, path escapes, oversized bodies and ambiguous JSON objects
are rejected. These checks authenticate consistency with the local collector's
record. They do not provide an independent cryptographic timestamp or prove a
provider publication time.

`source_fetched_at` is the actual recorded observation of the player response.
`available_at` is the later of that time and the independent final-game identity's
actual fetched_at. Both source versions are bound to the receipt. Game start,
assumed game end, file mtime and fixed delays never substitute for availability.
Fetching a 2023 boxscore today therefore does not create a 2023 training snapshot.
The output explicitly reports historical_point_in_time_backtest_ready=false.

Strict joins verify event, league, season, player, team and opponent where supplied,
and source-specific score/time/home-away details. MLB hitting and pitching remain
different stat groups. The only accepted statistical scope is a full player game:
overtime for NBA/WNBA/NFL; overtime but no shootout statistics for NHL; extra
innings for MLB. Nothing here prices periods or implies Stake settlement approval.
MLB innings notation `3.2` means 11 outs and is checked as such. NHL game_date is
the Eastern calendar day; it is not compared with a late-night game's UTC day.

Null remains unknown. Missing or zero minutes alone is not DNP. An explicit NBA/
WNBA did_not_play=true is retained as historical played=false; contradictions
are rejected. NFL weekly statistics do not establish snaps, starter or current
participation. Current-game participation remains unknown in all leagues; D03
requires a separate verified current event source. Exact duplicate player/game/
stat-group observations are all quarantined, not selected by scan order.

## Isolated development source export

```python
from betting_engine.player_archive_sources import export_verified_player_sources

report = export_verified_player_sources(
    data_dir,
    data_dir / "players" / "verified_history",
    workspace / "data" / "players" / "source_evidence",
    max_total_bytes=128 * 1024 * 1024,
    max_sources=256,
)
```

The destination must be new or empty. It is a self-contained **input root** with
raw/<urlhash>.json, raw/<bodyhash>.data, games.json and an export manifest. Pass
this destination as data_dir to the standalone adapter in an isolated workspace.
The original pointer JSON bytes are retained even after the live pointer changes.
Bodies are copied only after re-verifying the frozen source version's SHA256.
No credentials, betting ledger, user account data or arbitrary path is exported.

The byte bound covers copied source payloads and game identity data. The bounded
JSON audit manifest is additional bookkeeping. Round-robin source selection gives
each requested league a chance before a large league consumes the budget. Every
skipped source and per-league omission is reported. An exported subset is not
advertised as complete; an empty export is `missing`, never success. A second
export uses a new destination instead of mixing old and new sources.

## Actual archive validation, 2026-09-29

Read-only production archive probes were written under `data/completion-15`.
The 2026 probe decoded 54 source responses with zero source errors:

| League | Accepted observations | Games | Players | Rejected rows |
| --- | ---: | ---: | ---: | ---: |
| NBA | 34,607 | 1,316 | 591 | 276 |
| WNBA | 8,149 | 336 | 245 | 0 |
| NHL | 55,758 | 1,394 | 1,063 | 0 |
| NFL | 3,336 | 48 | 1,432 | 3 |
| MLB | 72,510 | 2,429 | 1,472 | 0 |

NBA rejections were 243 source rows with no independent schedule game and 33 with
invalid/missing numeric player identity. NFL had three missing exact identities.
These are excluded, not repaired through name guessing. Overall state is partial.
NFL played/starter remains unknown for all 3,336 rows; NHL starter is unknown for
50,183 rows. This is deliberate evidence preservation, not zero filling.

The complete 54-source export was approximately 109 MB, below the 128 MiB payload
bound. Rebuilding in an isolated directory reproduced all 174,360 observations
and the exact observations.parquet hash. The receipt is
`data/completion-15/player-final-probe-summary.json`; full manifests and source
hashes are in `player-archive-final-probe`, `player-source-evidence-final`, and
`player-bundle-rebuild`.

A separate NHL 2023 probe verified 1,400 official per-game response bodies and
55,980 observations across 1,400 games. The SportsDataverse aggregate export for
that year lacks game_id and is explicitly rejected as
`aggregate_without_game_identity`; official responses supply the valid rows.
Receipt: `data/completion-15/player-nhl-official-probe`.

169 targeted tests pass, including frozen-source export after a live pointer
change, offline reconstruction, corrupted files, path escapes, null participation,
wrong identities, duplicate quarantine, future availability and actual D01 use.
These tests and real probes validate the input foundation. Current roles, player
models, chronological calibration, exact Stake mappings and settlement validation
remain separate work; production_props_enabled and production_supported stay false.
