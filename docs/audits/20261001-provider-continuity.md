# Provider continuity release — 2026-10-01

The requested weekly-quota recovery is implemented, tested, independently
reviewed, installed and initially verified in the live background development
controller. The current selected model is `gpt-6-astra` with a successful real
response after installation. This is a provider-continuity release, not a claim
that all outstanding betting-model work is complete or profitable.

## Requirement and implementation

Task `ROOT-PROVIDER-RECOVERY-20261001` retains its original deadline of
2026-10-01 22:00 UTC. The exact release is identified by
[install-manifest.json](../../data/provider-recovery-20261001/release/install-manifest.json),
SHA-256 `ff338c3ae38ffd121f4c41ac9fb681d7c0fc7956dd181606453fab62e1cc1d8f`.

| User requirement | Implementation and verification |
| --- | --- |
| Use an available weekly reset | `codex_control.py` verifies the installed app-server protocol; `provider_recovery.py` requires fresh, account-bound weekly-exhaustion evidence and an available earned credit before `account/rateLimitResetCredit/consume`. One durable UUID is reused after uncertain responses. Context resets and ordinary timeouts do not qualify. |
| Prefer available GPT; otherwise Claude | Fresh catalog-listed GPT routes are tried in the explicit capability preference order; only an actual exact-model response establishes availability. Exact Claude Opus 5.5 is the temporary fallback. |
| Return to the strongest responding GPT | A separate 60-second monitor schedules bounded probes, normally with 5–15-minute backoff after quota failures. The next eligible step uses the strongest configured responding GPT. Running work is not killed for a provider change. |
| Continue real development | `development.py` routes implementation and candidate review through `provider_runtime.py`. Claude returns structured edits without tools; the controller applies validated edits only inside the isolated candidate workspace. Actual local tests and separate release controls remain mandatory. |
| Preserve supervision and evidence | Durable account-bound state, process locks, failure/success inboxes, per-attempt logs and output hashes survive restarts. A quota wait is distinct from a code/test failure. Production review still requires independent Codex. |
| Respect current authorization | Root configuration revocations apply immediately even with a fresh cached catalog. No paid purchase, new subscription, account switch, automatic wager, stake change or interactive chat-model change is implemented. |

The requirement ledger, AI delegation policy, AGENTS.md and developer directive
now record this explicit user-authorized temporary-provider exception. See
[PROVIDER_RECOVERY.md](../PROVIDER_RECOVERY.md) for operating paths and boundaries.

## Validation and independent review

- Complete final suite: **5,106 Python tests, 5 subtests and 216 DOM tests passed**.
  The Python run retained 25 warnings. Logs are
  [python.log](../../data/provider-recovery-20261001/release/python.log) and
  [dom.log](../../data/provider-recovery-20261001/release/dom.log).
- All **255 existing test files** were retained byte-for-byte. Four new test
  modules exercise transport, recovery, Claude isolation and integration.
- Independent checks covered 30 runtime cases, 97 installation/entrypoint
  cases, 160 control/router cases, and 61 Claude adapter/adversarial cases.
- Reset result variants, response loss, process crashes, concurrent reset
  claims, stale policy, exact-model identity, permission revocation, deadlines,
  stale output reuse, protected paths and rollback races were exercised.
- The independent Codex release approval is
  [release-review.json](../../data/provider-recovery-20261001/release/release-review.json),
  SHA-256 `f144a493fb867145ed48aadf5727c10fb1ea4178557234b97e5d01d2133e1513`.
  It binds all 16 installed files, all four installation helpers, the complete
  test logs and the production baseline.

Actual Astra and reserve responses were checked. Actual Claude Opus 5.5
structured coding, local tests and separate read-only review were checked in an
isolated toy workspace under the GUI service environment; the final adapter
also replayed those retained responses through its tightened validation. See
[Claude smoke evidence](../../data/status-checks/disk-recovery-20261001/claude-development-stage/evidence/real-smoke-result.json)
and [independent adapter review](../../data/provider-recovery-20261001/reviews/model-outcomes/claude-review.json).

The account was usable and had no available earned reset during implementation.
**No real reset was consumed and no real exhausted-weekly-quota incident was
manufactured.** Quota recovery and failback were fault-tested; a successful
individual provider response does not guarantee future service availability.

## Installation and live evidence

The existing developer jobs drained naturally. Supervisor/developer maintenance
terminated zero active jobs. The older F0013 release validation finished with its
retained timeout failure before the release service was stopped under its
complete-run lock. The failure was not relabelled as successful.

The checked source installation completed **20:18:10 UTC** and restarted the
release, development and supervisor services successfully. The sports engine
was not stopped. Original runner configuration and development task state were
preserved during installation. See
[install-journal.json](../../data/provider-recovery-20261001/release/install-journal.json)
and [state-preservation.json](../../data/provider-recovery-20261001/release/state-preservation.json).

At **20:18:17 UTC**, the newly installed monitor received a genuine exact
`gpt-6-astra` response. Its retained probe receipt hash is
`79f188ab76a59045ed57d0559a9065b91b0ec1718da4045f0b1c8e7f80aca853`.
The local and Tailscale development APIs exposed the same active provider.
Both health endpoints returned HTTP 200; Tailscale certificate verification
passed using the native system trust store, without disabling TLS verification.

The read-only live check at **20:19:48 UTC** confirmed fresh publication **4993**,
the exact installed source/review bindings, the re-hashed successful probe,
fresh service heartbeats and preservation of all **207 original bet records,
206 system decisions and 199 issuance records** present at installation.
See [live-verification-20261001T201948.json](../../data/provider-recovery-20261001/release/live-verification-20261001T201948.json),
SHA-256 `b882e6d74326e3816d1ff4a8c4c84dec6cade170476df792df0e95f878873ab1`.
Four resumed background jobs were subsequently observed; their running state
and actual provider-attempt records are retained in
[resumed-work.json](../../data/provider-recovery-20261001/release/resumed-work.json).
Running jobs are not represented as completed improvements.

An independent read-only [live review](../../data/provider-recovery-20261001/integration-review/live-review.json)
also verified those source, process, probe and ledger bindings and observed the
later fresh publication 4995. Its SHA-256 is
`7ba0d0b0bc5853f978d8ff86dbbc7467fbdb3989d1738267a2d0f930c70521fd`.

Initial failed checks remain archived, including the live verifier's initial
text/byte payload assumption, the first probe-schema issue and installation
helper entrypoint defects. Corrected checks passed; original failures were not
deleted. The verifier fix affected only read-only release evidence tooling.

## State and remaining scope

| State | Evidence |
| --- | --- |
| Implemented | Exact 16-file manifest and aligned project requirements |
| Tested | Complete Python/DOM suites plus independent fault/isolation cases |
| Independently reviewed | Exact-hash Codex component and release receipts |
| Installed | Checked installation journal and three successful service restarts |
| Initially live verified | Real post-install Astra response, both APIs, fresh publication and preserved originals |
| Real weekly incident observed | Not yet; no weekly exhaustion or reset credit was available during this work |
| Betting-result improvement | Not evaluated by this provider release; original lifecycle/P&L remains the measurement source |

Existing D25/D31 repair packages and unrelated model, validation-input and release
gaps retain their own tasks, deadlines and evidence. They are not closed by this
provider change. Publication guards, exact contracts, the 300-second quote limit,
USD1–5 funding policy, minimum20 daily objective and original betting outcomes
are unchanged. This release adds continuity for their ongoing development.
