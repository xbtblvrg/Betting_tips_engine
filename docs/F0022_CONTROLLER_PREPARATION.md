# F0022 — recover service-input preparation

The bounded candidate restores the prior attempt's offline preparation checker
and adds a controller-manifest entry point. The three missing controller files
are now supplied and match the explicit recovery hashes. Both unchanged
`test_resource_service_profile.py` regressions pass. This is candidate input
validation, not service installation, recovery certification or a release.

## Implementation and failure evidence

D14 already inspects accepted run evidence; E393159 compares candidate trees;
the existing F0022 drill restores offline artifacts. This increment addresses
the actual remaining preparation failure instead of repeating those tools.
The previous checker and its 38 tests were recovered from the explicitly supplied
`20260930T223133830092-F0022/files` archive. The two test files are copied
byte-for-byte into previously absent paths; existing workspace tests are unchanged.

`development_service_preparation.assess_controller_service_preparation` accepts
an explicit quiescent workspace, pinned controller manifest bytes, separate
test/contract pins, and the original failed-run/log bindings and checkpoint.
It validates the actual `runtime-error-evidence-v1` three-file manifest schema
before reading sources. Missing, duplicate, escaping, unpinned, malformed and
oversized entries fail closed. Both byte counts and SHA-256 must match. The
underlying checker rejects symlinks, hardlinks and special files, bounds reads,
and reports every dependency even after an earlier read fails. It never executes
service scripts, configuration or diagnostic logs.

The report retains the failed-run identity, diagnostic hashes, fixed checkpoint,
and all source observations. Its fingerprint includes the exact manifest binding
but excludes workspace location and observation time. Repeated identical input
does not look like new evidence after restart. It does not schedule retries,
write attempt state, clear failures, infer test success or authorize release.
Matching files report `inputs_consistent`; all approval/recovery flags stay false.
The caller must authenticate pins separately and retain returned observations.
This is not a hostile concurrent-filesystem security boundary.

The retained traceback is from `20260930T221303911779-F0022`, the original
Python-test failure referenced by the previous attempt. The previous attempt
itself failed at implementation evidence, not a new Python test. Both identities
remain separate in [the evidence directory](f0022_controller_preparation/).
The run/result hashes for `20260930T223133830092-F0022` match the recovery prompt.
The current runtime-failure manifest is `not_required`; the release-failure
manifest contains no files. Neither was silently substituted with an invented log.
No rebase conflict file is present.

The [actual assessment](f0022_controller_preparation/assessment.json) reports
five matching dependencies, no blockers, and the still-missed M01 checkpoint
`2026-09-30T14:15:39.029647+00:00`. The three controller sources are:

| Source | SHA-256 |
| --- | --- |
| `scripts/development_service.py` | `7ff1e66b35e9e7bd325ccfbfe3ec091ae27c03f6a461dfd8d8a4e70496e46a6f` |
| `scripts/release_service.py` | `5733a67be2d16d13e7e2d0fdebd98fb4030cec237373d3e82e702bc841f2f8d4` |
| `config/development_runner.json` | `86385d85cc8bb3d1de4e87b3504170d881463b6cdef81336b6df82ae4067ddb7` |

The manifest digest and test/contract pins were recorded locally, not portrayed
as independent controller approval. The three hashes above separately match
the user's explicit recovery bindings. Original failed logs are diagnostic
data only; their hashes establish retained bytes, not historical source availability.

## Validation and supplied source scope

Twenty new controller-manifest cases cover repair then drift, exact inventory,
untrusted pins, size/hash conflicts, invalid JSON/numeric evidence, stable failure
identity, missed deadlines and read-only behavior. Together with the recovered
38 preparation tests and two unchanged service-profile tests, **60 pass**.
Full-suite results, exact commands and output hashes are recorded in
[verification.json](f0022_controller_preparation/verification.json).
The full Python/API suite passed **4,036 tests and five subtests** (one existing
Starlette deprecation warning); all **224 frontend tests** passed. All **436**
pre-existing source/test/doc/config/script files, including **186** test/fixture
files, remain byte-for-byte unchanged.

All **301** development-input files, **22** frozen validation files and **three**
controller source files passed size/hash verification. Development and validation
manifests report no errors or truncation fields. Frozen fixtures remain in their
separate validation namespace. [Input verification](f0022_controller_preparation/input-verification.json)
records exact manifest hashes. No source export was rewritten.

K03/K08 were read as provenance and validation source material. The supplied NHL
blob `1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`
was hash-checked and inspected: `id`, `startTimeUTC`, `gameState`, `rosterSpots`
and 332 `plays`, with `eventId`, `periodDescriptor`, `timeInPeriod`, `sortOrder`
and `typeDescKey`. Its source publication time remains null, current participation
unverified, archive availability unauthenticated. This preparation utility does
not price bets or convert historical observations into current evidence.

## Developer audit and remaining work

Reviewed AGENTS.md, contract `2026-10-01.27` (R01–R39), M01/D14/E393159 evidence,
Stake market contract and project status. R25/R28/R30/R33 preparation traceability
advances; R37's unchanged service-profile assertions now pass with supplied inputs.
No requirement is declared fully fulfilled by this utility. It leaves all betting,
learning, UI, model, publication, settlement and execution behavior unchanged.
The active target is **20+ distinct funded first issuances**, not older documents'
15 target. USD1–5, null daily cap, manual wagering, immutable decisions, exact
fresh contracts, independent forecasts and measured-profit requirements remain.
Tests establish no issuance volume, betting edge, profitability or model approval.

The candidate implementation is complete; M01 and production delivery remain
open. The controller must run its fixed suites and independent read-only Codex
review. There is no exact passive release profile and discovery is disabled:
protected integration requires separately authorized root work, source-drift and
immutable-ledger checks. An authorized recovery environment must still exercise
actual stopped/stalled services, inflight work, durable attempts and restart
backoff/cooldown, retaining misses and timing. Fresh local/Tailscale publication
verification remains release-owned. Code rollback must never restore an old
wager database. No production services, launchctl, credentials, account state,
external messaging, writable ledger, nested agents or helper CLIs were accessed.
