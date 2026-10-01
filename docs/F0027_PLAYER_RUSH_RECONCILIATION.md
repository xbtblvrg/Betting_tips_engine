# F0027 — Reproducible NFL rushing target reconciliation

The bounded candidate adds offline play-level rushing reconstruction and comparison
with weekly player totals. It repairs the prior candidate's process-dependent
assessment checksum. It does not complete M03 or activate a player-prop model.
No rebase conflict manifest was present.

The accepted workspace already contains historical adapters, chronological model
studies, contract/identity parsing, participation checks, settlement arithmetic and
F0026 signed-yard payoffs. It lacks NFL play-level target reconciliation. The local
history has 41,697 NFL rows, all with unknown participation; weekly zero-yard rows
cannot identify active rushers. The copied native nflverse sources provide actual
play and weekly schemas for this increment. K03, K06 and K08 were read in full as
methodological source material, with their hashes retained in the evidence JSON.

## Candidate API

`reconcile_nfl_rushing(play_by_play, *, game_id, as_of, weekly=None)` accepts existing
`PlayerArchive` objects containing exact Parquet bytes, hashes, URLs, capture times
and references. It validates endpoint/season, native schema, GSIS identity,
game/team/week, flags, period order, unique play IDs, final markers and scores.
Play IDs identify records; they need not increase across native timeout records.
Regulation must expire; overtime can end early with a matching native clock.

Credited carries, kneels, scrambles and overtime contribute signed integer yards;
the result retains contributing play IDs. Two-point and no-play exclusions stay
visible. Contradictory no-play credits and lateral attribution require review.
Weekly comparison checks both carries and yards. Any mismatch clears all reconciled
values. Actual zero-net carries produce zero; absent rush evidence remains null,
even for zero weekly totals. Nothing infers DNP or current participation.

Results distinguish `historical_totals_reconciled`, `source_disagreement`,
`plays_reconstructed` without weekly evidence, and `rejected`. Both documents are
from nflverse; agreement is same-provider consistency, not independent ground truth
or proof of sequence completeness. The later source capture supplies availability;
future captures reject at the explicit cutoff. Publication time remains null and
collector timestamps are not independently authenticated.

Every result disables publication, execution, settlement, current participation
verification and production props. There is no network, file write, fitting,
pricing, stake allocation, ledger operation or production caller. The checksum is
a reproducibility identifier, not an EvidenceGuard seal.

## Review repair and tests

The previous implementation iterated `players.keys() - seen` when emitting missing
weekly-player reasons. Sorting those IDs makes both reason ordering and the
assessment hash stable across process restarts, without changing rejection semantics.
The new regression serializes archives once and sends identical bytes to separate
processes with `PYTHONHASHSEED=1,2,3,99`. It compares complete outputs, independently
recomputes hashes, and checks reconciled, missing-player, mixed-disagreement and
malformed-final-score cases. Before the fix, two cases failed with three distinct
outputs; after the fix, all four pass.

The prior candidate's 65 behavioral cases are carried into a new test file. Its
real-source replay now uses the actual later capture: the current 2026 play archive
was captured at `2026-09-30T06:54:17.706378+00:00`, later than the previous synthetic
06:00 cutoff. No source time was backdated. Synthetic tests retain their original
fixed cutoff; separate real-source probes reject each season one microsecond
before its play archive capture. All tests present at workspace entry are unchanged.

Validation used `/Users/belavarga/Betting_tips_engine/.venv/bin/python`,
`PYTHONPATH=src`, `OMP_NUM_THREADS=1`, `PYTHONDONTWRITEBYTECODE=1` and
`-m pytest -q -p no:cacheprovider`:

- F0027 reconciliation and reproducibility files: **69 passed** (15.94s).
- Existing F0023–F0026, archive sources, contracts, settlement, participation,
  D06 studies and API tests: **487 passed** (58.97s). API data used
  `/private/tmp/f0027-api-tests-current`. One existing Starlette/httpx warning remains.
- `node --test tests/web.test.mjs`: **40 passed** (8.53s).

## Source observations and candidate audit

[Evidence JSON](F0027_PLAYER_RUSH_EVIDENCE.json) records source/code/card hashes,
native schema counts, replay cutoffs, results and the current advisory disposition.
All 24 declared public-bundle files passed hash checks; manifest byte lengths equal
body plus metadata lengths. All four verified-history files passed hash/size checks;
the observation table has 550,729 rows. Native NFL bodies have 372 columns and
49,492/48,771/8,311 rows for 2024/2025/2026; weekly receipts and required fields were
also verified against the supplied bytes.

Nine deterministic probes select the lexically first game, first overtime game,
and first lateral/no-play-credit conflict where present, separately by season.
Six reconcile (47 observed rushers, 354 rows with unknown rushing participation);
three reject (two lateral cases, one no-play-credit conflict). This is sampled
historical consistency evidence, not full coverage, current participation, model
validation, sportsbook settlement or measured betting profit.

| Requirements audited | Actual disposition |
| --- | --- |
| R01–R04, R11, R15, R19, R21, R23–R24 | Source-bound target evaluator implemented; source clocks and missingness preserved. No model, exact-offer or settlement approval. |
| R05–R08, R13, R20, R29 | No instructions issued. Daily minimum and profit remain unfulfilled by this increment. Manual wagering, $1–$5 stakes, null daily cap, original decisions and core-market priority unchanged. |
| R09–R10, R12, R16–R18, R22 | UI/feed/coverage/supervisor unchanged; DOM tests pass. No live or Tailscale publication checked. |
| R14, R25–R28, R30 and later release instructions | Isolated utility/tests/docs only; original files and protected policy/code unchanged. No production access, credentials, account state, messaging, helper CLI, nested agent or network use. No fit or deadline change. Controller review and release remain pending. |

The current Grok checklist was used for scope, preserved-test and non-activation
checks; its text SHA-256 was verified. It supplied no source facts or test results.
Earlier dossiers' dispositions remain historical evidence and are not substituted
for review of this run's advice.

Next: controller full fixed suites and independent read-only review. Obtain
independent official evidence for lateral/no-play discrepancies and sequence
completeness; live-check adapters under separate authorization. Prospectively
archive participation/role and source availability, validate player forecasts with
separate chronological selection/test samples, bind exact Stake contracts and
verify DNP/void/push rules. A separately reviewed root change must integrate any
protected runtime/guard path and verify fresh local/Tailscale publication. Other
NFL metrics and remaining league/NCAA prop capabilities stay open.
