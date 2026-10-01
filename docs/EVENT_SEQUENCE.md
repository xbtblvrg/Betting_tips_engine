# D09 — Experimental event-sequence target contract

`betting_engine.event_sequence.extract_event_targets` extracts independent sports
labels from a complete, verified scoring archive. It performs no I/O, forecasting,
pricing, publication, settlement writes or wager submission. It has no production
call site. This implements D09's bounded extraction interface; it does not close
M04, a market-coverage gap, or a model-validation requirement.

## API and evidence boundary

```python
result = extract_event_targets(
    body, verification, as_of="2026-09-21T00:00:00Z",
    league="NHL", game_id="NHL:fixture", scope="regulation",
    kind="goal", subject="team",
)
```

The identifiers/date above are illustrative. `body` is immutable JSON bytes in
our **new normalized archive schema**, not a claimed live provider schema.
`verification` is a source verifier's attestation bound to those exact bytes:

| Field | Requirement |
| --- | --- |
| `snapshot_sha256` | SHA-256 of the exact body; recomputed on every call. |
| `complete` | Boolean true, never a truthy string. |
| `coverage` | `all_scoring_events`, including every scoring type and played period. A touchdown-only or goalscorer-only partial response is insufficient. |
| `source_type` | `independent_sports_events`; bookmaker prices are not sports evidence. |
| `source_url`, `completeness_reference` | Nonempty source and reference establishing complete scoring coverage, finality, exact identity and corrections. |
| `available_at`, `availability_basis`, `availability_reference` | Exact version's evidenced availability, with basis `source_publication` or `archived_observation`. Never guessed from scheduled start or game duration. |
| `fetched_at`, `verified_at` | Actual observation and verification times. Require completion ≤ availability ≤ fetch ≤ verification ≤ query cutoff. For archived observation, availability equals fetch. |

The function verifies the hash, attestation structure, chronology and numerical
reconciliation. It **cannot authenticate an external attestation or dereference
its evidence**. A future source adapter/verifier must establish those claims
against archived independent source bodies. A flag, reference string, score
reconciliation or test fixture alone is not real-world completeness verification.
No live adapter is approved in this increment. Source publication remains unknown
for the supplied aggregate observations; no timestamps have been added to them.
Every result explicitly retains `production_supported=false`,
`source_identity_verified=false`, and
`completeness_basis="caller_supplied_attestation"`. Extraction success does not
upgrade an external caller's attestation into verified provider evidence.

## Normalized body

Required fields are `schema="independent-scoring-sequence-v1"`, exact `league`,
league-prefixed `game_id`, timezone-aware `start` and `completed_at`, `state="final"`,
distinct `home_id`/`away_id`, `corrections_resolved=true`, `periods`, `events`,
`event_count`, and `final_score=[home, away]`. IDs are strings. Integer fields
reject booleans/floats. Duplicate JSON keys, NaN and unknown schemas reject.

Each period has `number` (consecutive from 1), `phase` (`REG` or `OT`),
`duration_ms`, `complete=true`, and independently observed period `score=[h,a]`.
Every played period must be included, even when scoreless. All regulation periods
are required. Shortened/exceptional games cannot be relabeled as complete.

Each row is **one confirmed scoring event**, with `event_id`, exact `game_id`,
`period`, `phase`, `elapsed_ms`, `kind`, `points`, `team_id`, `player_id` (nullable),
and `status="confirmed"`. Optional `order` is a nonnegative independently
observed ordering value at a shared clock time. Never derive it from row order or
lexicographic event IDs. Optional `occurred_at` is an aware wall-clock timestamp;
when supplied it must fall between start and completion. Every row is validated,
including rows outside the requested scope/type. No malformed row is skipped.

| League | Regulation; OT/extra periods | Scoring kinds |
| --- | --- | --- |
| NHL | 3 × 20 min; explicit 5/20 min OT periods | `goal` |
| WNBA / NBA | 4 × 10 / 12 min; 5 min OT | `basket_2`, `basket_3`, `free_throw` |
| NFL | 4 × 15 min; explicit 10/15 min OT | `touchdown`, `field_goal`, `extra_point`, `two_point`, `safety`, `defensive_conversion` |
| MLS | 2 × 45 min; optional two 15 min extra-time periods | `goal`, `own_goal` |
| MLB | 9 innings; additional innings | `run` |

Clocked leagues use elapsed milliseconds **within the period**, not remaining
clock time. MLS durations include observed stoppage time; a first-half goal at
45+2 remains in period 1. Clock coordinates are returned without inventing market
interval boundaries. MLB has no game clock: `duration_ms`/`elapsed_ms` must be
null, and each run requires `occurred_at`. Multiple runs on one play need individual
run rows with independently known runner attribution/order or unresolved ties.
No runs/9 scaling or inferred runner order is permitted. NCAA rules are not
silently borrowed from these leagues.

Per-period event points must equal the independent observed period scores; their
sum must match `final_score`. NHL may supply `shootout_award` as the winning team:
the final score may add one point only after tied regulation+OT. That award is
never a goal row or target. Shootout event rows are unsupported and reject. MLS
penalty-shootout kicks likewise are not match scoring targets or final-score
additions. Exceptional scoring/rules not represented here require a new verifier
and explicit contract, not permissive parsing.

NBA/WNBA/NFL/NHL overtime requires a tied regulation score. Additional basketball
OT requires the preceding cumulative score to be tied. NHL OT is sudden death:
at most one goal, with no later OT period after a goal. Mixed 5/20-minute NHL OT
rules and repeated 5-minute periods reject. A shootout award can follow exactly
one 5-minute NHL OT, not playoff OT. Supplied wall-clock event times cannot run
backwards across increasing period/game-clock coordinates.

## Targets, uncertainty and corrections

`scope` is `regulation`, `regulation_ot`, `overtime`, or `period_N`. Unplayed scopes
reject instead of returning zero. `kind` is `all_scoring` or an exact supported
kind. NFL field goals mean successful three-point scoring events, not attempts,
PATs or basketball baskets. Touchdown `player_id` means the actual scorer, not an
assisting passer. A source verifier must establish attribution, including return
and defensive touchdowns. `subject` selects `team` or `player`.

Outputs retain query identity, count, first/last event IDs, subjects and exact
period/timestamp coordinates, plus the verification provenance. Count is a count
of selected **events**, not points, bets or independent statistical samples.
Same-clock events without complete unique source order retain all possible
boundary subjects (`tied`). Multiple tied events belonging to the same team can
resolve the team while player attribution remains tied. Unknown scorers or own-goal
player semantics yield `missing_scorer`, never an invented player. `ready` means
the sequence passed extraction checks; consumers must also inspect each boundary
status. It does not mean eligible for a bet or automatic settlement.

A genuinely observed complete empty history returns `no_event`, count 0, and null
first/last timing. Missing history/evidence returns `rejected`, a stable reason,
and null targets. Zero clock time is valid only when explicitly observed.

Corrections use a new **full** reconciled snapshot and a new hash/availability
attestation. Pending/deleted rows reject; a verified removal must update the full
history and observed scores. Future corrections cannot be consumed at an earlier
cutoff, and an old attestation cannot authenticate corrected bytes. This API takes
one exact archive version; the caller must select the authoritative version for
the cutoff and retain earlier archives. It is not a correction-discovery service.

## Local evidence and remaining work

Before implementation, D09 engineering evidence was inspected:

- `0debdf326b56f8da5bdcdee05ee30a9a16264574706ca7bfc56a44aa1d4f8dde`:
  NHL first/last goal and period first/last team-to-score offers; zero supported outcomes.
- `b4815df02841f82c2cc235a47cf4b9e24656ab09588d1b9101a0fc01a4ecebb4`:
  MLB first-run timing, consecutive runs and match-decision timing.
- `31ff23f444c675bd6c4eb9e708256568fa55d4b92c3be1707fd0b3a169a1c6bc`:
  MLS first/last scorer, goal timing and bookings.

Knowledge cards K03 (availability/missingness), K04 (baseball sequence), K06
(football scoring types), K07 (regulation/OT/shootout) and K08 (chronology/game
clustering) informed the contract. They are methodological source material, not
sport observations or evidence of model performance.

Read-only schema inspection found 85,338 schedule records, 13,765 period-score
records and 9,832 MLB inning-score records. Player files have the existing
`league/game_id/start/player_id/team_id/stats/source_*` schema. The first real
2026 player row from MLB, WNBA, NHL and NFL was passed unchanged to this interface:
all four rejected with `unsupported_sequence_schema`, with no target/zero
inference. These samples are not an exhaustive event-source audit. No complete
normalized scoring archive was supplied to that worker; local period/player totals cannot supply event order,
scorer times, complete coverage or historical availability.

The later independent controller audit found native source bodies in the root raw
archive that had not reached this worker: seven nflverse play-by-play season
responses (2020–2026) and five official NHL play-by-play responses. The 2026 NFL
response has 8,311 plays over 48 games, all with an END GAME terminal and matching
final cumulative score, with no duplicate play IDs per game. One NHL sample
includes shootout goals: native `typeDescKey="goal"` must be filtered by period
type before interpreting a regulation/OT target. Native bodies correctly reject
this normalized-schema interface. These sources are a concrete adapter input,
not completed native correction/identity/completeness validation. Full hash and
terminal audit: `data/completion-15/utility-probes/d09-native-source-audit.json`.

Next steps: obtain and hash-archive complete independent play-by-play/scoring
sources; implement and live-check league-specific normalization, terminal and
pagination checks, native correction tracking, score reconciliation and
completeness attestations. Then fit family-specific targets using game-clustered
chronological selection and untouched tests, validate exact Stake rules/intervals,
and independently review any production/settlement integration. Booking targets,
consecutive-run and match-decision-time models are outside this scoring-interface
increment. None of D09's linked coverage gaps is closed by this candidate.
