# F0024 — Exact archived player-offer evaluation

Implemented `src/betting_engine/player_prop_offer_evaluation.py`, an offline
integration of the existing pinned D06 model reader, ECDB contract/identity
parser and F0023 payoff calculation. The bounded candidate increment is complete;
M03 and production player-prop activation remain unfinished. No conflict manifest
was present. No existing source, regression, contract or protected file was edited.

## Behavior

`evaluate_archived_count_offer(study_root, *, artifact_index_sha256, metric_key,
player_id, team_id, feed_body, feed_evidence, contract_id, as_of, resolution=None,
evidence_bodies=None)` loads the independent, selection-locked model before
inspecting odds. It renormalizes original endpoint bytes and selects one exact
contract hash; callers cannot supply a modified normalized row as an offer.

The evaluator checks original source hashes, active standard availability,
maximum 300-second quote age, timezone-aware replay cutoff, exact raw
`fixture.startTime` milliseconds and schedule agreement. It rejects started
events, future sources, studies whose recorded as-of follows the replay cutoff,
and targets before the model's fitting cutoff. These checks do not authenticate
the study's actual creation time or historical input availability.

Only NBA/WNBA full-game player points explicitly including overtime currently
share exact semantics between the existing studies and parser. Periods,
regulation-only points and other statistics cannot borrow that distribution.
NHL `shots` is not silently mapped to `shots_on_goal`; NFL continuous studies,
MLB's missing prop-parser intersection and NCAA's missing studies remain gaps.

The existing source-bound identity-resolution interchange is required. Its
mapping document must have one unique `/entries/N` record for the provider
event/player, with the existing official/provider fields plus:

```json
{
  "model_namespace": "espn",
  "model_player_id": "<exact model player ID>",
  "model_team_id": "<exact model team ID>",
  "model_game_id": "WNBA:<exact model event ID>",
  "fixture_start_time_ms": 1790721000000
}
```

This illustrates the interchange schema, not an observed mapping. IDs in the
three namespaces remain separate. Hashes, official-host checks, source times,
and exact record values are checked; conflicting/duplicate records and duplicate
JSON fields are rejected. The module does not establish the truth of an adapter's
assertions or derive this interchange from a native provider endpoint. Live
crosswalk validation is an explicit blocker even on successful calculations.
No identity or participation is inferred from names or historical appearances.

On success, the result preserves the original provider contract ID, tabs, line,
price, source and model hashes, plus the separate identity resolution. Changed
prices preserve contract identity and probabilities; changed lines change the
contract and recompute probabilities. Over/Under integer equality retains push
mass, while integer ladders include equality in wins. Conditional unit EV uses
`p_win * (odds - 1) - p_loss`. Void probability remains unknown.

Rejections contain reasons and `payoff=null`; successful synthetic cases return
`state=conditional_archived_offer_payoff`. Both have `publishable=false`,
`execution_eligible=false` and explicit activation blockers. The assessment hash
is a reproducibility checksum, not an EvidenceGuard seal. There is no caller in
the production runtime, model fitting, stake calculation, ledger write or wager
submission. A caller-supplied replay cutoff does not make an old quote current.

## Evidence and tests

[Evidence JSON](F0024_PLAYER_PROP_OFFER_EVIDENCE.json) records the inspected
550,729-row history schema and hashes, the 12-source public bundle (all 24
declared files passed byte/hash checks), native event/injury schema inspection,
knowledge-card hashes, and 14 real archive rejection probes. Historical sources
are present; authenticated historical availability and current participation
are missing. Source publication times were not invented.

The observed WNBA archive contains 330 normalized contracts; its fixtures and
odds copies are the same body, not 660 independent offers. Other archived counts
are NFL 130, NHL skater 184 and NHL goalie 252; sampled NBA/NCAA bodies contain
zero props. These are archived parser counts, not fresh supported opportunities
or complete website coverage. No real offer passed this evaluator. In particular,
the WNBA archive capture precedes the study's recorded as-of, and no verified
crosswalk was supplied. All successful end-to-end cases use explicitly synthetic
source/identity fixtures. No new fit, holdout metric, issued instruction or
realized profit was produced.

K01, K02, K03 and K08 informed refund arithmetic, independent forecasts,
source/identity clocks and chronological validation. Cards were treated as
methodological source material, not operational instructions.

Validation: **60 new tests passed (13.31s), 329 existing related Python/API tests
passed (49.20s), and 40 DOM tests passed (7.37s)**. The API suite emitted the
existing Starlette/httpx deprecation warning. Commands:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_f0024_player_prop_offer_evaluation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src OMP_NUM_THREADS=1 BETTING_DATA_DIR=/private/tmp/f0024-api-tests /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_f0023_player_prop_probability.py tests/test_d06_player_model_study.py tests/test_player_prop_contracts.py tests/test_player_prop_settlement.py tests/test_ecdb_player_identity.py tests/test_participation.py tests/test_f0016_injury_context.py tests/test_api.py
node --test tests/web.test.mjs
```

## Candidate requirement audit and remaining work

| Requirements | Actual disposition |
| --- | --- |
| R01–R04, R11, R15, R19, R21, R23–R24 | Independent pinned artifacts, exact scope/line/identity checks and rejection evidence implemented offline. Validated live prop models, native crosswalks, participation and sportsbook settlement remain incomplete. No guard or promotion change. |
| R05–R08, R13, R20, R29 | $1–$5 stakes, null daily cap, manual wagering and immutable decisions untouched. Zero new instructions; the daily minimum and profit objective are not declared fulfilled. |
| R09–R10, R12, R16–R18, R22 | No UI/feed/supervisor/navigation changes. DOM tests passed; current coverage, local/Tailscale publication and source recovery were not verified. |
| R14, R25–R28, R30 and later development instructions | Isolated new utility/test/docs only; no helper, nested agent, production service, credentials, account state or external messaging. Original-file byte preservation checked. Controller review and release remain separate. No deadline or release policy changed. |

Next: the controller runs the full fixed suites and independent read-only review.
Before a separately verified release or activation, implement and live-check
native event/player/team crosswalk adapters; collect event-bound participation
and role evidence; validate each supported family on fresh separate chronological
samples with authentic source availability; verify Stake DNP/void/push rules and
settlement adapters. Extend unsupported league/metric intersections only with
their own source/model/scope evidence. A root-reviewed integration must connect
these pieces to protected guards and settlement, followed by fresh local and
Tailscale publication checks. This candidate performs none of those release steps.
