# F0026 — Experimental signed NFL yard payoffs

Implemented `src/betting_engine/player_yard_probability.py` and new behavioral
regressions in `tests/test_f0026_player_yard_probability.py`. This completes one
bounded experimental implementation increment, not M03 or production activation.
No rebase conflict manifest was present. No existing file was edited, including
protected code, the project contract and original tests. Controller review remains
pending; no helpers or nested agents were used.

## Why this increment

F0023 explicitly rejects NFL's continuous Normal/Laplace studies because a density
cannot supply integer push mass. F0024 therefore cannot evaluate that league;
F0025 addressed a separate NHL outcome-source gap. This increment implements
signed NFL rushing-yard payoff arithmetic with an explicit new discretization
assumption. It does not repeat count pricing or assert that a new model passed
validation.

The supplied history contains 41,697 NFL rows with integer rushing-yard targets:
339 negative and 36,741 zero. Actual fields include `identity_namespace=nflverse-gsis`,
`stat_group=player`, `stat_scope=full_game_including_overtime`, exact player/team/game
IDs, JSON `stats`, and separate availability/source clocks. A zero target is not
proof of participation or a suitable rushing role. This population includes many
non-rushers; its pooled baseline is particularly unsuitable for unqualified
current-player betting claims.

## Working candidate API

`load_yard_forecast(study_root, artifact_index_sha256=..., player_id=..., team_id=...)`
loads the pinned D06 NFL rushing-yard artifact without odds, fitting or reranking.
It verifies the index, plan/report linkage, exact metric/namespace/scope,
chronological cutoffs, rejected validation state, earlier selection lock,
training-row count, signed means and scale schema. The model's original
player/team shrinkage and explicitly labeled pooled fallback remain intact.
Other metrics and leagues are rejected. Historical fit-event cutoffs are not
reported as model publication times. Training-corpus bytes are not reread by this
reader; their pinned hash is retained.

`yard_log_mass(forecast, integer_yards)` and
`evaluate_yard_payoff(forecast, operator=..., line=..., decimal_odds=None)` define
`Y=k` by latent model mass in `[k−0.5, k+0.5)`, across **all signed integers**.
This rounded latent model is a new, unvalidated assumption. The selected D06
Laplace/Normal recipe was chosen using continuous-density scores; those scores
are not validation of the discretization. The reader does not use outer-test
results to choose the distribution.

Over/Under equality refunds; integer inclusive ladders win on equality. Half-point
lines have no push. Quarter lines, tiny off-grid decimal differences, missing or
nonfinite numbers and unsupported scopes are rejected. Negative yards are never
clipped into zero. CDF/survival calculations preserve tails without a finite grid;
log-space interval subtraction avoids erased push mass when the CDF rounds to one.
`yard_log_mass` remains useful where ordinary probability underflows to zero.
No density is substituted for probability, no probability clipping is used and
no tail renormalization occurs. Location/target/line magnitude is bounded at one
million; scale is 1–1,000,000, preserving D06's scale floor. These are experimental
arithmetic bounds, not production or validation threshold changes.

Conditional scenario EV is `p_win * (decimal_odds − 1) − p_loss`; fair decimal
price is `1 + p_loss / p_win` when finite and defined. Price changes cannot alter
the forecast or outcome mass. Void probability remains `None`. Every result
has `publishable=false`, `execution_eligible=false`, `production_props_enabled=false`
and explicit blockers for discretization validation, rejected study, historical
availability, participation/role, exact Stake identity/contract and settlement
rules. The assessment hash is a reproducibility checksum, not an EvidenceGuard
seal. Directly constructed forecast objects are experimental scenarios, not
source-authenticated forecasts or approvals.

There is no production caller, stake sizing, database write, wager submission,
model promotion or current-offer integration. F0023's original NFL rejection
remains unchanged and tested. Hypothetical lines in tests are synthetic inputs,
never claimed observed Stake offers.

## Evidence and verification

[F0026_PLAYER_YARD_EVIDENCE.json](F0026_PLAYER_YARD_EVIDENCE.json) records source,
knowledge-card and model hashes. All 24 declared files in the 12-source public
bundle and all four verified-history files passed hash/size checks. The snapshot
contains 550,729 total rows. Three native NFL play-by-play Parquet schemas were
inspected: 2024 (49,492 rows), 2025 (48,771), 2026 (8,311), each with 372 columns.
Their native collector receipts, hashes, URLs, HTTP status and timestamps agree.
These play-by-play files were not used to fit or recalibrate anything here;
sequence completeness and current participation remain unverified.

The actual selection-locked NFL artifact loaded successfully. Its selected
recipe is `player_laplace`, scale `2.840967521187697`; its original rejecting
validation reasons are retained. No new fit, holdout measurement, calibrated
push/void probability, real offered-contract evaluation, issuance or measured
P&L was produced. Source publication times were not invented. Cards K01, K03,
K06 and K08 were read for payoff arithmetic, source-clock separation, NFL scope
and chronological validation; they were methodological data, not instructions.

Validation commands use the required interpreter, `PYTHONDONTWRITEBYTECODE=1`,
`PYTHONPATH=src`, `OMP_NUM_THREADS=1`, and pytest `-p no:cacheprovider`:

```text
/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_f0026_player_yard_probability.py
/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_f0023_player_prop_probability.py tests/test_f0024_player_prop_offer_evaluation.py tests/test_f0025_player_shot_reconciliation.py tests/test_d06_player_model_study.py tests/test_player_prop_settlement.py tests/test_player_prop_contracts.py tests/test_participation.py tests/test_api.py
node --test tests/web.test.mjs
```

The API run additionally uses `BETTING_DATA_DIR=/private/tmp/f0026-api-tests`.
The 95 new tests pass, including independent numerical integration, negative
support, translation invariance, tail stability, push/ladder semantics, price
independence, byte/semantic corruption, selection locks, actual artifact loading,
and probability-weighted comparison with the existing synthetic settlement
fixtures. Known DNP settlement remains separate from unknown forecast void mass.
The initial new symlink test failed because it reused an outdated index pin;
the new fixture was corrected without changing an existing test or implementation.
Final broader results and original-file preservation are recorded below.

## Candidate requirement audit and next steps

| Requirements reviewed | Actual disposition |
| --- | --- |
| R01–R04, R11, R15, R19, R21, R23–R24 | Independent pinned artifact input and exact signed payoff arithmetic implemented offline. No source clock, validation score or current market invented. Historical availability, calibrated per-family forecasts and live exact contracts remain unfinished. |
| R05–R08, R13, R20, R29 | Zero funded instructions; 15/day requirement and measured profit remain unfulfilled by this increment. Manual wagering, $1–$5 limits, null daily cap, original decisions and core-market priority unchanged. |
| R09–R10, R12, R16–R18, R22 | No UI, feed, coverage, navigation or supervisor change. No live service, local/Tailscale publication or recovery evidence was accessed. |
| R14, R25–R28, R30 and later release instructions | New isolated utility/tests/docs only. Original tests and protected files unchanged; no production data, credentials, account state, external messaging, helper CLI or nested agent accessed. No automatic release profile, deadline or guard changed. Independent controller review and release are later phases. |

Next: controller full fixed suites and independent read-only review. Before any
activation, pre-register and archive a separate signed-yard validation study,
with selection and untouched later samples, authenticated sports availability,
role-aware targets and event-level dependence handling. The existing continuous
study's outer test must not be used to tune this discretization. Collect current
participation/role evidence and exact Stake player/event crosswalks, then verify
DNP/void/push rules against actual sportsbook evidence. A separately reviewed root
change is required for protected integration and fresh local/Tailscale publication
checks. NFL's other metrics, NCAA and remaining league prop capabilities stay open.

Final results: **95 new tests passed (16.24s), 362 existing Python/API tests
passed (45.42s), 40 DOM tests passed (7.84s)**. One existing Starlette/httpx
deprecation warning remains. Byte comparison checked 299 original files,
including 99 original test/fixture files: no changes. Exactly four new
F0026 files were added. These are candidate-local checks; full controller suites,
independent review and production release verification remain pending.
