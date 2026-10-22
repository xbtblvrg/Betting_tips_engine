# F0042 — durable cycle chronology repair

Implemented a runtime change in the existing **offline candidate** history
wrapper. Previously, an observation could predate its durable cycle start, a
later service could receive an earlier timestamp, and a result could be marked
completed before the observation it summarized. The wrapper now checks every
clock sample against the last accepted instant, including the finish sample.
Equivalent timezone representations compare as instants. No time is invented,
clamped or substituted, and no stored record is rewritten.

An invalid service sample becomes a retained operation error through the existing
service-isolation adapter. The affected journal receives no observation or
restart; its empty dispatch ownership lock can still be created. Earlier valid
work remains. A regressed finish propagates an error and leaves the durable start
interrupted without a result. Reopening or reusing that ID never retries it.
This does not add a persisted diagnostic for a failed finish: callers must retain
the raised error; the history retains the interrupted start. Later new cycles
still obey original cadence, cooldown and checkpoint rules.

The current-base service-local rejection repair and F0047 `engine_assessment`
retention are preserved. The supplied accepted-drift hash identifies diagnostic
evidence, not required source bytes. This candidate does not resolve the
controller finding, rewrite accepted snapshots or mint a release receipt.

## Validation

- New file `tests/test_f0042_cycle_chronology.py`: five behavioral cases covering
  start/observation ordering, cross-service ordering and isolation, finish ordering
  with independent-process reopen, unknown restart outcomes with retained fixed
  checkpoint/cooldown, and equivalent UTC offsets.
- Before the implementation change: **3 failed, 2 passed**, reproducing the three
  ordering defects. Retained log: `f0042_chronology_validation/before.log`.
- New tests, original history/cycle/series/view/source-supervision tests,
  dependencies, supervisor, release-supervision and API checks: **1,307 passed**,
  one existing Starlette/httpx deprecation warning, 28.50 seconds.
- Frontend DOM suite: **216 passed**, no failures. No UI or market code changed.
- Baseline comparison: all **372 existing test-tree files** unchanged. The only
  changed pre-existing source/test/config/doc file is
  `src/betting_engine/development_supervision_history.py`. Protected code,
  configuration, policy and guards remain byte-identical.

Commands, hashes and preservation results are recorded in
`F0042_CYCLE_CHRONOLOGY_VALIDATION.json`. The child-process checks use temporary
private journals, not real services; these timings are not live latency or
hardware-durability acceptance. These are developer checks, not independent
controller approval.

## Evidence preparation and requirement audit

Read AGENTS.md, all 41 contract requirements, market/status docs, and the supplied
K03/K08 knowledge cards. Verified **346 development files**, **415 original source
bodies**, **44 frozen validation files**, four declared regression-test pins and
the protected validation catalogue hash. No declared reference was missing or
mismatched. Both export manifests report no errors; the development manifest has
no truncation field. The runtime manifest says `not_required`, and the release
failure manifest supplies no files or rejected entries. Task diagnostics,
root-recovery package and rebase conflicts are absent; TASK declares no root
repair targets or diagnostic binding requiring these optional inputs.

The supplied previous run pin matches exactly and retains the rejection
“No implementation change; a report alone is not a completed task.” Unlike that
attempt, this increment changes executable source and demonstrates failures
against the entry implementation. Its original due date and failed attempts
remain untouched. The advisory text hash was verified; durability, offset and
preservation suggestions were partly used. Automatic drift resolution from a
file hash was rejected as outside this candidate's authority.

Inspected the supplied official NHL landing snapshot
`f0df30e68a97b5c78edf6b8a563bdf6b87b104ad3f1d48ae689d1d91e118fe75`:
it has `startTimeUTC`, `gameState`, `periodDescriptor` and `summary`, not a `plays`
array. Sports timestamps are not service-clock or current-participation evidence.
Snapshot/export times do not establish historical availability or new validation.
This supervision increment does not need sports probabilities or price fixtures.

R17/R25/R28/R30/R39 receive bounded candidate evidence only. Re-audited R01–R41:
all-market validation, independent forecasts, exact guarded contracts, minimum20
daily first issuances, USD1–5/max5, no fixed daily cap, original decisions/outcomes,
manual wagering and measured profit remain separate unchanged requirements.
No new source/model/settlement approval or profit claim is made. No production
service, live wager database, credentials, account state, external messaging,
network, launchctl, helper CLI or nested agent was accessed.

## Remaining controller work

1. Run the complete fixed suites and independent read-only Codex review.
2. Reconcile the accepted snapshot and separately authorize integration through
   the trusted release path; no exact passive profile exists and discovery is
   disabled. Bind real observation provenance and bounded callbacks.
3. Perform the real-service recovery drill, retaining failures and fixed
   checkpoints; verify source/immutable-ledger consistency and fresh publication
   locally and through Tailscale. No production milestone is closed here.
