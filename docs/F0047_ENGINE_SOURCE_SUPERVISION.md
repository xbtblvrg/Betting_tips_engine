# F0047 — retain source failures without restarting healthy processes

Implemented the engine feed-degradation mismatch explicitly left open by
F0045/F0046. The offline journal previously counted completed partial/unavailable
feed cycles as process failures. After three checks it could dispatch a restart
even while the engine continued making progress. The protected supervisor
already distinguishes these conditions; this candidate brings the experimental
journal, retained cycle summaries and operator view into agreement.

`development_engine_health.py` recognizes only a completed cycle with a structured
partial/unavailable assessment, a positive integer error count, and an exact
match between the feed message and engine error. Other errors remain process
failures. Missing, invalid, future or stale progress still fails. Existing limits
remain: 300 seconds for completion, 900 seconds for running work, checks at least
60 seconds apart, three failures and a 900-second restart cooldown.

The existing `record_observation` and dispatch/series APIs now:

- Retain exact health/publication payloads and hashes, plus a versioned engine
  classification on new observations. Source-only failures reset the process
  failure streak, remain `source_degraded`, and never dispatch a restart.
- Reassess legacy trailing observations at their original observation clocks
  before counting new process failures. Old source-warning counters cannot
  accelerate a restart. Original events, reasons, counters, attempts, outcomes
  and deadlines are never rewritten.
- Refuse to certify recovery from a recognized source-degraded completion,
  even with a later publication. Clearing that warning still requires the
  existing post-intent completion/publication checks; this is caller-reported
  progress, not authenticated source recovery or delivery verification.
- Keep source classifications/messages in new cycle summaries and the escaped
  English operator view, including after rollover and later process health.
  Previously committed summaries remain unchanged. The view remains read-only.

This is tested candidate integration with existing offline utilities. It neither
changes the protected supervisor nor installs a service or modifies wagering.
Absence of a recognized warning does not prove complete coverage. Current views
expire journal observations after the existing 60-second observation lifetime;
retained feed metadata is diagnostic, not a current market offer. Synchronous
inspection still requires an isolated renderer for any future HTTP integration.
Finite journal/history capacity and caller-supplied, unauthenticated clocks remain.

## Developer evidence

- **673 Python/API cases passed**, including **32 new cases** and 641 unchanged
  predecessor/supervisor/API cases. One existing Starlette/httpx warning.
- **216 existing frontend DOM tests passed**. New HTML parser checks verify
  escaped warning text, retained history after recovery and read-only rendering.
- New behavioral coverage includes 33 successive source-warning cycles without
  dispatch; malformed/mismatched source metadata; stale/future/missing clocks;
  exact progress boundaries; threshold/cooldown; legacy counter migration;
  retained failed attempts and checkpoint misses; recovery-publication gates;
  independent worker progress; rollover; and reopening in another Python process.
- Final log hashes, commands, source pins and unchanged-file audit are recorded in
  `F0047_VALIDATION.json`; logs are in `f0047_validation/`. Initial new-test runs
  exposed callback fixtures using one argument instead of the existing two and
  an assertion using zero-based segment numbering. Those new fixtures were
  corrected; implementation thresholds and existing tests were not weakened.
- Git inspection was unavailable because the system Git launcher requires an
  accepted Xcode license. No system change was attempted. File SHA-256 comparisons
  against the pre-edit baseline verified all original test bytes and restricted
  the changed original files to the three documented experimental modules.

These are local developer checks, not independent review, production service
recovery, hardware durability, latency/D22 acceptance or measured profit.

## Supplied evidence and instruction audit

Read AGENTS.md, project contract `2026-10-01.profit-learning-directive-v1`,
predecessor limitations, market/status documentation and local K03/K08 cards.
Cards inform provenance and validation separation; underlying papers were not
fetched and source text was not treated as operational instructions.

Development manifest SHA-256:
`178834626c3cdca57f0b6f82201bac662d663852e88142d74cc1816650d5b957`.
All **346 file pins** and **415 declared source hashes** match; compressed source
bodies were decompressed for their content checks. Errors are empty and no
truncation field is declared. All **44 frozen validation-file pins** and **four
test pins** match. Bundle declarations match the protected catalogue, SHA-256
`b54253e4f3938511354ecc55b3b21037915a2e5939d082007b54ffac0a44df60`.
There are no missing required references in those manifests.

Task diagnostics, root recovery package and rebase conflicts are absent; TASK
declares no diagnostic targets or root recovery binding. Runtime diagnostics say
`not_required`; release diagnostics list no files or rejections. No prior failure
log or root authorization was invented.

Separately inspected the NHL play-by-play body
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
332 plays, `rosterSpots`, `startTimeUTC`, and play fields `eventId`,
`periodDescriptor`, `timeInPeriod`, `sortOrder`, `typeDescKey`. Its schema informs
a synthetic negative test: a sports log cannot substitute for a service heartbeat.
The body hash matches, but the current sports manifest row declares 133,955 bytes
while the file has 133,609; this metadata inconsistency remains unresolved and
does not affect the separately verified frozen validation bundle. Source publication
time is null and archive availability is unauthenticated. No source approval,
current participation, recovery observation or historical availability is inferred.

Re-audited R01–R41 for this bounded scope. R17/R25/R28/R30 advance at candidate
level only. All-market model/settlement validation, independent forecasts, exact
guarded contracts, minimum20 daily first issuances, USD1–5/max5, no daily aggregate
cap, immutable decisions/outcomes, manual wagering and measured profitability
remain unchanged requirements. No protected file, existing test, setting,
threshold, contract, source archive or decision export was edited. No production
service, writable wager database, parent-project data, credentials, account state,
network, messaging, launchctl, helper CLI or nested agent was accessed. No helper
advice was supplied.

## Remaining work and ownership

1. Controller: run full fixed suites and independent read-only review of these
   exact candidate bytes, retaining prior failures and deadlines.
2. Trusted root: separately authorize integration. No exact passive release
   profile exists and discovery is disabled; this changes existing experimental
   modules and cannot be installed under an inferred passive profile. Bind trusted
   observations, private journal ownership and bounded restart callbacks.
3. Authorized recovery environment: verify stopped/stalled real services resume
   useful work, retain restart failures/cooldown and fixed checkpoint misses, and
   measure timing. The full M01 recovery drill remains unfinished.
4. Evidence owner: reconcile the current NHL manifest byte-count discrepancy
   against the original archive without rewriting observation/publication times.
5. Trusted release: verify source drift and immutable ledgers, then fresh local
   and Tailscale publication. Rollback must never restore an old wager database.
