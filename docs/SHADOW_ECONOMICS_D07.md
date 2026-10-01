# D07: frozen economic evaluation

Implemented as the offline `betting_engine.shadow_economics.evaluate_shadow_economics`
function. This candidate evaluates supplied lifecycle exports; it does not
establish historical profitability, approve markets, integrate with a worker,
read a wager database, or deliver production software.

```python
from betting_engine.shadow_economics import evaluate_shadow_economics

report = evaluate_shadow_economics(
    frozen_decisions, frozen_tickets,
    as_of="2026-09-29T23:00:00Z",  # caller's actual evaluation cutoff
    accrued_system_cost=None,
)
```

## Accounting and evidence contract

Decisions use the decoded `Store.system_decisions()` schema: `family`, `tip_id`,
`issued_at`, immutable `payload`, `status`, `pnl`, `outcome_evidence`. Settled
decision evidence requires the existing `source`, `fetched_at`, `settled_at`
fields. Known results must be available by the supplied cutoff. Original odds,
stake, model, probability, market identity and issuance date remain unchanged.
Legacy `original_recommendation_stake=0` is disclosed separately. This evaluator
does not calculate daily funded issuance or treat ticket counts as issuance.

Tickets use the decoded `Store.bets()` fields `id`, `tip_id`, `created_at`,
`stake`, `odds`, `payload`, `status`. **The existing bets export alone is not
sufficient.** A reviewed export must additionally supply:

| Field | Required meaning |
| --- | --- |
| `currency` | `USD`; mixed currencies require a separate verified conversion adapter |
| `payout` | Total returned amount including returned stake, before separately recorded betting fees |
| `fee` | Explicit nonnegative ticket fee, including explicit zero when evidenced |
| `available_at` | Actual time the complete supplied ticket/payout/fee version was available |
| `evidence_ref` | Nonempty provenance reference for that version, including payout and fee evidence |
| `settled_at` | Actual settlement time for a settled ticket |

Open tickets require `payout=null`, `fee=null` and have separate exposure. No
fee is inferred from an absent field. Unknown operating costs are different:
`accrued_system_cost=None` leaves betting evaluation available and combined net
P&L unknown. A supplied operating cost must cover the caller's intended report
period; there is no invented accrual, budget or cost-allocation rule.

Tracking uses the existing `payload.tracking_source`, `odds_basis`,
`execution_verified`, `recorded_at`, `reported_source`, `reported_at` fields:

- `auto_assumed_placed` uses exactly the original stake and odds, recorded before
  start. Returns remain assumed estimates, not evidence of execution.
- `user_reported_placed` uses the reported accepted ticket stake and odds.
  Accepted means user-reported, never account-verified. Report time does not
  assert a known placement time. Original model and decision terms are retained.
- Multiple distinct accepted ticket IDs count individually for money and once
  per decision/game for those counts. Duplicate IDs are rejected in full.
  An assumed row alongside another ticket for the same decision is ambiguous
  and rejected until a reviewed reconciled snapshot is supplied; the evaluator
  never edits or guesses reconciliation. Distinct accepted tickets remain valid.

Only ordinary won/lost/push/void payoffs are currently evaluated. Explicit
payout must agree with the status, stake and recorded odds within 1.1 cents
(cent-rounding tolerance). If present, the existing ticket `pnl` must equal
payout minus stake before separately supplied fees. Manual adjustments,
half-win/split settlements, currencies other than USD and account verification
are unsupported. An exceptional settlement must receive its own validated
adapter, not be coerced into an ordinary win or loss. Decision and ticket
settlement views remain separate: a bookmaker void need not equal sports-score
settlement. The evaluator never settles a contract from final scores.

`betting_pnl_after_stakes_and_fees = payout - stake - fee`. Fees may make a loss
larger than its stake; that loss is retained. `pnl_after_stakes_before_fees`
corresponds to the original `economics.py` meaning of `gross_pnl`, not total
payout. `net_pnl_after_operating_costs` subtracts optional operating costs and
is withheld for an incomplete cohort. Original issued-decision evidence is a
separate report and is never added to ticket returns.

## Dependence, time and reproducibility

The isolated `shadow_economics._clustered_returns` implementation supplies the
shadow date bootstrap and drawdown. Live `economics.py` is unchanged. A private
copy uses collision-free league/game keys for the original issued report and
restores displayed IDs without changing source records. The estimator preserves
the existing thresholds: 30 settled
games and 14 event dates, 2,000 seeded whole-date bootstrap draws. Related
selections and duplicate tickets stay together. Game keys include league;
conflicting start times for the same league/game reject all affected decisions.
Dates use Europe/Budapest by default, with timezone-aware source times required.
Chronological daily P&L uses event dates, not receipt-import dates or input order.
It is not intraday or account cash-balance drawdown. Open exposure is not realized
P&L; partial games can contain both settled tickets and outstanding exposure.
Such partially settled game clusters retain all realized money but suppress the
return interval and report `incomplete_event_results`. An unrelated open game
does not invalidate already complete game clusters. Separate diagnostics expose
ticket/sports-score outcome differences, including bookmaker voids; those actual
supplied ticket returns are not overwritten by the sports result. Accepted
placement time remains explicitly unknown because receipt import is not placement.

Any rejected row or valid decision lacking ticket evidence suppresses intervals
for the entire report and execution subgroups. Eligible-subset arithmetic remains
visible with `invalid_records` and detailed reasons, never as a complete ledger.
Malformed/non-JSON export envelopes raise an explicit error. Even a fully valid
export cannot prove the caller supplied every decision. A canonical input hash
and a study hash (inputs, cutoff, costs, timezone and evaluator version) make
reports traceable without writing files. These hashes do not authenticate source
claims or provide durable storage; the controller must archive the exact export
and returned report. Source bodies are not fetched or verified by this evaluator.

The interval is descriptive, not a future-profit guarantee. Across-day
dependence, selective exports and nonstationarity remain limitations. Brier,
log-loss and closing-price gains cannot establish profit; none enters this
accounting calculation. No fitting, recipe selection or threshold search occurs.

## Evidence inspected and rebase disposition

Read `AGENTS.md`, contract version `2026-09-29.14`, `docs/STAKE_MARKETS.md`,
`docs/STATUS.hu.md`, D02 closing-price documentation, the actual decision/receipt
schema, and local knowledge cards K08 (validation/dependence) and K10
(quote versus execution/fees). Knowledge cards were treated as source material.

All eight `learning:<league>:a349dc20c3a1` engineering evidence files report
"No validated historical betting-profit backtest" and a summary count of five
settled decisions. These summaries supply no frozen payout/fee rows. They do not
establish five independent games per league or a profit measurement. D02's
closing-price implementation and public-price fixtures remain a diagnostic;
they cannot replace payout, fee or execution evidence. No historical sports log
was used as current participation or execution evidence.

`data/rebase/conflicts.json` names only `closing_prices.py`. The current upstream
file contains the candidate v4 behavior plus the v5 legacy full-game scope and
observed double-chance wording fixes. Resolution: retain upstream unchanged.
The candidate/current diff and existing regressions confirm both behaviors.
Current/upstream SHA-256:
`eebf3804b6d84e6f176c544aec63100786b78bebc0beba50a2356f7177f6d241`.
Prior candidate SHA-256:
`f5fd32d58291e88a06d4b65098623e4fe9c7085b81ad72b16c75665dbafa4fcf`.
The read-only conflict metadata was not rewritten because edits are restricted
to source, tests and docs. This document records the source resolution.

## Validation and developer audit

Synthetic tests verify losses, pushes, voids, fees, open exposure, accepted
repricing, duplicate tickets, immutable originals, date/game clustering,
chronological drawdown, deterministic intervals and threshold consistency with
the original economics report. They also reject missing/future evidence,
conflicting schedules/contracts, contradictory P&L, ambiguous reconciliation,
false account verification and malformed exports. Fixtures demonstrate accounting
behavior, not real returns or validated source availability.

Targeted command (223 passed):

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_shadow_economics.py tests/test_economics.py tests/test_receipts.py tests/test_captured_price_evaluation.py tests/test_closing_prices.py tests/test_closing_price_identity_conflicts.py tests/test_closing_integration_repairs.py tests/test_closing_capture.py
```

Broader verification: the same Python environment with `-m pytest -q` returned
767 passed, five subtests passed, one failed in 25.59 seconds. The failure is
`tests/test_ai_delegation.py::test_os_boundary_denies_helper_read_and_write_of_production`:
its temporary fake CLI returned `cli_error`, while the test expected
`received_unreviewed` with `[True, True]`. This checks a synthetic protected
directory under pytest's temporary root, not the actual production ledger.
The cause of the subprocess error was not established in this run; protected
delegation code and the existing test were not modified. The controller must
resolve/rerun this native boundary check; the full suite is not reported green.
No actual advisory provider was invoked or helper advice obtained.
`node --test tests/web.test.mjs` passed all 40 DOM tests (5.49 seconds).

Requirement audit is limited to this candidate; it does not certify the service:

| Contract requirements | Actual disposition in D07 |
| --- | --- |
| R01–R04, R12, R19, R21–R24, R29 | No forecasting, source, contract publication, sizing or model approval changes. All-market coverage/validation remains unfinished. |
| R05–R08, R13 | Original decisions, zero-allocation disclosure and distinct ticket money accounting preserved. No stake/cap change, issuance padding or wagering. Daily 15+ fulfillment is not measured here. |
| R09–R10, R16, R18, R22 | No UI, navigation, messaging or service changes. No live or Tailscale checks authorized. |
| R11, R14–R15, R17, R20, R28 | Shadow economic evaluation implemented; profit gap stays open. No retraining, promotion, scheduling or operational recovery claims. |
| R25–R27, R30 | Isolated source/tests/docs only; no protected file changes, helper CLIs, agents, credentials or production access. Rebase reviewed; controller's fixed suites and independent review remain required before release. |

Next work: obtain authorized immutable decision and reconciled execution exports
with actual payout/fee availability evidence; archive inputs and reports; evaluate
prospectively across sufficient independent dates/games. Add reviewed adapters
for exceptional settlements and durable report integration separately. Unknown
AI/data/hosting costs do not block that betting evaluation. All eight historical
profit gaps, the broader all-market milestone, production integration and live
release verification remain unfinished. No due date is changed by this candidate.
