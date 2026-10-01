# D08: Asian quarter-line arithmetic candidate

Bounded implementation complete; production support remains unfulfilled.
`src/betting_engine/asian_quarter.py` is a pure, inactive utility. It has no
production imports, registration, source fetching, persistence or issuance path.
No production service, account, credentials or wager database was accessed.
No rebase conflict file was present.

The adapter uses one quoted decimal price for both equal virtual stakes.
`-0.25` splits into `(-0.5, 0)`; `+0.75` into `(+0.5, +1)`; `2.25` into
`(2, 2.5)`. Integer/half lines pass through as one leg. Other fractions fail
without rounding. Handicap signs apply to the selected team, including away.
Supported mathematical families are `spreads` (home/away) and `totals`
(over/under). Scoped market names are rejected; callers must separately validate
event, scoring scope and completion before any future settlement integration.

For stake S and decimal price d, the five gross returns are:

| State | Gross return | Net profit |
| --- | --- | --- |
| Full win | Sd | S(d−1) |
| Half win | S(d+1)/2 | S(d−1)/2 |
| Push | S | 0 |
| Half loss | S/2 | −S/2 |
| Full loss | 0 | −S |

An integer result cannot push both distinct quarter-line legs. Individual leg
pushes are retained, and unsplit integer lines retain full pushes. All outputs
use exact `Fraction` values. Decimal/float inputs use their decimal spelling;
no cent rounding, commission, or minimum leg-size assumption is imposed.
For example, $1.05 at 1.87 on a half win returns exactly $1.50675 before any
venue-specific currency rounding. Stake is an arithmetic scale, not allocation.

`aggregate_asian(contract, [(home_score, away_score, probability), ...])`
evaluates both legs on each same score realization. It returns five state
probabilities and their joint leg states, never independent leg products.
Scores must be nonnegative integers (integer-valued floats are accepted).
Missing scores, negative/nonfinite probabilities and incomplete distributions
fail. Only mass errors within 1e-12 are proportionally normalized, with the
original mass returned explicitly. Repeated score rows are additive.

Let W = P(full win) + P(half win)/2 and L = P(full loss) + P(half loss)/2.
Expected profit is S[W(d−1)−L]; break-even price is 1+L/W when W>0.
With W=0 there is no unique finite break-even price, including all-push cases.
Aggregation takes no odds. Its arithmetic cannot authenticate model provenance;
supplied probabilities are conditional inputs, not evidence of a validated model.

Source material inspected locally: knowledge cards K01 (five-state payoffs),
K02 (independent joint sports distributions), K03 (availability times), and K10
(execution/rounding distinctions). Reading scope was these cards, not their
underlying books. `models.score_distribution` supplies paired home/away scores
and weights; no changes or production adapter were added to that function.

The supplied `data/games.json` has SHA-256
`b509f3630762bbb05af90de5b3b45b00f2c0207b635de69f88bb1eeea1c6e20a`.
Its NCAAF:401238035 row contains `state=final`, `home_score=45.0`,
`away_score=35.0`, start `2020-09-04T00:00Z`, and fetched_at
`2026-09-29T13:09:58.605304+00:00`. The new schema regression copies those
fields; its -10.25 line and 1.90 price are explicitly hypothetical, not observed
quotes. Other rows have missing scores. The separate period file uses
`games[id].scores[scope]` with home/away/complete/source/fetched_at/source_sha256;
it must not be replaced by a full-game result. The public source manifest was
also inspected: it labels current participation unverified. Historical results
and collection times do not establish contemporaneous publication, participation,
or executable quarter-line offers. No such claim or timestamp was created.

Validation performed in this workspace:

- `PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_d08_asian_quarter.py`: **85 passed**.
- Same interpreter/environment, `-m pytest -q tests/test_markets.py tests/test_contract_additions.py tests/test_market_expansion.py tests/test_api.py`: **53 passed**, one existing Starlette/httpx deprecation warning.
- `node --test tests/web.test.mjs`: **40 passed**.

New regressions include a direct quarter-distance oracle across integer score
pairs, positive/negative and home/away handicaps, low/high Over/Under totals,
exact cash conservation, odd-cent stakes, invalid evidence, price-independent
probabilities, unchanged inputs and absence of production imports. The example
PMF (win .45, tie .30, loss .25) on -0.25 at 1.90 gives conditional EV +.005
per unit, solely a synthetic algebra test, not measured betting profit.

Advisory text hashes were recomputed and match the supplied dossier:

- `test_cases:claude`, SHA-256 `96795cb2b6dbbdc2679773ef13c9caacac3c74fe79b8c125794e035ba4f6ec44`: **partly used**. Used decomposition, signed payoff, precision, mirror and shared-score checks. Corrected its Over 2.25 example: $2 at 1.95 yields $1.90 full-win profit, not $1.95. Rejected the assertion that independent-leg multiplication necessarily changes EV: expectation is additive, although the joint payout probabilities become wrong. The regression explicitly demonstrates this. A market-derived PMF cannot be identified by numerical values alone; no fictitious provenance gate or activation flag was added.
- `acceptance_checklist:agy`, SHA-256 `3aec3b4382c4e0d74ba22cbef307c0bab71c756810611e11fab9dc07e150fba0`: **partly used**. Used equal virtual stakes, shared realization, signed totals/handicaps, five states and isolation. Clarified full push is impossible for two adjacent quarter legs. Defined one common quoted price and exact unrounded arithmetic; synthetic adjacent-book construction and exchange commission are excluded. Chronological forecasting/backtesting is a later model-validation task, not evidence supplied by payout unit tests.

Developer scope audit: R01–R30 and AGENTS.md were reviewed against this bounded
change. Existing files, regression tests, thresholds, stake policy, project
contract and publication/ledger/guard code are unchanged. The $1–$5 ceiling,
null daily cap, immutable decisions and manual-only wagering are preserved by
isolation, not re-certified as a whole system. This task adds no funded
instructions, model training, retraining, coverage or profit evidence. Full
league/market support and the daily 15+ target remain separate unfinished work.

Next steps belong to the controller: run the fixed full suites and independent
Codex review. Before production activation, separately verify exact Stake
quarter contracts and settlement rules (including scope, currency rounding and
exceptional results), integrate a validated independent score distribution,
then review guarded pricing/ledger integration and live publication locally
and through Tailscale. These release checks were not run or claimed here.
