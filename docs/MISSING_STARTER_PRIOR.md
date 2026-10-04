# Explicit MLB missing-starter input policy

`missing_starter_policy=verified_model_prior_v1` permits an unavailable probable starter only when the current archived official MLB schedule also omits that starter, both team/event identities and scheduled start match, and the schedule remains fresh. It does not replace a published pitcher, suppress a failed source, or permit an unverified known pitcher's history.

The forecast must exactly reproduce the existing trained feature constructor's league prior: earned runs per nine 4.5, WHIP 1.35, strikeout fraction 20/90, walk fraction 7.2/90, home-run fraction 2.7/90, observed-batters feature 0, known flag 0. These are existing smoothing priors, not guessed pitcher statistics. No probabilities, confirmed identity, history or extra confidence are fabricated.

Readiness independently reads the exact deployed full-game or inning model artifact, checks its version and feature schema, and records its SHA-256. Frozen score-regressor scaler moments must demonstrate actual training rows with the binary known flag unset for the affected side. Each inning regressor must satisfy the condition. Missing artifacts, incompatible versions, malformed binary moments, and zero missing-input training rows fail closed. This verifies input representation support, not well-calibrated missing-starter predictions: small-sample support and unknown actual pitcher strength remain explicit uncertainty. Existing model quality, stress, funding, quote and exposure guards still apply.

The readiness receipt retains `known=false`, null pitcher identity, zero observations, actual model-artifact training counts, source hashes, warning and exact prior vector. It is sealed with the decision. Changing the policy invalidates current publication review. Omitted settings retain `required_v1`, including the old missing-starter rejection.

Read-only inspection of the deployed MLB artifact found 5 home/2 away missing training rows among 7302 score-regression rows. Both inning artifacts had 3 home/1 away missing rows. These are input-support counts, not evidence of predictive accuracy or profit.

Root must expose the Literal setting in the API and activate only after independent review. This implementation does not write a production model, database, or setting.
