# F0028 acceptance checkpoint repair

Candidate implementation complete for this bounded offline evaluator; M01,
the real missing acceptance receipt and production recovery remain open.
No automatic release profile applies. This document and its companion evidence
JSON are developer audit artifacts, not acceptance or production/release receipts.

The prior restart journal, tests and document are byte-identical to the supplied
previous candidate. There is no `data/rebase/conflicts.json`. The current base
already includes the D13 handoff evaluator and complete-delta assessment. Rather
than duplicate those checks, the new
`development_acceptance_checkpoint.assess_acceptance_checkpoint` composes them
with pinned checkpoint evidence and the requirement to preserve existing tests.
Protected controller, root policy, guards and runtime code are unchanged.

The supplied original gap remains `F0028:acceptance_missing`, state `open`, run
`20260930T065307025780-F0028`, first/last seen
`2026-09-30T07:12:12.381156+00:00`, due
`2026-09-30T11:12:12.381156+00:00`. No deadline or registry was edited. The
controller owns actual candidate acceptance after its tests and independent
review; a worker completion flag cannot manufacture that evidence.

## Implemented behavior

The API takes detached gap/receipt bytes, source byte maps, base/baseline hash
maps, independently retained gap and baseline SHA-256 pins, expected task/run
identity and explicit `as_of`. It accepts no filesystem paths and writes nothing.
The receipt uses the existing controller's `candidate-acceptance-v1` schema.

- Missing/prepared receipts, rejected review claims, incomplete Python/DOM test
  claims, changed source/archive/base, undeclared deltas and invalid pins block
  the assessment. Every original test must retain its bytes, even if an altered
  test was declared and included in the supplied review. An implementation
  change and a new Python regression are required.
- Gap identity and timestamps are validated before deadline assessment. The
  exact supplied gap, including unknown fields, is returned unchanged. Pins
  detect deadline changes only when retained independently; hashes do not
  authenticate the source or truth of review/test claims.
- `started_at <= finished_at <= integrated_at <= as_of` is required. Acceptance
  cannot precede the reported missing gap. All times require explicit zones.
  This narrow assessment covers the original run. A later repair run needs
  separate controller attribution; it cannot silently take over the old gap.
- Late consistent evidence reports `accepted_late` and retains the miss. On-time
  evidence remains on time when assessed later. At the exact deadline the check
  is not yet overdue. Without consistent evidence after the deadline, the state
  is `deadline_elapsed_without_consistent_acceptance`: an evidence shortfall,
  not proof that an unobserved historical handoff never happened.
- Duplicate JSON keys, nonfinite/overflowing numbers, malformed objects and
  documents over 8 MiB fail closed. The complete-delta evaluator retains its
  strict whole-snapshot semantics: merged peers require a new assessment.
- Even `consistent_acceptance_evidence` keeps the gap open and all release,
  model activation, independent-review verification and production-recovery
  claims false. The assessor does not persist history, change supervision or
  activate any betting capability. Existing durable stores remain controller-owned.

## Validation observed in this workspace

Using `PYTHONPATH=src OMP_NUM_THREADS=1` and
`/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q`:

- New `tests/test_f0028_acceptance_checkpoint.py`: **44 passed**.
- Existing suites listed below: **405 passed**, one existing Starlette/httpx
  deprecation warning.
- `node --test tests/web.test.mjs`: **40 passed**.

Existing suites: `test_f0028_restart_journal`, `test_supervisor`,
`test_f0022_recovery_drill`, `test_f0022_traversal_errors`,
`test_f0022_commit_durability`, `test_d14_development_acceptance`,
`test_d14_nonfinite_evidence`, `test_e393159_candidate_audit`,
`test_d13_acceptance_handoff`, `test_eb71df62487_acceptance_delta`,
`test_parallel_development`, `test_release_coordinator`,
`test_task_release_handoff`, `test_release_supervision`,
`test_release_superseded_tasks`, `test_api` (all under `tests/`, `.py`).

New integration tests invoke the actual protected candidate controller only in
temporary synthetic directories. An injected exception interrupts after the
candidate directory swap and before receipt commit. Prepared evidence remains
blocked; recovery after the deadline commits consistent candidate evidence
without erasing the miss. A changed accepted file prevents recovery. Repeated
recovery leaves the archive unchanged. All test/review claims in these fixtures
are explicitly synthetic. These tests verify candidate handoff recovery, not
service-manager restart, power-loss durability or production recovery.

The companion `F0028_ACCEPTANCE_CHECKPOINT_EVIDENCE.json` records source hashes,
the original-file preservation comparison and commands/results. It is not a
controller gate result. The full fixed suite and independent review are pending.

## Inspected sources and advice

Read AGENTS.md, contract `2026-09-29.15`, Stake market/status documentation and
local K03/K08 cards. Cards inform time/provenance separation; their underlying
papers were not fetched. Rechecked the supplied public sports manifest, SHA-256
`f3aacf69075e012bd03e57f58412e7dd220413abbc58f12bcdbf979566f7fa36`:
partial coverage, current participation unverified, historical point-in-time
backtest readiness false, production support false. Its first source entry has
null publication time and unauthenticated archive availability.

Verified NHL raw blob SHA-256
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
`id`, `season`, `startTimeUTC`, `rosterSpots`, 332 `plays`, and play fields
`eventId`, `periodDescriptor`, `timeInPeriod`, `sortOrder`, `typeDescKey`.
A minimal fixture with this observed shape is rejected as acceptance evidence.
Historical sports observations do not prove controller acceptance, current
participation or service recovery. No source/availability times were synthesized.

Advisory `acceptance_checklist:grok`, exact text SHA-256
`9c0b3d95739033661dca05f823ce6dd9655d929293ca4202bdcf3cc28e6322fd`,
was independently rehashed: **partly used**. Used preserved deadline/gap identity,
source binding, visible misses, recovery evidence and authority separation.
Rejected its ambiguous "15 for today and the next 48 hours" formulation: the
requirement is at least 15 distinct funded first issuances per Budapest calendar
day, separate from rolling opportunity windows. Receipt schema and timezone
are specified by the existing code/contract; they are not absent. Its rounded
defect timestamps were not substituted for the supplied microsecond values.
Advice is neither an independent review nor test/source evidence. No other
advice entries were supplied; cadence-wait provider rows are not advice text.
No helpers, extra agent CLIs or network were launched.

## Scoped instruction audit and next work

Reviewed all supplied requirements and R01–R30. This increment advances offline
R17/R25/R28/R30 traceability only. It changes no market/settlement/model gates,
forecasts, issuance counts, $1–$5 stakes, null aggregate cap, immutable decisions,
manual execution, UI, training jobs or release policy. It establishes neither
15/day fulfillment nor profit. The original tests and all preexisting source,
docs, config and knowledge files remain unchanged. Production, real wagers,
credentials, account state, messaging, launchctl and parent data were untouched;
only the expressly supplied prior candidate and advisory dossier were inspected.

1. Controller: run full fixed suites and independent Codex review. Attribute the
   new repair to the original gap explicitly; commit the exact accepted candidate
   through the existing serialized handoff. Only then assess its real receipt.
2. Root: separately review runtime integration under `no_automatic_profile`.
   Keep the existing restart journal protocol and source/model/settlement gates.
3. Authorized recovery environment: verify actual service interruption/resumption,
   durable attempt/error history, cooldown and component recovery with retained
   evidence. A successful request or offline handoff does not establish recovery.
4. Separate release: perform source-drift and immutable-ledger checks, then fresh
   local/Tailscale publication verification. No live checks occurred here. M01,
   unsupported markets and measured betting performance remain unfinished.
