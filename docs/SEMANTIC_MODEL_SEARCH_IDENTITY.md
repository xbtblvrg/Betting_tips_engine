# Semantic model-search work identity

The NHL studies `21743dc0c2f36394b734a99f` (2026-09-30T13:24Z) and `252cb45fef220f65b49cd901` (13:30Z) repeated the same six-recipe search. Both immutable training snapshots contain 5,644 games. Five recovered games used `+00:00` and integer scores in the first snapshot, then equivalent `Z` timestamps and floating-point integral scores in the second. All 759 held-out prediction probabilities and targets, recipe-selection metrics, and final aggregate metrics are identical. The raw work digest treated these representation changes as new evidence.

`experiments.training_digest` now creates `semantic-training-evidence-v2` work identities. It canonicalizes timezone-aware start times to UTC and finite integral scores to integers. Meaningful starts, scores, team identities, neutral-site/game-type fields, box features, pitcher observations, starter identities, final-state and resumed-game eligibility remain significant. Duplicate pitcher-row ordering remains ignored as before. Invalid/nonfinite values fail explicitly. Date-only/naive input strings retain their legacy representation.

The function is deterministic and independent of the wall clock. This patch does not change model targets, predictions, fitting, recipe selection, `as_of` partitions, result-availability rules, promotion gates, or issued bets. In particular, the existing `earlier_history` boundary remains `max(start+8 hours, next UTC midnight)`. Current final-state filtering and source-stability checks remain in their existing callers. Immutable raw archive bytes, fingerprints, training receipts, champion reports, and historical study results are never rewritten.

## Migration

The explicit identity-version change produces at most one new study per eligible league's current meaningful evidence generation. Existing completed legacy keys remain historical records. Pending older keys follow the existing supersede/replan path. The first new study must pass all normal training/archive/promotion checks. It is not automatically promoted and is not counted as an improvement. Subsequent representation-only refreshes resolve to the same completed study without refitting or changing its result bytes.

A broader availability-scheduling limitation is separate: this work identity does not encode wall-clock passage across a result-availability boundary. A fixed raw dataset can have a different eligible subset at a later `as_of`; the existing fitting and selection checks protect chronology, but this narrow repair does not add a new scheduler generation for such boundaries. Any follow-up must use explicit availability generations, preserve prior receipts, and prove it neither repeats unchanged eligible inputs nor reuses a study after its eligible input set changes.

## Evidence

- Read-only audit: `data/validation-unblock-20260930/model-research-audit.json`.
- Exact full input archives verified using `verify_inputs`: `2b2e74df78aa9e3097e37229d89cddda478ad82c217fb93249db60e8dcf78e02` and `4da3ffb5d6380f3bf8e380124a799430cecb87f26bb5150bdabae5269c8de542`.
- Both now resolve to work identity `2da7c2e71cb60d7f15c4b5e406c9ccc3d2666a22439726ce7002625296856d70`; original reports remained byte-identical. Replay artifact: `artifacts/actual-full-snapshot-replay.json` in the isolated stage.
- The small committed fixture contains only the five relevant raw-row projections and labels that scope. It does not pretend to replace the full archived input proof.
- Regression coverage includes real snapshot equivalence, meaningful time/score/stat changes, malformed inputs, unchanged chronology checks, planner deduplication, and byte-preserving reuse of a completed study. No production model was fitted or promoted during this repair.

Retrospective Brier improvements and promotion receipts are probability-model evidence, not proof of profitable live betting.

Finite-value policy: `_work_score` accepts only built-in integer/float, finite, nonnegative, integral values. Booleans, numeric strings, null, fractions, negatives, NaN and infinity are rejected rather than assigned a plausible work identity. This matches the existing sports Game score contract and does not round or impute scores. `allow_nan=false` also rejects nonfinite nested feature evidence. Root independently checked the current combined 85k+ game snapshot across all eight leagues without format fallout.
