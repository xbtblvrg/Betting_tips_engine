# E0E8DAE97EE — deterministic SGM numerical validation

The bounded candidate repair is complete in the existing experimental
`sgm_contracts.validate_sgm_quote` entry point. Caller Decimal settings can no
longer change contract IDs or admit unsupported fractional lines. This is not
production SGM support, a joint forecast, settlement implementation or release.
All results still have `publishable=False` and `availability_verified=False`.

## Candidate and reproduced defect

Read AGENTS.md, project_contract.json, D11 in development_plan.json, market/status
docs and knowledge cards K01/K03/K10. No rebase conflict file exists. The supplied
registry record `dc03944172753cd7dc2e111100d8ba763bb5559bc8a6807a1d57113f321b9872`
identifies D11 as candidate_ready with actual combined-source integration, joint
forecasting and settlement unresolved. Its exported file byte SHA-256 is
`59d9a0d3a76ded2debb1a21dd8e8e2e9b4b13b1cac8f77c357f7f64d0eb46c57`.
Earlier registry records describe joint consistency and JSON decimal precision
rejections. Those repairs are already present upstream and were preserved.
All 160 existing D11 tests passed before this increment.

New reproduction: at caller Decimal precision 3, distinct Total lines `155.5`
and `156.5` received the same contract ID; `155.25` also passed despite split
lines being unsupported. Exponent capitalization changed IDs, and caller traps
or exponent limits rejected otherwise valid contracts. The initial 19 new test
cases produced **13 failures and 6 passes** before repair.

The public validator now runs its existing implementation inside a fresh, fully
specified local Decimal context. It retains the original 28-digit arithmetic,
rounding and exponent rendering. Existing 24-digit wire bounds require at most
25 digits when doubling lines, so accepted arithmetic remains exact. Caller
context, flags and traps are restored unchanged. Default-context contract IDs
are preserved, including pinned pre-repair IDs; no version migration or record
rewriting is needed. Source binding still parses raw decimals losslessly.
The change is integrated into the existing candidate entry point, not a second
validator or a protected production integration.

## Sources and limits

- Catalogue version `20260930T004537433421Z`: **3,758 rows**, byte SHA-256
  `cdc37d01acd406c18e619ad14443b8e8a6aac38bad38b05f6f511c09d3859924`.
  Inspected actual fixture/start, market ID, specifiers, tabs, outcomes,
  raw_contract and source URL/fetched_at/hash fields. The latest catalogue
  explicitly reports `sgm_quote_integration: false`. These are historical
  observations, not current executable prices or complete website coverage.
- Inspected the supplied public-sports manifest and hash-checked NHL body
  `1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`:
  actual plays contain eventId, periodDescriptor and period clocks, with teams
  and rosterSpots at the root. Manifest source records retain null publication
  times and unauthenticated archive timing; current participation is unverified.
- Inspected the verified-history Parquet schema: 550,729 historical rows,
  including scope/stat_scope, stats, source hashes/fetched/published/available
  times and target_participation. These logs are not current participation or
  combined Stake quote evidence. No source adapter, source time or probability
  was invented. All new test envelopes are explicitly synthetic.

## Verification and instruction audit

New file: `tests/test_e0e8dae97ee_sgm_decimal_context.py`, **22 cases** covering
distinct and stable IDs, unsupported split/fractional lines, caller traps,
exponents/capitalization, caller-state preservation, impossible joint outcomes,
exact source mismatches, repricing/reordering, existing IDs and the wire bound.

Final command (required interpreter, local temporary API stores):

```sh
BETTING_DATA_DIR=/tmp/e0e8dae97ee-api-data PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_e0e8dae97ee_sgm_decimal_context.py tests/test_d11_sgm_contracts.py tests/test_d11_sgm_joint_consistency.py tests/test_d11_sgm_precision.py tests/test_d11_sgm_nfl_scope.py tests/test_market_expansion.py tests/test_contract_additions.py tests/test_periods.py tests/test_api.py
node --test tests/web.test.mjs
```

Results: **225 Python/API tests passed**, one existing Starlette/httpx deprecation
warning; **40 DOM tests passed**. The initial API import created an empty local
bootstrap database and raw directory; these test artifacts were removed, and
the final run used the temporary data directory above. No production service,
parent-project data, wager ledger, credentials, network or helper CLI was used.
SHA-256 comparison confirms all 93 pre-existing files under tests, protected
source files, config and AGENTS.md unchanged; only the existing sgm_contracts.py
changed. No model fitting, chronological split or validation threshold changed.

R01–R04/R19/R22–R24: bounded exact-identity validation improved; source and model
availability remain unresolved. R05–R18/R20–R21/R29: publication, losses, original
odds/stakes, game clustering, manual wagering, $1–$5 bounds, no daily cap and
Winner/Handicap/Total prioritization are unchanged. No daily issuance or profit
fulfillment is claimed. R25–R28/R30: isolated candidate repair and evidence are
provided; independent review and the release-wide live audit remain pending.

Advisory `acceptance_checklist:grok`, independently verified text SHA-256
`79f9a0e22b7608cba94185a9a71a4504652b56b74d68333959648269d83a9664`:
**partly_used**. Used candidate-first verification, exact identity, immutable
records, provenance and explicit blockers. Rejected its instruction to stop on
a candidate failure: the task authorizes repairing failures. Its claim that the
deadline/evidence were absent applies only to the helper's context; both were
supplied here. No helper assertions substitute for local tests or approval.

## Remaining work and fixed checkpoint

The original deadline remains **2026-10-01T19:57:25.163323+00:00**. D11/M06
production integration remains unmet; this repair does not close that registry
gap or move its deadline. Concrete next steps:

1. Obtain an authorized actual combined Stake response and settlement-rule
   evidence, archive exact bytes and genuine receipt/source times, then build
   and independently review the adapter. Standard/combo catalogue rows do not
   establish a custom SGM quote endpoint.
2. Validate independent joint forecasts with untouched chronological tests and
   matching correlated-leg settlement, including push/void semantics. Measure
   prospective results from immutable lifecycle records clustered by game;
   neither validation metrics nor profitable performance are established here.
3. Controller: full fixed suites, independent read-only review and separately
   verified integration/release, followed by fresh local/Tailscale publication
   checks. No production approval or live verification occurred in this phase.
