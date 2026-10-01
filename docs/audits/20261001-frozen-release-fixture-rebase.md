# Frozen release fixture delivery: isolated rebase

This repair rebases the previously reviewed D31 fixture exporter onto source
`08e7de93508570cf21d76a2608f219eea966c2ac`. It changes generic release validation
infrastructure only. It does not deploy a utility, change a model or decision,
repair receipt import, or establish improved prediction performance.

The unchanged original D31 collecting test requires three historical inputs:
the original manifest, publication 4615 from 2026-10-01T14:01:17.958564+00:00,
and the complete 179-record decision export. Exact bytes, original acceptance,
test hash and node identifiers remain pinned in
`config/release_fixture_inputs.json`. The original manifest lists 319 inputs;
the export receipt explicitly covers only the selected three and sets
`complete_development_export=false`. Historical timestamps are not refreshed.

The coordinator delivers the cohort only after its accepted-file overlay and
checks it at all four existing validation boundaries. Current provider routing,
Codex-only release review and both existing DOM suites remain intact. Unrelated
releases without the collecting test or a prior receipt remain read-only no-ops.
Existing regression tests remain byte-identical.

Independent review found that between-step hash validation alone did not make
the delivered namespace immutable inside release tests. `ReleaseSandbox` now
adds exactly `data/development_inputs` to its read-only input paths. Ordinary
development research permissions are unchanged. A native release-sandbox drill
checks read access, denies append/truncation/unlink/rename of the publication and
receipt, denies new files and namespace rename, retains outside-boundary read
and write denial, and confirms ordinary workspace scratch remains writable.
The exact frozen bytes remain unchanged after all twelve denied operations.

The exporter now reuses the protected `workspace_copy._directory` primitive
instead of duplicating ancestor traversal. Its receipt pins both exporter and
directory-helper source hashes. The reviewed directory correction is an explicit
integration dependency, not a repair implemented by this component. Existing
size/hash checks, strict JSON, no-link traversal, descriptor-based writes,
exclusive publication, immutable source identity checks and drift rejection are
preserved. No sandbox permission is widened. Source or destination changes fail
closed without overwriting unrelated data; private incomplete evidence remains.

Isolated acceptance evidence lives in
`data/engineering-integrity-20261001/fixture-stage/data/fixture-evidence`.
It includes targeted tests through the actual native worker test command and
the two unchanged original nodes following an actual isolated coordinator
prepare. Full integrated tests, independent review, controlled installation and
fresh production preparation are separate required release evidence.

The original repair checkpoint remains **2026-10-01T18:19:56.104900+00:00**,
including its recorded lateness. The original D31 task deadline remains
**2026-10-02T11:30:00+00:00**. D31's latest accepted-source reconciliation,
receipt-import capability and full task acceptance remain unresolved. This
fixture delivery does not repair F0038's protected copy failure or prove its
dispatcher acceptance. No task identity, deadline, retry, production state,
service, wager record or funding policy is changed by this isolated package.
