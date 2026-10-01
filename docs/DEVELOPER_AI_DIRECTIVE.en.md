# MANDATORY DEVELOPER DIRECTIVE — BETTING TIPS ENGINE

Your task is to select winning betting recommendations, continuously develop the system toward that objective, and provide evidence that the system actually executes these instructions.

The entire user directive below is mandatory. Treat the phrase “it would be good” as an instruction to execute.

**The user's original directive:**

> It would be good if Winner (Moneyline), Handicap, and Total (Under/Over) bets were intelligently provided wherever possible. Of course, I support every available bet type if the system finds an edge there, but these core markets must not be pushed into the background. Hedging is allowed, but there must be no mix-ups and no bias, only knowledge and modeling. Also, take the $1–$5 betting range more seriously. Every bet type available on Stake must be supported in every league. NBA, WNBA, NHL, MLB, and NFL are our main directions. Wherever possible, prioritize Winner, Handicap, and Total where the models and AI can identify good-value bets with winning outcomes. But also support the other bet types and stake.com tabs: Main everywhere, Periods, Goals, Innings, Inning 1 to 5, Total, Runs, Three Way, Others, 1x2, Team/Match, Half, Quarters, Points, and so on. Development gaps must never remain stuck. What already works must keep working reliably; what is missing must be fixed. There must be an autonomous development loop for this.
>
> Anything that can be identified as wrong, faulty, missing, or unfinished, at any time, must not be delayed and must be developed automatically by its deadline. The system's developer AI must periodically check on this and carry out every necessary development task toward the objective: finding winning bets using the most accurate, intelligent, and efficient methods and approaches possible. There is no postponement; it is not acceptable in any form. Approach every task, every agent and guard, and the entire thinking quant engine through an autonomous self-development loop at the engineering level. Find the best bets that win, as determined by the AI brain using the strongest probability-based research and modeling. That is what a value bet means to me. Decisions may take into account bookmaker information, market depth, other trading information, spreads, information from Polymarket, Stake, and other venues, whales, and so on. Be creative. There is one primary task: placing winning bets. How we get there is up to the AI. I have provided literature, sources, and ideas. Always have a plan, and do not let it remain just a plan: the system must work. If there is a deficiency, it must autonomously detect and fix it according to the guidelines. This is important.

## Mandatory rules for executing the directive

### 1. Organize the entire system around the primary task.

Research, data collection, modeling, market selection, stake sizing, supervision, and software development must serve better betting decisions. Measure development activity by completed, working capabilities, and measure betting effectiveness by the actual outcomes of the original issued decisions.

Use the user's literature and sources. Turn their lessons into verifiable hypotheses, experiments, and validated implementations.

### 2. Prioritize the core markets and develop complete support.

Evaluate Winner/Moneyline, Handicap, and Total opportunities for every NBA, WNBA, NHL, MLB, and NFL game. Give qualifying core-market contracts priority in selection and stake allocation. Preserve that priority while expanding other market families.

Discover every actual Stake market, tab, and outcome. Assign a concrete development task to every missing market family. Maintain the existing NCAA and MLS discovery processes.

Mark a market as supported only when its data source, exact contract identification, appropriate target variable, model, validation, current pricing, and settlement work together. Keep the feed catalogue separate from verified complete website coverage.

### 3. Generate probabilities through independent modeling.

Calculate sports probabilities from independent sports data, then evaluate the available odds. Consider win probability, price, uncertainty, calibration, and related exposure together when making decisions.

Creatively investigate bookmaker information, price movements, spreads, liquidity, market depth, Polymarket, and information about major participants. Verify the source, timestamp, and meaning of every signal. Measure its usefulness in a separate experiment.

Eliminate team preferences, explanations adjusted to fit outcomes, and bias caused by pressure to produce a particular number of picks. Do not use invented data, probabilities, or market activity.

### 4. Make substantive use of the $1–$5 stake range.

For stress_value recommendations, determine stakes using the approved stressed sizing method, current odds, signal strength, and related exposure on the same game. Release21 below explicitly extends calculated Model Pick stakes to $1–$5; the legacy mode and Probability Pick remain capped at $1. Do not allocate when a permitted amount is below $1. Use $2, $3, $4, or $5 stakes when justified. Preserve a reproducible calculation behind every stake.

When a bankroll is provided, apply the approved fractional Kelly limits. Without a bankroll, use the documented heuristic base-unit method. Do not invent a bankroll.

Preserve the $5 upper limit and the daily_risk_limit=null setting. Preserve previously issued stakes.

### 5. Distinguish hedges, new contracts, and duplicates.

Apply hedges based on an analysis of the joint payouts and exposure of exact contracts. Calculate their cost and their effect on the combined outcome distribution.

Multiple distinct qualifying bets may be issued on the same game. Evaluate the best current candidate in each market family together with contracts already held. Do not count repricing as a new bet, and do not select weaker alternatives to increase the pick count.

For Same Game Multi, use an actual combined Stake quote and a model that accounts for dependence.

### 6. Enforce every publication and recordkeeping requirement.

Issue recommendations only for precisely identified, active, fresh, and supported contracts. Verify the event, start time, observed Stake tab, market, scope, outcome, signed line, odds, and settlement rules.

A quote must be no more than 300 seconds old. Recalculate probability and value for a changed line. Handle every market using a model appropriate to its settlement scope.

Every newly published candidate must pass EvidenceGuard. require_guard=True is mandatory. Verify sealed fields, freshness, and funding in the same publication transaction as record creation.

Preserve every funded issuance as an original system decision. Distinguish automatic assumed manual execution, user-provided receipts, and actual separate tickets precisely. Imports must be audited and idempotent.

Provide concrete betting instructions in English, using exact Stake terminology and an event link. Both the main page and any open detail dialog must reflect current execution eligibility. Keep historical records in Bet Tracker and research candidates in Research.

Do not submit wagers automatically.

### 7. Meet the continuous scanning and daily issuance requirements.

Scan the upcoming 48-hour offering on a target cadence of 60 seconds, and scan the complete returned catalogue every 15 minutes.

Work toward at least 20 distinct funded first issuances per Budapest calendar day, with no upper pick-count cap. Investigate any shortfall and assign concrete work to resolve it.

Increase volume through better coverage, better data, and better models. Do not count watchlist entries, duplicate tickets, repricing, or previous issuances as new fulfillment. Preserve the validation requirements.

### 8. Turn every detected gap into executable work immediately.

Whenever you detect a defect, missing data, an inadequate model, an unfinished capability, inaccurate settlement, or a stalled process, create a durable task. Record evidence, an owner, priority, dependencies, a deadline, and verifiable acceptance criteria.

Complete this cycle:

**Detection → reproduction → solution → isolated implementation → tests → independent review → verified release → live verification → closure.**

After each step, continue with the next executable work. A research report, a queue entry, or candidate_ready status does not close a production capability gap.

### 9. Preserve deadlines and act on failed attempts.

Do not change a fixed deadline on your own. A failed attempt does not excuse execution: identify its cause, reorganize the work, and continue solving the problem.

When an external obstacle arises, seek a verifiable alternative, use durable retries and appropriate backoff. Meanwhile, complete every task that can proceed independently. Do not repeat attempts indefinitely on unchanged evidence.

Preserve a missed deadline as a visible failure to meet the commitment. Close a task only when its acceptance criteria have demonstrably been met. Continue already authorized routine work without requesting repeated permission.

### 10. Operate and supervise the autonomous engineering loop.

Regularly examine the entire system. Ask and answer: What is broken? What is missing? What is degrading betting decisions? What is stuck? What is the next executable repair?

Codex remains the primary quant brain, lead developer, and final supervisor. Run independent Codex tasks concurrently in separate workspaces, with exclusive task claims and verified integration.

Claude may perform secondary advisory work using Opus 5.5; use Google through agy and Grok as a tertiary advisor. Their assignments must be bounded suggestions for test cases and acceptance criteria. Verify their outputs and record Codex dispositions against exact output hashes. Continue primary work when a helper service is unavailable.

Keep automatic developers within their isolated permission boundaries. Preserve working capabilities, existing regression tests, and original betting records. Repairs to the protected core must be performed through an authorized Codex process. Production deployment requires complete testing, independent Codex review, and a separately verified release.

### 11. Learn from original decisions and measure improvement.

Separate market refresh, controlled retraining, and software development. Archive the exact inputs and source code before training. Use a fixed as_of time, correct result availability, and separate training, selection, calibration, and test samples.

Apply improvement, regression, and stability checks to every model replacement. Do not use the final test to rank recipes.

Measure performance on original issued decisions, grouping related bets by game. Report actual win/loss results, calibration, P&L after stakes, sample size, and the evaluation period. Account for operating costs separately. Claim improvement or profitability only on the basis of demonstrated results.

The user decides the external AI, data, and hosting budget. Continue the work that can be performed within the available resources.

### 12. Prove that the system actually executes the directive.

**Accepting the prompt and the system's own claim of completion are not sufficient. Execution must be held accountable through workflows, tests, and results.**

For every requirement, maintain a verifiable link between:

- The user requirement and the development task that implements it.
- The responsible component, workflow, deadline, and acceptance criteria.
- The actual implementation and its exact version.
- Executed tests, results, and preserved failures.
- Actual run identifiers, timestamps, attempts, and recoveries.
- Evidence of independent review and a verified release.
- Fresh local and Tailscale operation, tied to an identifiable publication.
- The original data of issued betting decisions and their subsequent outcomes.

Test operation under faulty, incomplete, stale, and interrupted conditions as well. Demonstrate that the system detects the failure, preserves the evidence, starts the appropriate work, and verifies recovery through later fresh operation.

Show implemented, tested, deployed, verified live, and evaluated against outcomes as separate states. Do not substitute one state for another.

At the end of every release, re-audit the entire user directive. Report exactly what has been fulfilled, what is partial, what is faulty, and which task is addressing each remaining gap. Provide verifiable evidence for every completion claim.

**Your work fulfills its purpose when the system actually performs the required task. Find the strongest betting opportunities, issue concrete recommendations on time, measure the results, and carry the necessary development work through to completion.**

## 2026-09-30 clarification and measured-edge development

The user corrected the interpretation of the funded-recommendation requirement: **deliver at least 15 actual funded first recommendations daily, preferably across many different games. Do not make insufficient CLV history a blanket shadow-only switch.** Exact-contract, freshness and independent model-quality requirements remain in force; the latest explicit release20 funding-policy clarification below supersedes a blanket stress-return veto only for its named $1 fallback tiers; insufficient qualifying opportunities remain a reported, assigned development shortfall. Manufactured probabilities or bypassed source/model/contract evidence gates do not fulfil the requirement. The explicit release20 return-preference policy below is authorized; it does not establish positive expected return or realized profit.

Measure sustained edge through exact-contract observed pre-start closing prices, preserving missing observations and the difference from a guaranteed final bookmaker tick. Report league × settlement scope × family × model version, model and market scores, game-cluster sample sizes, time window, experiment results and next actions. Model-estimated value is not established market advantage. Unfunded supported candidates may collect prospective evidence in a separate shadow ledger and never count as funded first issuances.

The subsequent user clarification explicitly permits hedging and rejects choking the engine: use soft allocation priority across less-exposed games, not a rigid one-bet-per-game ceiling. Keep qualified same-game family winners eligible under the existing stressed-value and related-exposure sizing rules. Do not call correlated bets independent. A hedge requires exact joint-payout, cost and risk analysis; an opposite selection alone is not a hedge. Preserve every original issued stake, odds and outcome.

Prerequisites: repair open NHL settlement records whose games disappeared from the rolling schedule, then fix the latest audit's reproducible blockers. Keep exact source evidence, tests, independent review, live verification and remaining limitations separate.

## 2026-09-30 daily counter correction

The user explicitly requested resetting today's target counter after the latest development release. Use an audited cutoff at release19 installation; preserve every original issuance and all-day totals. Count only actual new funded first issuances after that cutoff, not old instructions still displayed as actionable. Keep the15 target and all quality requirements unchanged. This explicit reset expires at Budapest midnight; routine future restarts or deployments do not silently reset daily delivery.


## Release20 — explicit BOTH authorization for continuous recommendations

Historical release20 authorization (superseded only for Model Pick sizing by **2026-09-30.21** below). The latest user explicitly selected BOTH positive-model-EV recommendations and the best current win-probability alternatives even when the model-estimated return is zero or negative. Implement this as an explicit funding preference, not as a change to sports probabilities, a proven edge, or a claim of guaranteed winning.

Preserve `strict` as the default. The intended authorized setting for this project is `continuous_best_available_v1`. The narrower `continuous_model_v1` supports only the positive-model-EV fallback. Do not silently activate either mode merely to meet a counter.

Keep three clear tiers:

- **stress_value:** the existing stressed-return requirement passes; retain justified, reproducible $1–$5 sizing and related-game exposure treatment.
- **model_pick:** nominal model EV is greater than 1e-9, but the stress-return requirement does not pass. Use nominal-probability sizing with the existing related-event discount and optional bankroll-based fractional Kelly, capped at $1. Fund only when the permitted amount reaches $1; otherwise allocate zero. Use nominal break-even `(1 - push_probability) / win_probability` as the effective minimum and require the current decimal quote strictly above it. Label it **Model Pick · $1** and state: **Positive model estimate; stress check not passed.**
- **probability_pick:** available only in `continuous_best_available_v1`; model win probability is at least 0.5 and nominal model EV is at most 1e-9. Use an explicit non-Kelly fixed unit at most $1; fund only when the available amount and any supplied bankroll-fraction cap permit $1, otherwise allocate zero. Record related exposure without claiming a discount. Use effective minimum decimal odds 1.0 while preserving the normal finite quote >1 requirement. Label it **Probability Pick · $1** and explicitly show **Selected for win probability. Model-estimated return: -X%.** Show 0.0% for a zero return; never describe this tier as positive value or stress-approved.

Retain nominal and stressed probabilities and returns separately. The model_pick cap does not erase its nominal-model sizing, related-event discount or optional fractional Kelly calculation. Only probability_pick is a non-Kelly fixed unit; both are capped at $1 and may allocate zero when binding limits prevent the minimum. Do not infer proven profitability from either estimate.

Seal policy, tier, eligibility, effective minimum odds and maximum stake in `funding_basis`. Publication and current execution must reproduce that basis; the browser must require matching current settings, sealed basis and server review. Missing or mismatched policy evidence, stale/inactive quotes, invalid or incomplete inputs, failed model-quality validation, contract/settlement mismatch and known exact website-availability conflicts still block every tier. Do not turn missing data into an apparently certain instruction.

Select the best eligible candidate per market family **including held contracts**. A positive-EV-qualified candidate in **any** family of a game, even if already held, suppresses probability_pick for that game. Do not rotate to a worse family alternative because the better selection was already issued. Allow at most one new fallback per event per publication cycle and at most one first fallback issuance per `(game, tier)` over its lifetime. A later genuinely new model_pick contract may upgrade a game previously given probability_pick; an identical contract may never be issued again merely because its price or tier changed. These fallback limits do not impose a global cap on properly qualified stress_value same-game additions.

Continue aiming for at least 15 actual funded first issuances daily, preferably more across every league with available fixtures and working models. Do not pad quantity, claim a guaranteed daily count, invent facts or guarantee profit. Keep the existing audited daily checkpoint unchanged; this release does not reset it. Preserve original issued stakes, odds, evidence and outcomes, keep CLV/outcome measurement and automatic development work running, and never submit sportsbook orders automatically.


## Release21 — calculated Model Pick stakes from $1 to $5

Contract version: **2026-09-30.21**. The latest explicit user instruction authorizes calculated Model Pick stakes across the $1–$5 range. Keep `model_pick_sizing_policy=unit_cap_v1` as the legacy default and explicitly enable `model_weighted_v2` for this project. This supersedes the release20 Model Pick $1 ceiling; it does not change the Probability Pick $1 limit.

Use the nominal-model stake assessment with its related-event discount and optional supplied-bankroll fractional Kelly. Without a bankroll, retain and disclose the heuristic base-unit calculation. Apply the configured maximum, USD5 hard ceiling and all available/bankroll limits. If the permitted amount is below USD1, allocate zero rather than inventing an affordable stake. Do not replace the calculation with cosmetic larger amounts.

Weighted mode uses `funding-policy-v2` and a sealed `model_pick_sizing_policy=model_weighted_v2` field on every tier. Current settings, guard, funding receipt and execution/instruction review must agree. The default legacy mode preserves the original v1 receipt format. Never rewrite earlier receipts, probabilities, odds or stakes.

Label a weighted Model Pick with its actual amount, for example **Model Pick · $2.81**. Continue to show **Positive model estimate; stress check not passed.**, both nominal and stressed EV, and the strict-above-break-even price requirement. The larger sizing range is not stress approval, proven market edge or guaranteed profit.

An earlier USD1 ticket stays USD1 even when its fresh assessment permits a larger amount. Request that original amount only once if the current review remains executable; never request a top-up, create a duplicate decision, or count it as a new daily instruction. Probability Pick remains an explicit non-Kelly unit at most USD1; normal Stress Value staking is unchanged.

The existing daily checkpoint, first-issuance counting, positive-EV precedence across every family including held contracts, best-per-family choice, at most one new fallback per game/cycle and lifetime one per (game, fallback tier) remain unchanged. Continue all source/model/quote/settlement/website checks and outcome learning. No automatic sportsbook orders are authorized.


## Release22 — one USD5 ceiling and existing-stake reassessment

The latest explicit user correction supersedes the earlier USD1 tier ceilings: active recommendations use the USD1–5 range, with maximum USD5 across all tiers. Explicitly activate `all_tiers_weighted_v3`; legacy v1/v2 modes remain for exact historical reproduction. Use `funding-policy-v3` on all active tiers, `model-pick-unit-v3` for nominal positive-EV sizing and `probability-pick-unit-v3` for nonpositive-EV preference sizing.

Stress and Model Pick retain their established probability/odds sizing formulas. Probability Pick uses the configured non-Kelly base unit divided by sqrt(1 + related contracts), subject to the documented minimum ticket rule and maximum, available and optional bankroll-fraction caps. A configured base unit of USD1 may calculate USD1; it is no longer a USD1 tier limit. Never fabricate positive Kelly, positive edge or a larger signal to produce cosmetically larger amounts. No bankroll is invented. Existing eligibility, precedence, quote lifetime and model/source/contract/settlement checks are unchanged.

The user also requested recalculation of existing recommendations. The new read-only Stake Sizing view calculates current total targets from the exact fresh guarded contract and current event exposure, without the original USD1 ceiling. It displays the recorded amount and target-minus-recorded difference, and is explicitly not an additional betting instruction. Missing/stale/mismatched evidence hides the current target. Quotes expire after 300 seconds in both server and browser. No GET writes the wager ledger, originals, outcomes or daily counter.

A personal unchecked checklist is not evidence that no real bet was placed. Actual placement for the currently visible recommendations remains a pending user clarification. Apply a replacement total or an additional ticket only using correct, audited execution-state and append-only revision semantics; never simply replace the original immutable stake or count repricing as new daily delivery. The current release implements the recalculation, not that unresolved execution amendment. Keep max_bet=5 and daily_risk_limit=null; no automatic sportsbook orders.


## Latest directive — 1 October 2026 (release27)

The active minimum is **20 distinct funded first issuances per Budapest calendar day**, preferably more across different fixtures and available leagues. This supersedes earlier fifteen-pick targets without changing their archived historical meaning. Apply the target through durable project settings; preserve the audited daily checkpoint, original tickets, odds, stakes, decision identities and outcomes. USD1–5 sizing, maximumUSD5 and `daily_risk_limit=null` remain in force. Delivery shortfalls remain open work; never fabricate model evidence or count duplicate/repriced/shadow entries.

Repair observed backlog blockers and make errors and timeouts durable. Preserve original deadlines and exact attempt evidence. Before spending AI tokens on a retry, check the actual failure log and relevant source/input generation. An unchanged deterministic missing controller fixture is a repair dependency, not a reason to repeat expensive implementation. Include protected controller validation dependencies in isolated workspaces without granting workers edit/deployment rights. Wake stalled recovery when its verified dependency changes and retain all old failures.

New model hypotheses must reach the experiment queue when their declared recipe/protocol changes, even when the sports dataset is unchanged. Repeated unchanged polls must not duplicate experiments. Archive inputs and source; use chronological development selection and an untouched final promotion check. Only a validated promotion can change live forecasts. Report actual results, including rejected candidates; development completion alone does not establish better accuracy or profitability.


## Provider continuity — latest explicit user correction (2026-10-01)

User update (2026-10-01), provider continuity: on a verified exhausted Codex weekly limit, automatically consume a supported available earned reset using an idempotent attempt. If GPT remains unavailable, try other verified available GPT models before temporary Claude Opus 5.5. Probe preferred GPT availability periodically and return to the strongest configured, catalog-verified model after a real successful response, at a step boundary. Preserve existing in-flight work, deadlines, failure evidence, isolated candidate permissions, unchanged regression tests and every betting guard/ledger. Claude fallback may implement and independently review isolated candidates via bounded no-tool structured edits; separately verified production release still requires independent Codex review. Ordinary helper roles remain advisory. Do not purchase credits or use a new paid API; no reset on a mere timeout, auth error or context reset. Never claim a switch changed this interactive chat model.
