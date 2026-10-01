# D01 — Experimental point-in-time player features

`betting_engine.player_history.build_player_history` is a pure, deterministic API.
It performs no I/O, reads no system clock, mutates no input, and has no production
call site. It builds descriptive historical features, not forecasts, prop approval,
participation predictions, betting instructions or a profit claim.

## Query contract

```python
from betting_engine.player_history import build_player_history

result = build_player_history(
    observations,
    as_of="2026-09-20T12:00:00Z",
    target_game_id="NFL:target", target_start="2026-09-20T17:00:00Z",
    league="NFL", player_id="p1", team_id="PIT", stat_group="player",
    stat_fields=("rushing_yards", "receptions", "passing_yards"),
)
```

The example identifiers are illustrative. `observations` is an iterable of
mappings. All query identities are required exact, nonempty strings: no name
matching, integer coercion, league aliases, roster inference or cross-team history.
A transfer's old-team records are excluded. `stat_group` separates MLB hitting
from pitching. Game IDs must have the exact `league:` prefix and a nonempty suffix.
College league IDs must likewise be supplied explicitly; this API does not imply
NCAA source or model availability. `stat_fields` is a nonempty sequence of unique
statistic names. All timestamps must be timezone-aware ISO strings or datetimes;
comparisons and output timestamps use UTC. Require `as_of <= target_start`.
Invalid query arguments raise `ValueError`.

## Observation contract

The base schema comes from `player_data.observation` / `write_partition` and the
supplied `data/players/*.parquet`, not an invented vendor response.

| Field | Required semantics |
| --- | --- |
| `league`, `player_id`, `team_id`, `stat_group` | Exact query identity match. |
| `game_id`, `start` | League-prefixed historical game ID and aware scheduled start; target game always excluded regardless of its timestamp. |
| `stats` | Existing JSON object string, or a mapping. Values must be finite numbers or null. Missing requested keys stay null. Boolean/numeric-string values, duplicate JSON keys, nonfinite values and malformed objects reject the row. Signed values such as negative rushing yards are valid. |
| `played`, `starter` | Optional `true`, `false`, or null. Missing is unknown; never inferred from statistics or minutes. |
| `minutes` | Optional finite, nonnegative number or null. No exposure conversion or imputation. |
| `source_url`, `source_sha256` | Nonempty source reference and 64 hexadecimal characters identifying the source body for this exact observation version. |
| `source_fetched_at` | Existing fetch metadata, retained for audit only. Never used to infer publication or historical availability, even if it precedes `as_of`. |
| `available_at` | **New required evidence field**: when this exact completed-result observation version was demonstrably available. Must be strictly after `start` and no later than `as_of`. Never scheduled start, guessed game duration, or publication of a different version. |
| `availability_basis` | **New required field**: `source_publication` or `archived_observation`. |
| `availability_reference` | **New required field**: nonempty reference to evidence binding this version/hash and its availability timestamp. |

Other existing columns (`season`, `player_name`, `opponent_id`, `home`) are not
features or identity substitutes. Upstream must validate completed-game identity
and statistics against independent sports data, as the existing parsers do.

`source_publication` means a documented publication timestamp for the exact final
result version. An initial release timestamp cannot be reused for later corrections.
`archived_observation` means an independently retained timestamped snapshot of the
completed result; its observation time is a conservative availability bound, not
an assertion about earlier publication. A historical `source_fetched_at` field
alone satisfies neither basis. A later fetch is permitted only when separate
evidence establishes the supplied version's earlier availability.

This pure function validates the **shape and chronology** of evidence claims; it
cannot authenticate their truth, inspect the referenced archive, establish final
game completion from a start time, or verify the source body hash itself. Callers
must establish those facts. No availability fields are added to local source data
by this increment. Test timestamps/references are synthetic fixtures, not verified
source publication evidence.

## Selection and outputs

Only observations with `start < as_of`, `start < target_start`, and
`start < available_at <= as_of` can contribute. Identity filtering and explicit
target exclusion precede availability checks. Each excluded row has one reason
and its input index in `rejected`; `rejection_counts` aggregates them.

Duplicate eligible observations for the same game/player/team/group are all
rejected, including identical copies. This deliberately avoids accidental weighting
and ambiguous correction selection. Unavailable future versions are filtered first,
so appending future corrections cannot erase an earlier eligible observation.
Version resolution must occur upstream with archived evidence, not input order.

`history` retains selected statistics, nullable participation/exposure, and source
and availability provenance, sorted by start then game ID. `statistics` reports
each requested field's non-null `count`, `missing_count`, and arithmetic `mean`.
Empty/all-null samples have `mean: null`; a genuinely observed zero remains zero.
All eligible rows contribute their observed statistics regardless of participation
status: these are means over observations, **not conditional per-appearance means**.
Historical played/DNP/unknown counts remain separate. `target_participation` is
always null, even when every historical row reports played. No hidden lookback
window, zero-filling, odds, or current lineup assumption is applied.

## Local evidence and validation

Read-only inspection of all 20 supplied player partitions found 706,273 rows by
Parquet metadata: MLB 287,097; NBA 112,679; NFL 60,318; NHL 223,639; WNBA 22,540.
Every partition lacks `available_at`. The first real observation from each 2026
league partition was passed unchanged to this API with an explicit later audit
cutoff: all five returned zero history rows and `missing_availability: 1`.
This is a schema/sample check, not a claim that every individual row was replayed.
In particular the supplied NFL schema includes nullable `played`, `starter`, and
`minutes`, and a JSON-encoded negative `rushing_yards` value; tests preserve these
semantics. No historical availability or current participation was fabricated.

Run the focused checks with:

```sh
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_player_history.py tests/test_market_expansion.py tests/test_hockey_source_recovery.py
```

Tests cover temporal boundaries/offsets, target exclusion, same-day and delayed
results, missing legacy availability, future-revision invariance, duplicate
observations, identities/transfers/stat groups, unknown participation, null/zero
distinction, malformed/nonfinite inputs, empty histories, and input immutability.
Existing ingestion regressions are run alongside the new API tests.
Result in this workspace: **64 passed** (54 new API cases and 10 existing
ingestion/market-expansion cases). The controller's full suite and independent
review remain separate; no frontend or production behavior changed.

Local knowledge cards used as methodological source material: [K03](../knowledge/cards/K03-adat-ido-forras.md)
§1–3 (separate event/publication/fetch times, missingness, lineup uncertainty),
[K05](../knowledge/cards/K05-nba-wnba.md) §2 (historical box scores are not current
lineup evidence), and [K08](../knowledge/cards/K08-validacio.md) §2 (chronological
validation). These cards supply no player values or availability timestamps.

## Integration still required

The bounded feature API is implemented; this is not production delivery. The next
source step is to collect or locate timestamped, hash-verifiable completed-result
snapshots, then build and live-check a version-specific availability adapter. Until
that evidence exists, the supplied historical logs remain unavailable for this API.
No network or production validation was performed or authorized in this workspace.

Subsequent work must validate archive references/hashes and final-game identity,
resolve revisions, integrate chronological training/evaluation, and obtain separate
current roster/role evidence. Protected ingestion/publication/guard/ledger paths
were not changed. League-specific model validation, exact Stake contracts,
settlement, live publication and separately verified release remain outside D01.
Daily issuance and measured profit requirements remain unfulfilled by this feature.
