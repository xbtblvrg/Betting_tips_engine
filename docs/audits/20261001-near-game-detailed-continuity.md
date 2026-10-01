# Near-game and detailed-market continuity — release audit

Scope: the 2026-10-01 request for genuinely new, informed near-game and detailed-market instructions after earlier instructions were placed. The change expands a versioned allocation policy; it does not establish a better model, measured edge or profit.

## Diagnosis

Frozen publication 5005/5006 had 1,138 guarded candidates and 310 family winners, with zero new funded instructions. 665 detailed candidates were evaluated and 223 detailed family winners selected. 78 detailed selections were stopped by the lifetime one-Model-Pick-per-game allocation rule. Existing models included 31 period models and two MLB segment models. No approved model scope was found silently omitted by the current parser. Zero unmarked instructions were ready within 24 hours, while five later NCAAF instructions prevented the old whole-list depletion trigger from firing.

## Reviewed changes

- Explicit opt-in `model_pick_allocation_policy=distinct_family_v1`; legacy default and original receipts remain reproducible. A nominal-positive Model Pick may allocate the current best exact contract in a previously unfunded canonical game/family. All original funded bets and decisions count, including payoff aliases and vanished quotes. No inferior-line rotation or repeated exact contract.
- Complete-batch related-event sizing avoids issuing an amount that immediately becomes unsupported when co-issued contracts enter the ledger. A subminimum candidate is removed without replacing it with an inferior candidate. The exact surviving batch, settings and actual exposure are rechecked in the publication transaction.
- Same-candidate conflicting copies and equivalent-contract aliases cannot evade atomic issuance checks. A concurrent ledger/settings change rejects the stale batch without a partial publication.
- Current execution, the web list and an open detail view agree on the sealed active allocation policy. Existing tickets retain their original stake and historical source receipt.
- Exhausting the next 12 hours triggers a durable coalesced priority scan across 48 hours even when farther instructions remain. Far-only new results do not falsely complete a near-game refill; backoff, original request identity and attempts remain.

## Evidence and remaining limits

All archived diagnosis, failed reproductions, source pins, independent component reviews, complete test logs, controlled-install receipts and post-install observations belong under `data/near-game-expansion-20261001/`. The completed source release manifest is `ed2c5bf3a42dab4773281dccd301544527f1dcd54202716d58aaf8c8224d4ff8`; independent release review is `36cd177736288a92370420b1a5263c04a2b1ab7d3efe61c4dc8860c2f95485e3`. Complete final tests: 5,158 Python checks plus five subtests, and 272 frontend DOM checks. All 259 original test-tree files remain unchanged.

Main-market priority remains among eligible family winners. More than one family on a match may be correlated. Existing related-event discounts and the USD1 ticket floor do not impose an aggregate event cap; the user has not authorized a new aggregate cap. Model Pick means positive nominal modeled EV with the stress requirement failing, not an empirically validated profitable bet. Original odds, probabilities, stakes and losses remain immutable. No sportsbook orders are submitted.

| Requirement group | Implementation/evidence state |
|---|---|
| Near-game continuity and additional detailed instructions | Implemented, independently reviewed, installed and live verified at 21:17 UTC |
| Exact quote <=300s, model/input/scope and website checks; EvidenceGuard publication | Preserved, tested; no freshness or quality bypass |
| USD1–5, max5, no invented bankroll, daily risk cap null | Preserved; complete-batch calculation reproduced |
| Minimum20 distinct funded first issuances per Budapest day | Objective preserved; no duplicate, reprice or research padding; future daily totals not guaranteed |
| Period/inning/team-total/three-way and other approved detailed contracts | Existing validated support used; actual live new families must be reported |
| Player props, sequence and combined/SGM markets | Partial: validated participation/history, target/model/settlement or exact combined quote still required; D06/D21 and existing family tasks remain open |
| NCAA/MLS inventory, core NBA/WNBA/MLB/NHL/NFL search | Existing scans preserved; absence of current fixtures is reported, not fabricated as coverage |
| Outcome tracking, closing-price evidence and learning | Existing originals and jobs preserved; this release makes no model-promotion or profitability claim |
| Engineering autonomy, independent review and controlled release | Existing boundary preserved; provider recovery unaffected; D31/D25 and recurring isolated validation failures remain concrete separate work |
| Local and Tailscale delivery | Verified native HTTP200 and TLS on publication5051; actual captured API rendered through the installed frontend DOM harness |

Fixed root task deadline: 2026-10-01T22:00:00Z. Original deadlines and failed attempts for other tasks are unchanged.


## Controlled installation and observed result

Installed 2026-10-01T21:12:49.481648Z (23:12 Budapest). The one authorized settings change activates distinct_family_v1; original bankroll, max_bet=5, daily_risk_limit=null, target20 and daily checkpoint remain semantically identical. Developer admissions were drained, existing jobs ended naturally, then idle services were stopped under locks. The market engine remained active until the short reviewed installation stop. All five services restarted successfully; immutable ledger comparison passed. Runtime source installation has no model promotion or sportsbook submission.

The independent read-only observation at 2026-10-01T21:17:03.403302Z found five fresh post-install publications5047–5051. Local and Tailscale both returned publication5051 with verified native TLS and HTTP200. There were96 new distinct funded first issuances after installation and97 currently actionable unmarked instructions across19 games (one existing instruction also remained ready). Of the97 instructions,87 were within12 hours across11 games.77 were non-full-game scoped instructions, including regulation, periods, halves, quarters and innings; another three were full-game team totals (WNBA away, MLB home, NFL away). Thus80 went beyond full-game Winner/Handicap/Total. The97 displayed original stakes totaledUSD98.63. These counts describe this observation, not a promise that changing quotes remain executable.

New first issuances by league: NHL67, NFL18, MLB5, WNBA2, NCAAF4. The current48-hour independent calendar contained no NBA games; NBA recommendations were not fabricated. The NBA model/feed path remains in the ordinary scan.

Of the96 new first issuances,95 were Model Picks: independently reproduced positive nominal expected value with the stress-value requirement failing. One later NFL first-half Total was a Stress Value issuance at21:16:58.161575Z, passing the configured stressed-return threshold. These classifications are not measured CLV-positive segments, a model improvement claim or guaranteed profitable bets. Several are on the same game. Complete-batch discounting and the USD1 minimum ticket are heuristics; they do not prove portfolio independence or bound aggregate match loss. Original probabilities, prices, stakes and settlement records remain available for grouped outcome analysis.

The immutable baseline contained207 tickets,206 system decisions and199 first-issuance rows; the observed ledger contained303,302 and295 respectively, with every original field unchanged. Canonical payoff aliases and previously funded market families produced no duplicate new first issuance.

The actual local and Tailscale captured dashboards both rendered97 matching cards,87 near12h, with exact original card/detail stakes and market/selection labels. Per-league filters, marked/stale exclusion and current policy were checked. This was the installed frontend in a DOM harness; CUA reported no available browser, so no real-browser visual claim is made.

The first verifier's untracked-display branch later received an additional stake-to-publication binding fix. Its first observed proof consisted entirely of tracked originals (97/97 on both surfaces) and is accepted only in that bounded scope. The general verifier fix is separately tested and captured by the later post-install-2 evidence. Runtime betting source was not changed by verifier repairs.

## Remaining engineering work, with original failures retained

- Player props remain partial: D06 historical-availability validation and D21 current participation/identity/source wiring are prerequisites. A discovered market name or a passive utility is not a deployable betting model. Some WNBA half/quarter families also fail their chronological model-quality gates. These models were not activated by this release.
- D31 frozen validation-input preparation is still a separate undeployed root repair. Its original repair deadline of18:19:56Z onOct1 remains missed; the original task deadline remainsOct2 11:30. D25's retained research/code continuation and reference-worker wiring also remain separate unfinished work.
- New read-only worker diagnosis: F0038's unchanged protected workspace-copy code opens the /private ancestor and is denied by its native sandbox. The unchanged failure repeated, eventually receiving a generic600s timeout. F0013's passive release also ended with a600s Python timeout; its partial log is insufficient to attribute every failure to that same cause. Preserve both failures and original deadlines. The next root repair is trusted-anchor/descriptor copying compatible with the existing sandbox, followed by the unchanged failing native test and path-escape tests. No broader file permission or repeated unchanged coding budget is an accepted repair.
- After restart the betting engine is healthy and continues fresh publications, while the engineering supervisor still reports existing development/release gaps. This release does not declare the entire autonomous-development backlog fixed.

Evidence: `data/near-game-expansion-20261001/release/`, `live-verification/post-install-1/`, `live-verification/post-install-2/`, `allocation-review/`, `reviews/model-outcomes/`, `worker-blockers-audit/`. Root task `ROOT-NEAR-GAME-DETAILED-CONTINUITY-20261001` is closed only after final independent live review; other task statuses and deadlines are unchanged.


Second fresh observation: at 2026-10-01T21:20:37.538078Z the corrected general verifier bound publications5050–5054, with local and Tailscale both showing5054 and the same97 ready/87 near12h/77 non-full-game scoped instructions. The96 distinct new first issuances remained unchanged. Receipt SHA256: `d0ca11013540a1f0fae5854d6a71104d00cc87019e302042a631a092655378b0`. Independent tool review `live-verification/independent-tool-review.json` pins the corrected verifier after40 Python and four DOM fixture checks. The earlier full DOM rendering remains a point-in-time proof of publication5051; it is not relabeled as a real-browser run or a DOM capture of5054.
