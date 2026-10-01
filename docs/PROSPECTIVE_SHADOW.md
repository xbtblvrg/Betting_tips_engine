# Prospective exact-market evidence

`prospective_shadow.py` owns a separate append-only SQLite evidence journal. It never writes the wager ledger, changes probabilities, allocates funds, or sends orders. Missing closing prices, small samples and absent sustained CLV are **not funding vetoes**. Shadows never count toward the minimum 15 daily funded first issuances.

## Runtime integration

After a successful committed publication, call:

```python
from betting_engine.shadow_runtime import record_publication
status = record_publication(data_dir, payload, games=None, store=None)
```

The default capture time is actual UTC now. Do not replace it with an older publication timestamp. The optional explicit `as_of` is for controlled fixed-cutoff replay. The wrapper reads `evaluated_candidates`, `alternatives`, and `recommendations`, requires current `verify_publication`, and verifies the quote against exact archived bytes. Capture errors remain visible in `shadow_capture.json`; they must not change committed wagers. A `funded_candidate_snapshot` records a candidate's allocation, **not proof of original issuance**. The existing immutable system-decision ledger remains authoritative.

Feed bodies referenced by current `payload.api_audit.fixtures[].responses` are captured for journal fixtures within two hours of start, including zero-stake candidates. `ShadowEvidenceStore.capture_targets(as_of=...)` exposes these exact fixture IDs/slugs for a shared refresh scheduler if it does not already fetch those fixtures. No network fetch occurs inside this module.

Run the following in the learning worker after result/source updates, separately from the refresh thread:

```python
from betting_engine.shadow_runtime import evaluate_shadow
summary = evaluate_shadow(data_dir, games=games, as_of=actual_utc_now,
                          store=readable_existing_store)
```

`store` is optional and used only for `system_decisions()` reads to create the separate historical funded summary. The dashboard reads `shadow_summary.json`, not SQLite or full study rows. Handle missing/failed jobs explicitly; neither a scheduled job nor source refresh implies measurement completion.

## Evidence and counting

Each trial is bound to one model version/report and fixed metadata. The first semantic contract observation is immutable: exact game, provider IDs, scope, side/line, quote, probability/push probability, model version, input hash, publication seal and hash-verified raw feed body. Repricing or subsequent probability changes never rewrite it. Equivalent moneyline/handicap payoff aliases are deduplicated using the production allocation-family definition. Different model trials are separate experiments and must not be summed as independent games.

Original quote validation and closing studies use the same exact-contract/lifecycle rules. A suspension, missing exact selection, conflicting start or corrupt capture prevents a positive comparison; the evaluator does not substitute an earlier favorable quote. A closing quote is the latest eligible **observed** archived price within five minutes before the recorded start, not a claim to the bookmaker's final tick.

Pending games produce compact counts, not full repeated studies. Only closed games produce contract reports. An input digest includes immutable decisions, available captures, result evidence and whether each start boundary has passed; unchanged inputs return the original fixed-`as_of` report. Thus the summary's `checked_at` may be newer than a reused study's `as_of`. Results are accepted only for the same game, league and settlement scope with an observed final timestamp at or before the cutoff. Caller-supplied result provenance is retained. A hash reference is explicitly `hash_referenced_not_reverified`; absent raw proof remains `missing_raw_proof`. The journal does not claim to authenticate external sports results itself.

## Report meaning

`prospective_trials[].families[]` exposes league, scope, family, first/last observation and start, CLV, model Brier, complete closing-market Brier, accuracy deltas, and hypothetical one-unit returns. Complete exclusive two/three-outcome markets can supply proportional de-vigged closing probabilities. A lone priced outcome cannot. Pushes are excluded from paired probability scores, and model probability is conditioned on no push. Closing prices have later information than the frozen model snapshot; this is stated in the output.

Related contracts are averaged within each game, then games receive equal weight. Bootstrap intervals resample games and appear only with at least 20 games; this reporting threshold is not a publication gate. All reports remain `exploratory_prospective_diagnostic`, `has_sustained_evidence=false`, `profit_established=false`. Neither a positive mean nor a 20-game interval establishes long-run profitability. A negative mean/interval remains visible. The existing funded `legacy_funded` cohort is reported separately using original stakes/results and exact archived closing studies; it is not backfilled as a new prospective trial.

## Journal safety

Creation initializes and fsyncs a temporary SQLite database before an exclusive link publishes it. An initialization crash cannot leave the advertised path as a permanently empty database. WAL, bounded writer waits, explicit snapshots, immutable-table triggers and serialized creation protect concurrent refresh/learning access. Existing non-journal databases are rejected. Source paths are constrained to exact content-addressed Stake archive names and bodies are size/hash checked. No credentials or account data are required.

## Validation evidence

The isolated production read-only probe on publication 3105 captured 541 first contracts in 0.682s, detected 6 semantic duplicates and rejected 618 records with explicit reasons. Of these, 553 exposed an older closing-price specifier parser that rejected proper scoped keys; a separately reviewed shared parser correction is required. The remaining 65 were a real 30-minute NHL start discrepancy (Chicago at Utah); they must remain rejected. The exact artifact is `artifacts/live-probe-130513/probe.json`, and the start discrepancy is `artifacts/actual-start-mismatches.json`.

The same probe separately summarized 129 original funded decisions and 64 comparable historical closing prices. Observed historical return after original stakes was -$0.6394 on $60.04 settled stake, with 71 decisions still open. This is neither account-verified profit nor evidence of sustained performance. Real future closing/result evidence must accumulate after runtime integration.

Final shared-parser verification: after `observed-close-v6`, a fresh read-only publication 3115 probe captured 1094 first contracts and 6 semantic duplicates in 0.815s; only the 65 real start conflicts remained rejected. All 553 false specifier rejections disappeared. Exact artifact: `artifacts/live-probe-131448/probe.json`. There were no journal fixtures within the next two hours, so this live probe honestly captured 0 closing-window sources and has no new prospective closing/result scores yet. Synthetic archive regressions exercise that full later lifecycle; they do not substitute for future observed performance. The combined independent run passed 268 shadow, scoped-quote, closing, archived-evidence and identity tests.

The cache fingerprint pins the exact shadow evaluator, closing-price helper and settlement-contract code hashes, as well as the closing evaluator version. Parser/rule changes therefore trigger a new study while preserving the old one. A concurrent identical evaluator returns an already committed matching study under the serialized write transaction. Individual stale source references produce explicit errors without discarding other valid captures in the same batch.

Result-provenance integration additionally carries full-game `official_result` and scoped `final_summary_source` hashes/archives into summary fields only when their game, URL and observation time match the returned result evidence. Conflicting references are explicitly labeled, and even matching references remain `source_verified_by_shadow=false`. The entire original source evidence is deep-copied and preserved. Two scope-specific regression cases bring the new test count to20.
