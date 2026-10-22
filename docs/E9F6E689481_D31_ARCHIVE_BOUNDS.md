# E9F6E689481: bounded D31 archive reads

Candidate implementation only. Original deadline remains
**2026-10-03T12:34:33.076647+00:00**. No production integration or release occurred.

## Evidence and bounded increment

The task-owned engineering observation
`63abe13d287c8d44f56cb8694dc1226c65c00de625266e2d2100ea320df89dc2`
reports D31 as `candidate_ready`, with independent review, root integration,
migration review and live verification outstanding. It supplies no new failing
test or model defect. Existing D31 source/tests were inspected and its **106
tests passed before changes**. The earlier D31 evidence document is historical;
its full-suite results are not this task's validation results.

Review found that `_unpack` used unlimited `gzip.decompress` before verifying
the archive's declared length. Preview and pruning also call this reader when
checking archived references. A small compressed member could therefore expand
far beyond its declared size before rejection, interrupting retention/restore.

The existing candidate reader now validates a positive integer length bounded
at 256 MiB, then streams at most that declared length plus one byte before
checking exact length, both SHA-256 checks, storage type and publication JSON.
The existing 128 MiB decoded-publication limit remains. The separate 256 MiB
stored-byte bound allows compressed BLOB envelope overhead; it is a resource
limit, not a market/model/funding threshold. Writes enforce the same bound
before creating an archive, so they cannot prune into an unreadable archive.
Oversized existing archives require root investigation and remain unchanged.
Malformed gzip failures roll back through the existing transaction boundary.
This bounds each archive expansion; it does not establish a whole-process
memory bound or measured HTTP responsiveness.

The change is integrated into the candidate's existing preview, prune and restore
operations. No alternate archive format, runtime switch or parallel ledger path
was introduced. Existing exact TEXT/BLOB bytes, hashes and audit semantics remain
compatible within the bound. The settlement/receipt candidate is unchanged.

## Preparation and source provenance

- Verified **325 development file pins, 406 raw source archives, four frozen
  validation bundles and their test pins**, the protected validation catalogue,
  and the task-owned engineering observation. No manifest errors or pin failures.
- The immutable decision export contains **179/179** records, `truncated=false`:
  53 open, 73 lost, 50 won and three pushes, across **37 recorded game IDs**.
  These counts describe supplied original decisions, not independent observations,
  verified account tickets, current availability or proof of profitable results.
- Existing integration tests round-trip the actual frozen publication and retain
  all exported original decisions/outcomes. They also exercise the real Engine's
  settlement method against isolated SQLite fixtures, including exact period
  scores, accepted receipt terms, separate tickets and audited cash corrections.
  Test-created dates/receipts/scores are synthetic; no source time is inferred.
- `data/task_diagnostics/manifest.json`, `data/root-recovery-package.json` and
  `data/rebase/conflicts.json` are absent, with no corresponding TASK binding.
  Runtime diagnostics are `not_required`; the release-failure manifest has no
  files or rejected entries. No required declared reference is missing.
- K03 and K10 were read as source material for timestamp and execution semantics.
  The export timestamp is never treated as historical availability. No sports
  forecast, price, settlement target or model approval was generated here.

## Tests and developer audit

All new regressions are in `tests/test_e9f6e689481_archive_bounds.py`.
Before repair: **12 failed, three passed**. The failures enforce bounded streaming,
early invalid-length rejection and refusal to write an archive beyond the reader
bound. Regressions also cover exact-bound TEXT/BLOB/nested-compressed restores,
CRC/truncation/appended-member rejection, idempotency, and rollback of an entire
batch after an earlier row has already been archived/pruned.

Final validation: **121 targeted tests**, **7,221 full-suite Python tests plus
five subtests**, and **216 frontend DOM tests** passed. The Python suite emitted
one Starlette/httpx dependency deprecation warning. Python checks used the
prescribed project interpreter with `PYTHONPATH=src OMP_NUM_THREADS=1`.

`E9F6E689481_EVIDENCE.json` records final validation results and hashes; logs are
under `e9f6e689481_validation/`. Only the existing experimental retention module
changed; all pre-existing tests, lifecycle code, protected files, configuration,
model/chronological validation and earlier evidence documents remain unchanged.

This increment supports R07/R25/R30/R38/R40 and O15 evidence preservation.
Other requirements are preserved, not newly fulfilled: independent forecasts;
exact contract/settlement identity; EvidenceGuard and publication gates; USD1–5
with no fixed daily cap; unchanged first-issuance checkpoint; manual wagering;
and separate decision/game/ticket counts. There is no new league/market support,
daily-volume fulfillment, fit/promotion, historical-availability validation,
realized-profit claim, deployment or account verification. No production service,
credential, parent data or live wager database was accessed. No helper was used.

## Remaining controller/root work

1. Run independent controller review and fixed release suites on the exact bytes.
2. Verify the production baseline; integrate candidate retention and lifecycle
   operations through the separately trusted root path, inventory external
   publication references, and review legacy migration and manual authentication.
   No exact passive profile exists and utility discovery is disabled.
3. Run the root-owned release-copy restore drill and immutable-ledger checks,
   then authorized activation and fresh local/Tailscale publication verification.

Those phases remain outstanding; the `development:D31` production gap is not
closed by this bounded candidate repair. The deadline and full original acceptance
are retained. Any still-unmet acceptance must be reported at that deadline.
