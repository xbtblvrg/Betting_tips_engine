"""Replay the supplied generation; never alias it to a missing older archive."""
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.player_archive import PlayerArchive
from betting_engine.player_rush_reconciliation import reconcile_nfl_rushing


@pytest.mark.parametrize('year,game', [
    (2024, '2024_01_ARI_BUF'), (2024, '2024_01_LA_DET'),
    (2025, '2025_01_ARI_NO'), (2025, '2025_02_NYG_DAL'),
    (2026, '2026_01_ARI_LAC'), (2026, '2026_01_NO_DET'),
])
def test_current_export_reconciles_without_fabricating_historical_availability(year, game):
    root = Path(__file__).resolve().parents[1] / 'data'
    sports_path = root / 'sports_source_evidence/manifest.json'
    players_path = root / 'players/source_evidence/manifest.json'
    if not sports_path.exists() or not players_path.exists():
        pytest.skip('Controller public observations not supplied')
    sports = json.loads(sports_path.read_bytes())
    players = json.loads(players_path.read_bytes())
    history = root / 'players/verified_history/runs' / players['history_run_id'] / 'sources.json'
    entries = json.loads(history.read_bytes())['sources']
    source = next(s for s in sports['sources']
                  if s['family'] == 'NFL/events' and s['season'] == year)
    weekly = next(s for s in entries if s['league'] == 'NFL'
                  and s['metadata']['url'].endswith(f'{year}.parquet'))

    def archive(base, body_path, body_hash, metadata_path, metadata_hash):
        body = (base / body_path).read_bytes()
        metadata_bytes = (base / metadata_path).read_bytes()
        assert hashlib.sha256(body).hexdigest() == body_hash
        assert hashlib.sha256(metadata_bytes).hexdigest() == metadata_hash
        metadata = json.loads(metadata_bytes)
        assert metadata['sha256'] == body_hash
        return PlayerArchive(body, body_hash, metadata['url'], metadata['fetched_at'],
                             body_path, 'archived_observation')

    events = archive(sports_path.parent, source['blob_path'], source['source_sha256'],
                     source['metadata_path'], source['metadata_sha256'])
    totals = archive(players_path.parent, weekly['body_path'], weekly['body_sha256'],
                     weekly['metadata_path'], weekly['metadata_sha256'])
    cutoff = max(events.observed_at, totals.observed_at)
    result = reconcile_nfl_rushing(events, game_id=game, as_of=cutoff, weekly=totals)
    assert result['state'] == 'historical_totals_reconciled', result['reasons']
    assert result['player_counts']['observed_rusher'] > 0
    assert result['publishable'] is False
    assert weekly['provider_publication_at'] is None
    assert weekly['timestamp_independently_authenticated'] is False
    if game == '2026_01_ARI_LAC':
        player = next(p for p in result['players'] if p['player_id'] == '00-0033119')
        assert player['kneels'] == 3
        assert player['reconciled_value'] == player['weekly_rushing_yards']
