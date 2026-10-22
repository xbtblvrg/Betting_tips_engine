# EA76D0E6B5C — D12 source identity repair

Candidate implementation only. The existing D12 NHL observation adapter now
rejects contradictory source provenance before emitting a normalized state.
The checkpoint stays **2026-10-01T20:45:09.728834+00:00**. This repair does not
close `development:D12` or approve production live betting.

The engineering record supplied for this task reports D12 as `candidate_ready`,
with controller review and verified live observations/training/settlement still
pending. There is no `data/rebase/conflicts.json`. The existing candidate was
present in `live_shadow.py`, with 106 existing tests passing before changes.

## Reproduced defect and integrated change

A real archived game body for `2022030145`, retaining its correct SHA-256, was
accepted with synthetic contradictory `provider_game_id` and URL metadata naming
another game. Hash integrity alone did not bind the observation to its source
identity. This was a provenance defect in the shadow inspector; its output
already denied production eligibility.

The existing `inspect_nhl_observation` entry point now checks native positive
integer game/team IDs, distinct teams, declared event ID, exact official
gamecenter play-by-play URL, and supplied league/format/family/kind/status.
Contradictions raise stable `LiveEvidenceError` reasons. Declared publication,
retrieval and availability times must parse with timezones and be ordered.
Absent metadata remains inspectable with explicit missing-evidence reasons;
no publication, possession, participation or mapping evidence is invented.
An ordered timestamp is a consistency check, not authenticated availability.

This is integrated into the existing experimental adapter, not a new production
call path. It reads no account or wager state, fetches nothing, fits no model,
and cannot publish, settle or submit a bet. Existing exact Stake identity,
signed line, scope, settlement rule, observed tabs, price-change, freshness,
model-provenance and latency checks are unchanged. All 106 existing D12 tests,
including Winner/Handicap/Total and historical-source rejection, still pass.

## Source and test evidence

All five supplied `nhl_json` bodies were hash-checked and passed the stricter
adapter. All have `gameState=OFF`; publication time and current possession are
missing. Original final scores are preserved as observations, not converted to
regulation targets or used to settle live contracts. NFL historical play logs
and injury files also present in the manifest are not current participation
or live-state evidence. No source adapter was verified against a live endpoint.

K02 independent forecasts, K03 source availability, K08 chronological validation
and game clustering, and K10 manual latency were read as source material.
Hashes, observed game IDs and helper disposition are in
`EA76_D12_SOURCE_IDENTITY_EVIDENCE.json`.

Validation used `PYTHONPATH=src OMP_NUM_THREADS=1` and the required interpreter
`/Users/belavarga/Betting_tips_engine/.venv/bin/python`:

- Before the repair, new regressions: 32 failed, 5 passed, exposing the gaps.
- `-m pytest -q tests/test_d12_live_shadow.py tests/test_ea76_d12_source_identity.py`:
  144 passed (106 existing, 38 new, including a later positive timestamp case).
- API, governance, event-sequence, event-sequence-audit and Stake-college suites:
  117 passed; one existing Starlette/httpx deprecation warning. API storage was
  redirected to `/tmp/ea76-api-test-data`.
- `/opt/homebrew/bin/node --test tests/web.test.mjs`: 40 passed.
- Baseline byte comparison: only `src/betting_engine/live_shadow.py` changed
  among preexisting source/tests/docs/requirements. All 70 preexisting Python
  and DOM test files and all protected source files remain unchanged.

## Bounded developer audit and remaining acceptance

R01/R02/R04/R19/R23/R24 gain source-consistency checks only. No model validation
sample, chronological split, training input, forecast probability or settlement
rule changed. R03/R05/R06/R20–R24 coverage, 15-per-Budapest-day issuance, sizing
and all-market support are not established here. R07–R18 lifecycle, losses,
original odds/stakes, execution and UI requirements are untouched. No bets were
issued or measured; after-stake P&L and unknown operating costs remain separate
unmeasured quantities. Related selections are not counted as independent data.
R25–R30 retain isolation, Codex ownership, fixed deadline and explicit gaps.

The bounded source-consistency repair is implemented and tested. Next, the
controller must run its full fixed suites and independent read-only review.
Production remains blocked on authentic live observations and availability,
measured manual latency, exact Stake event mapping, independently validated
state-conditioned models with chronological/game-clustered evaluation, and
scope-specific live settlement. Separate authorized release and fresh local/
Tailscale publication verification remain required. No production service,
database, credentials, parent data or external messaging was accessed.
Unmet acceptance remains unmet at the original deadline; this document does
not change it or claim that a release occurred.

The Grok checklist was partly used for provenance, immutable records and honest
integration reporting. Its assertions that the deadline/freshness/candidate
were unspecified were rejected: those are supplied locally. Its 15-count
wording was corrected to Budapest calendar-day issuance, distinct from the
rolling 48-hour horizon. Its review is advice, not independent Codex approval.
