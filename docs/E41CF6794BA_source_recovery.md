# E41CF6794BA — NCAA discovery repair and recovery validation

Run `20260930T232111108251-E41CF6794BA` integrates the existing candidate into
`stake_api.tournaments`. A failed core USA tournament endpoint now reports an
`NCAA_DISCOVERY` issue as well as the affected leagues. Malformed registry rows
retain valid siblings while marking coverage partial. Schema errors retain the
exact endpoint, response hash and original retrieval time; transport errors do
not invent a response. Verified absence of an enabled USA category remains
distinct from failure. The four-worker executor is joined before returning.

This is the bounded implementation completed here. It is not completion of M07,
recovery of every attached source gap, or a production release. The source is
byte-identical to the reviewed-by-this-worker prior candidate, SHA-256
`d653db3ffd843b5e26508926dd51ba951987f720fa76d1c7262db73288f29922`.
Comparison verified that changes from upstream are confined to registry parsing
and discovery. No rebase conflict file was supplied.

## Evidence

[Evidence JSON](E41CF6794BA_source_evidence.json) records exact hashes, source
times, contract identity, lifecycle totals, gap observations and validation logs.
All 301 development files, 384 decompressed Stake response references, 61
engineering files, 24 sports-source files, 397 player-source files, three
controller validation files and both 11-file frozen validation bundles match
their manifests. No required manifest-bound reference is missing or conflicting.
Development export errors are empty; no truncation field is supplied there.
The decision export explicitly reports `truncated=false`.

Sports/player bundles remain partial; the player bundle copied 198 sources and
skipped 10 under its byte bound. Five historical NHL play-by-play sources and an
injury observation are present, but do not reconcile the conflicting future
fixture schedule. Player history is not current participation evidence.

Replay of 29 actual registry responses retains professional paths, NCAA football
and women's volleyball. The catalogue contains 3,338 market rows. This is
retrospective feed coverage, not current availability or complete website coverage.
Unknown college sports remain inventory without automatic model approval.

The archived NCAAF full-game `Winner (Incl. Overtime)` example retains fixture
`3d4e4caa-9d0c-49e1-94d6-e7e4f35caead`, market
`e491c3a5-548e-486c-8a88-459a162eeb3c`, Houston Cougars outcome
`ed7a977e-47ee-4ffb-846f-4182f0a291f4`, decimal 1.17 and observation
`2026-09-30T22:59:48.142376+00:00`. Its raw contract was compared with the
hash-verified endpoint body. Discovery does not infer a line, outcome, settlement
rule or execution approval. Exact-scope pricing and settlement remain unchanged.

The immutable export contains 159 original decisions: 45 losses, 38 wins,
3 pushes and 73 open records, grouped into 32 league/game clusters (largest 14).
Settled decision P&L is **USD −9.3478 after stakes**. These are descriptive
decision-level totals, not ticket reconciliation, account verification or
independent performance samples. Operating costs are unknown. All original
payloads, odds, stakes, outcomes and issuance dates remain byte-preserved.

Knowledge cards K03 and K08 informed source-time separation and game clustering.
Only those cards were read; no full-text literature understanding is claimed.
Export time never establishes historical availability. No fit, recipe search,
chronological split change, test-set reuse or model promotion occurred.

## Validation

The prior run's exact identity hashes match the recovery request. Its retained
Python traceback diagnosed missing controller files. Those exact files are now
supplied and hash-verified; neither their contents nor their tests were modified.
The runtime/release-failure manifests contain no additional diagnostic references.

- Before integration: **42 failed, 7 passed** discovery regressions
  ([log](E41CF6794BA_before.log)).
- After integration: **79 passed** targeted discovery, replay, operating-gap,
  college API and service-profile tests ([log](E41CF6794BA_targeted.log)).
- Full Python/API suite: **4,033 passed; 5 subtests passed**, one existing
  Starlette/httpx deprecation warning ([log](E41CF6794BA_python.log)).
- Full browser DOM suite: **216 passed** using the unchanged package script's
  exact `node --test tests/web.test.mjs` command
  ([log](E41CF6794BA_dom_direct.log)). The npm wrapper exited 255 after its
  command banner without a diagnostic; this failed attempt is retained in
  [its own log](E41CF6794BA_dom.log).

Python used the required interpreter with `PYTHONPATH=src OMP_NUM_THREADS=1`.
New test files restore the prior candidate regressions, including malformed
siblings, every core USA endpoint, source timestamps, transport errors,
read-only source replay and sportsbook integration. A persistent discovery gap
survives a refresh request and resolves only after a later clean simulated
publication. Historical NFL reconciliation replay remains explicitly unapproved
for production props and does not fabricate source availability.

## Developer audit and remaining work

All 186 pre-existing test/fixture files are byte-identical. Only `stake_api.py`
changed among existing source/config/script files; two new test files were added.
All 301 development inputs remain unchanged after validation. The empty disposable
workspace database created by API tests was checked read-only, closed and removed.
No production database or service was accessed.

The bounded candidate implementation and validation are complete. Independent
controller review and separately authorized root integration remain pending.
There is no exact passive release profile and discovery is disabled; this worker
has no release authority. The controller must verify a fresh publication locally
and through Tailscale after authorized integration.

NHL source and schedule gaps remain unresolved. Frozen publication 3724 at
`2026-09-30T23:21:06.973446+00:00` still records Utah Mammoth–Chicago Blackhawks
at independent start `2026-10-02T01:30:00Z` versus Stake
`2026-10-02T01:00:00+00:00`. Obtain later independent schedule evidence and verify
a later publication before resolving these gaps. Prospective settlement source
archiving remains a separate unfinished increment; retained outcome references
do not authenticate historical raw evidence.

The original deadline **2026-10-01T07:42:40.409512+00:00** is unchanged. The
recovery checkpoint **2026-09-30T23:19:00.551329+00:00** preceded dispatch and
was not moved. Remaining M07 acceptance must still be reported at its deadline.

EvidenceGuard, publication gates, exact contracts, model validation, quote lifetime,
USD1–5 sizing, null daily aggregate cap, original tickets and the project contract
are unchanged. The 20+ daily issuance objective and measured profitability are
not established by this repair. No quota padding, invented bankroll, automatic
wagering, model activation, helper process, credentials, account access, messaging,
production service control or parent-project data access occurred. Only expressly
supplied previous-run evidence was read outside this workspace.
