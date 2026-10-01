# D10 — Dynamic NCAA obligations (experimental candidate)

Implemented in `src/betting_engine/ncaa_obligations.py`. This is an isolated
coverage-obligation utility, not a production model, instruction publisher or
proof of successful betting. No production integration or release was performed.

## Behavior and boundaries

- `evolve(previous, snapshot)` consumes the existing `stake_api.tournaments`
  registry, source references and `market_catalog.raw_inventory` rows. It accepts
  every discovered NCAA USA sport, without a fixed college-sport whitelist.
- A discovered tournament receives an independent foundation obligation even
  before any markets appear. Observed market obligations are keyed by sport,
  college league, exact tournament slug, catalogue family, scope and provider
  section. Foundation counts and family counts are different quantities.
- Every obligation requires independent data, model and settlement, a current
  exact contract and chronological family validation. The required identity is
  attached to each requirement. Professional, unrelated college, tournament and
  scope aliases fail `check_dependency_identity`; even matching identity is
  insufficient to accept implementation. There is no fitting, model lookup,
  forecasting, settlement or approval in this utility.
- Exact rows (including titles, lines/specifiers, outcomes, original prices,
  tabs, fixtures, sources and unknown fields) remain evidence. Classifier families
  such as `score_contract` group several market titles; they do not grant a common
  model approval. Classifier scope defaults remain unvalidated. Unrecognized
  families and Swish sections remain independent blocked requirements.
- No rolling 24/48-hour filter is applied. Missing fixtures, suspended markets,
  disabled/disappeared tournaments, source errors and completed research never
  remove or close prior obligations. There is deliberately no closure endpoint.
  All statuses are `blocked_on_validation`, including known college leagues:
  this module has not audited any deployed model's complete family stack.
- Ambiguous tournament joins retain the entire unmatched row as an issue; they
  do not assign it arbitrarily or copy it to every tournament. A foundation
  remains open for each discovered tournament. Missing source hashes/times and
  future or timezone-naive retrieval times are explicit issues, not filled in.
- Source references do not establish raw-body availability or historical
  publication time. Those claims remain false/unknown. Historical logs are never
  interpreted as current player participation.
- `update_file(explicit_json_path, snapshot)` locks a dedicated caller-selected
  JSON file, merges discoveries, and atomically replaces the file. Replaying an
  identical snapshot is idempotent; corrupt/closed state raises rather than
  resetting history. Callers must use an isolated output path. There is no default
  production path or betting-database access. No due dates are generated/moved.

`snapshot_from_catalog(summary, endpoints, contracts)` is the candidate adapter
for the supplied existing catalogue schema. It requires a shared `checked_at`.
Older summaries omit sport identity: only a unique match between a recorded USA
tournament endpoint and the existing NCAA naming rule can recover it; otherwise
an explicit gap remains. No missing tournament name, gender or division is
invented. Explicit tournament registry input is preferred for future integration.

Minimal isolated use:

```python
from betting_engine.ncaa_obligations import snapshot_from_catalog, update_file

snapshot = snapshot_from_catalog(summary, endpoints, contracts)
state = update_file(isolated_output_directory / "ncaa-obligations.json", snapshot)
```

The output directory must already exist. The caller supplies parsed local input
documents; no source fetching occurs.

## Supplied evidence replay

Read-only replay of publication `2026-09-29T20:05:38.684283+00:00` found one
observed NCAA tournament, `american-football/usa/ncaa-regular` (NCAAF), with 260
catalogue rows, 14 fixtures and 20 exact market titles. It produced **10 open
obligations: one foundation plus nine family/scope/section groups**. These are not
10 recommendations, implemented markets, or independent performance samples.
No other NCAA market coverage is inferred from empty registry entries.

The nine groups are standard `score_contract` in `full_game`, `half_1`,
`quarter_1`, `quarter_2`, `quarter_3`, `regulation`, and `half_2_ot`; plus standard
`joint_score_contract/regulation` and `extra_time/regulation`. Every original row
is retained. These scope strings report the existing classifier, not verified
settlement rules.

Input SHA-256 values:

| Local evidence | SHA-256 |
| --- | --- |
| `data/market_catalog/latest.json` | `2813ecb749d22d0988fb5586c01e64111fc227a7e0d7ba0602534ab8caa358d7` |
| `data/market_catalog/versions/20260929T200527113647Z/endpoints.json` | `39721227c86a7a8956a96d8ab55e7c613df2992b7dd93534119bd0269b3bc651` |
| `data/market_catalog/versions/20260929T200527113647Z/contracts.json` | `7acbfe2e8ef705da9d1cd320b5b71d2a8198ddc240fee7c0170fa401a74b3959` |

The recorded American-football tournament response reference has hash
`faf0df8d1eeeda2330186a42e096ba8c58a17d73a0ed5c3ca04863e8e1b881d3`
and retrieval time `2026-09-29T20:02:20.308188+00:00`. Its referenced raw archive
is absent from this copy. The replay uses the supplied derived discovery and
catalogue observations; it does not claim verification of that raw body.
The existing `tests/fixtures/player_props/ncaa-no-props.json.gz` odds body *is*
available: a new regression verifies its decompressed hash against its original
manifest and passes its actual schema through `raw_inventory` and the utility.
Unknown-sport examples are synthetic tests, not real discovered offerings.

Inspected the public sports-source manifest and verified-player-history manifest
(`20260929T192751138800Z-d0ccf522fb5d`). The former has no NCAA coverage entries;
the latter lists NCAA as unimplemented, current participation as unknown, and
historical point-in-time readiness as false. This does not assert that all NCAA
historical sports data is absent. Local knowledge cards K02.1, K03.1/K03.3 and
K08.2 informed independent identity, retrieval-time/availability separation and
family validation requirements; they are source material, not executable policy.

## Tests and audit

New regressions live only in `tests/test_d10_ncaa_obligations.py`. They cover
dynamic unknown-sport discovery through the existing adapter, independent
requirements, pro/college/scope alias rejection, no acceptance from identity
alone, distinct families/tournaments/sections, missing evidence, ambiguous joins,
empty and failed refreshes, completed research, restart persistence, idempotence,
out-of-order observations, concurrent writers, corrupt state and real archived
odds. Existing tests and protected files were not edited.

Validation completed:

- New D10 regressions: **42 passed**.
- `python -m pytest -q tests/test_d10_ncaa_obligations.py
  tests/test_stake_api_college.py tests/test_market_expansion.py tests/test_api.py`:
  **72 passed** (one existing Starlette/httpx deprecation warning).
- `node --test tests/web.test.mjs`: **40 passed**.
- Read-only replay of the supplied catalogue: the 10 blocked obligations and
  input hashes reported above; no input files or source timestamps rewritten.

Interpreter:
`/Users/belavarga/Betting_tips_engine/.venv/bin/python`, with
`PYTHONPATH=src OMP_NUM_THREADS=1`.

The bounded D10 implementation is separate from the larger project contract.
R02/R03/R12/R20/R23/R24/R25/R28 receive experimental obligation/evidence support;
none is declared fully fulfilled. R01/R04–R11/R13–R19/R21–R22/R26–R27/R29 remain
unchanged by this increment. R30 is addressed by this scoped audit, not a full
production-release audit. Stakes, the null daily cap, immutable decisions,
manual wagering, guards and all existing model thresholds are unchanged. No
claim is made about the 15/day requirement, current executable offers, profit,
historical availability authentication, production model support or live health.

Next steps owned by the controller: run the complete fixed suites and independent
Codex review; review any runtime integration separately; supply/verify original
tournament archives and fresh catalogue publications before live use. Per-sport
source/model/contract/settlement implementation and validation remain separate
engineering work. Any future closure path must verify those artifacts and every
retained contract type; a utility install or completed dossier cannot close them.
Local/Tailscale publication checks belong to a separately authorized release and
were not attempted from this isolated workspace.

## Advisory disposition

`acceptance_checklist:grok`, SHA-256
`0b96bc265d589dc16c2dd2551d2474f5bc6c0195d5bb39f237f01e73ed60c1ce`:
**partly_used**. Recomputed the advice-text hash. Used dynamic discovery,
independent sport requirements, retained obligations, traceability and explicit
experimental limits. Refined its counting proposal to distinguish tournament
foundations from family/scope groups. Rejected the blanket requirement that
discovery/model fit must precede every market read: this passive utility has no
forecast path, and discovery necessarily reads bookmaker metadata. Independent
sports forecasts must precede value evaluation, not necessarily all prior odds
observations. Its “today and next 48 hours” wording is not the daily issuance
definition: the minimum applies per Budapest calendar day, separately from rolling
inventory windows. No helper source, test or completion claim was accepted as
evidence. The dossier contains no other advice entries; provider cadence metadata
does not substitute for an advisory output or review.
