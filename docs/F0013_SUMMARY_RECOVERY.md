# F0013: preserve contradictory validation summaries

This isolated increment repairs both findings from rejected run
`20261001T225137076371-F0013`. The prior parser and its 60 regression cases were
absent from this workspace; they were recovered from the supplied read-only
candidate, with the prior test file copied byte-for-byte to a new path here.
The current upstream `decision_export_preparation.py` remains unchanged.

The detached `assess_validation_log_archive` now accumulates failure observations
across every recognized summary in both streams, including summaries beyond the
two-detail retention bound. Multiple summaries remain ambiguous: no combined test
counts are inferred. Invalid recognized summaries retain a count and bounded
reason details (duplicate label, unknown label, oversized count). Parsing continues
through their tokens so a later failure token is not lost. An invalid summary
prevents a later passing footer from producing `reported_pass`.

`summary_count` now includes valid and invalid recognized summary lines;
`invalid_summary_count` discloses the invalid subset. Each stream exposes
`failure_summary_observed` independently of the retained details. Timeout,
interruption and unverified termination retain precedence without erasing these
observations. Unsupported prose is not interpreted as a pytest summary. The
existing manifest/log size limits and bounded failure-name retention remain.

The evaluator receives detached bytes, performs no path reads or writes, executes
no log content and changes no runner or retry policy. Hashes establish byte
consistency; the caller must authenticate their origin and process outcome.
Even `reported_pass` leaves all test/retry/promotion/model-authority flags false.

## Evidence and tests

[Input verification](F0013_SUMMARY_INPUTS.json) records 431 matching development
files, 579 matching raw-source bindings, 44 frozen validation files, four pinned
validation tests and three controller source pins. The development content pin
and frozen catalogue pin also match. Manifest errors are empty. The supplied
decision export contains 334 records and declares `truncated=false`; its exact
bytes remain unchanged. Export time is not source availability or placement
verification. K03 and K08 informed provenance and evidence interpretation only.
The inspected official NHL JSON has actual game/season, `startTimeUTC`,
`periodDescriptor`, `homeTeam` and `awayTeam` fields; it is retrospective sports
evidence, not evidence that a validation process completed.

No task-diagnostic manifest, root recovery package or rebase-conflict declaration
is present, and TASK declares no root recovery targets. The runtime manifest says
`not_required`; the release-failure manifest contains no files. No missing log is
invented. The supplied prior run/review pins were independently verified:

- Run: `96cfd68c9fe2281e7110fc91c614fd6ab4fc7f9b3fc4b806f52219cb66bf7ebb`.
- Review: `17940625237e79b5e71c29d4175586cdf87b93eeb7a6f1f022a75c7d0df4b18e`.

[Exact synthetic replay](F0013_SUMMARY_REPLAY.json) demonstrates both findings
against the old and repaired implementations. `1 failed in 0.1s` followed by
`2 passed in 0.2s` now retains failure presence. A duplicate `1 failed` summary
followed by that passing footer changes from `reported_pass` to
`incomplete_test_report`, retaining failure presence and unknown combined counts.
These are synthetic review reproductions, not real process measurements.

New tests are in `tests/test_f0013_summary_recovery.py` (45 cases), alongside
the recovered `tests/test_f0013_validation_log_rehearsal.py` (60 cases). Coverage
includes every supported failure label, both streams, reversed ordering, failures
beyond detail bounds, invalid tokens before and after failure tokens, oversized
counts, zero failures, repeated invalid reports, termination precedence and
input immutability. Existing tests remain byte-for-byte unchanged.

Commands (fixed runners and acceptance assertions unchanged):

```sh
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_f0013_validation_log_rehearsal.py tests/test_f0013_summary_recovery.py
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q
node --test tests/web.test.mjs tests/web_model_pick_allocation_cases.mjs
```

The focused run passed 105 tests; the frontend run passed 272. Exact logs:
[focused](F0013_SUMMARY_FOCUSED.log), [Python/API](F0013_SUMMARY_PYTHON.log),
[frontend](F0013_SUMMARY_DOM.log). Final validation and preservation results are
recorded in [the audit](F0013_SUMMARY_AUDIT.json).

The complete Python/API run finished in 170.45 seconds: 8,617 passed, five
skipped, five subtests passed and one failed. The unchanged
`test_claude_development.py::test_native_no_tool_process_cannot_read_or_write_root_or_external_workspace`
failed because macOS denied nested `sandbox-exec` with
`sandbox_apply: Operation not permitted` (exit 71). This matches the prior
attempt's environment failure; the suite is not reported as passing. No test was
skipped, weakened or retried to conceal it. The controller must rerun the fixed
suite where nested sandbox execution is permitted. The focused tests and complete
suite exercised the same final implementation bytes.

## Developer audit and remaining work

AGENTS.md and contract R01–R41 were inspected. This bounded diagnostic repair
advances R25 validation-evidence retention. All pre-existing source, test,
configuration, documentation and knowledge files remain unchanged. It does not
change forecasts, model training, contract/settlement gates, publications, sizing,
USD1–5/max5, null daily cap, the minimum20 objective, checkpoint, immutable
decisions/outcomes or manual-wagering policy. There is no claim of current
participation, model approval, issuance quantity, profitable performance or
completed M07. No production service, ledger, credentials, account state,
messaging, launchctl, helper CLI or nested agent was used.

The Grok advice `acceptance_checklist:grok`, SHA-256
`7cd6a3b0b488f9f05f9ca81ae3368add1670ac8c0be0e49ca300c9b6e26715ff`,
was partly used: preserve original tests, open findings, deadlines and release
boundaries. Its inference of zero funded instructions is rejected: this task
does not measure current issuance. Its proposed timeout-limit change and full
milestone completion conditions do not authorize protected edits or replace this
bounded recovery acceptance. No helper test or source claims were adopted.

The original engineering findings remain open, with deadlines unchanged:

- `F0013:release_failed`, run `20261001T083205728710-F0013`, first seen
  `2026-10-01T19:20:46.387807+00:00`, due
  `2026-10-01T23:20:46.387807+00:00`, evidence
  `3f1df57bed45ec7eeccfed02fc04a6c204ae7c350f11208c0cb0d0ed64864ded`:
  development step exceeded its time limit. This evaluator does not prevent
  timeouts; the controller must collect actual phase timing and repair the cause.
- `F0013:accepted_drift`, run `20261001T130223324609-F0013`, first seen
  `2026-10-01T22:53:06.708402+00:00`, due
  `2026-10-02T02:53:06.708402+00:00`, evidence
  `c926824189b85263d9836ce1e2762ed46f0c399b2225e0ec986c375be14cc1d7`:
  accepted snapshot no longer contained the reviewed export-preparation change.
  The current file is present and preserved; presence alone does not resolve
  accepted-snapshot drift. The controller must review and bind its exact bytes.

Next phases belong to the controller: independent read-only review, complete
fixed-suite validation in its environment, separately authorized integration and
release, then fresh local/Tailscale publication verification. There is
`no_exact_profile`, and passive utility discovery is disabled; this candidate
does not grant itself a release path or mint a receipt. M07 still needs actual
shadow validation, rollback/restore drills, always-on host and alert evidence.
Code rollback must never restore an older wager database. Those broader gaps do
not convert the working experimental evaluator into production support.
