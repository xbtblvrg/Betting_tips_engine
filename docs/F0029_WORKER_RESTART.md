# F0029 — worker restart evidence and dispatch-stall repair

This is a bounded candidate increment for M01, not production delivery. It adds
durable worker restart observations to the existing engine-only journal and
repairs the exact retained review finding. Independent controller review, trusted
runtime integration and a verified live recovery drill remain pending. There is
no exact passive release profile and discovery is disabled.

## Implementation

The supplied base already implements D14/E393159 acceptance assessment, F0022
offline restoration and F0028 engine restart persistence/dispatch. Restored the
worker extension from the expressly supplied candidate
`20261001T001426105581-F0029/files` after comparing its changes with this base.
The three retained regression files are copied byte-for-byte into previously
absent paths. Existing tests and protected code are unchanged. The existing
F0028 hot-journal recovery and filesystem validation behavior is preserved.

`development_restart_journal.create_journal(..., service=...)` supports learning,
development and release workers as well as its original default engine. Separate
worker manifests pin service identity. The journal retains supplied observations,
restart intents, unknown/failed outcomes, fixed checkpoint misses and post-intent
heartbeat progress. Three failures spaced at least 60 seconds apart reserve an
intent; all intents consume the existing 900-second cooldown. Reopening never
replays an intent. Disabled workers cannot request activation. A failed task is
not automatically a failed worker. Engine completion/publication semantics and
legacy manifest bytes remain unchanged.

`development_worker_health.py` now preserves the protected supervisor's explicit
dispatcher-stall check. `health.progress.dispatch_stalled is True` is a process
failure only when that diagnostic's own `checked_at` is between zero and 180
seconds old, inclusive. A fresh idle heartbeat cannot reset those failures or
certify post-intent progress while the fresh stall persists. Failure reasons flow
through journal observations, reports and restart intents without new thresholds.

Stale, future, missing or invalid optional diagnostic timestamps do not establish
a current stall, matching the supervisor. The containing heartbeat and child step
deadlines are independently validated. Counts, state labels and attention flags
alone do not imply a restart: a legitimate resource wait can contain runnable
work. Original expired diagnostics remain in append-only history. A later healthy
heartbeat can establish only `worker_heartbeat_progress_only`, not successful
dispatch or completed work; `workflow_completion_verified` and
`production_recovery_verified` remain false.

The previously repaired child timestamp ordering remains: both child heartbeat
and step-start timestamps must fit inside the coordinator snapshot, independently
of their ordering with each other. The dispatch adapter remains engine-only and
rejects worker journals before any lock/database write or callback. No service
discovery, default worker callback, launchctl invocation, production activation or
wager submission is added. Worker execution integration is separate root work.

## Behavioral verification

New `tests/test_f0029_dispatch_stall.py` has 16 cases. Before this repair it produced
**8 failures and 8 passes**, reproducing the review finding through both the pure
validator and journal integration. Coverage includes:

- Freshness at zero and exactly 180 seconds; expiration one microsecond later;
  future, missing and invalid diagnostic timestamps; strict boolean identity.
- Three repeated stalls, failure-count retention, unknown intents, reopen without
  replay, and the original 900-second cooldown.
- Continuing stalls after both successful and failed request acknowledgements;
  later clear evidence permits heartbeat progress while preserving failures and
  the missed original checkpoint.
- The real `delivery.development_progress` producer in scratch storage, transitioning
  from resource pause to idle capacity with eligible work. Only the latter stalls.
- Expired diagnostic retention without treating it as current process failure.

The initial focused run passed **268 tests** across F0029, F0028, supervisor,
resource admission and release intake behavior. Final full-suite results and
file preservation counts are recorded in `F0029_VALIDATION.json`.

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q
node --test tests/web.test.mjs
```

Tests use synthetic timestamps and scratch stores, including actual scratch child
process termination in retained tests. They do not prove authenticated observation
times, real-service recovery, hardware power-loss durability or production readiness.

## Supplied evidence and advisory disposition

Read AGENTS.md, project contract, predecessor limitations, Stake market/status
documentation and local K03/K08 cards. Cards inform provenance and temporal
separation; underlying publications were not fetched. The recovery binding matches:

- Source run: `20261001T001426105581-F0029`.
- `run.json`: `f4147d71ed2fd69059f7a4a011fcdd272820cb88bae95d8a29d6bca1b2016781`.
- `review.json`: `80827bccec25c619db342f1913a79f17340a773a9a7685854bd4a24ba58265f2`.

Those files are diagnostics, not approval. Development manifest SHA-256 is
`5578f02ee96d20b6e4f06b44915df5c861162a4181fab1d024e9a05a5744df21`.
All 310 file hash/size pins, 386 decompressed Stake source-body hash/size pins and
22 separate frozen validation file hash/size pins matched. No missing references
were found in these checks. Development and validation manifests report no errors;
no truncation field is declared. Public sports coverage remains explicitly partial.
Runtime failure evidence is `not_required`; the release-failure manifest contains
no files or rejected entries. There is no rebase conflict or root recovery package.

Inspected hash-verified NHL public blob
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
332 plays, `rosterSpots`, `startTimeUTC`, and play fields `eventId`,
`periodDescriptor`, `timeInPeriod`, `sortOrder`, `typeDescKey`. Publication time is
null and archive availability is unauthenticated. Historical sports logs are not
worker heartbeats, historical availability authentication or current participation.
No source response, availability time, model probability, edge or profit is invented.

Claude advice `test_cases:claude`, verified text SHA-256
`a7729783662aacb9dfa605fbf9a05bceda627b091bacc49a09e14a4aabb7ecd7`, is **partly used**:
retention, missed-checkpoint, negative-recovery and explicit production-limit
suggestions fit this repair and its tests. A new generic attempt ledger would
duplicate F0028. The suggested target of 15 is obsolete (the active target is 20),
editing existing tests is forbidden, and destructive recovery drills are outside
this workspace's authority. No helper test/source claims are adopted. No additional
helper CLI or nested agent was launched.

## Scoped instruction audit and remaining work

This increment advances supervision traceability under R17/R25/R28/R30. It does
not close M01 or any betting capability. Independent sports models, exact guarded
Stake contracts, minimum20 funded first issuances daily, USD1–5 stakes, no fixed
daily cap, immutable decisions/outcomes and measured profitability receive no new
fulfillment claim. The project contract, funding/quote/model gates and original
decision evidence are unchanged. Manual wagering remains the only wagering path.
No production services, writable wager database, credentials, account state,
external messaging, network or unrelated parent-project data were accessed.

1. Controller: validate exact candidate bytes with the fixed suites and independent
   read-only Codex review, retaining earlier attempts, failures and deadlines.
2. Trusted root: integrate worker observation/intent ownership and status display
   under a separate policy, with authenticated provenance and bounded dispatch.
   The current engine adapter does not execute worker restart intents.
3. Authorized recovery environment: run the actual stopped/stalled service drill,
   measuring resumed work, restart failures, cooldowns and fixed checkpoint misses.
4. Separate release: check current source drift and immutable ledgers, then verify
   fresh local/Tailscale publication. No live release or independent approval is
   claimed here; code rollback must never restore an old wager database.
