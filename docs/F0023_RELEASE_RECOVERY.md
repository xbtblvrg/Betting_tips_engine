# F0023 candidate recovery: missing count-model reader evidence

The retained `release-20260930T102109727968-13a085cb/release-python.stdout.log`
reports **6 failed, 1529 passed**. All six failures are `FileNotFoundError` for
`docs/d06_player_model_study_evidence/artifacts.json` in the controller's stage.
The supplied workspace contains that index and all 61 indexed artifacts with
matching hashes. No rebase conflict file exists. The old four-file candidate
did not transport its real-data test dependencies. Changing numerical models,
weakening assertions or skipping these tests would not repair that omission.

This increment adds a passive [bundle utility](../src/betting_engine/player_prop_evidence_bundle.py)
and [exact source-byte bundle](F0023_COUNT_EVIDENCE_BUNDLE.json). The utility builds
a deterministic transport of the 13 files read by the existing count evaluator:
the unchanged artifact index, plan, report, and five selection locks and selected
test models. It verifies every member against an externally pinned index;
missing, corrupted, extra, duplicate and unsafe members fail validation. A
read-only stage audit enumerates exact missing/damaged paths before tests run.
It does not write, extract, install, train, authorize release or activate models.

The original index pin is
`02c1ac891e4f53a4f4273bd9c2d9ac97acbd3a39e81fb37cba3601bd6d10c907`.
The bundle is a **reader dependency subset**, not a complete training archive.
Other entries in the original index remain separate dependencies. No model
weights, study scores, original tests, source times or rejected gates changed.
NBA/WNBA points, NHL shots on goal, MLB hits and pitching strikeouts retain their
exact independent-source metric and scope. NFL continuous and NCAA absent
count models still reject. Hash integrity is not historical availability.

## Candidate integration and tests

`verify_count_evidence_bundle(body, expected_index_sha256=PIN)` returns verified
relative-path/byte pairs. A separately authorized controller can include those
exact pairs under `docs/d06_player_model_study_evidence/` in its isolated stage,
then call `audit_count_evidence_stage` before its unchanged test command. This
candidate does not alter the controller, protected policy or release receipts.
The supplied profile is `no_exact_profile` and discovery is disabled: inclusion
of these paths still requires a separately verified root integration/policy.

The recovery was exercised in a new temporary directory containing only the
13 bundled evidence files, byte-identical original F0023 tests, their two
existing helper test modules and existing prop fixtures. **All 74 original
F0023 tests passed**, including the six that failed in the retained release.
No access to the workspace's original D06 directory was needed by that replay.
The targeted evaluator/bundle/offer/yard suite passed **254 tests**. New
regressions live only in `tests/test_f0023_evidence_bundle.py`; they also test an
empty stage, each damaged member, wrong external pins, extra/traversal/duplicate
fields, symlinks, deterministic byte roundtrips and no synthesized missing data.
The full Python/API suite passed **2,806 tests plus 5 subtests**, with one existing
Starlette deprecation warning; all **54 DOM tests** passed. Results and hashes are recorded in the accompanying
[recovery evidence](F0023_RELEASE_RECOVERY_EVIDENCE.json).

Python commands use `OMP_NUM_THREADS=1 PYTHONPATH=src` and
`/Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q`.
DOM checks use the unchanged `npm test` command. The temporary stage run sets
`PYTHONPATH` to this candidate's absolute `src` directory; it tests unchanged
code against bundled evidence, not an installed production service.

## Evidence scope and developer audit

The supplied development manifest has no errors; all 264 file hashes/sizes and
329 referenced decompressed Stake response hashes verified. It declares a
bounded retrospective export, not exhaustive website coverage. The separate
public sports bundle is partial (12 sources, 24 payload/metadata files verified),
with NBA injury observations absent and older injury seasons missing as recorded
in its coverage. Those omissions do not cause this packaging failure.

The verified history contains 550,729 observations. Its actual schema includes
`identity_namespace`, `stat_group`, `stat_scope`, JSON-encoded `stats`,
`source_fetched_at`, `source_published_at`, `available_at` and participation
fields. The inspected MLB row has a null publication time; its recorded fetch
time is not backdated availability. K02, K03 and K08 informed independent-input,
time-provenance and per-game chronological-validation constraints; reading was
limited to those cards. No new fit, recipe selection or performance measurement
was performed. Existing rejected validation remains visible in the bundled
report. The immutable decision export was hash-checked, never rewritten or
treated as verified account execution.

All pre-existing source, test, documentation and config bytes are preserved.
Guards, publication, runtime, decisions, stake sizing, $1–$5 limits, no daily cap,
15/day requirement, English UI, manual wagering and immutable outcomes remain
unchanged. This utility issues zero recommendations and does not fulfill a daily
shortfall, claim profit or close any player-market activation gap. No credentials,
account state, external messaging, production service, live database or helper
agent was accessed. No live local/Tailscale publication was attempted.

Next steps belong to the controller: run its fixed suites and independent review,
authorize the exact dependency paths and stage them without modifying the old
tests, then verify any release separately. Before betting activation, obtain
authenticated availability, current event/player/team participation, independently
validated per-league models, exact Stake identity/contracts and verified
DNP/void/push settlement. The complete M03 milestone remains unfinished.
