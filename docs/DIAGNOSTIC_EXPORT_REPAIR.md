# D15/D24 diagnostic dependency repair

The blockers are two controller input defects, with authentic original evidence already present locally. This stage changes only `src/betting_engine/development_inputs.py` and adds two regression modules. No production service, accepted transaction, existing test assertion, outcome, wager record or task state was changed.

## Findings and original reproduction

- `20261001T052931870165-D15` inherits `test_e514cb13f60_lifecycle_replay.py`, which requires a specific historical cohort: eight diagnostics, 14 original decisions, four games and -13.85 P&L. Its ROOT variable pointed at mutable task-specific development inputs. D15 and D24 correctly receive only task-linked engineering observations, so each received zero of those eight diagnostics. Expanding every export to current diagnostics would be incorrect: the current registry contains 22 outcome gaps, and fixed cohort acceptance must remain frozen.
- The original accepted test bytes (SHA-256 `03a58e9a6f283635ab76bd487b3b3aebb2df6322f773ad2dce6b50b4d2c2537f`) and original eight diagnostic attachments were retained in `20260930T221951827265-E514CB13F60`. With the same D15 candidate source, the unchanged test fails `0 == 8` on D15 inputs and passes against this authentic original cohort. No diagnostic was regenerated or synthesized.
- D24 identifies exact missing final-response SHA-256 references for NHL games 2026020006–2026020008. Every one exists in root `data/nhl_settlement/sources` with its original hash. The exporter followed only Stake archive references, silently omitting official NHL outcome sources. No new request or historical availability inference is required.

Exact original run audit, reference pins, frozen source provenance, baseline failures and replay logs are in `evidence/`. Initial replay attempts using production source failed collection because `period_contracts.py` exists only in the accepted candidate; those failed setup logs are retained. Successful fixed-input comparisons use the exact D15 candidate source tree.

## Isolated implementation

The development exporter follows NHL source references retained in the immutable decision snapshot. It accepts only the exact `nhl_settlement/sources/<sha256>.json` path and official game-specific HTTPS landing URL, expected evidence version, game identity, original observation timestamp and bounded local regular-file content. It verifies hash, optional length and response game identity, preserves metadata and raw bytes, rejects symlink hops and unsafe URL metadata, reports missing or invalid referenced sources explicitly, and never discovers arbitrary source directories. The existing overall export byte bound still applies. Source references and reads have a 512-item bound; each NHL source has a 4 MiB bound.

`input_generation()` adds `nhl_settlement_sources`, based on exact reference identity and verified availability. Restoring the referenced bytes changes that component while preserving the existing decision-history, Stake, scoped-model and validation components. Root controller integration must map the appropriate NHL source blocker wording to this component; this stage does not edit the controller.

The root-declared validation catalogue now narrowly permits the three required lifecycle manifests/snapshot paths and hash-named engineering JSON files only in bundle `lifecycle-outcomes-v1`. Other new namespaces remain rejected, and the existing hash, byte, safe-path, atomic-export and post-test verification checks are unchanged.

## Concrete root handoff

`handoff/validation_sources.append.json` is the exact bundle to append to the existing root validation catalogue. `handoff/data/validation_source_archives/` holds 21 exact original files, totaling 2,954,135 bytes. The source manifest preserves its original broader export references; this frozen regression bundle intentionally includes only the original files read by the regression. It is not a complete current development export.

`handoff/tests/test_e514cb13f60_lifecycle_replay.py` changes only the ROOT assignment to the frozen `data/validation_sources/lifecycle-outcomes-v1` namespace. The corrected SHA-256 is `d4402b2c24e4876e5329ff70b037586ffa218ae017ccfdbbff68d9c59fdcc969`. All 24 assertions and all module AST outside that assignment are unchanged. The original test is archived unmodified. `evidence/prepare_handoff.py` regenerates and verifies the proposal.

This correction requires separate trusted root integration and independent review. The root coordinator plans to add the corrected test as a new root regression and use normal upstream rebasing, which preserves prior accepted candidate bytes rather than mutating acceptance metadata. The test also imports candidate-only `period_contracts.py`, SHA-256 `e382db789049e7758e69b4eae4f97616d206f98d15c600838174122c3127f926`; this passive helper imports only `math` and `re`. Installing that helper alone does not integrate candidate market parsing or activate betting behavior. Other imports are the installed contract/domain modules; exact accepted source pins are in `handoff/lifecycle-fixture-handoff.json`.

## Verification and actual source completion

- New regressions against original exporter: 24 failed, 7 passed.
- Existing export/validation suites plus new regressions: 78 passed initially; 85 passed after independent malformed-input review.
- Same unchanged lifecycle test/candidate source: D15 inputs 1 failed/1 passed; authentic original cohort 2 passed.
- Corrected proposed test after real bounded validation export and read-only verification: 2 passed.
- Authentic D24 snapshot replay: all 170 original decision JSON records remain byte-identical; all eight referenced original NHL raw response bodies export, including the three omitted sources.
- Original final responses reproduce all 40 recorded NHL outcome targets and P&L across eight games, including 13 decisions tied to the omitted sources. Status counts remain 25 losses, 13 wins and two pushes. This replay is read-only and creates no ledger.

Independent root review, full integrated suites, verified controller wakeup and production release remain separate. D24 runtime candidate repairs are outside this change. The result demonstrates restoration of existing authentic source inputs and a valid frozen regression dependency, not new observations, account-verified execution, profitable prediction, historical availability or live model approval.

## Independent review hardening

Independent review found that deeply nested raw JSON and nonfinite reference metadata could abort input-generation before an explicit manifest was written. The revised source uses the existing bounded JSON envelope (duplicate/nonfinite values and nesting above 32 rejected) and converts unhashable/nonfinite reference identity into unavailable evidence. Nonfinite immutable snapshots remain rejected whole; they are never normalized or exported with altered metadata. Seven new attack regressions pass, and all eight authentic original NHL sources still validate. Prior implementation bytes, manifest and logs remain archived under `evidence/`; no earlier evidence was erased.
