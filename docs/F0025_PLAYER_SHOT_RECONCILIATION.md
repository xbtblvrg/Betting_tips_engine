# F0025 — NHL player shot outcome reconciliation

Implemented a new offline source evaluator in
`src/betting_engine/player_shot_reconciliation.py`. This completes the bounded
candidate increment, not M03 or production player-prop activation. No rebase
conflict manifest was present. Existing source, tests and protected files remain
unchanged. Independent controller review is pending.

The accepted work already decodes NHL box scores, fits retrospective player
studies, parses props, calculates count payoffs and evaluates archived offers.
It lacked player-level reconciliation between native play-by-play and final
box-score counts. This increment addresses that gap using the supplied NHL
observations; it does not repeat contract parsing or injury-context work.

## Behavior and candidate integration

`reconcile_nhl_player_shots(play_by_play, *, as_of, boxscore=None)` consumes the
existing `PlayerArchive` type and optionally calls the existing
`decode_nhl_archive` box-score adapter. It performs no file writes or network
calls. Both inputs are exact archived endpoint bytes with hashes, observation
times and archive references. The explicit cutoff rejects future captures;
scheduled start and filesystem times never become publication times.

The native adapter checks event URL/ID, final state, teams, roster uniqueness,
player/team attribution, event IDs/order, period scope and clocks, terminal
markers, event code/name agreement, and team shot/goal totals. Unknown event
types and incomplete inputs are rejected. Goals add one shot on goal;
missed/blocked shots do not. Overtime counts; shootout events and the scoreboard
shootout award do not become player shots or goals. Each player count retains
its contributing event IDs, NHL namespace, team abbreviation and numeric team ID.

Without a box score, output is `team_totals_reconciled` with
`missing_independent_player_boxscore`, observed counts and null
`reconciled_value`. Agreement with the same response's team total is a consistency
check, not independent proof of completeness. The optional separate endpoint
must match event, start, game type, final period, teams, totals and roster, then
agree for each player's SOG. Moving a shot between teammates is rejected even
if team totals still agree. Any failure clears every reconciled value.

Positive final box-score time on ice is required for a `reconciled_value`,
including zero. Roster membership, missing/zero time on ice and zero observed
shots never imply DNP or current availability. Goalies with no SOG field retain
unknown values. Exact roster matching and unknown-event rejection are deliberately
conservative and may reject otherwise usable future source variants.

Successful two-document comparisons return `player_boxscore_reconciled`.
“Independent boxscore” here means a separately supplied endpoint artifact from
the same NHL provider, not independent statistical evidence or an independent
provider. The assessment hash identifies a deterministic result; it is not a
publication seal. Corrected versions produce distinct reports without changing
earlier reports; the caller must retain them. No revision is chosen automatically.

All outputs remain `publishable=false`, `settlement_eligible=false`,
`production_supported=false` and `current_participation_verified=false`.
`shots_on_goal` is never silently equated with Stake's `shots` label. There is no
settlement interchange, DNP/void/push approval, fitted forecast, price, stake,
ledger operation or production hook in this increment.

## Observed evidence

[Evidence JSON](F0025_PLAYER_SHOT_EVIDENCE.json) records hashes, source clocks,
replay results and original-file preservation. All 24 declared files in the
12-source public bundle passed byte-length/hash checks. Native NHL source
receipts were checked against body hashes, URLs, capture times and HTTP status.

| Native game | Home / away observed SOG | Result |
| --- | --- | --- |
| 2022030145 | NJD 43 / NYR 23 | Team totals agree |
| 2022020939 | VAN 27 / BOS 35 | Team totals agree |
| 2022020583 | CBJ 38 / CHI 28 | Team totals agree |
| 2022020543 | ARI 24 / LAK 27 | Team totals agree; shootout excluded |
| 2022020222 | WSH 24 / TBL 29 | Team totals agree |

These are 298 contributing shot events and 200 roster rows across five historical
games, not 200 independent observations or current opportunities. All five reject
a cutoff before their captures. Source publication times remain unknown;
collector clocks are not independently authenticated.

The pinned history loader verified the supplied 550,729-row snapshot, including
167,659 NHL rows. Actual fields include `identity_namespace=nhl`, player/game/team
IDs, `stats`, `played`, `minutes`, source hashes, conservative availability and
`stat_scope=full_game_including_overtime_excluding_shootout`. None of those NHL
rows matches these five game IDs. No native NHL box-score endpoint body was found
in the copied player raw-source receipts. Therefore real player-by-player
box-score confirmation remains missing; successful two-document cases are
explicitly synthetic native-schema tests. Historical sources are present, not
current participation evidence. No real model validation or P&L was measured.

K01, K03 and K08 were read as methodological source material: exact outcome
semantics, source-clock/identity separation, and independent chronological
validation. Their hashes are retained; their text supplied no operational
instructions or invented sports facts.

## Validation and requirement audit

Commands use the required interpreter, `PYTHONDONTWRITEBYTECODE=1`,
`PYTHONPATH=src`, `OMP_NUM_THREADS=1` and pytest `-p no:cacheprovider`:

```text
python -m pytest -q -p no:cacheprovider tests/test_f0025_player_shot_reconciliation.py
python -m pytest -q -p no:cacheprovider tests/test_player_archive.py tests/test_player_archive_sources.py tests/test_player_history.py tests/test_player_prop_settlement.py tests/test_player_prop_contracts.py tests/test_f0023_player_prop_probability.py tests/test_f0024_player_prop_offer_evaluation.py tests/test_participation.py tests/test_api.py
node --test tests/web.test.mjs
```

The Python command is `/Users/belavarga/Betting_tips_engine/.venv/bin/python`.
The API run sets `BETTING_DATA_DIR=/private/tmp/f0025-api-tests` and uses synthetic
test databases. Results: **53 new tests, 393 existing Python/API tests and 40 DOM
tests passed**. The existing suite emitted one Starlette/httpx deprecation warning.
New tests include real archive replay, goalie/zero/DNP boundaries, overtime and
shootout scope, missing data, ambiguous identity, source/order tampering, future
captures, player attribution conflicts despite matching team totals, correction
preservation and rejection by the existing current-participation validator.

| Requirements reviewed | Actual fulfillment in this candidate |
| --- | --- |
| R01–R04, R11, R15, R19, R21, R23–R24; independent inputs and exact scope | Source-bound outcome reconciliation implemented offline. No forecast fitting, model promotion, chronological gate change or prop activation. Native source/contract/settlement verification remains incomplete. |
| R05–R08, R13, R20, R29; instructions, stakes and immutable lifecycle | Zero issued instructions. Manual wagering, $1–$5 limits, null daily cap, original decisions and core-market priorities unchanged. Daily minimum and profit objective remain unfulfilled by this increment. |
| R09–R10, R12, R16–R18, R22; UI, coverage, links and supervision | No UI/feed/supervisor changes. Existing DOM checks passed. No live publication, local/Tailscale service or current coverage was accessed or verified. |
| R14, R25–R28, R30 and later release instructions | New utility/test/docs only; no protected edits, deadline changes, helpers, nested agents, credentials, account access, external messaging or production database access. Original tests preserved; controller review and release pending. |

Next: controller full fixed suites and independent read-only review. Obtain
matching real final box-score captures and live-check the native adapter,
including corrections, exceptional games and completeness. Separately obtain
event-bound participation/role and Stake identity/metric mappings; verify
DNP/void/push rules and sportsbook settlement. Validate each player family on
fresh chronological samples with authentic source availability. Only a separately
reviewed root integration may connect this utility to training or settlement;
any later release needs fresh local/Tailscale publication checks. NBA/WNBA/MLB/
NFL/NCAA prop gaps remain open. No automatic utility installation is authorized
by the supplied release profile.
