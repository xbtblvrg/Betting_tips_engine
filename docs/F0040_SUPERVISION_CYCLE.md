# F0040 — restart supervision across private journals

Candidate implementation only; M01 and production recovery remain open.
`development_supervision_cycle.py` coordinates the existing engine and worker
restart journals in one caller-driven cycle. It implements routing, independent
error handling and read-only aggregate inspection. No runtime is activated.

## Why this increment

Inspected D14/E393159 acceptance utilities, F0022 restoration, F0028 journals and
checkpoints, F0029 worker policies and F0038 dispatch. F0038 explicitly left
multi-service integration and status projection open. The legacy protected
supervisor already checks several services, but saves its overall state after
restart callbacks. It does not coordinate the candidate's per-service committed
intents. The new adapter connects those existing durability mechanisms without
reimplementing their three-failure, 60-second cadence or 900-second cooldown rules.

## Interface and behavior

```python
from betting_engine.development_supervision_cycle import (
    observe_services, inspect_services,
)

# handles: explicit service -> previously created private journal handle.
# health: every configured service explicitly present; None means unavailable.
# callbacks: trusted, caller-bounded callable for each configured service.
result = observe_services(
    handles, health, request_restart=callbacks,
    engine_publication=publication,  # omit unless engine is configured
)
status = inspect_services(handles)
```

- Supports an explicit subset of engine, learning, development and release.
  Unconfigured services are listed, never silently counted as healthy. No service,
  command, handle, callback or observation is discovered from production.
- Missing/extra routes or health keys, malformed handles and reused journals
  reject the configuration before writes. Explicit missing health is distinct
  from an accidentally incomplete batch. Routes, handles and bounded JSON
  observations are copied before callbacks run.
- Each service uses its own existing pinned journal and nonblocking dispatch
  lock. A corrupt database, bad evidence, mismatched service, busy dispatcher or
  ordinary exception does not skip unrelated services. Invalid evidence is not
  converted into invented healthy status or an acknowledged restart.
- Intent and dispatch reservation commit before a callback. Reopen never
  replays an old intent. Callback exceptions persist as failed requests; invalid
  returns and interrupted callbacks retain uncertainty. Outcome-storage failures
  expose whatever actually committed, including ambiguous successful commits.
- Services and callback outcomes use individual clock readings, not a fabricated
  common batch-start timestamp. There is no cross-service atomic transaction.
  Process exit can leave a partial cycle; the journals preserve it independently.
- Read-only inspection returns all retained histories, checkpoint misses, failed
  and uncertain attempts, and service-local errors. An unreadable journal has
  unknown checkpoint status, never zero misses. Cached process health expires.
  Inspection does not create locks, mutate SQLite or repair hot journals.
- A later worker heartbeat proves only heartbeat progress. Engine recovery
  evidence still requires later completion and publication under the existing
  journal protocol. Neither establishes workflow delivery, independent review,
  production readiness or a verified live recovery drill. Late progress never
  erases the fixed original miss.

Callbacks execute sequentially and **must be bounded by the trusted caller**.
This adapter cannot kill arbitrary Python callbacks; a hung callback can delay
the rest of that invocation. KeyboardInterrupt/SystemExit propagate. Ordinary
errors before a journal commit are returned, but their durable retention belongs
to the caller. All journal writers must cooperate through the existing dispatch
adapter. Pins provide consistency, not authenticated provenance. The caller
owns the scratch directories, observation provenance, clock and callback safety.

The inherited experimental health policies are not a claim of complete parity
with the live supervisor. In particular, the protected supervisor distinguishes
completed engine feed degradation from process failure, while the existing
experimental journal treats an engine error as a process failure. This increment
leaves both policies unchanged; root must reconcile that distinction before any
engine runtime integration. Production source/model gaps cannot be closed by
these callback results. No exact passive release profile exists and discovery
is disabled: installation requires a separate root policy decision.

## Developer verification

New `tests/test_f0040_supervision_cycle.py`: **34 passed**. Cases cover committed
intent routing, reopen without replay, callback and storage failures, malformed
and oversized inputs, per-service corruption and lock contention, symlink loops,
wrong bindings, disabled workers, stale status, publication requirements,
immutable checkpoint misses, caller mutation, individual clock readings and
interrupted partial cycles. One actual child Python process exits during the
learning callback after engine acknowledgement; reopen dispatches only the
previously unvisited workers. This is a synthetic scratch drill, not a stopped
production service or hardware power-loss experiment.

Prescribed Python environment:
`PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python`.

Related validation runs `-m pytest -q` on the new file and existing
`test_f0038_worker_dispatch.py`, `test_f0028_restart_dispatch.py`,
`test_f0028_restart_journal.py`, `test_f0029_worker_restart.py`,
`test_f0029_dispatch_stall.py`, `test_f0029_advancing_heartbeat.py`,
`test_f0029_worker_evidence_boundaries.py`, `test_supervisor.py`,
`test_supervisor_feed_recovery.py`, `test_f0022_recovery_drill.py`,
`test_f0022_commit_durability.py`, `test_f0022_traversal_errors.py`,
`test_f0028_acceptance_checkpoint.py`, `test_d14_development_acceptance.py`,
`test_d14_nonfinite_evidence.py`, `test_e393159_candidate_audit.py`,
`test_release_supervision.py` and `test_api.py`.
Final counts and source pins are in `F0040_VALIDATION.json`.
Frontend check: `node --test tests/web.test.mjs` — **216 passed**.
The full fixed suites and independent read-only review remain controller-owned.

SHA-256 comparison against a pre-edit inventory confirms all **783** original
source/test/doc/config/knowledge files, including **339** test-tree files and
AGENTS.md, remain byte-identical. Only the new module, new test and F0040 docs
are delivered. System Git reports an unaccepted Xcode license; no license or
system setting was changed to bypass it.

## Input provenance and scoped developer audit

Read AGENTS.md, contract `2026-10-01.profit-learning-directive-v1`, predecessor
limitations, Stake market/status docs and local K03/K08 cards. Cards inform
temporal/provenance separation; their underlying publications were not fetched.

- Development manifest SHA-256:
  `df23ec4fe73670f3eb0fca4876a7480cea575363b005f4003e603f621ed5e6c0`.
  All **325** exported file hash/size pins and **377** source-body hash/size pins
  match (compressed Stake archives checked after decompression).
- Protected validation catalogue SHA-256:
  `b54253e4f3938511354ecc55b3b21037915a2e5939d082007b54ffac0a44df60`.
  Exported bundle declarations agree; all **44** file pins and declared test
  pins match. Development and validation errors are empty; development declares
  no truncation field. These separate frozen fixtures are not current inputs.
- Runtime diagnostics are `not_required`; F0040 release-failure files/rejections
  are empty. No task-diagnostics manifest, rebase conflicts or root-recovery
  package exists. TASK declares no diagnostic targets or recovery binding.
- Inspected the hash-verified NHL public blob
  `1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
  332 `plays`, `rosterSpots`, `startTimeUTC`, `eventId`, `periodDescriptor`,
  `timeInPeriod`, `sortOrder`, `typeDescKey`. Source publication time is null;
  archive availability is explicitly unauthenticated. The new negative test
  uses that schema with synthetic values. Historical sports logs are neither
  health evidence nor current participation or authenticated availability.

Reviewed the ledger requirements R01–R41. This increment advances the candidate
supervision/durability aspects of R17/R25/R28/R30; it does not newly fulfill any
production requirement. Source/model approval, exact current Stake contracts,
20+ daily funded first issuances, USD1–5/no fixed daily cap, immutable original
decisions and outcomes, and measured profitability remain separate requirements.
All protected code, original tests, thresholds, settings and contract are
unchanged. No production services, parent-project data, live wager database,
credentials, account state, messaging, network, launchctl or helper agents/CLIs
were accessed. No helper advice was supplied. Manual wagering remains unchanged.

Remaining work:

1. Controller: run the full fixed suites and independent review of exact candidate
   bytes. The tests here are developer verification, not independent approval.
2. Root: separately authorize integration; reconcile health-policy differences,
   bind authenticated observations and journal ownership, provide bounded restart
   callbacks, persist pre-commit adapter errors and connect status rendering.
3. Authorized recovery environment: verify real service interruption, resumed
   useful work, restart failure/cooldown and elapsed recovery timing, preserving
   uncertain attempts and original checkpoint misses.
4. Trusted release: verify source drift and immutable ledgers, then fresh local
   and Tailscale publication. Never restore an old wager database in code rollback.
