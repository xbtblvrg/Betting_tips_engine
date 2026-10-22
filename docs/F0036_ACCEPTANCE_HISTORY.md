# F0036 — immutable decision retention across acceptance snapshots

The bounded increment is a working, read-only history evaluator in
`src/betting_engine/requirement_acceptance_history.py`. D15 and E5BBC53B172
already evaluate one export and conflicting outcome observations. They did not
compare original records across exports or retain successive daily shortfall
observations. This module composes their existing delivery/lifecycle evaluator;
it does not replace it or repeat its settlement logic.

`assess_acceptance_history(snapshots, as_of=..., first_day=...)` accepts detached
byte snapshots. Each item has `manifest`, its separately trusted
`manifest_sha256`, and `decisions`. It checks the actual
`development-inputs-v1` manifest's exact decision path, SHA-256, byte count,
error state and observation timestamp before evaluation. It performs no file,
database, network, account or service access. Bounds are 32 snapshots, 64 MiB
per input and 256 MiB total. Pins establish consistency; the controller must
establish their trust independently. Only the decision attachment is verified
by this API, not every other attachment named in the manifest.

The evaluator:

- Retains the earliest observed original ID, issuance time, family and complete
  payload hash. Changes or disappearance in a later complete export produce
  findings linked to exact export pins; later reappearance/reversion cannot
  erase them. Legacy zero-allocation originals also receive retention checks.
- Flags an older original first appearing after a complete export that should
  have included it. This is a history inconsistency requiring investigation,
  not a claim about why the row appeared or authority to repair it.
- Coalesces identical observations, orders distinct snapshots chronologically,
  rejects ambiguous equal timestamps, and retains conflicting original variants.
  Truncation or invalid rows prevent a deletion/completeness claim.
- Retains every observed daily deficit, its timestamp, completeness and open-day
  versus closed-day status. A later successful count cannot erase an earlier
  shortfall. The inherited target is the latest minimum20 observation target,
  not a reconstruction of the historical funding setting. No checkpoint is
  supplied, invented or reset.
- Preserves original probabilities, stakes, quotes and outcomes. Normal outcome
  updates remain separate from immutable-original checks; no cross-export
  outcome revision is approved. D15 still handles within-export conflicting
  outcomes. Duplicate tickets/repricing do not add funded credit, and distinct
  game counts remain separate from selection counts.

## Preparation and actual observations

[Preparation](f0036_validation/preparation.json) verifies 317 current input file
bindings and 392 response bindings, plus the separate protected frozen regression
catalogue. The current manifest has no errors; its SHA-256 is
`826b5510d41766249ad452bdecec140f2865020b03958ecdad23eaaa3527cc5d`.
No rebase conflicts, task diagnostic manifest or task-owned recovery package
were supplied or declared by TASK. Runtime diagnostics are `not_required`; the
release failure manifest supplies no logs. No missing traceback was invented.

The new schema integration test compares the existing `lifecycle-outcomes-v1`
frozen validation export (155 records) with this workspace's detached export
(172 records). The evaluator observes consistent originals and no retention
findings. The [pinned comparison](f0036_validation/history-fixture-observation.json)
is explicitly a regression-fixture observation, **not an authenticated live
longitudinal audit**. Frozen validation inputs remain in their separate namespace;
no source, model or historical availability claim is upgraded. Export time is
only an observation boundary, never sports publication time or participation.
Knowledge cards K03 and K08 informed chronology and sample separation; their
text was treated as source material. The observed outcome schema includes
`fetched_at`, `settled_at`, exact score scope and original published-price basis;
this utility adds no independent settlement reproduction.

The [current requirement audit](f0036_validation/requirement-audit.json) retains
all 39 ledger rules and all unfinished capabilities. R05, R20 and R39 remain
failed on delivery observations; 36 rows remain limited by missing full scoped
acceptance evidence. None is newly fulfilled. At the current cutoff
`2026-10-01T08:55:40.478582+00:00`, Budapest daily issuance for September 28–October 1
is 22, 52, 74 and 17; October 1 has 11 distinct games and a shortfall of three.
These are historical funded originals, not current executable instructions.

Publication 4308 contains 18 observed fixtures and 6,500 outcomes, with 5,582
unsupported/inactive outcomes, 1,752 unverified Swish observations and two
unmatched fixtures (categories overlap). This is frozen feed coverage, not
complete website coverage. Recorded P&L remains separate from unknown operating
costs and account verification. No measured profitability or new market support
is established.

## Validation and remaining acceptance

New behavioral tests are exclusively in `tests/test_f0036_acceptance_history.py`.
They cover original mutation/deletion/reappearance, earlier-time additions,
truncation, legacy ownership, aliases, game concentration, outcome evolution,
open/closed shortfalls, invalid pins/JSON/timestamps, bounds, input immutability
and both supplied schemas. Exact commands, results, log hashes and source pins
are in [the validation index](f0036_validation/evidence-index.json).

All pre-existing source, tests and configuration remain byte-identical according
to the [integrity check](f0036_validation/integrity.json). Git status was unavailable
because of the host Xcode license gate; no license or machine configuration was
changed. The initial full run is retained separately because one final history
check was added while it ran; the final full run checks the finished candidate.

This completes one experimental candidate implementation increment, not M08.
The controller must independently review the candidate and bind any authorized
integration/release path: no exact passive profile exists and discovery is
disabled. Production history retention would require controller-owned archived
exports, trustworthy observation pins and explicit integration. Live local and
Tailscale publication verification remains a later authorized release phase;
production services and the wager database were not accessed. Full market
models/settlement, durable operational shortfall intake, daily delivery and
measured economic performance retain their existing outstanding acceptance.
No helpers, nested agents, external messages or sportsbook orders were used.
