# F0045 — bounded supervision history rollover

The candidate increment implements restartable rollover across pinned offline
supervision histories. It addresses F0042's explicit remaining retention issue:
one history stops at 100 cycles. Existing history, journal, dispatch, recovery,
acceptance and protected runtime implementations remain unchanged. This is
candidate integration with those utilities, not production installation or a
verified real-service recovery drill.

`development_supervision_series.py` provides `create_series`, `record_cycle`
and read-only `inspect_series`. The caller supplies explicit private service
journals, trusted bounded callbacks and an observation clock. Example:

```python
from betting_engine import development_supervision_series as series

handle = series.create_series(scratch_directory, service_handles, segments=16)
# Retain handle independently; reuse it after process exit.
result = series.record_cycle(
    handle, cycle_id, observations, request_restart=callbacks,
    engine_publication=publication,
)
historical_evidence = series.inspect_series(handle)
```

The series pre-creates all empty slots and pins their manifests before returning
its handle. No mutable active-slot pointer or rollover registration is needed.
After a history fills, the next cycle uses the next pinned slot. The same service
journals retain failure counts, restart attempts, cooldowns and original deadlines.
No history or uncertain commit marker is deleted, rewritten or adopted as success.
Interrupted creation returns no handle and leaves an unusable orphan for explicit
operator inspection; it is never automatically adopted.

A series-wide nonblocking OS lock spans scanning, reservation and callbacks.
Cycle IDs are checked against every segment before any new observation. An old
ID resolves to its original stored result even after all slots fill; changed input
rejects under F0042's comparison rules. Incomplete/uncertain cycles never replay.
Missing or corrupt slots, changed pins, cross-segment duplicates, nonsequential
use and backwards cross-segment clocks block new work rather than silently
starting a clean history. Malformed service observations still retain F0042's
service isolation. Summaries retain historical checkpoint misses and attention
conditions even after later healthy observations.

Bounds remain explicit: 2–32 slots, default 16, at most 100 cycles per slot,
64 MiB aggregate retained cycle-read bytes, and the inherited 2 MiB record limit.
Thus the default allows up to 1,600 cycles, subject also to byte and service-journal
limits; it is not indefinite retention. Capacity exhaustion is visible and blocks
new cycles without deletion. Each call scans bounded history; no production
latency or HTTP responsiveness claim follows. Inspection performs no writes or
repair and does not claim a cross-file atomic snapshot.

All writes to constituent histories must go through this owner. Pins establish
consistency, not authenticated observations or protection against an actor able
to rewrite all retained evidence. There is no scheduler, command discovery,
default restart callback, live database access or model activation. An error-free
cycle means an adapter invocation completed, not that useful work or delivery
recovered. All production readiness/recovery and workflow-verification flags
remain false.

## Validation

The 31 new behavioral cases in `tests/test_f0045_supervision_series.py` cover:
the real 100-cycle boundary; failure threshold and cooldown across rollover;
historical checkpoint misses after later health; original-segment duplicate reuse
and conflicts; exhausted capacity; malformed service isolation; lock exclusion;
read-only inspection; uncertain start/result directory syncs; an actual scratch
child-process exit during the first rotated callback; incomplete start identity;
clock regression; invalid configuration; missing/symlinked/corrupt slots; changed
bindings; duplicate/out-of-order direct child writes; and aggregate byte bounds.

- **1,027 Python/API tests passed**, including all 31 new cases and unchanged
  F0042/F0040/F0038/F0029/F0028/F0022, D14/E393159, supervisor, release-supervision
  and API suites. One existing Starlette/httpx deprecation warning.
- **216 frontend DOM tests passed**, no failures.
- All **868** baseline source/test/doc/config/knowledge files and AGENTS.md
  included in that count remain byte-for-byte unchanged. Original test-file count
  and exact candidate/log pins are in `F0045_VALIDATION.json`.

Commands and logs are retained in `F0045_VALIDATION.json` and
`f0045_validation/`. These are developer checks, not independent review. The
process-exit test uses private synthetic journals; it verifies offline recovery
semantics, not actual service resumption, hardware durability or live latency.

## Supplied evidence and instruction audit

Read AGENTS.md, contract `2026-10-01.profit-learning-directive-v1`, predecessor
limitations, market/status documentation and local K03/K08 cards. Cards informed
time/provenance and validation separation; underlying papers were not fetched.

Development manifest SHA-256:
`3b23fb92046eb4e7118f92091865113e76d06bbcaa87096b7a7c83017ad7416d`.
All **325** exported file pins and **409** source-body hashes match, including
decompressed Stake archives; source sizes were checked where declared. All
**44** frozen validation-file pins and four declared regression-test pins match.
Bundle declarations agree with the protected catalogue, whose hash is
`b54253e4f3938511354ecc55b3b21037915a2e5939d082007b54ffac0a44df60`.
Both export manifests have empty errors; development declares no truncation field.
No pinned references are missing. Export time is not measurement time.

`data/task_diagnostics/manifest.json`, `data/root-recovery-package.json` and
`data/rebase/conflicts.json` are absent. TASK declares no root recovery binding
or diagnostic repair targets. Runtime diagnostics report `not_required`; F0045
release diagnostics contain no files or rejections. No prior traceback was
invented and no parent-project data was inspected.

The supplied official NHL blob
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`
contains 332 plays, `rosterSpots`, `startTimeUTC`, and play fields `eventId`,
`periodDescriptor`, `timeInPeriod`, `sortOrder`, `typeDescKey`. Its observed schema
informs a synthetic negative test: a sports log does not prove service recovery.
The public manifest preserves null source publication times, unauthenticated
archive availability and unverified current participation. None is inferred
from historical logs or export time.

Re-audited R01–R41 for this scope. Candidate supervision/durability advances
R17/R25/R28/R30; no production requirement is newly fulfilled. All-market
validation, independent forecasts, exact guarded contracts, minimum20 actual
daily first issuances, USD1–5/max5, no fixed daily cap, immutable decisions and
outcomes, manual wagering and measured profit remain separate requirements.
Protected sources, original tests, thresholds, settings, ledger contract and
supplied source bytes remain unchanged. No credentials, account state, external
messages, network, production services, launchctl, writable wager database,
helper CLI or nested agent was accessed. No helper advice was supplied.

## Remaining work

1. Controller: run full fixed suites and independent read-only Codex review of
   exact candidate bytes, retaining original attempts and deadlines.
2. Trusted root: separately authorize integration; no exact passive release
   profile exists and discovery is disabled. Bind authenticated observations,
   journal ownership, bounded callbacks and status rendering. Define long-term
   archival beyond this finite series without forgetting IDs/errors, and resolve
   the inherited experimental engine feed-degradation policy mismatch.
3. Authorized recovery environment: verify real stopped/stalled service recovery,
   resumed useful work and measured timing, retaining failures and fixed misses.
4. Trusted release: verify source drift and immutable ledgers, then a fresh local
   and Tailscale publication. Code rollback must never restore an old wager DB.
   No live checks, independent approval, daily fulfillment or profit is claimed.
