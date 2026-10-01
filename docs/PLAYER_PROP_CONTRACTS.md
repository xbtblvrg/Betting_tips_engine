# Exact player-prop contracts and revisioned settlement

Implemented as separate modules on 2026-09-29. These modules do not price, publish, place, or write settlements to the production ledger. Recognition, normalized identity, model validation, execution availability and settlement approval are separate states. There is no full-game probability substitution.

## Source evidence and actual coverage

The reproducible probe is `data/validation/player-prop-catalogue-probe.json`. It reads the complete original endpoint archives associated with catalogue version `20260929T184257055207Z`, checked at `2026-09-29T18:43:13.486346+00:00`, SHA-256 `1dae2fcd71c4fe4783438fd887866e03fd7e80829c8721741020205669dd37cf`. No live database was changed. These historical quotes are not current execution instructions.

| League | Archived fixtures | Unique prop outcomes | Normalized contracts | Pending review |
| --- | ---: | ---: | ---: | ---: |
| NBA | 21 | 0 | 0 | 0 |
| WNBA | 4 | 1,385 | 1,284 | 101 |
| NHL | 22 | 3,488 | 0 | 3,488 |
| NFL | 31 | 130 | 0 | 130 |
| NCAAF | 14 | 0 | 0 | 0 |

NCAAM/NCAAW have no fixtures in this selected archive. No missing offers are invented. NBA/NCAA schema-extension tests are explicitly synthetic mutations, not proof of observed prop availability.

WNBA standard props preserve actual `sr:player:*` specifiers, market/outcome IDs, exact title, signed line, Over/Under or integer N+ selection, raw source hash, observed tabs and explicit overtime scope. N+ ladders resolve a player only from a unique exact name and explicit player ID within that same snapshot. Opaque variant strings do not become guessed IDs. The 101 WNBA pending outcomes have invalid decimal prices.

The Swish rows observed for NFL/NHL have player display names but no player IDs. Their game IDs differ from the enclosing Stake fixture ID, availability is unspecified and their bare metric names do not establish overtime/shootout rules. Numeric prices at or below one are not executable decimal odds. All these conditions remain explicit. Real NFL duplicate IDs with different team labels are deduplicated by semantic contract identity; every raw variant and the conflict remain attached. Related duplicates are not extra opportunities.

Seven fixtures in `tests/fixtures/player_props/` retain complete original gzip endpoint bodies and a manifest linking each to URL, fetch time, fixture, league, archived path and decompressed SHA-256. The NBA and NCAA fixtures demonstrate actual absence of props in those responses. Tests retain the difference between original data and synthetic adversarial cases.

## Contract API

```python
from betting_engine.player_prop_contracts import normalize_player_props

report = normalize_player_props(original_endpoint_bytes, evidence, league="WNBA")
```

`evidence` requires `sha256`, `url`, `fetched_at`, `league`, `fixture_id`, and `fixture_slug`. It binds the already verified tournament identity to the exact original decompressed `/odds/<slug>` or `/fixtures/<slug>` bytes. The adapter verifies source hash, official endpoint host, slug and fixture identity. League classification remains the responsibility of the tournament source adapter, rather than being inferred from similar team names.

The report retains each contract, normalization reasons, malformed-record issues and source provenance. `contract_id` hashes exact event/player/metric/scope/line/operator/provider-market semantics; repricing does not alter it. Missing or conflicting IDs, unknown metrics or settlement scope and invalid prices cannot produce normalized contracts. Active availability requires explicit standard-feed status and finite decimal odds above one. Feed freshness is enforced later by the production quote guard; archived normalization never establishes present availability.

Every output remains `model_approved=False`, `publishable=False`, `settlement_rules_verified=False`. Current metric recognition covers observed WNBA scoring/rebounding/assisting/three-point fields, observed NFL receiving fields, and observed NHL skater/goalie fields, plus explicitly tested schema extensions. This is semantic parsing, not trained model coverage.

### Explicit identity resolution

```python
from betting_engine.player_prop_contracts import resolve_player_identity

resolved = resolve_player_identity(contract, resolution,
                                   evidence_bodies={sha256: original_bytes})
```

The resolution binds the original `contract_id` and `feed_source_sha256`. `player_source` must identify hash-verified official league JSON bytes, with exact `player_id_pointer` and `player_name_pointer`. `mapping_source` plus `mapping_pointer` must point to a separately verified explicit crosswalk containing:

- `league`, `fixture_id`, `provider_game_id`, `provider_player_name`;
- `official_player_id` as a string and `official_player_name` matching the official record;
- `provider_team_name` when the raw prop contains a team name;
- `provider_player_id` when the contract already has an explicit provider ID.

Source records require `url`, `sha256`, and `fetched_at`. Official league or Stake source hosts and actual supplied bytes are checked. The function verifies these supplied facts; it does not discover a crosswalk, certify arbitrary caller-provided files as official, fuzzy-match names or assume provider IDs are interchangeable. The current crosswalk tests are explicitly synthetic interchange records. A production source adapter must establish and archive real mappings. Merely putting a real website URL beside invented JSON is not valid integration evidence.

Identity resolution clears only missing-player/event-mapping reasons. It does not clear unknown scope, Swish availability, conflicting market IDs, or approve a model. Apply it before issuing a ticket. It returns a new contract identity, retains the original identity and resolution hash, and never rewrites an issued ticket.

## Pure settlement API

```python
from betting_engine.player_prop_settlement import settle_player_prop

assessment = settle_player_prop(ticket, observation, rules,
    evidence_bodies={sha256: original_bytes}, as_of=timestamp,
    previous=previous_assessment_or_none)
```

The accepted ticket has `id`, `contract`, `stake`, `odds`, and `placed_at`. Payout uses the accepted decimal stake and odds, not a subsequent feed price. Historical accepted amounts are preserved; the existing publication guard owns the current $1–$5 instruction range.

Observation identity is exact across league, fixture, player, metric, scope, overtime and shootout. It requires an official player record, source bytes/hash/time, `available_at`, positive integer `revision`, final event and scope status, participation evidence and the actual integer stat. Team scores cannot substitute for player stats. Missing participation, missing stats and unverified scope remain pending. Zero is a value only when explicitly observed; it is never a default. Negative yards and NHL plus/minus retain their signed meaning.

The separately reviewed rules record binds league/metric/scope/overtime/shootout to archived Stake sportsbook-policy bytes. It must specify participation requirement (`played` or `started`), nonparticipation treatment, integer equality, correction policy and any cancellation/abandonment rule. No DNP or special void rule is guessed. The normalized observation and rule records must come from trusted source adapters: this pure kernel checks the supplied bytes and record identities, but does not parse every official API or derive the truth of arbitrary supplied stat values.

Supported payout semantics are Over/Under with explicit integer push, inclusive N+, and binary occurrence for the defined touchdown/shutout/goalie-win metrics. A player scoring multiple touchdowns satisfies an anytime Yes. Verified DNP void and final sourced cancellation void refund the accepted stake only when their explicit rule applies. Otherwise the assessment stays pending.

Every result hashes the unchanged ticket, observation, rules and full assessment. A correction requires a strictly increasing stat revision, explicit superseded-observation hash, nonbackdated availability, unchanged settled rules and an `apply_official` correction policy. It returns a new linked assessment; the original ticket and previous assessment remain unchanged. Repeating the same valid observation is idempotent. The integration owner must append these assessments to an audited ledger and apply accounting deltas; this module makes no ledger changes itself.

## Rules and knowledge used

The design follows the local K03 provenance/missing-data card, K05 basketball exposure card, K06 NFL scope card, K07 NHL goalie/shootout card, K08 chronological validation card and `docs/STAKE_MARKETS.md`. These documents guide implementation; they do not supply player probabilities or settle an actual wager.

Primary source references: [Stake Sports Data API documentation](https://docs-odds-data.stake.com/) and [Stake sportsbook rules](https://stake.com/policies/sportsbook). The rules distinguish basketball participation, overtime inclusion and separately named football periods; NHL regulation and shootout treatment require explicit scope. The implementation therefore requires a reviewed rule record for the exact contract rather than treating a general rule excerpt as a complete DNP or correction policy. No verified production rule registry was fabricated from documentation snippets.

## Validation and remaining integration

The two dedicated test files cover actual raw identity/line/source contracts and synthetic adversarial semantics: changed source hashes, cross-league/event/player mapping, missing and duplicate IDs, exact raw variants, signed values, inactive/nonfinite/overflow prices, pushes, DNP, starter requirements, cancellation, timestamps, corrections, immutability and idempotency. Full regression and DOM results are reported with the integration handoff.

To enable production prop recommendations, wire verified current player/event identity and participation sources; archive and review the applicable settlement-rule catalogue; train/calibrate/validate separate player metric and scope models with point-in-time exposures; implement prospective settlement adapters and immutable assessment accounting; and pass the unchanged publication evidence guard on fresh exact Stake offers. No production engine, storage, governance or probability gate was changed in this candidate. D04 contract-normalization and D05 settlement-kernel code is delivered; complete live player-prop modelling, rules verification and deployment remain distinct unfinished capabilities.
