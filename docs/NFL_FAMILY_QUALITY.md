# Professional full-game quality: bounded root integration

This change supplies a publication check and stable repair intake, not a trained
replacement, betting-profit claim or production release. The proposed narrow
gate refuses the known Winner comparator regression without reusing Elo as a
Handicap/Total model. The `blocks_publication=False` result never authorizes a
bet by itself. Existing guards remain mandatory.

## Current NFL finding

The deployed `NFL-sport-v3-bc70d592b08c-5dd511a5` report was trained on
2026-09-27. Its 239-game chronological Winner test has Brier 0.227613511585,
versus 0.222924958147 for Elo (lower is better). Log loss is 0.645314559939
versus 0.635896362720. This is an observed aggregate regression, not a claim
of statistical significance or proof of negative betting EV. The recorded
initial/manual promotion does not remove it.

The model's Winner blend was selected on earlier selection data (50% logistic,
50% Elo), then calibrated separately. The frozen NFL calibrator has coefficient
0.644131177464 and intercept 0.206931081816. These numbers do not prove the
cause of underperformance; replacing it after inspecting the final test would
be test-driven tuning. The same score distribution reconciles its win/loss
mass to Winner probabilities, so dependencies remain relevant, but Elo supplies
no independent score/Handicap/Total probability comparator.

The report's Handicap/Total Brier values (0.251963275354 and 0.248723623064)
are synthetic model-centered half-point diagnostics, not historical offered
lines. Home/away team-total diagnostics are absent. The helper returns these
as `synthetic_diagnostics_only` or `family_evidence_missing`, respectively;
it neither declares those families failed against Elo nor fully validated.

## Exact integration points for the trusted root

1. In `EvidenceGuard.check`, after the existing input readiness calculation,
   derive the parsed full-game market kind from the exact contract. Call
   `assess_professional_quality(model_report, market.kind)` and put its result
   into **`tip.input_readiness.model_quality`**, also used by the guard's
   input-readiness snapshot. Append the quality errors if `blocks_publication`.
   Compute the existing candidate seal only after this field is attached.
   Do not alter `SEALED_FIELDS` or old original-budget verification.
2. In current publication verification, call
   `verify_quality_receipt(receipt, league=..., model_version=...,
   scope=..., market=...)`. It returns `{passed, errors}`, not an exception.
   The **outer existing publication seal remains mandatory**; the receipt's
   plain SHA-256 is not independent authentication. Current execution review
   must use this same current verification. Old receipts without this field
   cannot claim current approval, while original issued records stay intact.
3. Receipt verification needs no disk/report read. It checks the compact
   guard-attested report summary, source-report digest, frozen-prediction
   rescore summary, identity and numerical verdict. It does not claim to rerun
   training or independently retrieve evidence on each HTTP request. No full
   test-prediction array is copied into every bet.
4. For each active report/family feed `quality_repair_task(receipt)` into
   trusted engineering intake. Stable identity is
   `model_quality:<league>:full_game:<market>`; Winner failures are priority 1,
   score-family evidence gaps priority 2, linked to existing D18. Preserve the
   existing deadline and original failure history; a quote refresh or newer
   candidate does not close the gap. Close only with changed hash-bound report
   and verified corresponding family evidence. Calculate once per unchanged
   report/family within a publication to avoid repeated report serialization.
5. Before deployment test current publication rejection of inferior Winner,
   old sealed-budget validation, same-version changed-report rejection, and
   current execution review. Independently verify actual fresh publication,
   supported score-family outputs, API latency and immutable ledger after root
   integration. This staged module does not change production.

## Legitimate path to restore Winner

The existing recipe search already includes `elo_anchor`, selected only using
history strictly before the champion final-test cutoff. It fits a calibrated
Elo component plus separate score regressors; it is not the raw reported Elo
benchmark and is not guaranteed to be better. A winning recipe may replace
the champion only through the existing paired weekly Brier/score-regression
gate and the new Winner regression check. The final test must never choose
the recipe, and already inspected final events must remain disclosed.

The latest completed NFL search (2026-09-30 00:07 UTC) selected
`flexible_calibration`, not Elo-anchor. On 225 common games across 19 weeks,
its delta-Brier 95% interval was [-0.004286259556, 0.002782633034]; promotion
was correctly rejected. The previous baseline retrain was also rejected.
Source changes may trigger a new legitimate study, not forced promotion.

Latest D18 replay reproduced all NFL Winner predictions but not the original
history fingerprint (`bc70d592b08c` versus `c96c47bce6b6`). The original NFL
report has no authenticated fit-input snapshot. This is unresolved exact
reproducibility, not proof that its current parameters are corrupt. Future
fits now archive inputs; an old missing snapshot cannot be invented. D18's
20260930T113122724847 run remains incomplete with source-evidence requirements.
Collect genuinely uninspected forward predictions/exact offers and compare
by independent game clusters before claiming reliable profitability.

## Owned staged files

- `src/betting_engine/professional_quality.py`
- `tests/test_professional_quality.py`
- `docs/NFL_FAMILY_QUALITY.md`

No live models, configuration, services, odds, stakes, or database are changed.
