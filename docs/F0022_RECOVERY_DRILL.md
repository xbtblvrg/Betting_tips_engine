# F0022 — offline recovery evidence

Candidate implementation only. Adds a restartable, persistent **offline artifact
restore drill**, repairing the prior candidate's false certification after a
result-directory sync error. No runtime integration or release authority is added.

## Evidence and implementation

Read AGENTS.md, contract `2026-09-29.15`, M01/D14, the D14 acceptance and
E393159 candidate-audit documents, market contract and project status. Those
accepted utilities inspect archives and source drift; restoration remained open.
No `data/rebase/conflicts.json` exists. The supplied previous F0022 source and
tests were inspected as read-only repair evidence. The review identified a
specific failure: `result.json` was readable before directory fsync returned,
so reopening could incorrectly certify a failed commit.

`development_recovery_drill.py` archives an explicit bounded mapping of
source/test/doc names to bytes in its own new directory. The caller retains a
manifest hash separately. An OS lock excludes concurrent cooperating owners.
Every attempt restores into a new generation, verifies exact inventory and
hashes, and synchronizes directories. Failed and interrupted attempts, original
deadlines and missed checkpoints remain in the returned inspection history.
It has no arbitrary restore destination, service-control API, command execution,
production discovery, ledger access or database restore facility.

Version 2 establishes an empty `.commit-pending-<filename>` marker and syncs
its directory **before** publishing the target. The immutable target's bytes and
parent directory are synced before removing the marker. A surviving result
marker means `commit_uncertain`, regardless of the result's recorded claim.
Inspection reports `offline_restore_verified=false` and a reason without writing.

Marker removal deliberately has no later durability operation. Losing that
removal on a crash can resurrect the marker and cause a conservative rejection;
it cannot certify an unsynced result, because target sync already succeeded
before removal. Version 1 manifests are rejected rather than retroactively
treated as following this protocol. This is an OS fsync protocol, not a claim
of measured hardware power-loss durability or authenticated evidence provenance.

A retry first syncs uncertain history before extending its hash chain, then
performs a complete new restore. It never removes the old uncertainty marker,
rewrites the old result or treats that attempt as on-time success. A successful
new attempt can verify the latest offline generation while preserving the
uncertain attempt. Failure to sync the old history blocks creation of a new
attempt. Interrupted starts remain recoverable, with unknown start time disclosed.

Example in an explicitly authorized offline scratch directory:

```python
from betting_engine.development_recovery_drill import (
    create_drill, inspect_drill, restore_drill,
)

handle = create_drill(
    scratch_directory,
    {"docs/example.md": b"Explicit selected offline bytes\n"},
    checkpoint_at="2026-10-01T00:00:00+00:00",  # caller's fixed deadline
)
# Retain handle separately; reuse it after process exit.
report = restore_drill(handle)
reopened = inspect_drill(handle)
```

No archived source is executed. Even verified output always retains
`production_ready=false`, `full_engine_ready=false` and
`production_restore_verified=false`. The checkpoint is for this selected
offline drill; it does not replace a service or milestone deadline.

## Behavioral validation

The previous two F0022 test files were copied byte-for-byte into this workspace:
44 cases covering process loss, exact restoration, damaged archives/generations,
locking, bounds, traversal errors, deadline retention and candidate integration
with the protected worker's temporary accepted-artifact persistence. External
implementation/review processes in that integration fixture are stubbed; it is
not independent review or a production deployment.

The new `tests/test_f0022_commit_durability.py` covers the actual post-link
directory `os.fsync` failure, process exit at that boundary and inspection in
another process, repeated failures, recovery-sync failure, publication boundaries,
failed results, on-time uncertainty crossing a deadline, resurrected/invalid
markers, legacy protocol rejection and interrupted start-marker recovery.
Child-process deadlines are test-hang guards, not production recovery SLAs.

The process-exit regression was also run against the supplied previous module:
it **failed**, reproducing `offline_restore_verified=true` after exit before
directory sync. The repaired module passes that same regression.

Validation commands:

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_f0022_recovery_drill.py tests/test_f0022_traversal_errors.py \
  tests/test_f0022_commit_durability.py tests/test_d14_development_acceptance.py \
  tests/test_d14_nonfinite_evidence.py tests/test_e393159_candidate_audit.py \
  tests/test_development.py tests/test_parallel_development.py \
  tests/test_task_release_handoff.py tests/test_release_supervision.py \
  tests/test_release_superseded_tasks.py
node --test tests/web.test.mjs
```

Final candidate checks: **256 Python/API tests passed**, including 58 F0022
cases (44 preserved and 14 new durability cases); **40 frontend DOM tests
passed**. The Python run emitted one existing Starlette/httpx deprecation
warning. SHA-256 comparison found all **303 original source/test/doc/config
files unchanged**; both copied previous F0022 test files are byte-identical to
their originals. Only one new module, three new test files and this document
are delivered. Full fixed suites and independent controller review remain pending.

## Source scope, advisory review and developer audit

K03 and K08 were read as provenance/validation source material, not instructions.
The supplied public sports manifest and NHL JSON blob
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`
were inspected and the blob hash verified. Actual fields include `id`, `season`,
`startTimeUTC`, `gameState`, `rosterSpots`, and 332 `plays` with `eventId`,
`periodDescriptor`, `timeInPeriod`, `sortOrder` and `typeDescKey`. Source
publication time is null and archive availability is not independently
authenticated. These historical observations provide no current participation,
past availability, forecast or profit evidence; this utility does not use them
to price or settle bets. No source times were synthesized.

Both advisory text hashes were independently recomputed:

| Advice | Exact SHA-256 | Disposition and reason |
| --- | --- | --- |
| `test_cases:claude` | `029369ce4b281755a55fd3965a60d95b51ea66526c8bcd3db5a83602a6e9a65b` | Partly used: retained interrupted-attempt, deadline, failure-history and recovery assertions. Rejected the suggestion that a ledger essentially provides service restartability, and any assumption that recovery must generate 15 bets. No evidence supports such a guarantee. Live service/cache recovery and betting-generation tests are outside this offline increment. |
| `acceptance_checklist:agy` | `c0be9051e9433e8fa6b95653b58436e3bdbda1b9c2ce126917f9ce87cf5bdcb4` | Partly used: checked prior gaps, preserved tests, defined the crash boundary and explicit production evidence register. Did not treat actual service restart, independent review, live generation or broker credentials as completed or required inputs to this offline tool. Manual wagering remains the contract. |

All user requirements and the ledger were reviewed. This increment advances
R25/R27/R28/R30 with offline recovery and audit evidence. It does not certify
R01–R24, R26 or R29, change any contract, guard, stake limit, issuance or immutable
decision, or activate a model. The $1–$5 ceiling, no fixed daily cap, 15+ distinct
funded instructions per Budapest day, no padding and manual wagering remain
unchanged requirements. Full market support, chronological model validation,
settlement, freshness, learning, daily shortfalls and measured profitability
remain outside this implementation. No production files, real wager database,
credentials, account state, external messages, services or nested agents were
accessed. No helper's claimed source facts or tests were accepted without checks.

## Remaining controller and production work

- Controller: run the full fixed suites and independent read-only Codex review
  of these exact candidate files. Candidate completion is not that review.
- Root: separately review and authorize any integration; this profile has no
  automatic installation or production activation. Preserve source-drift and
  immutable-ledger checks without rolling a wager database back.
- Authorized recovery environment: exercise actual stopped/stalled services,
  inflight work, cache recovery, restart cooldown/backoff and storage durability;
  retain attempts, checkpoint misses and measured recovery timing. This offline
  process-loss drill does not verify those production behaviors.
- Authorized release: verify a fresh publication locally and through Tailscale,
  and inspect the existing immutable ledger. Neither live verification nor a
  production recovery drill occurred here. M01 remains open.
