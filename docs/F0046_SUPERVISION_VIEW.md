# F0046 — read-only supervision operator view

Implemented the status-rendering gap explicitly left in F0045's remaining work
(item 2) and F0040's candidate integration notes. The new
`src/betting_engine/development_supervision_view.py` connects the existing pinned
series/history readers to the existing per-service journal inspection and renders
an inert standalone English HTML view. This is working candidate integration,
not production dashboard wiring, a new supervisor, or a live recovery drill.

```python
from betting_engine.development_supervision_view import inspect_status, render_status

# Explicit retained scratch-series handle and caller observation clock.
assessment = inspect_status(handle, as_of="2026-10-01T16:00:00+00:00")
html = render_status(handle, as_of="2026-10-01T16:00:00+00:00")
```

The view shows current process assessment separately from retained cycles,
original checkpoint deadlines, persisted versus currently assessed misses,
individual restart intent IDs, request outcomes and scope-limited recovery
observations. Unknown or failed attempts stay visible after later health and
across history rollover. Corrupt individual journals do not hide other services
or their previously retained historical evidence. Unreadable series bindings
produce unknown health and counts, never an empty healthy history. Unconfigured
services are explicit. Capacity exhaustion stays visible; nothing is pruned.

Every render re-inspects journals with the caller's explicit as-of time, applying
the existing freshness policy. Future evidence cannot become current evidence.
A saved HTML file explicitly says it does not update; it is not a live dashboard.
Reads do not create dispatch locks, persist a newly inferred miss, replay an
attempt, change deadlines, repair hot journals or mutate records. There is no
service discovery, command, callback, network dependency, form or action link.
All evidence text is escaped; native collapsed details expose the original
attempt/error/checkpoint context. No completed cycle or acknowledged request
claims completed work, verified service recovery, independent review or release.

This synchronous bounded-history reader must run in a separately controlled
renderer if integrated with HTTP; it must not run in the HTTP/SSE event loop.
Inherited history/journal bounds remain in force. No latency or D22 acceptance
is inferred. Reads are not a cross-file atomic snapshot. Caller pins establish
consistency, not provenance authentication; callers retain responsibility for
trusted handle ownership and observation times. Existing experimental engine
feed-degradation policy differences and finite retention remain unresolved.

## Developer validation

- Related Python/API suite: **636 passed**, including the initial 17 new cases
  and 619 unchanged predecessor/supervisor/API cases. One existing
  Starlette/httpx deprecation warning.
- Final new suite: **22 passed**. Added checks cover acknowledgement versus
  recovery, disabled versus unconfigured services, reading while the writer
  lock is held and journal evidence later than the requested view time.
- Existing frontend DOM suite: **216 passed**.
- New HTML parser assertions verify safe text rendering and collapsed evidence.
  No browser/live UI, production responsiveness or independent review is claimed.
- A real scratch child process exits during restart dispatch. A second Python
  process reopens the series and renders the same uncertain attempt ID and
  interrupted cycle. Hash snapshots verify that reading changes no scratch
  files. This is an offline synthetic process-interruption drill, not a stopped
  production service or hardware durability experiment.

The first new-test run exposed two fixture/assertion issues: JSON-escaped error
text needed decoding for exact comparison, and the child process needed the same
synthetic two-cycle slot bound as its parent. A later added disabled-worker test
incorrectly used the learning schema; the corrected fixture uses the existing
release disabled-state schema. No implementation policy or original assertion
was weakened. The retained later failure log is `f0046_validation/new_tests_initial.txt`.
Final commands, successful log hashes and candidate pins are in
`F0046_VALIDATION.json`. Full fixed suites and independent review remain owned
by the controller.

## Supplied evidence and scoped audit

Read AGENTS.md, project contract `2026-10-01.profit-learning-directive-v1`,
predecessor implementation/limitations, market/status docs and local K03/K08
cards. Cards inform separation of observation time, provenance and validation;
no underlying paper was fetched or treated as operational instructions.

- Development manifest SHA-256:
  `e8b1958521125cf9985fbca5a34248240941964c61018d45f15c543c824dae21`.
  All 332 file hash/size pins and 418 source-body hashes match (gzip bodies
  decompressed only for compressed archives; sizes checked where declared).
  Errors are empty; no truncation field is declared.
- All 44 frozen validation-file pins and four test pins match. Exported bundle
  declarations agree with protected `config/validation_sources.json`, hash
  `b54253e4f3938511354ecc55b3b21037915a2e5939d082007b54ffac0a44df60`.
  Validation export errors are empty. These are separate validation fixtures.
- No task-diagnostics manifest, rebase conflicts or root recovery package exists;
  TASK declares no required diagnostic targets or root recovery binding. Runtime
  diagnostics say `not_required`; F0046 release diagnostics list no files or
  rejections. No missing required reference or prior traceback was invented.
- Inspected and hash-verified the supplied NHL play-by-play blob
  `1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
  332 plays, `rosterSpots`, `startTimeUTC`, and play fields `eventId`,
  `periodDescriptor`, `timeInPeriod`, `sortOrder`, `typeDescKey`. A synthetic
  schema-based negative test confirms it cannot stand in for a service heartbeat.
  Source publication time remains null; archive availability is unauthenticated.
  Historical logs and export time prove neither current participation nor recovery.

All 888 baseline source/test/doc/config/knowledge files (including AGENTS.md)
remain byte-identical. Only the new module, new regression file and F0046 docs
are added. Re-audited contract R01–R41 for this bounded scope: R17/R25/R28/R30
candidate supervision visibility advances; no production requirement is newly
fulfilled. Exact guarded contracts, independent forecasts, minimum20 daily first
issuances, USD1–5/max5, no fixed daily cap, immutable original decisions/outcomes,
manual wagering and measured profitability remain unchanged requirements.
No protected source, settings, thresholds, original tests, contract, source
archives or decision exports were edited. No production services, writable wager
database, parent-project data, credentials, account state, network, messaging,
launchctl, additional CLI helper or nested agent was accessed.

## Advisory disposition

`acceptance_checklist:grok`, text SHA-256
`ca6bcfb400450940a62bf764ec082a4b16d02ddda43bc80a9d2cd742a17989e3`:
**partly used**. Independently verified the text hash. Used the new-capability,
behavioral failure-path, original-test preservation and explicit remaining-evidence
checks. The suggestion does not inspect this workspace or constitute independent
review. Its item 11 mixes a daily issuance target with a 48-hour opportunity
window; rejected that framing and its applicability to this passive view.
No bets or fulfilled daily objective are inferred. Real service recovery and
independent controller review remain later evidence, not helper-approved facts.

## Remaining work

1. Controller: run the full fixed suites and independent read-only Codex review
   of these exact candidate bytes, preserving prior attempts and deadlines.
2. Trusted root: no exact passive release profile exists and discovery is
   disabled. Separately authorize integration and bind private journal ownership,
   authenticated observations and isolated status rendering; do not install from
   this candidate completion flag.
3. Authorized recovery environment: verify real stopped/stalled service recovery,
   resumed useful work, restart failures/cooldown and measured timing. Retain fixed
   checkpoint misses and uncertain attempts. Complete remaining M01 acceptance.
4. Trusted release: check source drift and immutable ledgers, then verify a fresh
   publication locally and via Tailscale. Code rollback must not restore an old
   wager database. No daily-target fulfillment or profitable performance is claimed.
