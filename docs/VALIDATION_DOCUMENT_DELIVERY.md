# Root-pinned validation document delivery

The accepted development workspace predates `docs/UTILITY_EVIDENCE_REPLAY.json`.
Normal initial preparation copies documentation, but accepted-workspace rebasing
uses a production manifest that excludes general documentation. The original
`tests/test_passive_evidence_release.py` therefore fails during collection in
five observed development workspaces: D24, D17, F0013, E5BBC53B172 and F0037.
The retained F0037 controller log proves the exact missing path. The other four
latest implementations report the same unavailable fixture.

Two examined later release workspaces already contain the exact replay document.
Their failing lifecycle tests instead lack `data/development_inputs/manifest.json`
and/or `system_decisions.json`. This document repair does not resolve that
separate lifecycle export requirement or complete any task's full acceptance.
Original task identities, histories and deadlines remain unchanged.

## Explicit root declaration

An optional `delivery: "workspace-documents"` on a validation-source bundle binds
ordinary exact regression-test paths and hashes to canonical JSON document paths,
SHA-256 hashes and byte lengths. Each document is at most 2 MiB. Source bytes come
only from the existing content-addressed `data/validation_source_archives` store;
no task prose, candidate path or directory discovery grants access. Model
subdirectories, alternate suffixes, path traversal, duplicate case aliases and
unknown document-declaration fields are rejected.

The proposal adds only `utility-evidence-replay-v1` to the existing catalogue.
Its test hash is `392c2c20bbbb0d31b881f28f7dec40fd865344fcbb2904445fc9fb4c815eeffc`.
The unchanged 1,562,667-byte document has hash
`6b215c132e829a6b3cd308f9327890373fc0096895e1d26fe2de01b26e4d2606`.
The original source body and append-only catalogue proposal accompany the handoff.
Existing bundle declarations and their separate frozen-source delivery remain
unchanged.

## Shared isolation boundary

The existing exporter used by development and trusted release preparation first
validates the relevant tests, every archive and every destination. It retains the
original immutable bundle and supplies the same bytes at the existing document
path. Exact existing copies are reused without changing modification times;
conflicts are rejected and never overwritten. Files are created exclusively.
Only the established development workspace namespaces and release staging
namespace may receive internal document copies; production subtrees are rejected.

The exporter validates strict JSON, size, hashes and source/copy equality again
after delivery, and rejects catalogue drift. Verification before and between
validation/review stages rechecks catalogue, receipt, tests, archives, frozen
copies and document copies. Removed declarations, missing sources and altered
bytes fail without repair. Exact native sandbox read-only paths are supplied by
`validation_workspace_document_paths`; candidate documentation directories stay
writable. The controller integration is owned and reviewed separately.

`validation_document_paths` and `validation_document_test_paths` expose only
validated root declarations. Per-document generation binds the selected document,
collecting tests, actual archive bytes and exporter source. Unrelated feed or
other catalogue-bundle changes do not change that document's generation. Exported
input generations retain a `validation_documents` map for future authenticated
failure comparison; absent historical entries are not invented.

## Evidence and limits

The retained replay reproduces collection exit 2 with the original test and the
missing fixture, then passes all 83 unchanged original tests after exact archive
delivery. New regressions exercise accepted-workspace reuse, release preparation,
idempotency, malformed JSON, namespace and symlink rejection, conflicting bytes,
source drift, catalogue removal and post-test tampering. No production workspace,
service, task state, wager data, model or original test was modified by this work.
This supplies regression evidence only; it is not a new sports observation,
validated betting capability, release approval or measured profit improvement.
