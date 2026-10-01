# F0016 — Native injury evidence for player-prop research

Implemented a working offline source adapter and exact-player injury-context
assessment in `betting_engine.player_injury_context`. This is the next bounded
M03 increment, not an activated player model or verified current participation.
There is no production caller, automatic release profile or wager-ledger access.

## Why this increment

The accepted workspace already contains D06 per-league shadow forecasts and
chronological studies, the E8B9 identity-quarantine repair, ECDB contract identity
checks, pinned historical replay, an internal participation interchange, and a
pure prop settlement evaluator. Their existing files and tests remain unchanged.
No rebase conflict manifest exists. The referenced previous-attempt directory
was not needed or accessed outside this workspace.

The missing source-level step was decoding the supplied native injury reports.
The source bundle explicitly names identity/current participation as a next
dependency. It contains actual injury Parquet bodies, so another synthetic-only
interchange or a claim that injury sources are absent would miss available work.
Historical box scores remain a separate evidence class. No new model was fitted
and no previously inspected holdout was reused to select a recipe.

## Behavior and candidate integration

`load_injury_bundle(root, manifest_sha256=...)` reads only the pinned copied
bundle. It checks manifest and file hashes/lengths, bounded native decoding,
collector receipt, exact provider endpoint, league/season, HTTP status and
capture-clock agreement. Unsafe paths and symlinks are rejected. A damaged
league retains an explicit source failure while intact leagues remain usable.
Hashes establish byte identity, not independently authenticated source clocks.

`decode_injury_archive` reads the actual 27-column sportsdataverse ESPN injury
schema. Each row retains its original index and all source fields, including
comments, report IDs, injury dates and expected return dates. Invalid rows are
retained with reasons. Source prose is inert data. Structured status ID/name and
display text must agree; unknown or conflicting values cannot resolve a status.
MLB negative injury IDs are retained as report identifiers, never player IDs.

`assess_injury_context` takes the existing typed `ParticipationIdentity`, an
explicit cutoff and a positive experimental context age policy. It accepts only
the exact ESPN athlete/team namespace; NHL, MLBAM, NFL GSIS and Stake IDs need
separate bridges. It orders eligible rows by **reported date**, rejects conflicting
latest statuses/teams, and prevents invalid latest records from exposing older
optimistic records. Conflicts across same-date revisions are not resolved by
fetch recency or input order. Every relevant accepted/rejected row is auditable.
Future and stale captures cannot resolve the query; started events are rejected.
Missing sources, failed sources and absent players remain distinct.

This result is **dated injury context only**. A fresh download may contain a very
old report; both dates remain visible. `max_age` bounds the collector observation,
not the age of a current-event claim. The date-only field has no asserted
timezone/time of day; it is never converted to an availability or publication
timestamp. A report date later than the UTC observation date is conservatively
rejected. Injury/return dates never become publication times or expiry.
“Active,” absence from a report, roster position, injury prose and historical
playing logs do not prove availability, role or final DNP. The output always
has null current participation, role, publication time and validity, plus false
production/current-participation flags and explicit activation blockers.
The existing participation validator rejects this output as a missing
current-event source document; a regression verifies that boundary.

Example, using an explicitly synthetic target event for an offline probe:

```python
from datetime import timedelta
from betting_engine.participation import ParticipationIdentity
from betting_engine.player_injury_context import load_injury_bundle, assess_injury_context

bundle = load_injury_bundle(
    "data/sports_source_evidence",
    manifest_sha256="eb9221c29918e8592132f27cafea9400bae0d847bddea1ac22579b8184519952",
)
query = ParticipationIdentity(
    "WNBA", "WNBA:synthetic-f0016-audit", "4433790", "3", "player", "espn",
    "2026-09-30T20:00:00Z",
)
assessment = assess_injury_context(
    bundle, query, as_of="2026-09-30T02:47:00Z", max_age=timedelta(hours=1),
)
# reported_status='out'; current_participation=None; production_supported=False
```

No source collection, raw-file mutation, JSON interchange fabrication, model
promotion or production hook is performed. The pure loader-to-assessment path
is the candidate integration available in this workspace.

## Actual supplied-data evidence

[Machine-readable evidence](F0016_INJURY_CONTEXT_EVIDENCE.json) records the exact
bundle/source/metadata hashes, code hash, source-card hashes and replay probes.
All four source bodies were decoded in full, not sampled or replaced by fixtures.

| League | Source season | Decoded / structurally valid rows | Reported source dates |
| --- | --- | --- | --- |
| WNBA | 2026 | 968 / 968 | September 8–29, 2026 |
| NHL | 2027 | 1,012 / 1,012 | September 15–29, 2026 |
| NFL | 2026 | 17,600 / 17,600 | September 8–29, 2026 |
| MLB | 2026 | 6,208 / 6,208 | September 8–29, 2026 |

Total: **25,788 rows**, zero decoder/source failures. These are repeated report
rows, not independent players, games or instructions. “Structurally valid” does
not verify the truth or current applicability of a reported injury. Capture
receipts range from `2026-09-30T02:46:09.417656+00:00` to
`2026-09-30T02:46:15.030236+00:00`. Source publication times remain unknown.
NBA has no supplied injury source; shared NBA schema behavior is fixture-tested
and unverified against live observations. NCAA is explicitly unsupported here.

Four exact-ESPN sample queries at the documented audit cutoff returned dated
contexts (out, day-to-day, active, 60-day IL), always with unknown current
participation. The MLB query retains September 8 as its latest matching report;
later absence is not a recovery. Replaying all four at September 29 00:00 UTC
rejects all their rows as `not_observed_as_of`. No collection timestamp was
backdated to obtain eligibility. Target event IDs/start times in these probes are
labeled synthetic and are not asserted future fixtures.

Local source cards [K03](../knowledge/cards/K03-adat-ido-forras.md),
[K05](../knowledge/cards/K05-nba-wnba.md) and
[K08](../knowledge/cards/K08-validacio.md) informed source-clock separation,
role uncertainty and independent chronological validation. They are methodology,
not operational instructions or independent sports observations.

## Tests and remaining acceptance

`tests/test_f0016_injury_context.py` adds 66 passing behavioral cases using
explicitly synthetic native-schema archives. They cover decoding/status
semantics, replay cutoff and freshness boundaries, same-date revision conflicts,
transfers, namespace collisions, malformed/latest-row fallback, missing sources,
injury prose, receipt tampering, unsafe paths, per-league failure isolation,
byte-preserved inputs and rejection by the existing participation validator.
Existing tests are byte-for-byte unchanged.

Final validation: **433 Python/API cases passed** (including all 66 new cases),
and **40 frontend DOM cases passed**. The Python run emitted one existing
Starlette/httpx deprecation warning. All 283 pre-existing files hashed under
`src`, `tests`, `docs` and `config` remain byte-identical; only this new utility,
its new test file and these two new evidence documents were added.

Validation commands:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src OMP_NUM_THREADS=1 BETTING_DATA_DIR=/private/tmp/f0016-api-tests /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_f0016_injury_context.py tests/test_participation.py tests/test_player_history.py tests/test_player_archive.py tests/test_player_archive_sources.py tests/test_player_prop_contracts.py tests/test_player_prop_settlement.py tests/test_d06_player_model_study.py tests/test_e8b9_d06_identity.py tests/test_ecdb_player_identity.py tests/test_ea6ff4_player_replay.py tests/test_api.py tests/test_governance.py
node --test tests/web.test.mjs
```

The controller still owns the complete fixed suites, independent Codex review,
and any separately verified release. No live publication, Tailscale, account,
credentials, production service, external messaging, helper CLI or nested agent
was accessed. No helper advice was supplied.

Before prop activation: obtain and verify event-bound participation/role reports
and exact provider/Stake bridges; collect authenticated prospective evidence;
validate each league/metric on a fresh chronological holdout; verify offered
contracts and DNP/void/push rules against source-bound settlement adapters.
NBA live adapter checks and all NCAA-specific injury sources remain outstanding.
These prerequisites are not closed by this offline context evaluator.

## Developer requirement audit

| Contract requirements | Actual disposition |
| --- | --- |
| R01–R02 | Independent public sports data and local source cards used; no odds input or invented probabilities. |
| R03–R06, R12–R13, R16 | Scans, market coverage, daily 15 minimum, $1–$5 limits and null daily cap unchanged; zero issued instructions. |
| R07–R10, R18–R19 | Immutable decisions, losses, original stakes/odds, manual wagering, English UI, navigation and guards untouched. |
| R11, R14–R15 | No model fitting, promotion, retraining or new performance measurement; historical-availability gaps remain. |
| R17, R20–R24 | Supervisor, core market priority, sizing, inning/period models and production contract/settlement unchanged; this adapter does not close a betting capability. |
| R25–R28 | Bounded isolated candidate, original tests preserved, no deadline changed or delegation launched; independent review and release pending. |
| R29–R30 | Allocation/hedge policy preserved; actual implementation, evidence and remaining work audited here. |

No profit, hit-rate improvement, full market coverage or daily issuance fulfillment
is established. Related report rows are not independent performance evidence;
betting P&L and unknown operating costs remain outside this increment.
