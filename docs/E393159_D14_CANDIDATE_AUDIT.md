# E393159E9AC — D14 candidate-tree binding

Candidate implementation only, 2026-09-30. Deadline unchanged:
**2026-10-01T20:53:15.151045+00:00**. Production integration, restoration and live
verification remain unmet; their absence at the deadline must remain visible.

## Evidence and bounded increment

Read AGENTS.md, contract `2026-09-29.15`, D14 in the development plan,
`docs/D14_DEVELOPMENT_ACCEPTANCE.md`, the worker/acceptance persistence schema,
market contract and project status. No rebase conflict manifest was supplied.
The specified previous attempt's `files/` directory does not exist; it was not
reconstructed. The native-boundary positive-control failure is controller-owned
and not repaired by this change; protected native-boundary code is untouched.

Registry evidence `data/engineering-evidence/60eca86104ca3fe27061d57c9dbb0a1e228f4fd843f72e546de7ab83259147fc.json`
observes D14 as `candidate_ready` at `2026-09-30T00:52:35.544653+00:00`, with
integration/restore evidence still pending. Its raw SHA-256 is
`b46f429f43852b34d16bb1bc1fca5f64a39b257375ac5887311ce8e07f336551`.
The older numeric-overflow rejection is already repaired upstream. All 80
existing D14 tests passed before implementation; that code and those tests are
unchanged.

The existing inspector validates archived **changed** artifacts, but cannot
compare the complete accepted manifest to another candidate tree. The new
`src/betting_engine/development_candidate_audit.py` composes that inspector with
a read-only comparison of every manifest file, including unchanged code and
configuration. Missing, altered and unexpected files block agreement. This
implements one source-drift diagnostic; no worker or release integration is added.

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python \
  -m betting_engine.development_candidate_audit \
  /absolute/offline-archive RUN_ID /absolute/offline-candidate \
  --conflicts conflicts.json
```

Both roots must be explicit, quiescent offline exports. No default production
discovery, writes, subprocesses or network. Exit 0 means candidate-tree agreement
only. Every report retains `production_ready=false`, `full_engine_ready=false`
and `restore_drill=required_not_verified`. An omitted conflict manifest stays
unassessed. An invalid archive blocks before candidate traversal.

The comparison uses the worker's manifest exclusions: hidden paths, `data`,
`node_modules`, `.venv` and caches are pruned, never read. Thus it is neither a
wager-ledger check nor a whole-filesystem integrity claim. Symlinks in visible
paths or root ancestors, nonregular files, noncanonical paths and malformed
hashes block. Limits: 10,000 manifest files, 20,000 traversed entries, 8 MiB per
file and 64 MiB per read phase. Exceeding a bound is a blocker, not partial success.
The receipt is rehashed against the original inspection before use. This does
not establish atomicity against concurrent mutation or authenticate the producer;
the controller must lock/freeze exports and separately validate provenance.

## Validation

`tests/test_e393159_candidate_audit.py` adds 45 cases. It covers changed and
unchanged file drift, additions/deletions, read-only success, CLI exit/JSON
behavior, malformed full manifests, symlinks, FIFO rejection, excluded ledger
directories, blocked review/implementation/conflicts/nonfinite evidence, bounds,
missing/unreadable trees and receipt replacement. Its integration test uses the
actual protected worker's temporary candidate persistence and integration path,
then detects subsequent drift. External implementation/review/DOM subprocesses
in that fixture are stubbed. This is not an actual independent AI review.

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q \
  tests/test_d14_development_acceptance.py tests/test_d14_nonfinite_evidence.py \
  tests/test_e393159_candidate_audit.py tests/test_development.py \
  tests/test_parallel_development.py tests/test_task_release_handoff.py \
  tests/test_release_supervision.py tests/test_release_superseded_tasks.py
node --test tests/web.test.mjs
```

Final validation: **198 Python/API tests passed**, one existing Starlette/httpx
deprecation warning; **40 DOM tests passed**. Baseline SHA-256 comparison found
all 280 existing source/test/doc/config files unchanged. Only the new module,
new regression file and this document are delivered. Controller full suites and
independent read-only review remain pending.

## Source scope, advice disposition and developer audit

K03 and K08 knowledge cards informed provenance and evidence boundaries; their
publications were not newly fetched. Inspected the supplied sports-source
manifest and hash-verified NHL play-by-play blob
`1fa6a0c601c22f3d4171a0fc40ef7f0576ceccca8d881c3a94aee379be52aed4`.
Its actual schema includes `id`, `season`, `startTimeUTC`, `gameState`, `plays`,
`rosterSpots`; plays include `eventId`, `periodDescriptor`, `timeInPeriod`,
`typeDescKey`, `sortOrder`. The manifest explicitly has null publication time,
unauthenticated archive availability and unverified current participation.
These historical observations are not inputs to this development utility and
do not establish sports-model or betting approval. No source times were invented.

Grok advice `acceptance_checklist:grok`, exact text SHA-256
`9715dc08644f460ca24f8160f3e062b761e38e114797d6b3a784b54478188da6`:
**partly used**. Independently verified this hash. Used its requests to inspect
actual D14 evidence, retain gates/deadlines and separate candidate/release claims.
Rejected assertions that freshness, deadline, contract fields and guard rules
are unspecified: supplied rules define them. Corrected its conflation of daily
issuance with rolling 48-hour coverage: the minimum is 15 distinct funded
decisions per Budapest calendar day; shortfall need not mean missing source
evidence. No helper test or source claims were accepted as verified. Claude/agy
cadence entries contain no advice in this dossier. No helpers were launched.

All contract requirements were read; this increment fulfills only the bounded
offline D14 diagnostic and its candidate integration tests. R25/R27/R28/R30 gain
test and audit evidence, not production fulfillment. R01–R24, R26 and R29 retain
their existing implementation status without new certification. Exact Stake
contract/settlement identities, chronological model validation, EvidenceGuard,
publication gates, losses and original odds/stakes are unchanged. No forecasts,
allocations, bankroll, current offers, performance observations or profit claims
are generated. $1–$5 stakes, no daily aggregate cap, daily shortfall reporting and
manual wagering remain requirements. No production files, wager database,
credentials, services or messaging were accessed.

Next: controller runs full fixed suites and independent review, resolves the
native-boundary environment failure and checks the new audit against trusted
frozen real candidate exports. A separate root change is needed for any runtime
integration. The trusted coordinator must perform the actual restore drill,
source-drift and immutable-ledger checks, and later local/Tailscale publication
verification in an authorized release environment. No old wager database may
be restored during code rollback. Full-market implementation, validated sports
models, daily delivery and measured profitability remain outside this utility.
