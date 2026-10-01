# F0028 — durable restart intent evidence

Candidate implementation only. The bounded increment is complete; M01 and
production recovery remain open. No automatic installation profile applies.

## Why this increment

Read AGENTS.md, contract `2026-09-29.15`, M01/D14, D14 acceptance, E393159
candidate-tree audit, F0022 recovery limitations, market contract and project
status. No `data/rebase/conflicts.json` exists. The accepted implementation
already inspects candidate evidence and restores selected offline artifacts.
Repeating those capabilities or adding another generic attempt ledger would
not address the remaining service-recovery gap.

The existing `supervisor.supervise` invokes its restart callback before returning
state; `run_once` saves that state afterwards. An exit in between can lose the
restart attempt, its error and its cooldown. Existing tests cover ordinary
callback failure but not durable intent across that boundary. Protected code
is unchanged. This candidate implements a separate protocol for later root
integration, not a replacement production supervisor.

## Working API and boundaries

`src/betting_engine/development_restart_journal.py` creates its own new
`offline-restart-<uuid>` scratch directory. A separately retained manifest pin
binds its fixed deadline, identity and private SQLite journal. It does not accept
an existing database, discover production, invoke callbacks, execute commands,
control services or submit wagers.

```python
from betting_engine.development_restart_journal import (
    create_journal, record_observation, record_outcome, inspect_journal,
)

handle = create_journal(authorized_scratch_directory, checkpoint_at=fixed_deadline)
report = record_observation(handle, supplied_health, supplied_publication)
# Only report['new_intent'] can identify a newly reserved request.
# This utility executes nothing. A separately reviewed caller owns any action.
# After an actual caller attempt, record its actual outcome:
# record_outcome(handle, intent_id, 'failed', error=observed_error)
reopened = inspect_journal(handle)
```

- Three consecutive engine-process failures, observed at least 60 seconds
  apart, reserve a unique intent. Every intent consumes the existing 900-second
  cooldown, including unknown and failed outcomes. Healthy observations reset
  the failure count. Missing publication alone never restarts a healthy process.
- SQLite `BEGIN IMMEDIATE`, `synchronous=FULL` and one transaction commit the
  observation, checkpoint miss and intent before the API returns an action.
  Concurrent writers cannot reserve the same check. Exceptions roll back;
  process death releases the transaction lock. A writable reopen performs any
  required SQLite hot-journal recovery; read-only inspection may fail until then.
- No acknowledgement means **unknown**, not failed, succeeded or automatically
  replayable. A crash after commit but before return can lose delivery of the
  action while retaining cooldown. This deliberately does not promise exactly
  once execution. It prevents immediate duplicate requests at that boundary.
- Outcome acknowledgements are append-only and idempotent for identical values;
  conflicting outcomes are rejected. Errors, uncertain older attempts and
  superseded attempts remain queryable. A successful request is not recovery.
- Recovery evidence requires a later completed engine refresh and a fresh
  publication, both strictly after the latest intent. The actual supplied schema
  is `health.engine.running/started_at/finished_at/error` and
  `publication.generated_at`. Publications expire after 300 seconds. Running,
  stale, future, naive-time or pre-intent evidence cannot certify recovery.
  This observes progress, not causation, publication authenticity or every
  component's readiness. It never closes source/model implementation gaps.
- Every accepted observation retains its canonical JSON and SHA-256. Unknown
  fields are preserved. JSON nonfinite values, oversized objects, backward time,
  duplicate/fast checks, wrong pins, database swaps and symlinks are rejected.
  Observations are bounded to 256 KiB each; histories to 10,000 events. Hitting
  a bound blocks the operation rather than pruning evidence silently.
- A missed fixed recovery checkpoint stays missed after late recovery. A
  read-only inspection reports an elapsed but not yet persisted miss explicitly;
  the next successful write persists it once. An on-time observed recovery
  remains on-time. Cached process health expires and is reassessed against the
  engine's progress timestamp, never indefinitely reported healthy.

The journal has append-only SQL triggers and a manifest identity check; these
protect normal cooperating use, not a malicious owner modifying SQLite bytes or
its schema. Hashes are consistency evidence, not authenticated provenance.
Caller-supplied times support offline trials and are not attested wall time.
No power-loss/hardware durability or production recovery claim follows from
SQLite configuration or the fault-injection tests. Every report explicitly keeps
`production_ready`, `full_engine_ready` and `production_recovery_verified` false.

## Validation performed

New `tests/test_f0028_restart_journal.py`: **44 cases** covering three-failure
cadence, cooldown across reopen, transient resets, uncertain/failed/successful
outcomes, late/on-time recovery, immutable deadlines/history, stale cached
health, publication requirements, malformed/nonfinite inputs, bounds,
idempotence, concurrent-process exclusion, pin/database mismatch and symlinks.
Actual child Python processes exit immediately before and after intent commit,
and after failed-outcome commit; another process inspects retained evidence.
Injected commit errors cover both rollback and ambiguous already-committed
results. No external implementation/review agent or service manager is launched.

The schema integration cases compare supplied engine health against the existing
protected supervisor's pure `problem` function. This is candidate integration
evidence, not a production wiring change or independent review.

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_f0028_restart_journal.py tests/test_supervisor.py \
  tests/test_f0022_recovery_drill.py tests/test_f0022_traversal_errors.py \
  tests/test_f0022_commit_durability.py tests/test_d14_development_acceptance.py \
  tests/test_d14_nonfinite_evidence.py tests/test_e393159_candidate_audit.py \
  tests/test_development.py tests/test_parallel_development.py \
  tests/test_task_release_handoff.py tests/test_release_supervision.py \
  tests/test_release_superseded_tasks.py
node --test tests/web.test.mjs
```

**304 Python/API tests passed** (one existing Starlette/httpx deprecation
warning); **40 frontend DOM tests passed**. SHA-256 comparison confirmed all
384 baseline source/test/doc/config/knowledge files unchanged, including all
103 original test files. Only this document, the new module and the new test
file are delivered. Full fixed suites and independent controller review remain
pending; these results do not attest unrun tests.

## Source scope and advisory dispositions

K03 and K08 informed the separation of capture time, source availability and
validation evidence; their underlying publications were not fetched. Inspected
the supplied public sports manifest (SHA-256
`4c4e18ed3d7fd30b0baefe338794d69d58db4fcb059bfaae0b3b76e15d26111b`)
and verified the NHL JSON blob
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`.
Its schema includes `id`, `season`, `startTimeUTC`, `rosterSpots` and 332
`plays`, with `eventId`, `periodDescriptor`, `timeInPeriod`, `sortOrder` and
`typeDescKey`. The manifest records null source publication times, unauthenticated
archive availability, and unverified current participation. Historical sports
logs are not engine heartbeats and are not used to manufacture recovery,
current participation, forecasts or profitable performance. No source times
or network responses were synthesized.

Both advisory text hashes were independently recomputed and match the dossier.

| Advice | Exact SHA-256 | Codex disposition |
| --- | --- | --- |
| `test_cases:claude` | `bb1ebbd1989c88f0d30938bb8cb3c55b32f474752a8b202fb9c33ff118f1513e` | Partly used: crash persistence, late recovery, fixed misses, stale-health and preserved-test suggestions informed behavioral tests. Rejected another generic ledger because durable implementation history already exists. An unknown restart outcome must not be invented as INTERRUPTED or failed. Betting/forecast/settlement suggestions are outside this isolated protocol; destroying a store or restoring betting decisions is not authorized. |
| `acceptance_checklist:agy` | `4e2e60c2469bcbb33ca2ef1bf3a93b6a0b4f0a0a39d9023a8171076be05824b0` | Partly used: chose the increment from actual predecessor limitations, defined fault boundaries, persistent/queryable misses and remaining evidence. Did not accept its full-service recovery, zero-corruption, independent-review or throughput checklist as accomplished. No measured production latency or daily instruction capacity follows from these offline tests. |

## Scoped developer audit and next work

All supplied requirements and R01–R30 were reviewed. This increment advances
R17/R25/R28/R30 with a candidate restart protocol and test evidence. It does not
fulfill those requirements in production or newly certify the other requirements.
Protected code, existing tests, contract, thresholds, stake limits and test
runner are untouched. No production files, wager database, credentials, account
state, external messaging, production services, launchctl or parent-project data
were accessed. The expressly supplied advisory dossier was read as untrusted
advice. No helper CLIs or agents were launched.

Concrete remaining work:

1. Controller: run full fixed suites and independently review the exact candidate.
2. Root: separately review integration with the service controller, observation
   provenance, multiple services, status UI and retention policy. Require intent
   commit before any restart and never replay an unknown intent on reopen.
3. Authorized recovery environment: exercise actual stopped/stalled services,
   inflight work, component/source failures, process-manager errors, cooldown,
   restart latency and storage faults. Retain attempts and checkpoint misses.
   This offline protocol does not verify a real service recovery drill.
4. Separate release: check source drift and immutable ledgers, then verify fresh
   publication locally and through Tailscale. Code rollback must not restore an
   old wager database. None of these live steps occurred here.

Full market coverage, independent model/source/contract/settlement validation,
15+ distinct funded instructions per Budapest day, $1–$5 sizing without a fixed
daily cap, immutable original decisions and measured profit remain separate
requirements. This utility creates no picks, source observations, edge or P&L.
Manual wagering remains unchanged; M01 and full-engine readiness remain open.
