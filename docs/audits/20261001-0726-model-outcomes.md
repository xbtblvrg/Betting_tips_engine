# Immutable model and original-outcome audit

Read snapshot: 2026-10-01T07:26:18.182847+00:00. No live database, code, settings or model writes were performed. All eight loaded model artifacts exactly match their report and the 07:26:17 UTC production publication. No newly validated improvement remains waiting for activation.

## Delivery and actual money

Today’s Budapest calendar-day count is 15 funded first issuances across 10 games, $21.65 original stakes; 5 below target20. Last new issue was04:01:22UTC. Preserve checkpoint and originals; existing/repriced offers and duplicates do not count again.

| League | Settled originals | Games | W/L/P | Stakes | Return | Net P&L |
|---|---:|---:|---|---:|---:|---:|
|MLB|55|8|21/33/1|$68.45|$49.6156|$-18.8344|
|NFL|5|1|5/0/0|$5.00|$11.0800|$6.0800|
|NHL|40|8|13/25/2|$42.88|$29.1577|$-13.7223|
|WNBA|24|2|11/13/0|$25.80|$22.4036|$-3.3964|

Total:124 settled original decisions across19games;50W/71L/3P, $142.13stakes, $112.2569return, −$29.8731net (−21.02%). Ticket money has one extra legitimate winning ticket:125settled, $143.13stake, $113.8969return, −$29.2331. Account execution is not verified; operating costs unknown. Settled match starts span2026-09-29T00:15UTC through2026-10-01T02:00UTC. Original issue times begin2026-09-28.

The loss concentration is material: MLB:849846 alone −$13.2188 from11decisions/$18.80; WNBA:401918018 alone −$11.21 from14decisions/$15.27. These are19game clusters, not124 independent trials.113 of124settled originals have legacy unspecified funding tier; do not attribute the entire history to today’s funding policy.

## Exact as-issued probability evidence

All121 decisive original contracts: conditional Brier0.23813536, logloss0.67156122, mean frozen p/(1−push)57.70%, selected-outcome hit rate41.32%. Threshold classification accuracy is65.29% (whether the selected event wins at p≥0.5), a different quantity from betting hit rate. Game-equal-weight Brier0.21704484. Pushes and open contracts are excluded from these probability scores.

A complete original contemporaneous market is hash-verified for62 decisive contracts across14games. Same-source quote time, pregame freshness, exact IDs/line/scope, original odds and full mutually exclusive outcome partition pass. Proportional inverse-odds normalization gives paired market Brier0.19360388 versus model0.23436804; game-equal-weight scores0.17465397 versus0.20061507. This is a descriptive price benchmark, not true probabilities, independence, a fitted model or proof of future superiority.57 decisive originals lack original API archive references;2Double Chance contracts are excluded because outcomes overlap. No later quote fills those gaps. Three-way scores are selected-event binary Brier, not multiclass Brier.

| League | Decisive/Games | Original Brier | Paired prices/Games | Paired model Brier | Paired market Brier |
|---|---:|---:|---:|---:|---:|
|MLB|54/8|0.235476|42/8|0.23759531114653434|0.19026153485375974|
|NFL|5/1|0.121140|0/0|missing|missing|
|NHL|38/8|0.217663|11/4|0.18657695029173713|0.16833676661441022|
|WNBA|24/2|0.300908|9/2|0.27771875536204443|0.24008354617194458|

## Overnight search and active models

14completed job receipts cover8leagues plus6later MLB/NHLsource generations. NBA’s01:07receipt reads an already completed study from2026-09-30T22:46; its identity candidate was root-promoted earlier. It is not a second improvement. The previous NBA champion Brier0.19895087 fell to0.19591417 on720games/22weeks; paired delta CI[−0.00524812,−0.00055336], score checks pass. Historical winner Brier alone is not score-family, live-profit or source-availability proof.

| League | Selected overnight recipe | Final common games | Delta95%CI | Disposition |
|---|---|---:|---|---|
|MLB|elo_anchor|1588|[-0.0010797271478235092, 0.001455128314300859]|Retain champion; CI not strictly below0|
|NBA|identity_calibration|720|[-0.005248116014049446, -0.0005533634144482129]|Previously promoted; cached receipt|
|NCAAF|richer_interactions|719|[-0.00044371783028732055, 0.0015913021125900976]|Retain champion; CI not strictly below0|
|NCAAM|identity_calibration|3325|[0.0005412123591148533, 0.004003649814383751]|Retain champion; CI not strictly below0|
|NCAAW|identity_calibration|3052|[-0.0010130737020089806, 0.0004479006309219976]|Retain champion; CI not strictly below0|
|NFL|flexible_calibration|225|[-0.004286259556402898, 0.002782633034469248]|Retain champion; CI not strictly below0|
|NHL|identity_calibration|745|[-0.005825840926258834, 0.005802841457622394]|Retain champion; CI not strictly below0|
|WNBA|elo_anchor|191|[-0.003438089132753587, 0.0014608056140520006]|Retain champion; CI not strictly below0|

Every listed score-MAE regression guard passes. NCAAF/NCAAM/NCAAWcandidate family gates pass only their reported family lists; that does not override failed paired promotion. NCAAM’sCI is entirely positive (worse). Newer MLB/NHLjobs preserve rejected evidence; no apparent lower point estimate is sufficient. Overlapping historical tests across repeat source generations are not independent replications.

- MLB: `MLB-sport-v4-baseline-b2243438ca56-5277ee67`; artifact SHA256 `c35557d4cdb9e60a7597004cb6342271168c92c33ab123987b27930cd514e929`.
- NBA: `NBA-sport-v4-identity_calibration-bdb1183358d6-a726f37a`; artifact SHA256 `f5d4005584b61ad4f43801c2ea06e7e0dcf487a6425cccc0591e748b19317fba`.
- NCAAF: `NCAAF-sport-v4-baseline-5f8e9f64b0ec-eeb110c7`; artifact SHA256 `cdd82282139dbd73133da72f7f66bb72caa7a1433fad3f7fc59f5887d7210b75`.
- NCAAM: `NCAAM-sport-v4-baseline-1eb0e05b8d49-eeb110c7`; artifact SHA256 `e1a9784d18443f78fa74e5f63cc7cd7e36c11601290f75f71b33d4ab648ba5f2`.
- NCAAW: `NCAAW-sport-v4-baseline-f1bb3984c4e0-eeb110c7`; artifact SHA256 `c41601cb09136db1d5281b3745d460051f4513d4338bbc8bf51c40d8287a75ab`.
- NFL: `NFL-sport-v3-bc70d592b08c-5dd511a5`; artifact SHA256 `dd029b455e015af3df064be3b5de9b61017162a5adaa7633af351e991de8407e`.
- NHL: `NHL-sport-v3-1d853d04374c-5dd511a5`; artifact SHA256 `e8ea5c31d43799a73f3ff77a4472270243ad0ac5c7f1ca755c06cfde8e29f98a`.
- WNBA: `WNBA-sport-v4-elo_anchor-51dc242bb08b-f1968a45`; artifact SHA256 `4bf62cea743e2e4be2973e92410d5a98084a9c7de16035f8dece06d7a63052f0`.

NFL remains the clear same-test independent benchmark gap: modelBrier0.22761351 versusElo0.22292496. Other champion winner Briers beat their recorded Elo point estimates; no extra inferential claim follows. Professional score-family diagnostics use synthetic model-centered half-point thresholds, not real historical sportsbook lines. NBA’s score diagnostics around0.25 do not establish live Total/Handicap quality.

## Bounded next work and ownership

No narrow new production defect was established from this audit. The preregistered hypotheses below first require exact reproduction/source diagnosis; none is authorized for automatic activation or result-selected tuning. All have existing owners; no duplicate task is needed. Original deadlines are retained.

- **MLB full-game Total** — gap`outcome:9a3dce1ac6f0247dfa82c6b1`, task`D18` (retry); original due`2026-10-01T22:12:37.412670+00:00`. Audit original starter readiness and score residual shape at exact issued Under lines. Houston totals at00:27/00:33UTC September30 lacked later input_readiness; absence of that field is not proof starters were wrong. Reproduce original source/mean/probability before a correction. 10 decisions/5games,2W8L,−$6.2694; paired8/4games Brier model0.28195165 market0.20762782.
- **MLB first-five Total** — gap`outcome:2a209c3c3777b52d2ce8f5e1`, task`E514CB13F60` (candidate_ready); original due`2026-10-02T00:33:25.104706+00:00`. Reproduce official inning targets and archived starter identities; inspect frozen count-rate/dispersion separately from full-game recipes. Test a target/source/calculation defect only if reproduced. 5decisions/3games,2W3L,−$5.72; paired4/3games Brier model0.42352263 market0.18728508.
- **NHL regulation Three Way** — gap`outcome:49498db84bcfde2dce0ac029`, task`E514CB13F60` (candidate_ready); original due`2026-10-01T22:12:37.412670+00:00`. Reproduce exact side, home-away-draw probability mass and official60-minute targets. Separate website availability conflicts from calibration; retain shootout handling. Four losses do not establish a draw-model defect. 4decisions/4games,0W4L,−$4.63; mean selected p0.39236130; no complete original paired-price benchmark.
- **WNBA home Total** — gap`outcome:41f430d6ec912cfe154ef90c`, task`E5E9AD751B5` (retry); original due`2026-10-01T22:12:37.412670+00:00`. Audit original home-away score/residual asymmetry and input availability, preserving correlated lines on two games. Do not fit a home penalty to these losses. 5decisions/2games,0W5L,−$5.08; mean p0.64173529; paired3/2games Brier model0.38196048 market0.18297378.
- **NFL Winner benchmark gap** — gap`model_quality:NFL:full_game:moneyline`, task`D18` (retry); original due`2026-10-01T12:16:11.221047+00:00`. Reconcile authentic v3 fit inputs/fingerprint. Keep rejected flexible_calibration candidate archived; its lower point estimate failed paired gate. Do not fit to one winning live game. Retrospective modelBrier0.22761351 worse than Elo0.22292496; candidate paired225games/19weeks CI[-0.00428626,+0.00278263].

`preregistered_work.json` provides exact acceptance: archive inputs/code/as_of and untouched final IDs before fitting; exclude this inspected cohort from new selection/testing; freeze paired prospective predictions with exact contract/quote/settlement hashes; count game clusters; report at original deadline even if sample remains incomplete; target first150distinctgames over12weeks for the planned prospective comparison; preserve existing promotion/family/score guards. A valid improvement handoff must bind exact live model artifact SHA, input/source/code/report hashes, root approval and later local/Tailscale publication. Candidate-ready utility or report cannot close betting improvement.

## Evidence and reproduction

`manifest.json` pins read-only SQL queries, raw records and evaluator sources. `statistics.json` is the production pure evaluator on the pinned snapshot; `price_benchmark.json` contains per-contract paired inputs, copied raw endpoint bodies and exclusions; `active_model_verification.json` checks actual artifact/report/publication identities. `preregistered_work.json` maps existing tasks and immutable deadlines. `complete-manifest.json` hashes every delivered file. Run `audit.py` only to create a NEW audit snapshot; it would overwrite this folder, so preserve this evidence directory before rerunning. `price_benchmark.py` deterministically evaluates the pinned raw snapshot and copied sources; no model fitting is performed.
