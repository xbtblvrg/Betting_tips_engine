# D13 candidate acceptance repair

The isolated implementation adds a read-only acceptance evaluator and tests the
current controller's candidate-publication/recovery interface. It does not write a
real acceptance or production/release receipt. `D13:acceptance_missing` remains a
controller-owned gap until the actual reviewed candidate is committed through
`parallel_development.publish_candidate(..., acceptance=...)`. Its supplied due
date remains **2026-09-30T03:51:42.135060+00:00**; no registry or deadline was edited.

`release_acceptance_rehearsal.assess_candidate_acceptance` accepts bytes and hash
maps, never filesystem roots or database paths. It requires the existing
`candidate-acceptance-v1` schema, committed state, matching run/task identities,
Python and DOM test claims, accepted review, timezone-aware completion/integration
order, the complete candidate manifest, exact archived changed-file membership,
and an unchanged base. Missing evidence, duplicate JSON keys, ambiguous paths,
source drift and malformed records return an explicit blocked assessment.

Success means `consistent_candidate_snapshot`, with promotion and model activation
authorization both **false**. It is a deterministic consistency check, not an AI
review, authenticated receipt, model gate, release policy or durable gap resolver.
The controller must authenticate its archive. A hash does not prove the truth of
test or review claims. This experiment intentionally requires an unchanged whole
candidate snapshot; later peer integrations require reassessment. It grants no
exceptions for managed production files. The protected controller and root policy
were not changed or wired to this new evaluator.

New regressions exercise the actual candidate controller only in disposable
directories with explicitly synthetic review/test claims. They check interruption
after directory swap but before receipt commitment, idempotent recovery, and
rejection of a changed accepted tree without committing the receipt. These are
candidate handoff fixtures, not production/release receipts. The evaluator never
writes any receipt. Existing `ReleaseRehearsal` tests and the new combined drill
require tests/model/settlement reports, reject concurrent changed-back source
generations under the writer lock, preserve original odds/stakes and later tickets,
and restore code without restoring a wager database. All database evidence here
comes from synthetic temporary stores.

## Observations and limits

No rebase conflict file exists. The existing utility, recovery implementation,
protected code, root policy, contract and every preexisting test remain unchanged.
Earlier D13 documents/manifests describe prior attempts; this addendum and
`D13_ACCEPTANCE_SOURCE_MANIFEST.json` describe this repair. No actual release
receipt is replaced by either document.

Read contract `2026-09-29.15`, Stake market/status documentation and knowledge cards
K03/K08. Inspected the supplied verified-history manifest and Parquet schema:
player/game/team/opponent identity, scope/stat_scope, played/starter/minutes/stats,
source hash and fetched/published times, availability basis/reference, game identity
and target participation. The manifest reports 550729 observations and 161 sources;
current participation is unknown, archive timestamps are not independently
authenticated, historical point-in-time readiness is false and production props
are disabled. The supplied public-source bundle likewise marks current
participation unverified. Existing observation fixtures are explicitly rejected as
candidate acceptance. No publication/availability timestamp, live response,
probability, edge, settlement result or profit was invented.

## Scoped developer audit and handoff

Validation used `PYTHONPATH=src OMP_NUM_THREADS=1` and the supplied
`/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q`:

- Final new `tests/test_d13_acceptance_handoff.py`: **46 passed**.
- Existing D13 rehearsal/recovery, parallel development, release coordinator,
  task handoff, release supervision and superseded-task regressions: **190 passed**
  (the first run included 36 new cases: 226 total; after additional cases/path
  validation, all 46 new cases were rerun).
- `tests/test_api.py tests/test_governance.py tests/test_decisions.py`: **46 passed**;
  one existing Starlette/httpx deprecation warning.
- `npm test`: **40 DOM tests passed**.

This is targeted local validation, not the controller's later full-suite result.
The source manifest binds implementation, tests, docs, inspected inputs and
unchanged protected interfaces. Before/after hashing found zero edits or deletions
among preexisting source, tests, docs and configuration files.

| Requirements | Actual disposition |
| --- | --- |
| R01–R04, R11–R12, R21–R24: independent sources/models, exact contracts and scope | No capability enabled. Model/settlement evidence remains mandatory; synthetic protocol tests do not establish sports validation. |
| R05–R08, R13, R19–R20, R29: issuance, sizing, immutable decisions | No requirement/threshold changes. Temporary-store drills preserve original odds/stakes and newer tickets. $1–$5 and no fixed daily cap remain unchanged; no sustained 15/day or profit claim. |
| R09–R10, R16–R18: English UI, access, coverage, supervision, links | No runtime/UI changes. DOM/API regressions only; no live publication or coverage claim. |
| R14–R15, R25–R28: learning, isolated development, helpers, deadlines | No fit, promotion, helper delegation or production access. Failed/unfinished work stays visible; no deadline moved. |
| R30 and release-15 instructions | Source-bound local evidence recorded; real observations, historical availability, participation, contract parsing, model approval and settlement remain separate claims. |

The bounded evaluator and candidate integration rehearsal are implemented. Before
real candidate acceptance, the controller must run its full fixed suites and an
independent Codex review, then commit the exact tested sources and acceptance
archive through its existing serialized integration path. The worker cannot repair
the missing real receipt by self-asserting approval.

Separately reviewed runtime integration and a verified production release remain
pending under `no_automatic_profile`. The release owner must recheck current source
and immutable-ledger evidence at commit and verify a fresh local/Tailscale
publication. Real model/source/settlement gates remain required for any capability
activation. Persistent host restart, physical power loss and running-code/schema
compatibility require separate recovery drills. No production service, launchd,
credentials, account state, messaging, parent data or real wager database was
accessed. Helpers were not used.
