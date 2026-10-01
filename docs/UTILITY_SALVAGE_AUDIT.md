# D07 / D09 independent salvage audit — 2026-09-29

The isolated utility-stage salvages new files from failed controller runs:

- D07: `20260929T183245392348-D07`
- D09: `20260929T184426200691-D09`

Every initial copied file matched that run's candidate-manifest SHA256. The
worker attempts failed a native macOS helper-boundary test. That failure was not
evidence that every new utility was broken, and the worker's task_complete claim
was not accepted as release approval. Existing root source/tests remain unchanged.
In particular, D07's modification of existing economics.py was not imported.

## Implemented repairs

D07 formerly could not import against the current live tree: clustered_returns
was a new function in the worker's modified economics.py. Shadow economics now
owns its estimator and uses a private, reversible league/game aggregation key
when invoking the existing issued report. This avoids cross-league provider-ID
collisions without rewriting original decisions or changing live accounting.

A game with some settled and some unresolved selections no longer contributes a
misleading apparently complete profit interval. Realized money and outstanding
stake remain visible; the interval is withheld with incomplete_event_results.
The same condition is visible in the separate issued-decision report. Unrelated
open games do not invalidate genuinely completed game clusters.

Bookmaker and sports-score outcomes are compared explicitly. A bookmaker void
does not overwrite original score-based decision ownership. Receipt import time
is explicitly distinct from unknown accepted placement time; the audit did not
introduce a false requirement that an after-game receipt import is an after-game
bet. Per-ticket fees, including fees larger than stake, remain counted.

D09 now rejects impossible OT after unequal regulation scores, basketball extra
OT after a non-tied preceding score, NHL events after a sudden-death goal, mixed
NHL OT rules, repeated five-minute OT, and playoff/multi-OT shootout awards. Known
wall-clock event times must agree with period/clock ordering. Exact identifiers
cannot contain leading/trailing whitespace. The output explicitly states that
its external completeness attestation is caller-supplied and that neither source
identity nor production support has been established.

## Actual read-only evidence

`data/completion-15/utility-probes/d07-live-evidence.json` records a SQLite
`mode=ro` transaction: 75 original decisions (5 won, 70 open) and 76 tickets
(6 won, 70 open). Every ticket lacks the supplemental actual payout, fee, currency,
availability, evidence reference and settlement time required by this utility.
No missing field was synthesized from odds, final scores or an assumed zero fee.
The result is invalid_records, zero authenticated economic ticket rows, and
profit_established=false. The existing original score-based decision report is
still a different, explicitly assumed execution measure. Actual execution export
and fee/payout provenance remain an unfinished integration requirement.

`data/completion-15/utility-probes/d09-native-source-audit.json` independently
verified 12 raw source body hashes: seven nflverse play-by-play seasons and five
official NHL per-game play-by-play responses. NFL 2026 has 8,311 rows for 48 games:
48 END GAME terminals, all final cumulative scores matching, no duplicate play ID
within a game. The NHL sample includes a shootout with native goal rows in period
type SO, demonstrating why a goal-type count alone is wrong. All five NHL bodies
have terminal game-end records. These are actual archived responses, not fixtures.

The raw sources cannot directly enter the normalized D09 interface: native
provider normalization, player/team identity binding, current corrected-version
selection, complete period/terminal validation and observed-completion timestamp
semantics remain necessary. No actual game-completion timestamp was manufactured
from a missing field. No sequence model, Stake settlement approval or profitable
coverage is claimed. Native data existence removes the blanket 'no event data'
assumption but does not close the implementation gap.

## Acceptance boundary

The saved original worker test files remain byte-identical. New regression files
cover the audit fixes, including a 40-game/40-date cohort with partial game
settlement, a bookmaker void, late receipt import, regulation/OT consistency and
NHL sudden death/shootout exceptions. Existing economics regressions pass against
unchanged economics.py. This is utility acceptance, not production deployment or
market-model approval. Parent/controller integration and full-suite checks remain
separate. No production source, service, wager or release receipt was changed.
