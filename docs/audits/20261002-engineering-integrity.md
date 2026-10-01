# Engineering integrity release — 2 October 2026

The three reproduced infrastructure blockers are repaired and installed. This
release does not complete the entire betting engine or establish an improvement
in betting results. The scope is workspace-copy safety, frozen validation-input
delivery, and correct execution of the original native isolation regression.

## Installed changes

| Capability | Implemented | Tested | Installed | Live evidence |
| --- | --- | --- | --- | --- |
| macOS workspace copying without forbidden ancestor-directory reads | Yes | 116 native targeted checks; full suite | 22:25:43 UTC | Installed helper used by the new isolated preparation and fixture smoke |
| Exact D31 validation cohort export | Yes | 69 new checks; both original frozen-cohort regressions replayed | 22:25:43 UTC | Original publication 4615 / 179 decisions exported with unchanged source times and hashes |
| Frozen release inputs remain read-only inside validation | Yes | Native denial and tamper tests | 22:25:43 UTC | 12 prohibited operations denied; disposable scratch writes allowed |
| Original Claude native-boundary regression runs in its required environment | Yes | Original trusted native node plus complete isolated candidate suite | 22:25:43 UTC | Controller source and service restart verified; subsequent candidate validation is still pending |

The copy repair uses macOS kernel enforcement against symlinks in every path
component. The new test protocol executes only the exact pinned original native
control on a private copy of trusted root sources. It runs candidate code inside
the existing sandbox. A node is excluded from that sandbox run only after its
required separate control succeeds and its source, test and import bindings
verify. Changed or missing evidence fails validation. Existing tests were not
weakened or edited.

## Validation and controlled release

- Base: `08e7de93508570cf21d76a2608f219eea966c2ac`.
- 263 original tracked test/fixture files remained byte-identical.
- Complete Python validation: **5261 sandbox passes + 1 separately executed
  original native pass**, no failures or skips; 5 additional subtests passed.
- Complete unchanged frontend suite: **272 passes**, no failures or skips.
- Independent installer checks: **41 passes**, including malformed and
  inconsistent native-evidence rejection.
- Independent review accepted the exact 13-file package before installation.
- Original settings, decision/ticket fields and controller task histories were
  preserved. No models, betting policy, stake calculations or orders changed.

Development and release admissions were drained; active jobs finished naturally.
Only idle engineering services were stopped for maintenance. All three were
restored after the checked installation. Web PID 266 and learning PID 271 stayed
unchanged. Failed validation attempts and the two operator-harness mistakes remain
in the evidence archive: omitted controller preflight in an early probe, and an
initial live check that incorrectly expected the periodic supervisor to have a
resident PID. Neither failure was erased or presented as a successful run.

## Fresh operational evidence

At 22:27:30 UTC, local and Tailscale health endpoints responded successfully.
Both returned the same governance body for publication **5121**, generated at
22:27:00 UTC. The guard accounting was consistent: 1239 checked = 1164 passed +
75 rejected. This is an operational consistency check, not betting-performance
evidence.

The new developer process is PID 12243; release coordinator PID 12239. The
supervisor is registered on its 60-second interval, with last exit code zero.
Its second post-install cycle reported all four supervised services healthy,
without a restart action. The overall delivery status correctly remains
`attention_required` because capability gaps still exist.

F0038 automatically resumed as run `20261001T222610332542-F0038` (attempt 25),
following the relevant protected-source change. D20 advisory work also resumed.
This proves scheduling recovery, not successful completion of those tasks.

## Remaining requirements and measured limits

The retained read-only audit at 22:16:32 UTC found 59 unfinished tasks,
196 open engineering gaps and 55 blocked release candidates. Twenty-one explicit
deadlines were already missed. Those deadlines and failed attempts remain intact.
These are different inventories; their counts must not be added together.

| Requirement area | Evidence-backed status |
| --- | --- |
| Generic engineering validation and isolation | This release's defects are fixed, tested and installed |
| F0038 complete worker recovery | New attempt running; accepted-source reconciliation and missing prerequisite modules still require its full acceptance |
| D31 complete receipt/import/retention work | Frozen-input blocker fixed; the complete feature remains unfinished |
| Detection, durable failures and bounded recovery | Existing supervisor and task machinery are running; unchanged failures retain backoff, relevant source changes can trigger a new attempt |
| Every market/league, new inputs, calibration and advanced models | Open implementation work; this infrastructure release does not enable or validate them |
| Outcome recording and evaluation | Retained statistics contain 304 original decisions, including 126 settled decisions; the separate ticket ledger contains 127 settled tickets |
| Autonomous learning improvement | Latest eight completed retraining jobs did not promote a model; no measured improvement claimed |
| Profitability or guaranteed daily output | Not established by this release |

At the retained outcome snapshot, the **126 settled original decisions** were
50 wins, 73 losses and 3 pushes across 21 games. Settled original stakes were
$144.13; returns were $112.2569; net result after stakes was **−$31.8731**.
Decisive win rate was 40.65%. Ticket totals differ because one reported duplicate
is a separate cash exposure. These are system-assumed/user-reported executions,
not direct sportsbook-account verification. The evaluated match-start period was
29 September 00:15 UTC through 1 October 02:00 UTC. Infrastructure fixes are not
evidence of a win-rate or profit increase.

## Reproducible local evidence

All paths below are relative to `data/engineering-integrity-20261001/`.

| Artifact | SHA-256 |
| --- | --- |
| `release/install-manifest.json` | `31c73a437d3c3aa0c022c5edb9889f29b5ec4d686fc4e3e72b18b2153254afbf` |
| `release/release-review.json` | `030a6f5fda892e89e66b146d655a2acb8955f08d22205c43a610e48cf5643281` |
| `release/python.log` | `7ccd83ef7be580417d8a92a0cade4df8ccb73882aaba5644aa7de95d4234466d` |
| `release/live-20261001T222730339258/report.json` | `9aaa796e1ac10cfb3e554b66d8eae133f5bcf7e9c3c494ef1e798031cc0850f6` |
| `independent-review/post-install-independent-observation.json` | `81084e81297db17108258c9ed55e75fb8c8cb06eb6b06b0ea5c480eb6eefc721` |
| `final-audit/installed-fixture-smoke/verification.json` | `4addd312eff9bffb4c7220da14faf102c860f379cc403a9a6147dfae44298c09` |
| `final-audit/remaining-gap-inventory.json` | `6ae03bcf99e840a87524e7d7fae96959ffe3456ee43fc932069961c72296346e` |

`release/install-journal.json` retains the exact file replacements, service
restoration and immutable-record checks. The complete evidence receipts retain
individual test runs and failures. The three content-addressed private cohort
archives remain under `data/release_fixture_archives/`; their exact hashes and
sizes are versioned in `config/release_fixture_inputs.json`. As with other
private project data, the archives are retained locally outside Git and must be
included in private backups. Missing or changed archives stop the affected
validation rather than substituting current data.
