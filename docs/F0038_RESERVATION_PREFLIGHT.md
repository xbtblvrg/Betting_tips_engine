# F0038: validate the durable reservation boundary

This candidate adds a missing dependency check to the existing worker dispatch
adapter. A generator replacement for `development_restart_dispatch._reserve`
previously passed worker preflight and returned without writing a reservation;
the restart callback could then run without durable dispatch evidence. An
incompatible signature could instead consume the new intent before failing.
The adapter now validates the reservation writer's synchronous callable path
and exact positional call shape before journal access, observations or callbacks.
It uses the existing callable validation rules, including metadata and partial
checks. This does not prove the semantics of an arbitrary synchronous wrapper;
dependencies must remain stable during dispatch and still require code review.

The existing source already contains the earlier F0038 dependency and callback
repairs. They are preserved. The supplied prior run
`20261001T173756487986-F0038/run.json` matches its recovery pin
`d04262452585888f0fe73b21ff20aec0e480f06793f1798f00e3c5e47ad5d546`
and reports `No implementation change; a report alone is not a completed task`.
That run's `files/src/betting_engine/development_worker_dispatch.py` is absent;
its retained files contain tests and documentation. This increment changes
implementation and does not claim to restore an authenticated missing blob.
The open `F0038:accepted_drift` citation
`290e3b0996fab0eb029f2efc12fecea7ecce5a7ad7f307a84d2ce602d8cd6658`
is a finding hash, not a module hash. Controller snapshot reconciliation remains
required; neither the finding nor its original deadline is rewritten here.

New tests in `tests/test_f0038_reservation_preflight.py` cover missing,
noncallable, incompatible, coroutine, generator, async-generator, partial and
misleading-signature reservation dependencies through all three public entry
paths. They require rejection before journal access or callback invocation.
All 30 cases failed before the repair. The three worker recovery drills reject
the broken reservation without consuming the two prior failures, then dispatch
in a fresh Python process and terminate after the durable reservation. Reopening
retains uncertainty, forbids replay, preserves the original checkpoint miss and
900-second cooldown, and permits a distinct later attempt. These are synthetic
offline process-exit drills, not restarts of production services.

Preparation verified all 346 supplied development-file hashes and sizes, all 44
frozen validation files, their four test pins, and exact root-declaration/export
agreement. The development manifest has no errors or reported source truncation.
`data/task_diagnostics/manifest.json`, `data/root-recovery-package.json` and
`data/rebase/conflicts.json` are absent; this TASK declares no diagnostic document
or root-recovery target. Runtime diagnostics say `not_required`; the release
failure manifest contains no files. No missing evidence was synthesized.
Knowledge cards K03 and K08 inform the provenance and measurement limits:
retrospective sports snapshots are not current participation or historical
availability evidence. This interface repair neither fits nor activates models.

Current-run commands, results, hashes, preservation checks and the hash-bound
Grok advice disposition are in `F0038_RESERVATION_EVIDENCE.json`; raw test logs
are in `f0038_reservation_validation/`. Earlier F0038 documents are historical
evidence, not validation results for this increment.

Developer audit: only the existing worker adapter changes. All original tests,
project contract, root policy, guards, funding settings and immutable decision
exports remain byte-identical. USD1–5 sizing, no fixed daily stake cap, the daily
target/checkpoint and manual-only wagering remain unchanged. No live database,
production service, credential, account, model activation or release receipt
was accessed or modified. This utility establishes no daily delivery, betting
performance or profit result.

The bounded candidate implementation is complete subject to controller checks.
Independent controller review, reconciliation of the accepted snapshot,
separately authorized root integration/release and live recovery/publication
verification remain pending. There is no exact passive release profile and
discovery is disabled. The broader M01 milestone remains open; callback request
success does not establish workflow completion or production recovery.
