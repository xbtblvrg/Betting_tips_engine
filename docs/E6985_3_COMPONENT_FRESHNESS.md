# E6985CA1F4D-3: prevent stale component recovery

The bounded candidate repair is implemented in the existing `audit_components`
path, consumed by `/api/component-health` and engineering intake. A new health
poll cannot label expired publication-derived source, model, endpoint or NCAA
reports healthy. Explicit failures and missing/unimplemented evidence remain
visible. This is component evidence validation, not new betting capability.

## Observed defect and implementation

The pinned engineering registry recorded a fully fetched catalogue (107/107
fixtures, 214 endpoint responses, no endpoint issues), but its last check was
`2026-09-30T23:32:56.255912+00:00`. At the registry observation
`2026-10-01T00:04:41.669250+00:00`, that catalogue was stale. The separate
operating audit was also stale. Retrieval counts alone did not explain either
failure. The registry's successful catalogue observation at 23:33 remains valid
historical evidence; it does not establish continuing freshness.

Before this change, a fresh component poll over a 20-minute-old publication
returned **healthy** for independent sources, model forecasts, Stake endpoints
and NCAA discovery. The unchanged engineering consumer accepts healthy component
checks from a fresh poll as recovery evidence. The new integration regression
demonstrates that this could incorrectly resolve an existing component gap.

`component_health.py` now binds publication-derived checks to the original
`generated_at`. It reports the timestamp field, original value, age, existing
maximum age and `fresh`/`stale`/`future`/`missing`/`invalid` state. Expired successes
become stale; original errors remain degraded, missing reports remain missing,
and implementation gaps remain unimplemented. A stale empty fixture observation
does not imply a valid empty calendar. Catalogue diagnostics separately expose
their own evidence clock and explicit failure reason, retaining endpoint issues.

The publication window remains 300 seconds and the existing catalogue health
window remains 1800 seconds. The catalogue refresh target remains 15 minutes;
this change neither reschedules jobs nor repairs their execution. Fresh catalogue,
worker and literature observations retain their independent clocks. Source fetch
and publication times are never replaced with the health poll time. A fresh
source report is not certification of current player participation.

## Provenance and existing candidate

[Machine-readable evidence](E6985_3_COMPONENT_FRESHNESS_EVIDENCE.json) pins the
inputs, implementation and tests, retains exact engineering references, and
records an explicitly retrospective replay at the frozen export time.

- All 303 development input file hashes, five engineering files, 24 sports source
  files, two frozen validation bundles and their test pins verified. All 214
  catalogue endpoint references matched decompressed response hashes. No manifest
  errors or hash failures were found. The decision export is not truncated.
- The rebase conflict only concerns three test paths in
  `tests/test_d12_live_shadow.py`. The upstream paths correctly use frozen
  `tests/fixtures/d12_origin` evidence. They and all acceptance assertions remain
  byte-for-byte unchanged; no root fixture repair package was supplied/required.
- Existing catalogue context and NHL recovery candidate tests passed before the
  repair. The requested previous-attempt `files` directory is absent. Runtime
  diagnostics are `not_required`; the release-failure manifest contains no logs.
  No missing traceback was invented.
- Read AGENTS.md, the project contract, market/status documentation and local
  knowledge cards K03/K08. Inspected actual official NHL JSON fields including
  `startTimeUTC`, teams, period descriptor, plays and roster spots. The actual
  WNBA injury Parquet schema contains athlete/team identity, `as_of_date`,
  `injury_date`, status and source fields. Its manifest has unknown source
  publication time and unverified current participation. Neither historical
  observations nor the export timestamp establish current availability.

The later frozen publication retains three failures: NHL/2027 schedule,
NHL/2027 period scores, and NCAAF/injuries (`not_integrated`). The supplied public
sports bundle contains no NCAAF injury source. The catalogue replay remains
degraded at age 2005.148265 seconds. No refresh or source recovery is claimed.

The unchanged immutable export contains 161 original decisions across 34 games:
45 lost, 38 won, three pushes and 75 open. The 86 resolved decision P&Ls sum to
**−$9.3478 after recorded stakes**, rounded to four decimals. The JSON retains
game clusters and the original issuance interval, September 28–October 1 UTC.
These are decision outcomes, not individual-ticket or account-verified returns;
related selections are not independent samples. Operating costs remain unknown.
No model selection, fit, chronological split or new performance claim uses these
descriptive results.

## Tests and scoped instruction audit

All Python tests used `PYTHONPATH=src OMP_NUM_THREADS=1` with
`/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q`.

- Existing candidate and frozen rebase regressions: **149 passed**.
- New component freshness, existing component/engineering/catalogue regressions:
  **70 passed**, including 18 new cases in
  `tests/test_e6985_3_component_freshness.py`.
- Broader Python/API suite: **523 passed**. Files: `test_api`, `test_operations`,
  `test_e6985_3_component_freshness`, `test_component_health`,
  `test_engineering_loop`, `test_e6985_catalog_evidence`,
  `test_e6985_nhl_box_download_recovery`, `test_e6985_nhl_recovery_integration`,
  `test_d13_acceptance_handoff`, `test_d13_release_recovery`,
  `test_d13_release_rehearsal`, `test_d12_live_shadow`,
  `test_f0025_player_shot_reconciliation`, `test_f0027_player_rush_reconciliation`,
  `test_governance`, and `test_decisions` (all under `tests/`, suffix `.py`).
- `node --test tests/web.test.mjs`: **216 passed**. Python reported only the
  existing Starlette/httpx TestClient deprecation warning.

The new tests cover exact expiry boundaries, future/malformed/missing timestamps,
separate evidence clocks, preserved failures and input immutability. The HTTP
test uses a read-only publication stub without a database or Engine loop. The
engineering integration test preserves the gap's first-seen time and deadline
through stale polls, then requires later fresh success for resolution.

Before/after hashing found only `component_health.py` changed among existing
source/test/config files. All existing tests, protected files and configuration
remain unchanged. Exact event/market/scope/signed-line/outcome/price and settlement
identities remain original; no contract adapter, model, promotion gate,
EvidenceGuard seal, funding receipt, USD1–5 sizing, null daily cap, checkpoint,
decision, outcome or execution state was edited. The broader API/guard/decision
and DOM checks passed. Manual wagering remains the only execution mode.

## Unmet acceptance and next owners

The bounded implementation is complete; independent review and production
delivery have not occurred. The original deadline
**2026-09-30T00:05:33.938012+00:00** remains overdue and unchanged. The attached
operational/source gaps are not resolved by this repair.

1. Controller: run full fixed suites and independent Codex review. The protected
   engineering consumer is unchanged; its integration is tested here, not
   independently approved.
2. Trusted release owner: perform separately authorized integration, source-drift
   and ledger checks, then fresh local/Tailscale publication verification. There
   is no exact passive release profile and utility discovery is disabled.
3. Operations owner: inspect retained catalogue/learning job attempts and recover
   catalogue cadence and the independent operating audit. This workspace has no
   evidence proving why those jobs fell behind; do not infer recovery from a
   heartbeat or this diagnostic change.
4. Source/model owner: verify a later successful NHL schedule and period-score
   observation, implement attributable NCAAF availability inputs, and then verify
   the affected models/contracts/settlement before claiming recovery.

All-market coverage, current participation, sustained 20+ daily first issuances
and measured profitability remain unfinished requirements. No training/holdout
validation was changed. Production, parent-project data, credentials, account
state, messaging and live services were not accessed. No helper or nested agent
was launched; helper dispositions are empty.
