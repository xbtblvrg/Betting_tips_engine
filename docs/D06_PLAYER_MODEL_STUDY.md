# D06 — Independent player model experiment

This candidate implements an offline, shadow-only study in
`src/betting_engine/player_model_study.py`. It fits and compares independent
sports-statistic distributions on the supplied identified player observations.
It has no runtime, wagering, publication, model-promotion or settlement integration.
Every validation gate rejects production use and unauthenticated historical
availability, including when descriptive test scores can be calculated.

## Fixed experimental design

The six deliberately bounded targets use the actual archive schema:

| League/group | Target field | Candidate distributions |
| --- | --- | --- |
| NBA/player | `points` | Pooled Poisson; player/team Poisson; player/team negative binomial |
| WNBA/player | `points` | Same count baselines |
| NHL/player | `shots_on_goal` | Same count baselines; overtime included, shootout excluded |
| MLB/hitting | `hits` | Same count baselines; extra innings included |
| MLB/pitching | `strikeOuts` | Same count baselines; separate from hitting |
| NFL/player | `rushing_yards` | Pooled Gaussian; player/team Gaussian; player/team Laplace |

Signed NFL yardage uses a continuous approximation, not a nonnegative count model.
Basketball scoring is compound; Poisson is a deliberately simple benchmark, not
an assertion of its data-generating mechanism. No selected statistic is converted
into a current market probability. Other player metrics and NCAA remain outside
this bounded experiment.

Each exact player/team estimate shrinks its training mean toward the same
league/group/metric pooled mean with five pseudo-observations. New players or
new team assignments use that pooled training mean. There is no name join,
cross-league borrowing, inferred current participation, postgame exposure feature,
market price, market line or odds-derived rating. Explicit DNP rows are excluded;
unknown historical participation with an observed statistic is retained. Thus
scores describe recorded-statistic observations, not a verified current-player
appearance population. Missing targets remain missing, never zero-filled.

Negative-binomial NB2 dispersion is estimated from training residuals using
`max(0, sum((y-mu)^2-y)/sum(mu^2))`. Near-zero dispersion uses Poisson. Continuous
scales use training residual RMS (Gaussian) or mean absolute residual (Laplace),
with a one-yard numerical floor. Count means have a `1e-9` numerical floor.
All constants, package versions, source hashes and cutoffs are archived in the
plan; none change production thresholds or stake limits.

The caller supplies three aware timestamps. Scheduled starts assign entire events
to train, later selection, and later test intervals; equality belongs to the later
interval. Conflicting event times and duplicate player/game identities are
excluded, including duplicates with conflicting teams. Selection models fit only
train. The lowest **game-balanced negative log likelihood** selects one recipe,
with deterministic declared-order ties. A selection lock is written before test
scoring. That recipe and the predeclared pooled benchmark refit train plus
selection, then score the test once without updating parameters on test outcomes.
Only those two test models are evaluated; test performance cannot choose a recipe.
Player rows from one game are not independent performance observations. Reports
include distinct games, row/game-balanced NLL, MAE, RMSE and central 80% interval
coverage, without significance, profit or calibrated-market claims.

A split with fewer than 100 rows or 10 distinct games rejects fitting as
insufficient experimental evidence. These minimums only prevent obviously empty
or concentrated experiments; they are **not** production approval thresholds.
Nonfinite fits/scores also reject. Adequate descriptive samples still fail the
historical-validation gate: the evaluator does not authenticate source times,
completion evidence or point-in-time availability. It records missing publication
times, absent availability evidence, and observations or identity evidence first
seen after the historical fitting cutoffs. An embargo or a guessed game duration
cannot repair those missing facts.

## Reproduction and retained evidence

Run from this isolated workspace with a **new** output directory:

```sh
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m betting_engine.player_model_study \
  data/players/verified_history/runs/20260929T192751138800Z-d0ccf522fb5d \
  docs/d06_player_model_study_evidence \
  --selection-start 2025-07-01T00:00:00Z \
  --test-start 2026-03-01T00:00:00Z \
  --as-of 2026-09-29T20:00:00Z
```

An existing output directory is refused. The utility verifies archive file hashes,
byte counts and observation count, then archives exact Parquet inputs, manifest,
provided source/identity/quarantine evidence, its own source, its history-helper
source, and the three knowledge cards before fitting. `plan.json` and
`row-audit.jsonl.gz` precede every fit. The latter contains one indexed disposition
for **every input row**, with source timestamps and exclusion reasons. Training,
selection and test membership can be recovered against the unchanged Parquet row
indices. Per-recipe fitted parameters, selection locks and test predictions are
retained. `artifacts.json` hashes all artifacts except itself. Hashes bind bytes;
they do not independently authenticate the collector or historical time.

The archive manifest reports 550,729 observations; all were inspected, not a
synthetic replacement or a first-row schema sample. Earlier source exclusions
remain separate: the supplied manifest reports NBA 672 and NFL 47 rejected rows,
and 42 duplicate identities in its quarantine file. Their absent original rows
cannot be reconstructed by D06. Original exclusions/evidence are retained;
this study's per-row accounting concerns the supplied 550,729 rows.

These inputs were collected in September 2026. Their manifest explicitly declares
`historical_point_in_time_backtest_ready=false` and
`archive_timestamps_independently_authenticated=false`. No publication time or
historical participation has been invented. This is a retrospective comparison
of corrected observations ordered by game starts, **not** a historical executable
backtest. Repeated inspection or tuning against these test results would require
a new holdout; a different output directory does not create independent evidence.

The local source cards [K02](../knowledge/cards/K02-fuggetlen-modell.md),
[K03](../knowledge/cards/K03-adat-ido-forras.md), and
[K08](../knowledge/cards/K08-validacio.md) were read and byte-archived. They support
independent inputs, count/continuous distribution caveats, distinct source clocks,
and chronological validation. They are methodological source material, not
operational instructions or measured player evidence.

## Scope and developer audit

D06 implements the bounded experiment; it does not close M03 or the all-market
capability. Existing source adapters and identity/contract/settlement experiments
remain separate acceptance claims. Historical player logs cannot authorize current
participation or exact Stake settlement. No protected file, existing test or project
contract was modified. Production services, ledger, accounts, credentials and the
release policy were not accessed. There was no network use, helper delegation or
deployment.

The contract's independent-forecast, chronological evidence, archived-input,
exclusion-accounting and shadow-only requirements are addressed here. The broader
requirements remain unchanged: $1–$5 stakes, no aggregate daily cap, immutable
funded decisions, manual wagering, exact guarded offers, and a daily minimum of
15 funded instructions. This experiment issues zero instructions and establishes
no profit, hit rate, operating-cost result, current feed coverage or production
capability. It preserves full-game production priorities by adding no runtime hook.

Next steps: independently review and run the controller's full fixed suites;
collect authenticated prospective result-version and completion evidence; collect
current participation/role observations; validate family-specific probability
calibration on a fresh holdout; integrate exact contracts and settlement under a
separately reviewed change. Live source checks and separately verified release
remain controller-owned. Frontend DOM and local/Tailscale publication evidence
were not run: this isolated experiment changes neither UI nor market publication,
and production access is prohibited in this task.

## Candidate test evidence

The new `tests/test_d06_player_model_study.py` has **35 passing cases**. Tests
exercise all six real schema targets using explicitly synthetic observations:
held-out outcome invariance; odds/exposure invariance; signed yardage and zero
counts; likelihood arithmetic; game-balanced scoring; new-team priors; fixed
boundary timestamps; duplicate identities and conflicting event starts; individual
exclusion accounting; missing/late timestamps; future corrections; insufficient
independent events; empty/NCAA input; corrupt archive evidence; nonfinite scores;
source/input archival before every fit; selection lock before test evaluation;
artifact hashes; and refusal to overwrite a prior run.

Validation commands and observed results:

```sh
OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_d06_player_model_study.py tests/test_player_history.py \
  tests/test_player_archive.py tests/test_player_archive_sources.py \
  tests/test_player_prop_contracts.py tests/test_player_prop_settlement.py
# 224 passed in 145.54s (initial 29 D06 cases + 195 existing cases)

OMP_NUM_THREADS=1 PYTHONPATH=src /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_d06_player_model_study.py
# 35 passed in 12.83s (final D06 suite, including six additional cases)
```

That is 230 distinct passing cases across the two runs, not a claim that the
controller's complete suite or independent review has already run. Existing test
files were not modified.

## Measured supplied-data result

The [complete report](d06_player_model_study_evidence/report.json),
[fixed plan](d06_player_model_study_evidence/plan.json),
[artifact hash index](d06_player_model_study_evidence/artifacts.json), and
[per-row audit](d06_player_model_study_evidence/row-audit.jsonl.gz) are retained.
All 61 indexed artifact hashes were recomputed successfully; the archived study
source matches the candidate source byte-for-byte. The audit was read back and
verified to contain consecutive original indices for all **550,729** rows.

Row accounting: **291,624 train + 111,067 selection + 108,214 test + 39,824
excluded = 550,729**. Exclusions are 23,058 explicit DNP and 16,766 missing target
rows. There is no zero imputation. Every one of the 510,905 retained rows lacks
source publication time. Every one of the 402,691 train/selection observations
has supplied availability and identity-fetch timestamps after the respective
historical fit cutoff. Thus **all six historical-validation gates reject**.

| Target | Selected on earlier selection | Test rows / distinct games | Test game-balanced NLL: selected / pooled | Selected 80% interval coverage |
| --- | --- | --- | --- | --- |
| NBA points | Player/team negative binomial | 9,230 / 423 | 3.4110 / 5.5058 | 67.16% |
| WNBA points | Player/team negative binomial | 6,722 / 336 | 3.4555 / 5.1418 | 63.43% |
| NHL shots on goal | Player/team negative binomial | 16,416 / 456 | 1.5486 / 1.6662 | 93.58% |
| MLB hits | Player/team Poisson | 51,708 / 2,429 | 1.1464 / 1.1514 | 95.66% |
| MLB pitching strikeouts | Player/team negative binomial | 20,802 / 2,429 | 1.7942 / 2.1341 | 91.09% |
| NFL rushing yards | Player/team Laplace | 3,336 / 48 | 3.0069 / 4.0593 | 90.50% |

Lower NLL is better within a target; scores across different targets and between
count mass and continuous density are not interchangeable. The selected recipes
improve this retrospective likelihood comparison, but that does not establish
prospective performance or betting value. Basketball intervals substantially
under-cover their nominal 80%. Discrete low-count intervals can substantially
over-cover due to integer mass. NFL includes non-rushers' observed zeros and
unknown participation; MLB pitching mixes observed starters and relievers. Those
population/role issues require independent work before current-prop modeling.
MLB hitting/pitching game counts overlap and must not be added as independent
observations. The NFL outer test covers only 48 games. No confidence interval or
statistical-significance claim is made from these dependent player rows.

Report SHA-256:
`3e1f856f4197e9aae57069439b11ecafe88d1c5c2d0ff984cd76bf2aae252963`.
These are descriptive sports scores, not P&L, expected value, executable offers,
a completed betting capability or a production release.
