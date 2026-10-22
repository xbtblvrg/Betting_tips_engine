# D31 candidate: publication retention and lifecycle integrity

Recovery of run `20261001T132639285949-D31`; candidate only, not activated.
The exact prior run/review hashes were verified before restoring its two
experimental modules and six regression files. Those six tests and the retention
module remain byte-for-byte unchanged. No protected integration was edited.

## Reproduced and repaired settlement race

The unchanged Engine reads an open ticket and calculates its return before
calling `SportsOutcomeStore.settle`. A receipt committed between those steps
changed USD1 at 1.90 to USD2 at 1.98, but settlement still recorded about USD0.90
profit. The loss case recorded -USD1 instead of -USD2. The new regression file
produced **4 failures, 3 passes before repair**; the retained log is
`d31_validation/race-before-fix.log`. Fixtures are synthetic software checks,
not claims about actual bets or account execution.

Settlement now reads accepted terms and calculates cash inside `BEGIN IMMEDIATE`,
serializing the calculation with receipt imports and manual corrections. Exact
receipt decimals are used where available; legacy tickets retain REAL arithmetic
using their current locked terms. The caller's earlier P&L is not authoritative.
Cash and sports outcome commit together or roll back. Audited manual cash
overrides remain intact while pending sports outcomes receive exact-scope result
evidence and original-probability diagnostics. Existing outcomes cannot be
replaced by a retry, and original system decisions remain unchanged.

The new tests exercise the real `Engine.settle_bets` method with deterministic
interleavings, wins/losses/pushes, renamed receipt replay, manual overrides before
and during settlement, exact decimal arithmetic, and cash-write failure/retry.
Only settlement runs; no Engine loop or source fetch is started.

## Complete bounded acceptance retained

- Receipt identity is stable across upload names; conflicting terms fail closed.
  Distinct receipt IDs remain separate tickets. Explicit reconciliation retains
  accepted decimals separately from original model/quote fields and records
  before/after audit rows. Historical import and manual-settlement defects are
  reproduced against the unchanged Store in `test_d31_baseline_defects.py`.
- Manual overrides require actor, reason, evidence, request identity and a current
  optimistic-concurrency token. Replays are idempotent; new revisions append
  audit rows. Sports outcomes and cash corrections remain distinct.
- Retention uses an explicit cutoff and protects latest/recent/referenced
  publications. Database references are rechecked inside the pruning transaction,
  including serialized audits and archived references. Ambiguous JSON fails
  closed. Exact TEXT/BLOB bytes, SHA-256 indexes and archive events commit with
  persisted readback verification before pruning. Restore refuses identity
  conflicts. Ticket, decision, source/review/closing/outcome tables are not pruned.
- Restore drills include the actual frozen publication and exported immutable
  decisions/outcomes, plus synthetic separate-ticket/manual-revision lifecycles.
  This is logical SQLite hot-table retention, not an external backup or measured
  disk-space reduction. External-only links still require root inventory.

## Preparation and developer audit

Verified 319 development file pins, 378 raw source-response hashes, four frozen
validation bundles and their test pins, the protected catalogue hash, and the
task-linked engineering observation. The manifest reports no errors; the
decision export has 179/179 records and `truncated=false`. K03 and K10 were read
as provenance/execution source material. Export/copy time is never historical
availability, current participation or verified execution.

`data/task_diagnostics/manifest.json`, `data/root-recovery-package.json` and
`data/rebase/conflicts.json` are absent; TASK declares no diagnostic payload or
root-recovery binding. Runtime/release-failure manifests contain no retained logs.
The exact prior review and engineering observation supply the recovery finding.
No declared frozen fixture is missing or changed. This isolated Git branch has
no commits; the controller's claimed root baseline cannot be verified locally.

`D31_EVIDENCE.json` records validation results, preparation and candidate hashes,
and the hash-specific advisory disposition. Logs are under `d31_validation/`.
All pre-existing source/config/test/document bytes were checked for preservation.
Validation: **106 D31 tests**, **7,114 full-suite Python tests plus five subtests**,
and **216 frontend DOM tests** passed. The Python suite emitted one dependency
deprecation warning. All checks used isolated fixtures and the prescribed Python
interpreter with `PYTHONPATH=src OMP_NUM_THREADS=1`.

Controller next: full fixed-suite validation and independent read-only review.
Root next: verify the baseline, integrate these candidate operations and the
settlement-only adapter, authenticate manual actions, review legacy migration
and external references, run a release-copy restore drill and immutable-ledger
checks, then verify authorized activation and local/Tailscale publication health.
No passive profile is available; passive discovery is disabled.

No production services, credentials, account state or live wager database were
accessed; no live pruning, ledger edit or deployment occurred. No deadlines,
funding settings, stake limits, models or evidence gates changed. USD1–5, no fixed
daily cap, immutable decisions and manual wagering remain requirements. D31
establishes no new market support, daily issuance fulfillment or measured profit.
