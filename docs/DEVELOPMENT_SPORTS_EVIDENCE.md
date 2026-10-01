# Public native sports evidence for isolated development

`development_sports_evidence.export_development_sports_evidence` removes the
missing-source-copy obstacle for D20 native event adapters and D21 player identity/
participation work. It exports existing public collector archives; it does not
fetch data, read the wager database, access account/authentication state, approve
a sports fact, price a market or enable production models.

```python
from betting_engine.development_sports_evidence import export_development_sports_evidence

report = export_development_sports_evidence(
    data_dir,
    workspace / "data" / "sports_source_evidence",
    max_total_bytes=256 * 1024 * 1024,
    max_sources=64,
    max_nhl_sources=24,
)
```

The destination must be new or empty. It contains the exact original
`raw/<SHA256(url)>.json` pointer bytes, `raw/<SHA256(body)>.data` response bytes,
and a final manifest. Each manifest file entry has a SHA256 and byte count.
Do not mix sources from an older destination into a new task.

Only these exact public endpoint patterns are accepted, with no credentials,
query parameters, alternate hosts, arbitrary raw files or unrelated context:

- nflverse `.../releases/download/pbp/play_by_play_20xx.parquet`;
- official NHL `https://api-web.nhle.com/v1/gamecenter/<id>/play-by-play`;
- SportsDataverse `.../espn_<nba|wnba|nhl|nfl|mlb>_injuries/injuries_20xx.parquet`.

Metadata must have exactly the collector's seven public fields: url, fetched_at,
epoch, sha256, blob, bytes and http_status. Additional fields, including possible
authorization data, reject. URL-hash filenames, content-addressed blob names,
byte count, body hash, status 200 and aware observation time/epoch consistency are
verified. Future observation times, symlinks, traversal, oversized bodies and
duplicate/nonfinite JSON reject. NHL native game ID must match its endpoint;
both team IDs and the event array must be structurally valid. Parquet source
families need their actual expected native columns; injury league/season values
must match the URL. Missing player IDs and statuses remain unknown in raw input.

NFL and injury exports retain the current three seasons. NBA/NHL use season-ending
years, advancing the current anchor in July; September 2026 therefore includes
NHL injury season 2027. NFL/MLB/WNBA use calendar seasons. NHL per-game sources use
the latest actual observed UTC timestamps, with the configurable bound above.
Round-robin family selection prevents many NHL events from consuming the entire
budget before injury or NFL sources are considered.

Hard ceilings are 256 MiB and 64 sources. The byte ceiling reserves 2 MiB for the
manifest, so the entire successful exported directory remains within the limit.
Policy exclusions, malformed sources, missing family/seasons and exhausted
budgets are separate diagnostics. A zero-source export reports missing; partial
family coverage reports partial. `exported` concerns this bounded archive set,
never all leagues, website markets, current lineups or complete sports history.

`available_at` is exactly the actual archived fetched_at. Earlier game dates,
injury dates and as_of_date rows inside a recently captured body do not create
earlier source availability. Source publication remains unknown and collector
timestamps are not independently authenticated. No historical PIT snapshot is
invented. Native event completeness and current-game participation remain
unverified; D20/D21 must establish their identity, correction, scope, terminal and
current-event evidence contracts before downstream model use.

## Actual probe, 2026-09-29

The production raw archive was read without modification. The isolated probe at
`data/completion-15/sports-source-evidence-probe` exported 12 sources:

- three NFL play-by-play seasons, 2024–2026;
- five official NHL per-game play-by-play responses;
- four injury archives: MLB/NFL/WNBA 2026 and NHL 2027.

All 24 copied metadata/blob files were independently rehashed after export.
Payload bytes: 46,190,392; entire directory including manifest: 46,208,997.
Four older NFL sources (2020–2023) were intentionally outside the season window.
NBA injuries are absent from the local raw archive. The other injury families
have their current archive but lack the two older requested seasons. Overall
state therefore remains partial; these gaps are not hidden by the successful
event export. Full coverage/source hashes:
`data/completion-15/sports-source-evidence-probe-summary.json` and the probe's
manifest.json.

15 new tests verify exact byte preservation, actual availability, current winter
season handling, hash/length and filename checks, private metadata exclusion,
lookalike domains, symlinks, NHL source identity, injury league identity, UTC
ordering, round-robin bounds, missing/partial status and immutable destinations.
Only the new exporter, its new test file and this document belong to this increment.
