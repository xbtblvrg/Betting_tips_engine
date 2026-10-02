# Frozen regression input delivery and recovery — 2026-10-02

Scope: generic validation infrastructure. No betting model, probability, selection, stake, original decision, settlement rule or automated order change.

## Result

The two diagnosed test-input blockers were repaired and deployed at **08:00:07 UTC**. The existing supervisor dispatched fresh original-task runs after the reviewed dependency handoff: **F0038: 20261002T080640758486-F0038**, **D20: 20261002T080707914622-D20**. Both were observed running at 08:08:58 UTC. Their underlying feature work is not declared complete.

The old NFL test failure used a changing research snapshot as a historical regression fixture. Validation now uses an isolated copy with the exact archived cohort while preserving current research. D20 release preparation now supplies the complete original manifest and all 24 referenced bodies. Missing, changed, partial or unsafe inputs fail validation. A root-reviewed exact-run handoff checks installed sources, archives, original acceptance chains and validation/review artifacts before one retry; it does not reset old deadlines or erase failures.

## Evidence and status

| Requirement | Actual status |
|---|---|
| Authentic frozen inputs, separate from current research | Implemented and independently tested; 106 immutable config/archive inputs provisioned |
| Full installed-baseline regression suite | 5552 Python tests plus 1 exact original native-boundary control passed; 272 DOM tests passed |
| Original tests preserved | All 268 pre-existing test artifacts unchanged |
| Pending-candidate fixture paths | 17 original E514 tests passed through the new context; 497 original D20/D09 tests passed with the complete frozen cohort; these do not certify those candidates' full acceptance |
| Native write/isolation boundaries | 12 context permission denials and 9 D20 mutation denials verified, including alias paths |
| Controlled installation and independent review | Complete; 10 code/test files installed with rollback and immutable-ledger checks |
| Local and Tailscale operation | Verified at 08:03:31 UTC; publication 5696 generated 08:03:02 UTC, same response hash on both routes |
| Original decision records and runtime continuity | Immutable fields unchanged; web PID 266 and learning PID 271 uninterrupted |
| Exact failed-run recovery | Both tasks automatically dispatched; F0038 attempts 34→35 and D20 35→36; original backoff and failed-run evidence retained |
| Original F0038/D20 feature completion | Still in progress; separate complete validation, review and release remain mandatory |
| Model learning improvement or profitability | Not evaluated or claimed by this infrastructure change |
| Private validation-copy cleanup | Pending separately tracked work; no unsafe path-based deletion introduced |

The complete installed baseline does not contain the accepted-but-uninstalled E514/D20 collecting tests. Its context routing therefore correctly reported `not_required`; a separately bound post-validation absence check covers all 29 trigger paths. The accepted candidates' fixture proofs remain distinct from installed-baseline validation. No pending model code was imported into production to force a test path.

## Reproducible records

All paths below are relative to the project and contain exact source/data hashes:

- `data/fixture-delivery-followup-20261002/task.json` — fixed original two-hour deadline, scoped completion and remaining work.
- `data/fixture-delivery-followup-20261002/release/install-manifest.json` — exact installed version and original test inventory.
- `data/fixture-delivery-followup-20261002/release/release-review.json` — independent source-bound approval.
- `data/fixture-delivery-followup-20261002/release/native-suite-proof.json` and `dom.log` — complete actual controller validation.
- `data/fixture-delivery-followup-20261002/release/install-journal.json` — controlled install, service restarts and preserved records.
- `data/fixture-delivery-followup-20261002/release/live-20261002T080331318001/report.json` — fresh local/Tailscale verification.
- `data/fixture-delivery-followup-20261002/release/handoff-application.json` and `live-recovery.json` — exact handoff and observed new run identities.
- `data/fixture-delivery-followup-20261002/final-audit.json` — requirement/status mapping.
- `data/fixture-delivery-followup-20261002/retention-task.json` — separate retention owner, deadline and acceptance.

Initial partial-cohort failure, unsafe-cleanup probe, malformed-evidence failures, first full-suite compatibility failure and first post-install publication wait are retained under the same evidence folder. They were not replaced with success reports. The successful full run followed the relevant fixes; no original test was edited or weakened.

The overall engine and every user requirement are **not** declared complete. This release restores these two validation/retry paths; currently running jobs and broader capability gaps remain separately accountable.
