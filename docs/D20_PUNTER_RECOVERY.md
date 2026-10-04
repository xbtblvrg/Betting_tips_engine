# D20 native event-sequence candidate: punter identity recovery

The read-only native adapter now includes nflverse `punter_player_id/name` in
its required Parquet projection and validates supplied punters against `posteam`.
It retains that role, identity and team field in each event's evidence. Punts no
longer use the kicker role for attribution. This fixes the reviewed case where
Kansas City's M.Araiza (`00-0037284`, event 903) could be assigned Baltimore's
made field goal at event 2086 without rejection. Both events come from supplied
game `2024_01_BAL_KC`; no player name matching or current roster is inferred.

The prior candidate adapter and 22 native regression files were absent here.
They were restored from the explicitly supplied prior candidate. Only the
adapter's version and punter attribution changed. Unrelated prior catalogue and
rebase changes were not carried over; the upstream acceptance evaluator remains
unchanged. Every pre-existing workspace test remains byte-for-byte unchanged.

## Source validation and actual replay

`load_archive()` checks confined paths, raw-body and collector-sidecar hashes,
byte lengths, endpoint, schema and row counts. `normalize()` checks game/team/
player identity, native order, correction markers, terminal completeness and
running/final scores. `targets()` uses D09's event-sequence validators. There is
no synthetic completeness attestation or invented `completed_at`.

[Per-game replay](D20_PUNTER_SOURCE_REPLAY.json) records the exact source hashes,
observed availability, normalized hashes, final scores, terminal IDs and failures:

| Source | Games | Validated | Rejected |
| --- | ---: | ---: | ---: |
| nflverse 2024 | 285 | 258 | 27 |
| nflverse 2025 | 285 | 252 | 33 |
| nflverse 2026 | 48 | 41 | 7 |
| NHL gamecenter | 5 | 5 | 0 |
| Total | 623 | 556 | 67 |

NHL `2022020543` reconciles 2–1 with two regulation/OT goals and a separate
shootout award to `NHL:53`. Shootout attempts never enter regulation/OT targets.
NFL END GAME and earlier quarter terminals remain mandatory. The 67 rejections
retain 22 nonmonotone event-time sequences, 19 unsupported NFL overtime-rule
cases, 11 unverified touchdown identities, eight incomplete numeric inputs and
seven unsupported NFL overtime scoring sequences. None become scoreless games.

Supported NFL overtime remains the conservative 2024 regular-season single
decisive touchdown or scoreless timed-out tie subset. Other OT rules, other
feeds/leagues, unknown NHL penalty schemas and shootout target requests remain
explicitly unsupported. Native IDs are not contiguous; reconciliation does not
prove that a provider never omitted a non-scoring event. Corrections are bound
to each snapshot, without a cross-snapshot latest-version resolver.

## Tests and preparation

The new `tests/test_d20_punter_identity_recovery.py` reads all native columns
independently of the adapter projection. Ten tests cover the exact reported
contradiction, a later opposing punter, malformed IDs/names, missing team and
columns, valid same-team multiple roles, and real-game evidence preservation.
The deliberately mutated archives are test fixtures, not source observations.
Before the repair: **nine failed, one passed**. After the repair: **506 native
and D09 tests passed**, including existing missing/corrected/duplicate-event,
exceptional-game and shootout cases. **216 DOM tests passed** using the package's
exact `node --test tests/web.test.mjs` command; the npm wrapper exited 255 before
test output. Logs: [before](D20_PUNTER_BEFORE.txt),
[native](D20_PUNTER_NATIVE_TESTS.txt), [DOM](D20_PUNTER_DOM_TESTS.txt).
The complete unchanged Python/API suite plus the recovered/new regressions
passed **6,829 tests and five subtests**, with one dependency deprecation warning
and no failures: [full log](D20_PUNTER_PYTHON_TESTS.txt).
[Validation receipt](D20_PUNTER_VALIDATION.json) pins the implementation and logs.

[Preparation evidence](D20_PUNTER_RECOVERY_INPUTS.json) records verified pins for
325 development-export files, 24 raw sports files, one task engineering record,
and 48 frozen validation file/test entries. The export reports no errors;
178/178 immutable decision records are supplied, untruncated. Frozen validation
data remains separate from current research evidence. Prior run/review pins
match the targeted recovery binding. The task engineering record independently
retains the reported punter defect. No root recovery package, rebase conflict or
task-diagnostic manifest is supplied or bound to this task; runtime diagnostics
are `not_required` and release diagnostics contain no files. No substitute
payload was invented. Local cards K03, K06 and K07 were read as source material.

## Developer audit and remaining work

This is candidate implementation, not production delivery. Collector observation
times remain unchanged; they are not authenticated historical publication times.
Unknown completion/publication times stay unknown. Historical identities do not
establish current participation. No model support, probabilities, edge, profit,
live publication or latency acceptance is claimed.

Controller validation rerun and independent review, trusted root integration and
authorized live release verification remain separate phases. No exact passive
release profile is available. Before betting use, independently validate
chronology and models, current participation, exact Stake contracts and settlement.
Unsupported scopes need real source observations and their own verifiers.

Production, credentials, services and the writable wager database were not
accessed. Project contract, funding settings, guards and original decisions are
unchanged. The all-market capability, 20+ daily instruction objective and measured
profit remain unfinished broader objectives; this adapter neither fills counts
nor changes $1–$5 stakes, the absence of a fixed daily cap or manual-only wagering.
No helper advice or delegated work was used.
