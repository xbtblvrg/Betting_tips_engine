# E8B9BF9FBBA — D06 identity quarantine repair

The bounded implementation is complete in the existing offline study entry point,
`player_model_study.run_study`. This is candidate integration, not production
integration or closure of M03. Original deadline remains
**2026-10-01T20:31:59.228466+00:00**. Remaining capability acceptance is unmet;
neither this document nor a passed test moves the deadline.

## Evidence and scope

The supplied engineering record
`data/engineering-evidence/b384c9a55f38e063da43eb28db7b96f9dc85c1b508e5d20a7fdce05c640fdf2b.json`
records D06 as `candidate_ready`, with controller review, authenticated historical
availability, prospective validation, participation and production integration
outstanding. `config/development_plan.json` defines D06 as a **shadow-only
player-stat study**, not a production prop model. No rebase conflict file exists.

Before changes, all 35 existing D06 cases passed; all 61 files indexed in the
previous study's `artifacts.json` matched their hashes, and the archived study
source matched the workspace source. The prior candidate was already present;
no replacement candidate was copied over it. Its archived inputs, models,
selection locks, predictions and report remain unchanged.

Code inspection exposed a narrower defect: target/participation rejection
preceded identity indexing. A valid player target plus a DNP or missing-target
duplicate silently retained the valid target. An excluded row with a conflicting
event start also failed to quarantine eligible rows from that event. Restricting
requested metrics could hide a conflicting MLB hitting/pitching event clock.

## Implemented behavior

The existing preparation path now checks identity evidence before target
eligibility, across the six recognized league/group schemas. Conflicting starts
exclude eligible rows from the entire event. Duplicate player/game/metric keys
exclude eligible copies, including conflicting team assignments. Hitting and
pitching remain distinct targets; a duplicate player does not exclude unrelated
players when the event clock agrees. Equivalent timezone offsets agree.

The identity precheck retains the original namespace, scope, provenance-field
and as-of checks. Evidence dated after as-of, wrong namespaces, invalid hashes
or wrong scopes cannot erase earlier samples. This precheck validates supplied
fields; it does not authenticate source truth or historical availability.

Every row now has `identity_conflicts`; original DNP, missing-target and
outside-requested-metric reasons remain intact. Report
`identity_conflict_counts` counts affected rows per conflict type; overlapping
types must not be summed as distinct exclusions. The versioned `d06-shadow-v2`
plan records this preparation policy. Input/source archival and the complete row
audit still precede every fit; the selection lock still precedes test scoring.
No distribution, sufficiency threshold, recipe ranking or model gate changed.

## Actual source check; untouched validation

[Machine-readable evidence](E8B9BF9FBBA_D06_EVIDENCE.json) records the exact
snapshot pin, observation/source/card hashes, schema and comparison counts.
The supplied run is `20260929T192751138800Z-d0ccf522fb5d`; its manifest hash is
`37d168526d11216ed4b59d4614ba9aade02ef713e26efc4c0a5aeeeedbf34e51`.
The manifest and all four declared artifact sizes/hashes were verified locally.

The repaired `_prepare` processed **550,729 actual observations**, using the
previous plan's fixed selection/test/as-of boundaries. Every original row's
membership, exclusion reason, metric and timestamp limitations were compared
with the archived row audit: **zero differences**, zero identity conflicts in
this supplied snapshot. Counts remain 291,624 train, 111,067 selection,
108,214 test and 39,824 excluded (23,058 DNP; 16,766 missing targets).
The regression defect was reproduced with labeled synthetic conflicting
records; it was not observed in this already-quarantined source snapshot.

There were **zero new fits or holdout score evaluations** on real data. The prior
report hash remains
`3e1f856f4197e9aae57069439b11ecafe88d1c5c2d0ff984cd76bf2aae252963`.
Its retrospective scores remain descriptive, with all six historical-validation
gates rejecting. This repair supplies no new profit or model-performance result.

The supplied public sports bundle reports 12 copied sources, unverified current
participation and no historical point-in-time readiness. The identified player
snapshot likewise disclaims authenticated archive clocks. Sources are present;
the missing prerequisite is authenticated availability/participation, not the
existence of historical statistics. No publication timestamp was invented.
Local K02, K03 and K08 cards were read as methodological source material:
independent forecasts, identity/missingness, chronological validation and event
clustering. Their prose was not treated as instructions or sports observations.

## Tests

Only `tests/test_e8b9_d06_identity.py` was added. Its first 18 cases produced
**11 failures and 7 passes before the repair**. All 20 final cases pass, including
the two additional checks for unaffected players and equivalent time offsets.
The integration regression prevents fitting when duplicate DNP records remove
all otherwise sufficient samples and checks durable row/report accounting.

```sh
OMP_NUM_THREADS=1 PYTHONPATH=src BETTING_DATA_DIR=/private/tmp/e8b9-api-tests /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_e8b9_d06_identity.py tests/test_d06_player_model_study.py tests/test_player_history.py tests/test_player_archive.py tests/test_player_archive_sources.py tests/test_player_prop_contracts.py tests/test_player_prop_settlement.py tests/test_api.py tests/test_governance.py
# 285 passed in 43.17s (18 repair cases at collection time)
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_e8b9_d06_identity.py
# 20 passed in 3.29s, final repair suite; 287 distinct Python cases overall
node --test tests/web.test.mjs
# 40 passed
```

One existing Starlette/httpx deprecation warning remains. All pre-existing tests,
protected files, contract, thresholds and prior study artifacts are byte-identical
to their pre-change copies. The only changed pre-existing file is
`src/betting_engine/player_model_study.py`. Production, live ledgers, services,
credentials and account state were not accessed. No helper CLI/agent was launched.

## Advisory disposition and remaining acceptance

`acceptance_checklist:grok`, exact text SHA-256
`1fde5a6e1320b6b35162a4dd9ceb74b8db8983689060fe32068771a5211faf06`,
is **partly used**. Its immutable-evidence, chronological-validation, unchanged
deadline and no-production-claim checklist informed verification. Its statements
that deadline, timezone, D06 identity and distinctness rules are unavailable do
not apply to the supplied task/contract. Its 15-instruction wording conflates
Budapest daily issuance with rolling 48-hour opportunities; these remain separate.
It provides no independently verified tests or source facts. Claude and agy
attempts report `capacity_busy`, with no advice entries to assess.

| Requirements | Actual fulfillment in this increment |
| --- | --- |
| R01–R02, R11, R15 | Independent statistics, source/card provenance and chronological selection preserved; ambiguity rejection repaired. No new performance claim. |
| R03–R06, R12–R13, R16, R20, R29 | Coverage, daily 15 minimum, core Winner/Handicap/Total priority, $1–$5 and no daily cap unchanged. Zero new instructions. |
| R07–R10, R18–R19, R21–R24 | Immutable money records, manual execution, exact contracts, freshness, settlement, UI and model gates untouched. No current contract/settlement support added. |
| R14, R17, R25–R28 | Worker/deployment behavior unchanged; bounded source repair tested; controller review and release remain separate. |
| R30 | This audit records actual tests, source evidence and unfinished work; no live fulfillment asserted. |

Next: controller runs its full fixed suites and independent read-only review,
then separately decides integration/release and verifies fresh local/Tailscale
publication if shipping. Before production props, collect authenticated
prospective result/availability evidence and current player participation,
validate probabilities on a fresh chronological holdout, and separately integrate
exact provider/Stake contract identities and matching settlement rules. Historical
logs cannot satisfy those gates. This offline repair accepts no Stake contract,
changes no settlement identity, evaluates no immutable wager lifecycle and
establishes no daily-volume or profitable-performance fulfillment.
