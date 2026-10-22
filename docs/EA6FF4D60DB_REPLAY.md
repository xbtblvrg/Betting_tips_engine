# EA6FF4D60DB — pinned D01 feature replay

Original deadline: **2026-09-30T18:32:44.723365+00:00**, unchanged.
The bounded candidate repair is implemented and tested. Independent controller
review and production release have not occurred. This is not completion of M02,
all-market support, daily issuance, or demonstrated profitable performance.

## Rebase and existing candidate

The conflict manifest names `src/betting_engine/closing_prices.py`. The supplied
upstream file already retains the older candidate's identity-conflict rejection
and adds legacy full-game scope and double-chance compatibility. It remains
byte-for-byte unchanged. Both the selected file and upstream copy have SHA-256
`eebf3804b6d84e6f176c544aec63100786b78bebc0beba50a2356f7177f6d241`;
the older candidate is
`f5fd32d58291e88a06d4b65098623e4fe9c7085b81ad72b16c75665dbafa4fcf`.
The read-only conflict manifest was not edited; this records the resolution.
The previous `player_archive.py` and its tests also remain byte-identical to the
supplied previous-attempt files. Before edits, 252 existing player-history,
archive, closing-price and integration-repair tests passed.

## Implemented integration

The upstream `player_archive_sources` adapter already supplies hash-checked
historical observations to the existing pure D01 builder. Its public feature
API always selected `latest.json`, so a later archive refresh could change the
same `as_of` replay or remove formerly eligible history after a source revision.

`load_verified_player_history` and `build_verified_player_features` now accept
an optional keyword-only `snapshot` with exactly `run_id` and
`manifest_sha256`. A pin selects that immutable run directly, independently of
`latest.json`. The existing loader checks the manifest bytes, adapter/schema,
all four artifact hashes and lengths, and observation count/schema before
exposing inputs. Missing, corrupted, mismatched or unsafe pins fail explicitly;
they never fall back to a newer run. Omitting the pin preserves latest-run
discovery. No run, source document, model, training sample or decision is written.

```python
import json
from pathlib import Path
from betting_engine.player_archive_sources import build_verified_player_features

root = Path("data/players/verified_history")
# Retain this reference with the exact query and result for subsequent replays.
snapshot = json.loads((root / "latest.json").read_bytes())
result = build_verified_player_features(
    root, snapshot=snapshot, identity_namespace="nflverse-gsis",
    league="NFL", player_id="00-0023853", team_id="ARI", stat_group="player",
    stat_fields=["passing_yards", "rushing_yards"],
    as_of="2026-09-29T19:25:57.043798+00:00",
    target_game_id="NFL:audit-query-only", target_start="2026-09-30T20:00:00Z",
)
```

The target above is an explicitly synthetic audit query, not an observed future
fixture or participation assertion. The player history is supplied source data.
Pins bind bytes, not historical truth or current code: callers must also retain
the query, outputs and implementation version for complete reproducibility.
This increment does not fit a model or replace the protected training-input
archival workflow. It adds no production caller or automatic release permission.

## Actual supplied observations

The previous attempt's missing-source conclusion is superseded by the supplied
verified-history run `20260929T192557043798Z-b96babbf572d`, manifest SHA-256
`484e002d2453cb2d91ec9f3955c9a80754d076679a72515b13da5d0e89f843cd`.
The new pinned loader verified all artifact receipts and loaded **550,729** rows:
MLB 215,666; NBA 104,489; NFL 41,697; NHL 167,659; WNBA 21,218.
These are observation counts, not independent games or instructions.

The actual schema includes exact provider IDs, `stat_group`, `scope`,
`stat_scope`, nullable participation/exposure, JSON statistics, source hashes,
`source_fetched_at`, `available_at`, `availability_reference`, and game identity
hash/time. The run is explicitly partial: 42 duplicate identities were
quarantined; NBA reports 672 rejected source rows and NFL 47. These gaps remain.
The supplied public sports bundle also distinguishes observation timestamps from
unknown publication timestamps and does not verify current participation.

The example query above was executed against the unchanged supplied run through
`build_verified_player_features`. It examined 21 NFL player rows: 17 rejected
for team mismatch, four eligible at the run's creation time. All four retained
unknown historical participation and the target participation remained null.
At `2026-09-29T00:00:00Z`, the same four were rejected as `unavailable_as_of`.
No data or capture timestamps were moved to obtain eligibility.

The first accepted row is `NFL:2024_01_ARI_BUF`, starting
`2024-09-08T17:00:00+00:00`. Its source fetch is
`2026-09-29T12:28:51.215537+00:00`; joined availability is
`2026-09-29T19:06:11.540844+00:00`. Source:
`https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2024.parquet`,
body SHA-256 `847569d194ca3d96b5efcf91e6eac5e18780fc47568a713fd545a82d29d155c1`.
That body hash is retained provenance from the supplied verified artifact; this
probe did not independently retrieve the underlying provider response. The
manifest still states `archive_timestamps_independently_authenticated=false`
and `historical_point_in_time_backtest_ready=false`. A 2026 observation does
not establish availability before a 2024 game. No source publication time is
fabricated and no live validation is claimed.

Methodological source material: [K03](../knowledge/cards/K03-adat-ido-forras.md)
§1–3 (availability, identity and missingness) and
[K08](../knowledge/cards/K08-validacio.md) §2 (chronological validation and
game-level dependence). Neither supplies operational instructions or sports facts.

## Validation

Added **16 cases** only in `tests/test_ea6ff4_player_replay.py`: refresh drift,
future source replacement, absent latest pointer, invalid/mismatched/missing
pins, manifest and all four artifact corruptions, directory symlink rejection,
cutoff/namespace checks, null participation and byte-preserved inputs. The
initial 14 cases failed before implementation; all 16 pass after the repair.
All synthetic collector fixtures are labeled as such.

Final checks: **377 Python/API tests passed**, including the previous candidate,
source-export integration, D01, participation, ingestion, guards, and rebase
regressions. **40 frontend DOM tests passed**. One existing Starlette/httpx
deprecation warning remains. No existing test or protected file was changed.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src OMP_NUM_THREADS=1 BETTING_DATA_DIR=/private/tmp/ea6ff4-replay-tests /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ea6ff4_player_replay.py tests/test_player_archive_sources.py tests/test_player_archive.py tests/test_player_history.py tests/test_participation.py tests/test_market_expansion.py tests/test_hockey_source_recovery.py tests/test_api.py tests/test_governance.py tests/test_closing_prices.py tests/test_closing_price_identity_conflicts.py tests/test_closing_integration_repairs.py tests/test_development_source_evidence.py
node --test tests/web.test.mjs
```

The controller still owns the full fixed suite and independent read-only review.
The prior macOS helper-isolation failure was inspected, not rerun: current
upstream has a nested-sandbox/controller-proof branch, whose successful runtime
validation is not established here. No helper CLI or agent was launched and
there is no helper advice to review.

## Requirement audit and remaining work

“Unchanged” below means preserved code/policy, not verified live fulfillment.

| Contract requirements | Actual disposition |
| --- | --- |
| R01–R02 | Sports-only historical features; local cards consulted; no odds-derived forecasts. |
| R03–R06 | Market coverage, daily 15 minimum, exact fresh offers, $1–$5 and no fixed daily cap unchanged; no new instruction or coverage claim. |
| R07–R10 | Immutable decisions, losses, accepted odds/stakes, manual execution, English UI and access unchanged; no production/database access. |
| R11–R16 | No fitting, recipe selection, allocation, model promotion or opportunity-count changes; chronological D01 gates retained. |
| R17–R22 | Supervisor, source recovery, Stake links/tabs, freshness seals and inning models untouched. |
| R23–R24 | No new market approval, NCAA model, player identity bridge, current contract or settlement integration. |
| R25–R28 | Isolated bounded implementation, fixed deadline, preserved candidates/tests; root review remains required; no delegation. |
| R29–R30 | Winner/Handicap/Total prioritization and sizing unchanged; this audit records actual evidence and limitations. |

Concrete remaining work: controller runs all fixed tests and independent review,
including the native isolation check, before deciding integration. Any production
release requires separate verification and fresh local/Tailscale publication
evidence. For model use, obtain trustworthy prospective source timing, retain
each exact pin/query/code version before fitting, validate current provider/Stake
identity and participation, and pass chronological model and exact-contract
settlement gates. Pinned replay cannot fill historical availability gaps or
establish profit; related outcomes must still be clustered by game. These wider
capabilities remain unfinished at the original deadline unless separately proven.
