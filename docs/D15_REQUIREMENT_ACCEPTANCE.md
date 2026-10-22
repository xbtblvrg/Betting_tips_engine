# D15 requirement acceptance evaluator: candidate audit

The read-only evaluator in `src/betting_engine/requirement_acceptance.py`
implements the bounded D15 objective. It dynamically enumerates every current
contract requirement: **39 rows, including R01–R30**. It maps declared code and
referencing tests, preserves missing references, validates supplied scoped
code/test/live assertions against exact hashes, and reports calendar-day
delivery and same-publication inventory limits. References alone earn no
acceptance credit; there is no completion percentage. Failed relevant
subclaims override passing assertions. Live assertions expire after 300 seconds.

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m betting_engine.requirement_acceptance --as-of 2026-10-01T08:05:05.455360+00:00 --first-day 2026-09-28
```

The CLI prints JSON without writing files. It imports no Engine, Store, SQLite,
network client or model loader. Pure evaluator inputs remain unchanged.
Exact hash agreement establishes evidence consistency, not independent review,
historical availability, model approval or production readiness.

## Recovery and integration

The previous attempt's eight missing outcome diagnostics now exist in the
controller-pinned `lifecycle-outcomes-v1` validation namespace. The upstream
lifecycle test changes only its fixture root; all its acceptance assertions
are retained. Its unchanged test now passes, reproducing 14 original contracts
across four games and recorded P&L of **−$13.85**. This is frozen regression
evidence, not a current performance estimate or account verification.

D15 now verifies the newly declared lifecycle JSON paths within that separate
namespace. It rejects missing/changed/unsafe fixtures, with no fallback to
current research files. It also supports the actual exported
`nhl-official-settlement-v1` raw JSON schema alongside gzip Stake responses.
The raw schema binds its size through the manifest file entry and preserves
source timestamps and identity; it never fabricates a source publication time.

Both rebase conflicts were reviewed before implementation. The upstream
lifecycle test was left byte-for-byte unchanged. `stake_api.py` combines the
prior candidate's partial-registry diagnostics with upstream MLB seed-range
resolution. New regressions retain valid NCAA siblings while reporting malformed
rows and core-sport discovery failures. Existing seed tests verify that real
team conflicts remain blocked.

The previous evaluator and 11 absent D15 test files were restored. The newly
added retained-failure test compares every supplied metadata field, allowing
the current `not_required` diagnostic manifest without inventing an old run ID.
Two further new test files cover source schemas and the registry merge.
All pre-existing tests, protected sources and configuration remain unchanged:
**362 of 363** inventoried files are byte-identical; the sole permitted existing
change is the `stake_api.py` merge. See [integrity evidence](d15_validation/preexisting-integrity.json).

## Supplied observations, not live verification

The [full matrix](d15_validation/snapshot-observation.json) uses contract
`2026-10-01.27` at the explicit cutoff above. R05, R20 and R39 report delivery
failures; the other 36 requirements remain limited by incomplete scoped
acceptance evidence. **No persistent requirement is declared fulfilled.** All
rules, historical policy precedence and unfulfilled capabilities remain visible.

| Budapest issuance day | Funded first issuances | Distinct games | Shortfall to 20 |
| --- | ---: | ---: | ---: |
| 2026-09-28 | 22 | 7 | 0 |
| 2026-09-29 | 52 | 10 | 0 |
| 2026-09-30 | 74 | 24 | 0 |
| 2026-10-01, open at cutoff | 17 | 11 | 3 |

The untruncated export contains 172 original decisions, including seven legacy
zero-allocation records excluded from funded delivery. The 165 funded contracts
retain their original dates, stakes and quotes, including mistaken or losing
decisions. Duplicate tickets, repricing and equivalent payoff aliases add no
first-issuance credit. Distinct same-game contracts retain separate issuance
counts and explicit game clusters. A 15-pick day fails the current minimum20
objective; no good day closes an ongoing daily obligation. No audited checkpoint
was supplied or reset. Current execution is not inferred from recorded issuance.

The lifecycle view retains 117 chronologically available recorded outcomes.
Recorded P&L remains separate from unknown operating costs and does not establish
profitability. Knowledge cards K03 and K08 supplied methodological context for
availability times and game clustering; their text was treated as data.

Publication **4258**, created `2026-10-01T08:04:57.253853+00:00`, carries 18
fixtures and 6,500 observed outcomes. Its inventory reports 5,582 unsupported
or inactive outcomes, 1,752 unverified Swish observations and two unmatched
fixtures; categories overlap. Missing matches, unsupported contracts and source
failures stay explicit. This is observed feed coverage, not complete website
coverage or current executable instructions.

## Validation and remaining work

[Input verification](d15_validation/input-verification.json) records 317 development
files, 46 frozen fixture/test bindings, one engineering artifact and three
controller source files, with no pin errors. All 392 source-response bindings
verify: 384 gzip Stake observations and eight raw NHL observations. Export time
is only the report cutoff, never substituted for historical source availability.
No root recovery package was supplied. The task registry still retains its
original incomplete-attempt diagnosis; this evaluator does not close that record.

The [validation index](d15_validation/evidence-index.json) binds commands,
results, output hashes and candidate sources:

- Targeted D15/lifecycle/Stake tests: **228 passed**.
- Full Python/API suite: **5,156 passed**, five subtests passed; one dependency deprecation warning.
- Unchanged DOM entrypoint: **216 passed** using `node --test tests/web.test.mjs`.
- The initial `npm test` wrapper exited 255 without test output; its log is
  retained separately. Direct execution of its exact configured command passed.

The original deadline remains `2026-10-02T00:04:28.263503+00:00`; prior failed
run identities and pins remain in [recovery evidence](d15_validation/recovery-pins.json).
Both supplied helpers were independently checked and partly used, with exact
output hashes and rejected suggestions in [review dispositions](d15_validation/helper-reviews.json).
No helper or nested agent was launched.

The bounded evaluator and targeted recovery are complete as a candidate.
The controller must run its independent review and trusted integration, then
perform fresh local/Tailscale publication verification. No exact passive release
profile exists and discovery is disabled; this candidate grants no release
authority. Production, services, credentials, account state and the writable
wager database were not accessed. No betting model, guard, stake policy, original
decision or daily checkpoint was changed, and no sportsbook order was submitted.
Remaining sports/model capabilities, daily shortfalls and measured profitability
require their own implementation and evidence; this utility cannot close them.
