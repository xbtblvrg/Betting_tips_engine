# D09 candidate audit — 2026-09-29

Bounded objective: implemented a standalone, deterministic first/last scorer,
scoring-time and scoring-type count extractor. It rejects missing/incomplete
history and requires hash-bound completeness/availability attestations, full
period coverage and independent score reconciliation. Ties, scoreless games,
corrections, regulation/OT, extra innings and shootout exclusion are tested.
This candidate is not production delivery or a verified event-source integration.

## Rebase resolution

Reviewed `data/rebase/conflicts.json` before implementing D09. The sole flagged
file was `src/betting_engine/closing_prices.py`. Compared the preserved candidate
and upstream versions: upstream contains all earlier logic, plus exact-title
legacy full-game scope compatibility and the observed `Draw or Team` double-chance
word order. Retaining upstream preserves both changes and the candidate's
identity-conflict guards. No source replacement was necessary.

- Retained source/upstream SHA-256:
  `eebf3804b6d84e6f176c544aec63100786b78bebc0beba50a2356f7177f6d241`.
- Prior candidate SHA-256:
  `f5fd32d58291e88a06d4b65098623e4fe9c7085b81ad72b16c75665dbafa4fcf`.
- Existing scope, double-chance and identity-conflict tests passed unchanged.
- The rebase input manifest remains untouched: only `src/betting_engine`, `tests`
  and `docs` are authorized edit locations. This document records the resolution.

## Verification

Python checks used the required interpreter with `PYTHONPATH=src` and
`OMP_NUM_THREADS=1`:

```text
/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_event_sequence.py tests/test_periods.py tests/test_segments.py tests/test_api.py
106 passed (62 new extractor cases); one existing FastAPI/httpx deprecation warning

/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_closing_prices.py tests/test_closing_price_identity_conflicts.py \
  tests/test_closing_integration_repairs.py tests/test_closing_capture.py
136 passed

node --test tests/web.test.mjs
40 passed
```

Read-only local schema/sample evidence and the exact extraction contract are in
[EVENT_SEQUENCE.md](EVENT_SEQUENCE.md). Synthetic fixture dates and evidence
references are explicitly test data. No real event-sequence model metric or live
source completeness is claimed. The controller's full fixed suites and independent
read-only review remain required. Git status was unavailable because system Git
requires Xcode license acceptance; no system settings were changed.

## Requirement ledger review

Read AGENTS.md and all R01–R30 in `config/project_contract.json` (2026-09-29.14).
This is an audit of the increment's impact, not a claim that the whole engine has
fulfilled these requirements.

| Requirements | Actual fulfillment in this candidate; remaining work |
| --- | --- |
| R01–R02 | Independent sports-label interface and traceable K03/K04/K06/K07/K08 methodology. No odds-derived forecast. Real event source verification remains pending. |
| R03, R12, R24 | No scanner/catalogue change. Supplied schemas inspected. All-league/NCAA source/model coverage remains unfinished; unknown college rules reject. |
| R04, R18–R19, R22 | No new Stake instructions, contract reinterpretation, links or guard/publication change. Exact current contract, scope, freshness, sealing and execution checks remain required before integration. |
| R05–R06, R20, R29 | No funded instructions or allocation changes. $1–$5, no fixed daily cap and core Winner/Handicap/Total priority preserved. Daily 15+ delivery and measured profit are not established by target extraction. No gap or due date changed. |
| R07–R08, R13 | No production ledger access, historical decision rewrite, order submission or checklist change. Full archived input versions remain separate; no automatic settlement writes. |
| R09–R10, R16 | English docs; existing DOM/API regression checks pass. No UI/count/window changes. Local live publication/Tailscale/service verification not performed under workspace restrictions. |
| R11, R14–R15, R17 | No worker, source-refresh or promotion changes. Exact version availability is enforced in extraction; chronological model selection/test and durable retraining require separate work. |
| R21, R23 | Separate event targets rather than fractions of final scores; regulation/OT/inning/scoring-type distinctions tested. No player-prop, special, combination or event-sequence probability model approved. NHL shootout award cannot become a scoring target. |
| R25–R28 | Isolated candidate; protected code/contract/test runner and all existing tests retained. No helper advice, nested agents or helper CLIs used. Codex performed implementation/review; controller independent review/full tests and any separately verified release remain pending. |
| R30 | This audit records concrete tests, source limitations and next steps. No production or profit completion claim. |

## Concrete remaining work

1. Obtain complete independent scoring/play-by-play archives and authoritative
   version/correction evidence. Implement provider-specific schema and terminal/
   pagination verification, with live checks. Existing box scores do not prove
   current participation, timestamps or complete event history.
2. Validate exact scorer semantics, market intervals, regulation/OT rules and
   settlement against real archived matches. Add exceptional/shortened-game and
   sport-specific cases only with source evidence; keep unsupported families open.
3. Train/evaluate family-specific independent models with original lifecycle
   outcomes, game-clustered observations, chronological selection and untouched
   tests. Report measured betting P&L separately from unknown operating costs.
4. Complete controller full suites and independent review. Integrate only through
   separately reviewed production gates, then verify fresh local/Tailscale
   publication under separate production authorization. No release receipt or
   wager-database write was created here.
