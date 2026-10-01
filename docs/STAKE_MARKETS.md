# Stake market presentation

Verified against public Stake pages on 2026-09-28. The UI remains English.

| League | Winner, Handicap and Total suffix |
| --- | --- |
| NBA / WNBA / NFL | (Incl. Overtime) |
| MLB | (Incl. Extra Innings) |
| NHL | (Incl. Overtime and Penalties) |

Internal model keys `moneyline`, `spreads`, and `totals` remain unchanged. The presentation layer maps them to Winner, Handicap and Total respectively. A Winner selection displays the team name; Handicap displays the team and signed line; Total displays Over/Under and the line. Team identity and signed line are preserved regardless of how Stake orders the teams.

Supported picks are pregame, full-match two-way Winner, Handicap, match Total and exact named-team Total with explicit overtime/extra-innings scope. Team totals are marginals of the existing joint final-score distribution, not odds-derived projections. NHL bare team totals use the independently validated regulation model. NBA/WNBA/NFL/NHL scoped 1x2, halves, quarters and periods require their own approved model and validated market family. Player props, Same Game Multi and in-play bets are not yet approved.

Stake NHL markets require the explicit overtime-and-penalties scope: regular time is the default unless specified otherwise. MLB shortened-game settlement differs by market. The details panel links to Stake's rules and reminds the user to match the contract.

The engine now reads Stake's public Sports Data API on every market refresh. Recommendations are built from the exact Stake fixture, market name, signed line, Over/Under outcome and current decimal odds returned by that feed. A selection is withheld if the matching Stake event or value is absent. `availability_verified: true` identifies these feed-verified records. The UI still asks the user to recheck the displayed price because odds, suspensions and account limits can change before a manual bet is placed. The actual-bet form leaves accepted odds blank until the user enters the price accepted by Stake.

## Primary sources

Development contract: independently estimate sports probabilities, then score exact current Stake selections. Query `/schedule/sport/{sport}/usa/tournament/{league}` and the returned fixture's odds endpoint. Require an active outcome and exact validated scope; explicitly accept MLB Extra Innings. Reject a total outcome whose explicit line conflicts with the structured total. Stake pushes use stake-refund expected value, not exchange half-share payouts.

Both ranked recommendations and per-event alternatives are displayed and saved in the durable tip ledger, with identical league/time filters and working detail views. Alternatives are not extra funded bets. Each new candidate carries `stake_evidence` (fixture slug, original market/outcome names, decimal odds and retrieval time). Archive entries retain historical prices; already placed bets keep the accepted price and original contract.

MLB playoff calendar entries can retain names such as `NL Seed 3 - NL Seed 6` while the same fixture's active `Winner (Incl. Extra Innings)` outcomes name the actual teams. The adapter now resolves only these recognized placeholders using a unique, consistent pair of active Winner outcome names and the existing unambiguous team/time join. It rejects conflicting pairs, inactive contracts, real-team title conflicts and ambiguous times. It never derives teams from seed numbers. The original fixture name and `active_winner_outcome_team_names` matching basis remain in the evidence.

All model-evaluated sides and alternate lines are persisted in `evaluated_candidates` and visible in a collapsible comparison table. Each refresh selects the best current contract per market family, including already held contracts. A newly qualified best line or new market can be issued on an existing game. There is no fixed per-event count cap. Identical contracts are not reissued on repricing, and inferior alternate lines are not rotated in merely because the best is already held. Each selected family has an explicit allocation reason and rank, including alternatives. The dashboard distinguishes exact-contract coverage from related earlier lines. Issuance and ownership use contract IDs; historical dates, odds, stakes and duplicate reported tickets survive migration unchanged. The main page shows issued instructions and records; unfunded analyses are in Research. MLS tournament and Swish categories are inventoried without claiming an MLS or player-prop model. Validated non-MLB period models are listed separately.

Stake schedule and odds errors must surface as partial or unavailable feed status. A successful empty schedule alone does not prove complete sportsbook coverage. Supported markets include pregame full-game markets and independently validated MLB 1st Inning / Innings 1 to 5 markets. Other unsupported scopes require separate models and settlement tests before publication.

- [Basketball](https://stake.com/sports/basketball)
- [WNBA market example](https://stake.com/sports/basketball/usa/wnba/46722680-new-york-liberty-atlanta-dream)
- [NFL market example](https://stake.com/sports/american-football/usa/nfl/46560384-cincinnati-bengals-tampa-bay-buccaneers)
- [MLB market example](https://stake.com/sports/baseball/usa/mlb/46692105-san-diego-padres-minnesota-twins)
- [NHL market example](https://stake.com/sports/ice-hockey/usa/nhl/46604575-vegas-golden-knights-carolina-hurricanes)
- [Sportsbook rules](https://stake.com/policies/sportsbook)

Historical example pages are used only to verify terminology, never as current odds.

## Rolling opportunity coverage (2026-09-29)

`/api/dashboard.opportunities` contains `windows.24` and `windows.48`, recalculated against server UTC time on each request. Per-league counts use `(now, now + hours]`; the Budapest calendar-day issuance counter remains separate. `opportunity_inventory` is a compact fixture summary committed atomically in the same SQLite publication as the candidates, so a separately refreshed inventory file cannot contaminate the count.

Observed outcomes include standard and Swish sections and may overlap semantically. Fresh active standard outcomes require an active fixture, active market/outcome, finite decimal odds above 1 and source age no greater than 300 seconds. Valid-priced Swish offers remain unverified and do not enter that active count. No claim of exhaustive website coverage follows from these numbers. Model-evaluated outcomes count unique current guard-passed candidates, not unsupported offers. Qualifying families are the best selected contract per game/market whose stressed ROI meets the configured threshold, including already held contracts.

Recorded/unmarked counts use distinct exact contracts in the open ledger, not individual duplicate tickets. They include original issued instructions from earlier publication dates and retain records when current model/price eligibility changes. A checklist mark does not create or settle a bet. The frontend adds a Next 24 Hours filter and displays rolling coverage; detailed per-league counts stay in Research.

## Direct instruction buttons (2026-09-29)

Every Stake-backed card and its detail dialog now expose `Bet on Stake` using `stake_market.event_url`. This is validated from the record's original HTTPS `stake.com` URL and the exact league event path; query-string and fragment guesses are rejected. The button opens the match in a new tab and repeats the exact market/selection alongside it. Opening the link does not change the checklist, create a ledger entry, fill a bet slip or submit a bet. Direct prefilled outcome links have not been verified. Native Safari verification was unavailable because Computer Use permissions were not granted; the public event page also could not be opened by the web fetcher. The deployed button href is verified against the recorded feed fixture URL, not claimed as a tested Stake prefilled selection.

## MLB inning contracts (2026-09-29)

Separate actual-inning count models now price named first-inning and first-five-inning Winner, 1x2, Total, Handicap and team totals where the exact offer and validated family exist. Only integer and half lines are enabled. Full-game forecasts cannot substitute. Settlement reads verified named-inning scores; incomplete exceptions stay pending. Two-way Winner ties push, three-way ties win only Draw. Equivalent zero handicaps/two-way winners and minus-half handicaps/three-way team wins share allocation eligibility without changing recorded IDs. Details: [model/data/settlement report](MLB_INNINGS.hu.md).

## Market tab navigation (2026-09-29)

The Padres–Cubs example exposes `Innings 1 to 5 - Total / Over 4.5` in the feed group `innings 1 to 5`, not `main`. The [public event page](https://stake.com/sports/baseball/usa/mlb/46939434-nl-seed-4-nl-seed-5) lists the corresponding `Innings 1 to 5` tab. Main-market text alone is not the complete event offering. The raw market and group evidence is archived in `data/imports/stake-market-navigation-source.json`.

The parser retains all exact group IDs/names for the selected market/line, without duplicating its outcomes. `stake_evidence.market_groups` is checked and sealed by EvidenceGuard. Presentation prefers Main when that exact market is listed there; otherwise it uses a recognized website navigation group. API groups are not automatically website tabs (see the correction below). Cards and details show the tab, market and selection in order. No group is invented for historical records without evidence. An existing recorded bet can display the current exact-selection navigation while its original price, stake and model remain immutable. Buttons still open the event; no unverified query-string bet-slip or tab link is manufactured.

The event page and raw feed were readable. Interactive browser verification was unavailable because Computer Use had no available browser. The change does not assert account-specific availability or that a bet was submitted.

## Wider scope models and complete-market development

See [ALL_MARKETS.hu.md](ALL_MARKETS.hu.md). Quarter, half, period and regulation contracts have immutable separate scopes and exact observed Stake titles/tabs. Newly supported derived contracts include parity (zero is Even), Both Teams to Score and Double Chance in validated scopes. Positive half-point handicap aliases of Double Chance share economic ownership. Quarter Asian split lines remain blocked. The separate 60-day catalogue inventories development needs without becoming current betting instructions.

## Website/feed navigation correction (2026-09-30, release19.2.2)

The API `winner` group is **not** a visible Winner tab on the observed NHL event pages. The website exposes Main, Same Game Multi, Player Props, Quick Picks, Periods, Goals and Combo. Raw groups remain unchanged for contract evidence, while unknown/internal names no longer become fabricated tabs. NHL navigation uses only recognized names in the observed menu; other leagues retain recognized existing routes, including the independently observed MLB Innings 1 to 5 route. This is not proof of complete website coverage.

Direct Safari inspection of [Vancouver–Edmonton](https://stake.com/sports/ice-hockey/usa/nhl/46728056-vancouver-canucks-edmonton-oilers) and [Colorado–Los Angeles](https://stake.com/sports/ice-hockey/usa/nhl/46728057-colorado-avalanche-los-angeles-kings) found no regulation Double Chance result in Main, Goals or Combo searches. Periods exposes only the separately named period contracts. Search is scoped to the current tab; no global search or deep link to the regulation contract was verified. Neither an overtime Winner nor a period Double Chance substitutes for the proposed regulation contract.

The exact two event/market conflicts therefore block all regulation Double Chance sides in EvidenceGuard, the publication transaction and current execution review. They require a reviewed website recovery before removal. This is a bounded observation, not a universal claim that Stake never offers this market. Existing records and settlement remain unchanged; other games and markets stay eligible. Evidence: `data/stake-navigation-20260930/`.

Current navigation can explicitly lack a tab without falling back to an obsolete recorded tab. The event URL and route come from the same exact-selection source. Partial legacy metadata does not override the complete recorded route. Navigation-only changes close an open main-list instruction, preventing a stale click path after refresh.
