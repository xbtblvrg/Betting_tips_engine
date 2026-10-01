# EB71DF62487: complete candidate change evidence

Implemented one bounded D13 increment: a read-only complete-delta evaluator in
`release_acceptance_delta.py`, layered on the existing acceptance evaluator.
This is candidate implementation, not production integration or M07 completion.
The fixed task deadline remains **2026-10-01T18:47:03.458974+00:00**.
No real acceptance/release receipt or engineering-gap status was written.

The supplied D13 engineering record
`data/engineering-evidence/281faf6d98d57ee0fde6d26196e1037e2f86f8dd58d2783fe302671ce5afc2fc.json`
records source drift requiring revalidation. The existing D13 utilities and tests
are present upstream; they were verified first, without edits. No rebase conflict
file exists. The supplied previous-attempt `runs/None/files` path does not exist.

## Reproduced defect and candidate integration

A consistent receipt can declare two files while its full candidate manifest
contains another added/modified file, or omits a baseline file. The old evaluator
checks archive membership against the declared list, without deriving the complete
task delta. New regressions reproduce all three omissions using the actual
`parallel_development.publish_candidate` interface in disposable directories.
The old evaluator accepts these internally consistent synthetic snapshots; the
new evaluator blocks them with the omitted paths reported explicitly.

`assess_candidate_delta` first invokes the unchanged handoff evaluator, retaining
its identity, receipt, review, test, chronology, archive and source checks. It then
requires the complete pre-edit task-baseline hash map and its separately pinned
canonical JSON SHA-256. Every added or modified path must appear exactly in the
declared and archived changes; unchanged declarations cannot replace omissions.
Deletions are blocked because the existing receipt format archives extant files.
Missing/malformed baselines and digest drift also block assessment.

The task baseline includes already accepted peer changes and is deliberately
separate from the production base. Substituting production for that baseline
would incorrectly attribute peer work to this task. Rebased work needs a newly
reviewed baseline. The caller must authenticate both baseline and receipt; hashes
alone do not establish review authenticity. Inputs are detached byte/hash maps;
the utility has no filesystem, database, service, fit or deployment operations.
Success is only `consistent_candidate_delta`; both promotion and model activation
authorization remain false. No protected controller or policy integration changed.

## Source and validation evidence

Read contract version `2026-09-29.15`, market/status documentation, and knowledge
cards K03/K08 (methodological data, not instructions). Inspected the actual local
verified-history Parquet schema and 550729-row metadata: league/game/player/team
identity, scope/stat_scope, participation/stats, source URL/hash, fetched/published
times, availability basis/reference and game identity. A sampled row has null
`source_published_at` and `target_participation`, with `archived_observation` basis.
The manifest marks current participation unknown, historical point-in-time
readiness false and archive times unauthenticated. The public sports-source bundle
also records missing publication times and unverified current participation.
These sources exist; neither is a candidate acceptance or a current contract.
No historical timestamp, quote, model score, settlement outcome or profit was made up.

Commands used the required interpreter with `PYTHONPATH=src OMP_NUM_THREADS=1`:

- Initial unchanged D13 acceptance/rehearsal/recovery suites: **149 passed**.
- `pytest -q tests/test_eb71df62487_acceptance_delta.py
  tests/test_d13_acceptance_handoff.py tests/test_parallel_development.py
  tests/test_api.py tests/test_governance.py tests/test_decisions.py`:
  **144 passed**, including **27 new regressions**; one existing Starlette/httpx
  deprecation warning. This run includes 46 tests from the initial run.
- `node --test tests/web.test.mjs`: **40 passed**. Initial `npm test` exited 255
  without diagnostics; the exact test command specified in package.json passed
  when invoked directly. No runner/dependency files changed.

Existing D13 tests cover synthetic model/chronology/settlement gates, original
odds/stakes/losses, later and duplicate tickets, source races, process interruption
and rollback without database restoration. New integration cases use explicitly
synthetic review/test claims and never represent real independent review.
No training, model selection, holdout reuse or actual sports validation occurred.
`EB71DF62487_SOURCE_MANIFEST.json` binds implementation, tests, documentation and
inspected evidence. Before/after hashing found zero changes or removals among
preexisting source, test, documentation and configuration files.

## Advisory disposition

`acceptance_checklist:grok`, SHA-256
`effb2fbf979cf5b5a2c84c8be3034c2eeb6c4a6ad646b6022f0693c82dbbc933`:
**partly used**. Recomputed the digest from the exact advice text. Used the
candidate-first, source-integrity, gate-preservation and honest-release checklist.
Rejected its assertion that engineering evidence, contract, deadline and freshness
definition were absent: they are supplied locally/in the task (300-second maximum).
Rejected item 7's conflation of 15/day with the rolling 48-hour window: the minimum
is per Budapest calendar day. Its suggestions and claimed missing evidence are
not test results, sports facts, approval or a reason to invent observations.

## Scoped instruction audit and remaining work

| Requirements | Disposition in this increment |
| --- | --- |
| R01–R04, R11–R12, R21–R24: independent sources/models, exact markets/scopes | Unchanged. No new market, model or settlement support; history is not participation or current-price evidence. |
| R05–R08, R13, R19–R20, R29: issuance, limits, immutable records, guards | Unchanged. $1–$5, null daily cap, freshness and EvidenceGuard remain mandatory. No quota/edge/profit claim or ledger access. |
| R09–R10, R16–R18: English UI, coverage, navigation, supervision | English documentation; unchanged UI/API regressions pass. No live coverage or service claims. |
| R14–R15, R25–R28: learning, development, helpers, deadlines | No fit or deployment. Complete task-delta consistency implemented; controller acceptance and fixed deadlines remain separate. No helper processes launched. |
| R30 and release-15 instructions | Scoped audit and source hashes retained. Existing files/tests/protected code and training evidence remain untouched. |

The bounded experimental evaluator is implemented and tested. Controller-owned
next steps are full fixed suites and independent read-only Codex review, followed
by separately reviewed integration using an authenticated task baseline captured
under its source lock. This utility alone cannot resolve `development:D13` or
repair a missing real acceptance receipt.

Production release, immutable-ledger checks and fresh local/Tailscale publication
verification remain with the release owner under `no_automatic_profile`; no such
access occurred here. Host restart/power-loss and runtime compatibility remain
separate recovery work. Actual market/source/settlement approval is still required
before any betting capability activation. All-market support, sustained 15/day
fulfillment and measured profitability remain unfinished broader requirements.
At the unchanged deadline, any unmet acceptance must remain reported as unmet.
