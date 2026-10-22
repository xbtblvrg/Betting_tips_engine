# D15 integration increment — E5BBC53B172

The existing D15 requirement evaluator is integrated and exercised in this
workspace. This increment repairs an outcome-integrity gap: duplicate rows for
one immutable original decision previously selected the first row's outcome,
making reported P&L depend on row order when observations disagreed. The evaluator
now retains sorted, content-hashed variants of recorded status, P&L and outcome
evidence, marks the lifecycle outcome unresolved, and excludes that outcome from
observed-settled P&L. It preserves the original funded first issuance, date,
contract, probability, odds and stake. Identical duplicates still count once;
other valid losses remain visible within the same game cluster. Repriced aliases
cannot replace the original result. This is diagnostic reconciliation, not an
execution amendment or a settlement writer.

The conflicting-row case is a synthetic regression, **not an observed conflict
in the supplied decisions**. All 165 funded original contracts and 117
chronologically available outcomes in this snapshot retain their prior results.
Originals and source observations remain read-only. No model fitting, thresholds,
funding policy, publication guards or live integration paths changed.

## Registry and rebase assessment

The task-bound engineering artifact reports D15 `candidate_ready`, with
controller review/integration/live verification pending. Its file SHA-256 is
`7bf547390fc87a63aecb59cdb69df7c0456712bf80379981685d26506d7a2911`;
the registry evidence identity remains
`9637a676d40139ef10e903d211a9abe26fb860e13c98cf2f1d02f149d7a1b62c`.
These are different bindings, not interchangeable hashes.

`data/rebase/conflicts.json` names only `markets.py`. Comparison with both copied
versions confirms upstream retains rejection of malformed specifiers and exact
inventory attribution while additionally binding status and outcome evidence.
The upstream file was kept byte-for-byte unchanged. Existing inventory/lifecycle
and D15 tests passed (225) before this increment. No protected files or existing
tests were edited; the final integrity evidence identifies the evaluator as the
only changed existing source.

## Supplied evidence and acceptance limits

At cutoff `2026-10-01T08:24:54.628546+00:00`, the input manifest has no errors.
All 319 file bindings, 392 raw-source bindings (384 Stake gzip observations and
eight official NHL JSON observations), and the separately pinned frozen
validation catalogue verify. The original export contains 172/172 decisions,
untruncated; seven legacy zero-allocation records receive no funded credit.
No root recovery package was supplied. Runtime diagnostics are `not_required`;
the release-failure manifest attaches no logs. No missing prior traceback was
invented. Export time is not historical availability or current participation.

The new [matrix](e5bbc53b172_validation/snapshot-observation.json) re-audits all
39 contract requirements, preserving full rules, traceability and unresolved
capabilities. None is declared fulfilled from this candidate. Snapshot Budapest
daily first issuances are 22, 52, 74 and 17 for September 28 through October 1;
October 1 has 11 distinct games and a shortfall of three at cutoff. No checkpoint
was supplied or reset. This is issuance history, not a current execution list.
Recorded P&L is not account verification or profit after unknown operating costs.

Knowledge cards K03 and K08 informed availability-time and game-clustering checks;
their prose was treated as source material, never executable instructions. Exact
market/scope/line identity and chronological outcome checks remain in the existing
evaluator. No new sports model or settlement-family support is claimed.

## Validation and next steps

New regressions are in `tests/test_e5bbc53b172_outcome_conflicts.py`: outcome
status/P&L/source/time/missing-evidence conflicts under all input permutations,
identical duplicates, preserved related losses, repriced aliases, and integration
with the actual pinned export. The new and existing D15 tests passed (204).
Full suite results and exact log/source hashes are recorded in
[the validation index](e5bbc53b172_validation/evidence-index.json).

The bounded evaluator increment is candidate-complete. The deadline remains
`2026-10-02T00:04:28.263503+00:00`; this work neither moves it nor closes the
registry's integration gap. The controller must independently review this source,
verify trusted integration authority (no exact passive profile is supplied and
discovery is disabled), then verify a fresh publication locally and through
Tailscale. Production readiness and fulfillment at the deadline require that
later evidence. Production services, credentials, parent data and the writable
wager database were not accessed. No helper or nested agent was launched.
