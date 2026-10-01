# F0023 — Count-model payoff evaluator

This candidate implements a missing numerical step between the existing D06
player distributions and later prop evaluation. The new
`src/betting_engine/player_prop_probability.py` loads a selection-locked count
model and evaluates hypothetical Over, Under and integer inclusive ladder
payoffs. It does not activate player props or finish M03.

Inspection found existing history ingestion, D06 chronological model studies,
identity quarantine, contract parsing, participation/injury checks and a pure
settlement evaluator. None provided this count-distribution payoff calculation.
No `data/rebase/conflicts.json` was present. The supplied previous-attempt `files`
directory did not exist; its native-boundary failure is controller evidence,
not a source-code defect this change claims to repair. No nested agent or CLI
helper was launched.

## Implemented behavior

`load_count_forecast(study_root, artifact_index_sha256=..., metric_key=...,
player_id=..., team_id=...)` verifies the pinned artifact index and the selected
model, plan, report and selection-lock bytes. It checks the earlier-selection
choice, exact supported metric/scope, independent-input declarations and
train-plus-selection row count. It never selects on outer-test scores or refits.
It supports the existing NBA/WNBA points, NHL shots on goal, MLB hits and MLB
pitching strikeouts count studies. Namespace and group remain separate. The
existing pooled fallback for an unseen player/team is explicitly labeled.

The immutable forecast retains model, plan, input and artifact-index hashes,
the event-start fitting cutoff, the study's as-of and its rejecting validation
reasons. These are the study's recorded fields, not new publication timestamps.
This reader verifies selected model/control files; it does not reread the entire
training corpus or authenticate a collector's clock. The corpus was separately
checked during this task, as recorded in the evidence file.

`evaluate_count_payoff(forecast, operator=..., line=..., decimal_odds=None)`
computes win/push/loss mass using Poisson or the existing NB2 parameterization.
It preserves D06's `1e-9` mean floor and `alpha <= 1e-8` Poisson limit. It uses
CDF, survival function and point mass without a truncated score grid. Integer
equality refunds Over/Under; half-point lines have no push; integer ladders win
on equality. Changing a line recomputes its probability. Changing a price cannot
alter the forecast or probabilities.

The optional scenario EV is `p_win * (decimal_odds - 1) - p_loss`, with fair
decimal price `1 + p_loss / p_win`. Undefined/nonfinite fair prices are `None`.
These are conditional model/payoff calculations, not demonstrated betting edge
or measured profit. `p_void=None` explicitly preserves missing participation
and void probability; it is not folded into integer pushes or assumed zero.
Every result has `publishable=false`, `production_props_enabled=false` and five
explicit activation blockers.

The evaluator rejects unknown metrics, scopes, operators, continuous models,
nonfinite inputs, quarter lines and noninteger ladders. Decimal comparison
rejects even a tiny difference from an exact half point instead of rounding it
to another contract. Its numeric input bounds (absolute line at most one million,
decimal price at most one trillion) are experimental arithmetic bounds, not new
production thresholds. No stake is accepted, calculated or changed.

NFL's Gaussian/Laplace rushing-yard studies remain rejected here: continuous
density does not establish the integer mass needed for sportsbook pushes. A
discretization would be a new modeling assumption requiring separate validation.
NCAA has no D06 player model and remains unsupported. NHL `shots_on_goal` is not
automatically equated with a feed market called `shots`.

## Actual evidence and limits

[Machine-readable evidence](F0023_PLAYER_PROP_PROBABILITY_EVIDENCE.json) records
hashes and the five real artifact-adapter checks. The supplied snapshot contains
550,729 rows: NBA 104,489; WNBA 21,218; NHL 167,659; MLB hitting 153,339; MLB
pitching 62,327; NFL 41,697. Its manifest and all four declared files passed
hash/size checks. Actual schema fields, namespaces and scopes were inspected;
the model reader loaded all five selected count-model artifacts successfully.
The 12-source public bundle remains explicitly unverified for current participation.
Sources are present; historical availability and current participation are the
missing evidence, not the existence of historical logs.

There were no new real-data fits, holdout evaluations, offered contracts priced,
issued instructions or P&L measurements. Earlier test data was not reused to
tune distributions. K01, K02, K03 and K08 were read as methodological source
material, respectively informing refund arithmetic, independent forecasts,
source-time limitations and chronological separation. Their hashes are retained.
No source publication or availability time was fabricated.

## Validation

Only the new `tests/test_f0023_player_prop_probability.py` contains added tests.
It covers analytic Poisson/NB cases, mass conservation, tails, integer refunds,
inclusive ladders, price independence, exact line rejection, corrupt artifacts,
selection locks, chronological cutoffs, namespace/scope rejection, team changes
and the actual five archived model schemas. Synthetic outcome enumeration agrees
with the existing pure settlement evaluator's returned net payouts. DNP voids
remain distinct from pushes. These fixtures do not verify live Stake rules.

Commands use `OMP_NUM_THREADS=1`, `PYTHONPATH=src` and
`/Users/belavarga/Betting_tips_engine/.venv/bin/python`:

```text
python -m pytest -q tests/test_f0023_player_prop_probability.py
python -m pytest -q tests/test_d06_player_model_study.py tests/test_e8b9_d06_identity.py tests/test_player_prop_contracts.py tests/test_player_prop_settlement.py tests/test_participation.py tests/test_f0016_injury_context.py tests/test_api.py
node --test tests/web.test.mjs
```

The API test command additionally sets
`BETTING_DATA_DIR=/private/tmp/f0023-api-tests`; it exercises local synthetic test
databases, not the live wager database. Final results are recorded below.

Final results: **74 new regressions passed in 13.70s; 261 related Python/API
tests passed in 83.44s; 40 DOM tests passed in 38.84s**. The Python/API run has
one existing Starlette/httpx deprecation warning. A hash comparison against the
pre-edit snapshot found no changed original source, test, doc or config file;
only the four new F0023 files are present. The machine-readable evidence records
the preservation counts and source/test hashes. This is local candidate evidence,
not the controller's full-suite result or independent review.

## Candidate instruction audit and next steps

| Requirements reviewed | Actual fulfillment in this candidate |
| --- | --- |
| R01–R04, R11, R15, R19, R21, R23–R24; independent inputs, scope, chronological validation, source archives | New arithmetic is sourced from existing independent models and keeps rejected validation explicit. No guard, market publisher, training policy or promotion change. Exact offer matching and sportsbook validation remain missing. |
| R05–R08, R13, R20, R29; daily instructions, stakes, immutable lifecycle, full-game priority | No issuance or ledger access. $1–$5, no fixed daily cap, 15/day minimum, manual wagering and original decisions remain unchanged. Zero instructions from this increment; no shortfall is declared fulfilled and no profit claim is made. |
| R09–R10, R12, R16–R18, R22; UI, feeds, coverage, links and supervision | No UI, current-feed, schedule, link or supervisor changes. DOM regressions were run; live coverage and local/Tailscale publication were not accessed or verified. |
| R14, R25–R28, R30; controlled work, development ownership and review | Only a new experimental module, regression file and evidence/docs were added. Existing tests and protected code remain unchanged. Advice was reviewed by exact hash; independent controller review and separately verified release remain pending. No deployment/model activation authority is claimed. |
| Later instructions; participation, all markets, injury roles, retraining evidence, passive release | Count arithmetic does not close these capabilities. No retraining, provider call, credential/account access, deployment or automatic utility installation occurred. No authenticated history, current participation or operating-cost evidence was invented. |

Next: controller full fixed suites and independent read-only review; separately
validate a discrete NFL distribution and new NCAA/family models; collect
authenticated prospective sports versions and current event/player/team/role
evidence; validate probability calibration on fresh chronological samples; bind
forecasts to exact current Stake contracts through verified identity/metric/scope
crosswalks; verify DNP/void/push rules from source bytes. Only a separately
reviewed integration may connect those pieces to EvidenceGuard, settlement and
publication. Live source and local/Tailscale release checks are still required
before activation. No production release was attempted here.
