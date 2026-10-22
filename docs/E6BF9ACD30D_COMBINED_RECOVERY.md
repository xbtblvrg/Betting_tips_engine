# D17 combined recovery candidate

The candidate integrates both retained repairs: complete lifecycle records in
closing-price studies, and the engineering attachment inventory correction.
Only `closing_runtime.py` and `requirement_acceptance.py` change among existing
files. Existing tests, protected code, configuration, model artifacts and
publication gates remain unchanged. This is candidate implementation evidence,
not a production release or a new betting-model approval.

## Repair and provenance

The runtime previously passed CORE-only scheduling records into the frozen
report store, losing status, P&L and outcome evidence. It now reads the original
export once, schedules using immutable issuance fields, then passes complete
records for that cohort to the existing append-only report store. Summaries
retain original stake and an explicitly retrospective outcome snapshot. Later
outcome evidence produces a separate study without changing the quote cutoff,
price comparison, original issuance or scheduling identity. Related contracts
stay grouped by game; they are not independent performance samples.

Restored byte-for-byte from the verified candidate manifest in run
`20261001T140814994225-E6BF9ACD30D`:

| File | SHA-256 |
| --- | --- |
| `src/betting_engine/closing_runtime.py` | `733682b04e13e25bd8f61091b23c77c8b1d58ebb4ac6d97b61b531840f9b0756` |
| `tests/test_e6bf9acd30d_closing_lifecycle.py` | `bcf574dc8fa21a4b8ce8f6e01a363072b769d90c5fb4cf55c044aece85dbd122` |

Restored byte-for-byte from the verified candidate manifest in run
`20261001T142606405198-E6BF9ACD30D`:

| File | SHA-256 |
| --- | --- |
| `src/betting_engine/requirement_acceptance.py` | `7e5675b2b075b5892da60064c84dff98433b31b742639858130965694f8327fa` |
| `tests/test_e6bf9acd30d_attachment_inventory.py` | `79411ae40e383d26ff04f0e13dc43545cb6cb5dc808f5a21965722a03f0d72fb` |

Receipt-only attachments remain visible with an empty gap-ID marker; they never
become observed gaps or evidence of completed implementation. Malformed gap IDs
remain raw diagnostics. The prior run/review hashes match the targeted recovery
pins `6a7e70f18550dab13ae385e5a9ed8b9de68d074074aeecefd6b2ec4713a66866`
and `9003f89a002206f867b60657b5ab664aa946af1b36b15e030a552f3b5045f075`.
These are untrusted diagnostic evidence, reviewed against the code and tests.
No helper advice was supplied or used.

## Input audit and real-export replay

Verified 309 development file pins, 342 decompressed source-body hashes, both
task-linked engineering files, all four frozen validation bundles, their four
test pins, and the protected validation catalogue hash. Development errors are
empty; the decision export contains 179 records and `truncated=false`.
The engineering observation still identifies D17 as candidate-ready with
production integration outstanding. The supplied receipt alone does not close it.

`data/task_diagnostics/manifest.json` is absent. No root recovery package or
rebase conflicts are declared or supplied. Runtime diagnostic state is
`not_required`; release diagnostics list no files. Exact prior review evidence
is available in the explicitly supplied read-only run directory. No required
declared frozen fixture was missing or mismatched.

The replay in [E6BF9ACD30D_COMBINED_REPLAY.json](E6BF9ACD30D_COMBINED_REPLAY.json)
uses the real exported record schema, including original contract payload and
official score-scope/source evidence. All 126 closed decisions across 21 games
retain their exact original payload, odds, stake and outcome: 73 lost, 50 won,
three push. The remaining 53 decisions have not reached the closing grace
period at the explicit replay boundary. Repeated evaluation is idempotent;
the source JSON and temporary read-only input SQLite remain byte-identical.

No closing-capture row export is supplied. All 126 evaluated closing prices and
CLVs remain missing. Catalogue observations and export time do not authenticate
historical closing quotes. The export timestamp is only a replay scheduling
boundary; each game's price cutoff remains its original scheduled start.
Outcome observations are never pre-start model inputs. K03, K08 and K10 local
knowledge cards informed this distinction; they are research material.

## Validation and remaining acceptance

Before restoring the runtime, all seven lifecycle regression cases failed.
After restoration, all 325 closing-price tests passed, including those seven;
all 13 attachment-inventory cases passed. The two restored test paths were
absent at preparation and are new files in this workspace. All 355 pre-existing
test/fixture files remain byte-identical.

Full validation: **7,437 Python tests plus five subtests passed**, with one
installed FastAPI/Starlette test-client deprecation warning; **216 DOM tests
passed**. Commands and hashed complete logs are bound in
[E6BF9ACD30D_COMBINED_VALIDATION.json](E6BF9ACD30D_COMBINED_VALIDATION.json).
Synthetic successful quote cases test exact contract/settlement
identity and cutoff behavior; they do not establish real CLV or profit.
No training, chronological split, model threshold, stake policy, funding policy,
EvidenceGuard, issuance counter or original decision has been changed.

The original task deadline remains `2026-10-01T19:27:34.902505+00:00`.
The targeted recovery checkpoint `2026-10-01T14:45:32.370767+00:00` was missed;
it is not reset. Production integration remains unfulfilled. The controller
must independently review the combined candidate, complete the trusted release
path (no exact passive profile is supplied), and verify fresh local/Tailscale
publication. Supply pinned capture rows with matching full archives for actual
CLV measurement. No production service, credential, account state, parent data
or live wager database was accessed. This repair does not establish all-market
coverage, 20 daily instructions or profitable performance; those objectives
remain open. The USD1–5 ceiling, no fixed daily cap and manual-only wagering
requirements are preserved.
