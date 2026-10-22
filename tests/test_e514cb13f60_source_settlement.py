"""Recheck current supplied public observation bytes without historical claims."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from betting_engine.contracts import contract_value, settlement_scores
from betting_engine.domain import Game
from betting_engine.period_data import attach_scores, parse_football

ROOT = Path(__file__).resolve().parents[1]


def test_supplied_nfl_schema_preserves_end_game_and_exact_period_loss():
    root = ROOT / 'data/sports_source_evidence'
    manifest = json.loads((root / 'manifest.json').read_text())
    source = next(s for s in manifest['sources'] if s['family'] == 'NFL/events' and s['season'] == 2026)
    path = root / source['blob_path']
    assert path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()
    raw = path.read_bytes()
    file_record = manifest['files'][source['blob_path']]
    assert hashlib.sha256(raw).hexdigest() == file_record['sha256'] == source['source_sha256']
    assert len(raw) == file_record['bytes']
    observed = json.loads((ROOT / 'tests/fixtures/e514cb13f60_nfl_endpoints.json').read_text())
    game = Game(**deepcopy(observed['game']))
    columns = list(observed['endpoints'][0])
    rows = pq.read_table(io.BytesIO(raw), columns=columns).to_pylist()
    plays = [row for row in rows if row['game_id'] == game.id.removeprefix('NFL:')]
    endpoints = [row for row in plays if row['quarter_end'] == 1 or row['desc'] == 'END GAME']
    assert endpoints == observed['endpoints']
    meta = {'url': source['url'], 'sha256': source['source_sha256'], 'fetched_at': source['fetched_at']}
    result = parse_football(plays, meta, {game.id: game})
    assert len(result) == 1
    assert attach_scores([game], {game.id: result[0]}) == 1
    # Hypothetical payoff test on observed scores, not a recorded wager.
    tip = {'market': 'quarter_4:totals', 'side': 'over', 'line': 13.5}
    home, away, evidence = settlement_scores(tip, game)
    assert (home, away) == (0, 10)
    assert evidence['source_sha256'] == source['source_sha256']
    assert evidence['fetched_at'] == source['fetched_at']
    assert contract_value(tip['market'], tip['side'], tip['line'], home, away) < 0
    assert contract_value(tip['market'], tip['side'], tip['line'], game.home_score, game.away_score) > 0
    assert parse_football(endpoints[:-1], meta, {game.id: game}) == []
    game.context['segment_scores']['quarter_4']['complete'] = False
    with pytest.raises(ValueError, match='completed segment'):
        settlement_scores(tip, game)
    assert source['source_published_at'] is None
    assert source['archive_timestamp_independently_authenticated'] is False
