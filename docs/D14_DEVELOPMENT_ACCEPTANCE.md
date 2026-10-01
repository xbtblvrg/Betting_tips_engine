# D14 development acceptance evidence

Candidate implementation, 2026-09-30. Adds a passive offline inspector and independent behavior tests without changing the protected worker. Candidate evidence never sets production or full-engine readiness. Actual restore drill remains required.

## Inspected evidence

Read `AGENTS.md`, contract `2026-09-29.15`, development plan, worker/integration/delivery schema and regressions, market contract and project status. No `data/rebase/conflicts.json` was supplied. The previous candidate under `20260929T221021012985-D14/files` was read as repair evidence; its behavior tests are preserved verbatim in a new workspace test file.

The supplied registry file `data/engineering-evidence/2d59d6a56f3b639c9f493a33b5cb9d23e6ca087fb8d159bb15ad03f631faa944.json` records D14 in `retry`, observed at `2026-09-29T22:27:57.794792+00:00`, with an independent-review rejection for numeric overflow. Its **raw bytes** hash to `337afb1bcae236f2903d7a1cb9c396724ddb54828d5863f457bb0331df3fe04c` (distinct from its registry filename identity). Missing restore/live evidence and the daily instruction shortfall remain open; no new daily issuance measurement was made.

Local knowledge cards K03 and K08 were read as methodological source material: captured timestamps are distinct from source publication times; software validation is distinct from sports-model or profit evidence. Their underlying publications were not newly fetched. The supplied public sports-source manifest explicitly preserves `source_published_at: null`, unverified current participation and unauthenticated archive timestamps. This development tooling does not transform those observations into forecasts, historical availability or production market support.

## Tool and repair

`src/betting_engine/development_acceptance.py` exposes `inspect_run(archive_root, run_id, conflicts=None)` and a JSON CLI:

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m betting_engine.development_acceptance /absolute/offline-export RUN_ID --conflicts rebase-conflicts.json
```

Supply an explicit offline export with canonical paths and no symlink ancestors. The inspector reads the worker's actual archive layout: `runs/<run_id>/run.json`, `result.json`, `review.json` (or `integration-review.json` after integration revalidation), `candidate-acceptance.json`, and changed artifacts in `accepted-files/`. It does not discover production folders, execute archived content, start processes, recover files or write anything.

Checks cover completed implementation, passed Python/DOM gates, matching accepted review, run/task identity, timezone-aware chronology, committed candidate acceptance, manifest digest and exact changed-file bytes. Every consumed file receives a raw hash and byte count. Bounds are 8 MiB/file, 64 MiB/inspection and 1,000 changed files. Duplicate keys, malformed/missing evidence, duplicate artifacts, traversal, absolute artifact paths and symlinks block inspection.

The prior implementation rejected literal `NaN`/`Infinity`, but JSON `1e999` overflowed to infinity. The repair applies a finite-float parser to **every JSON document**, before any fields are echoed or assessed. Nested and unused fields are covered, including implementation evidence and `run.remaining`. Invalid numeric evidence now produces a blocked, serializable report instead of passing consistency or crashing CLI serialization. Finite numbers retain ordinary float parsing.

Exit 0 means candidate archive consistency only; exit 1 means blocked. Both always report `production_ready=false`, `full_engine_ready=false`, `restore_drill=required_not_verified`. Omitted conflict evidence reports `not_supplied_not_assessed`; a missing requested manifest blocks; an empty list is distinguished from omission; a nonempty list requires separate reconciliation review. No completion message clears it.

Hashes and matching review documents establish internal consistency, not authenticated controller provenance, actual test execution, unchanged current production, complete unchanged-file coverage, release authorization, live publication or restoration. The tool is not integrated into any approval or release path.

## Validation performed

New tests:

- `tests/test_d14_development_acceptance.py`: 37 cases using actual worker persistence, retry selection, candidate publication/recovery and delivery status in temporary synthetic projects. Covers syntax/behavior failure, rejected review, interruption at each gate, backoff, retained deadlines/history, conflicting source versions, corrupted recovery artifacts and candidate-versus-production readiness. External implementation/review/DOM processes are stubbed; generated Python is compiled and its behavior assertion executed. These are failure-injection tests, not an actual restore drill or independent AI review.
- `tests/test_d14_nonfinite_evidence.py`: 43 cases covering nested nonfinite tokens in all six document types, positive/negative overflow in echoed fields, strict JSON CLI output, read-only behavior and finite boundary controls. Synthetic archives follow the worker's schema; they are not real review receipts.

Before the fix, the new positive and negative `1e999` implementation-evidence cases both failed because the old inspector accepted them. After the fix:

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_d14_development_acceptance.py tests/test_d14_nonfinite_evidence.py tests/test_development.py tests/test_parallel_development.py tests/test_task_release_handoff.py tests/test_release_supervision.py tests/test_release_superseded_tasks.py
node --test tests/web.test.mjs
```

**153 Python/API tests passed**, with one existing Starlette/httpx deprecation warning; **40 frontend DOM tests passed**. SHA-256 comparison confirmed all 164 original source/test/config files unchanged. No Git metadata is supplied. Controller full fixed suites and independent review are still pending.

## Scoped developer audit and handoff

| Requirement | Actual fulfillment and next action |
| --- | --- |
| D14 failure, resume, conflict, evidence and readiness boundaries | Candidate implementation tested; protected worker and existing regressions unchanged. Controller must run full suites and independently review exact files before integration. |
| Restore drill and release | Not performed. Trusted coordinator must rehearse restoration from a known archive in an authorized recovery environment, compare bytes, exercise interruption/restart and retain outcomes. Code rollback must never restore an old wager database. Separate release verification must include source drift, immutable-ledger checks and fresh local/Tailscale publication. |
| Independent sports forecasts and every market family | Unchanged, not newly validated. Model, source, exact current contract and settlement gates remain separate requirements; no production model is enabled by this utility. |
| Daily instructions, stakes, immutable decisions | No allocations or ledger access. Fifteen-plus distinct funded instructions/day remains a requirement with unresolved shortfalls. Preserve $1–$5 stakes, no fixed daily cap, original decisions and manual wagering. |
| Profit, learning, sources and outcome evidence | No new performance claim. Continue chronological validation and game-clustered lifecycle evaluation; historical logs do not verify current participation or past availability. |
| Remaining contract requirements | UI, feed freshness, inventory, navigation, retraining, supervision and all other contract requirements are unchanged and not certified by this increment. Full engine readiness stays open. No helpers, external messaging, credentials, account state, production services or real wager database were accessed. |

The bounded candidate tooling is implemented. M01, actual restoration, full-market coverage, daily delivery and production release remain separate unfinished work.
