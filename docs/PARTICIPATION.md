# D03 — Experimental participation and role evidence

`betting_engine.participation.validate_participation` implements a pure, typed
current-event evidence validator. It reads no files or clock, makes no requests,
and has no production call site. It does not forecast participation, price props,
infer exposure, settle wagers or approve instructions. The bounded validator is
implemented; production participation support remains unfinished.

## Evidence contract

The frozen dataclasses `ParticipationIdentity`, `SourceDocument`,
`ParticipationEvidence`, `EvidenceReview` and `ParticipationAssessment` preserve
identity, provenance, chronology and uncertainty. `Participation`, `Role` and
`Uncertainty` are explicit enums. Results include every input's review, including
rejected claims; input documents are never modified.

An identity contains exact `league`, league-prefixed `game_id`, `player_id`,
`team_id`, `stat_group`, `identity_namespace`, and timezone-aware scheduled `start`.
All must match the query. Names, integer coercion, team aliases, transferred
players' old teams and cross-provider ID guesses cannot substitute for a match.
Equivalent timezone offsets match; schedule discrepancies remain visible.

`SourceDocument` holds raw `body` bytes, their `source_sha256`, HTTPS `source_url`,
timezone-aware `observed_at`, nonempty `archive_reference`, and `verification`
(`fixture`, `unverified`, or `archived_observation`). The SHA-256 is recomputed
before parsing. Verification labels are retained caller claims, not certifications.
The caller must authenticate the archive timestamp, source and identity mapping.
Passing these deterministic checks never establishes production support.

`decode_document` reads **our internal JSON interchange**, not a purported NBA,
ESPN, NHL or other vendor response. Its body requires:

| Fields | Meaning |
| --- | --- |
| `schema`, `evidence_kind` | Exactly `participation-evidence-v1`, `current_event_report`. |
| Identity fields above | Explicit target event and participant, including provider namespace. |
| `participation` | `unknown`, `available`, `out`, `dnp`, `questionable`, or `doubtful`. |
| `role` | Explicit family-compatible role from the table below; otherwise `unknown`. |
| `uncertainty` | `unknown`, `reported`, or `confirmed`, as asserted by the source. `conflict` is reserved for validator output. |
| `valid_until` | Explicit expiry of this exact claim. Never filled from a default or historical fetch time. |
| `source_published_at` | Optional aware timestamp for this version; absent/null remains null. |

Duplicate JSON fields, malformed enums and naive timestamps are rejected. A
source's reported DNP is retained as a distinct nonparticipation label; OUT is not
silently renamed DNP. Neither label proves final participation or Stake settlement.
An unavailable player's simultaneous non-unknown role is rejected as inconsistent.
Unknown uncertainty suppresses its claim in the resolved result. Reported or
questionable claims retain their uncertainty; no numerical confidence is invented.

| Separate league/model identity | Permitted roles in addition to `unknown` |
| --- | --- |
| NBA, WNBA, NCAAM, NCAAW / `player` | `starter`, `bench` |
| NFL, NCAAF / `player` | `starter`, `bench` |
| NHL / `player` | `skater`, `starting_goalie`, `backup_goalie` |
| MLB / `pitching` | `starting_pitcher`, `relief_pitcher` |
| MLB / `hitting` | `batting_lineup`, `bench` |

These are evidence vocabularies, not production model approvals. NCAA football,
men's basketball and women's basketball match the existing separate model family
IDs. Dynamically encountered other NCAA sports retain their query identity and
`unsupported_model_family`; the validator never borrows a professional model or
pretends that the known NCAA families exhaust feed inventory. Batting order,
minutes, snaps, usage, position and exposure projections require further evidence.

## Time and conflict handling

The caller supplies an aware `as_of` and a positive `max_age: timedelta` research
policy. This introduces no production threshold or stake/quote policy change.
Require observation no later than cutoff, publication (when present) no later than
observation, and expiry strictly after observation and cutoff. Age equal to
`max_age` is stale. Re-fetching an old published claim cannot renew freshness.
Started events return unknown for this pregame evaluator. Resolved expiry is the
earliest contributing claim expiry, observation/publication age limit or start.
Missing publication time remains an explicit limitation: observation establishes
only a conservative availability bound, not a publication timestamp.

Identity/time-invalid rows are excluded with reasons before conflict resolution.
Appending a future revision cannot erase usable current evidence. Multiple
eligible reports with different non-unknown participation or role values produce
`source_conflict`, the conflicting fields, unknown resolved participation/role and
`conflict` uncertainty. An OUT/DNP claim also conflicts with another report's known
role. This includes differing revisions of one source. No source priority, latest
report selection or majority vote hides disagreement. Duplicate agreeing reports
remain visible but do not increase confidence. Unknown roles cannot be filled
from stale observations. Rejected/stale claims remain in reviews even when another
source supplies usable evidence.

## Local observations and test evidence

The supplied `player_data.observation` / `write_partition` schema and the first
row of each 2026 NBA, WNBA, NHL, MLB and NFL Parquet partition were inspected in
this workspace. They contain `played`, `starter`, `minutes`, JSON-encoded `stats`,
`source_sha256`, `source_url`, and `source_fetched_at`. They do not provide the
current-event evidence contract. No publication/expiry fields were backfilled.

Passing those five real rows unchanged through the validator returned
`participation=unknown`, `role=unknown`, and
`historical_log_not_current_evidence` for all five. The historical participation
audit retained True for NBA/WNBA/NHL/MLB and None for NFL. This is a five-row schema
check, not a full-partition availability audit. Test fixtures separately cover
historical False (DNP), True, and None; none becomes current availability.
Audit target IDs and cutoffs are illustrative, not observed fixtures or source
publication times. No current roster/lineup source was supplied or live-verified.

Validation command:

```sh
PYTHONPATH=src OMP_NUM_THREADS=1 /Users/belavarga/Betting_tips_engine/.venv/bin/python -m pytest -q tests/test_participation.py tests/test_player_history.py tests/test_market_expansion.py tests/test_hockey_source_recovery.py
```

Result: **114 passed**, including **50 new participation cases** and 64 existing
history/ingestion regressions. New cases exercise each league/family, all identity
fields, hashes, timestamps and expiry boundaries, unknown/DNP/out distinctions,
multiple-source conflicts, revisions, future/stale exclusion, historical rows,
uncertainty, malformed inputs and dynamic unsupported NCAA identities. Current
report fixtures use `example.invalid` and are explicitly labeled synthetic.

Methodological sources read locally: [K03](../knowledge/cards/K03-adat-ido-forras.md)
§1–3 (separate times, joins and lineup uncertainty),
[K04](../knowledge/cards/K04-mlb.md) §1–2 (pitcher/lineup versus historical skill),
[K05](../knowledge/cards/K05-nba-wnba.md) §2 (lineup effects),
[K06](../knowledge/cards/K06-nfl.md) §1 (depth-chart evidence),
[K07](../knowledge/cards/K07-nhl.md) §1 (confirmed goalie starts), and
[K12](../knowledge/cards/K12-ellentmondasok.md) (unresolved evidence gaps).
These cards provide methodological context, not current player facts or weights.

## Remaining work

Collect independently timestamped current reports for each league/family; retain
original bytes and version-specific publication evidence where provided. Implement
and live-check actual provider adapters and explicit ID mappings into this
interchange, with documented expiry policies. Authenticate archive references;
the pure validator checks bytes and chronology, not remote source truth. Extend
new NCAA sports only with their own source and role semantics. Validate exposure
models and participation-aware settlement separately, then integrate through a
separately reviewed release of protected paths. Production services, live wager
data, UI, publication and protected code were untouched. No network/live
publication or Tailscale checks were performed. The controller's full fixed suites
and independent review remain pending; daily issuance and profit are not measured
or established by this increment.
