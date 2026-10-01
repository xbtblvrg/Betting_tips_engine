# Provider continuity

The background development controller prefers Codex and GPT-6 Astra. A weekly
Codex quota failure starts a durable recovery episode. The controller reads the
actual account limits and, only for a verified exhausted weekly window, redeems
an available earned reset through the installed Codex app-server interface.
Every logical redemption has one persisted UUID; uncertain retries reuse it.
Reset success is followed by a new limit read and a real model response probe.
A context reset, arbitrary error text, timeout, or catalog entry is insufficient.

When the preferred GPT cannot respond, the controller tries the explicitly ranked
GPT models actually returned by the account catalog, followed by temporary
Claude Opus 5.5. The configured ranking is a reviewed capability preference, not
an invented numerical benchmark. Unknown future model names are not silently
promoted. The catalog refreshes automatically; expired catalog evidence is never
treated as current availability. The installed protocol version is verified for
earned-reset support; unknown protocols remain visible and cannot redeem resets.

The monitor runs separately from task heartbeats on a 60-second cadence. Failed
preferred-model probes use bounded backoff, normally 5 to 15 minutes for quota
failures. Successful real responses also refresh availability evidence. The
strongest configured responding GPT becomes the next step's provider. An
in-flight step is not killed merely because a better provider becomes available.
Control calls, inference probes and task attempts have finite execution budgets.

All controllers share account-bound routing state and OS locks. Failure and
success notifications are durably queued even while another controller probes a
provider. Codex coding tasks retain isolated workspaces and exclusive claims.
Claude shares a single capacity lock with optional Claude advisory work.

## Claude implementation boundary

The fallback CLI runs without tools in a private temporary directory. A native
macOS boundary prevents it from reading or writing the project or candidate
workspace. The trusted controller supplies at most 96 source files and 384 KiB
of source text with exact hashes and an explicit omission inventory. Claude
returns structured proposed edits and a report. The controller validates the
reported Opus model, paths, old hashes, protected files and unchanged existing
tests before applying edits inside the isolated workspace. Missing context is
reported explicitly and is never fabricated. No credentials enter the bundle.

The existing local, network-disabled Codex sandbox runs the actual tests without
using model tokens. Candidate review is a separate read-only step. Production
release still requires independent Codex review and a separately checked release;
Claude does not obtain access to production, betting decisions or the ledger.
The existing live sports engine, outcome tracking and already approved models
continue independently during provider recovery.

## Evidence and operation

- Configuration: `config/provider_recovery.json`.
- Account/catalog/control receipts: `data/development/provider-recovery/control/`.
- Shared routing evidence: the account-hash directory under
  `data/development/provider-recovery/`, including immutable attempts and inbox.
- Current status: `data/development/provider-recovery/status.json`, also included
  in the development worker heartbeat exposed by `/api/development`.
- Each AI step retains separate provider logs, original partial outputs, actual
  provider/model metadata and output hashes in its run archive.
- Errors and old deadlines are retained. A provider wait uses a bounded retry;
  unrelated test/data failures keep their own diagnostic and recovery process.

There is no automatic purchase, new paid API subscription, account switching,
bet submission, stake change, or hidden alteration of the interactive chat model.
Temporary provider fallback changes the background engine's development steps.

The real account was usable during implementation, with no available earned reset.
Reset outcomes, lost responses, concurrency and failback are tested with controlled
faults; no real reset was consumed just to test the feature. Actual Astra response
and Claude structured-code/test/review smoke evidence is retained separately.

The official interface is documented in
[Codex App Server](https://learn.chatgpt.com/docs/app-server); the CLI exposes
earned-reset redemption through
[`/usage`](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
