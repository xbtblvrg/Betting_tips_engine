"""Exact archived NHL outcomes reach isolated workers without new settlement."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from betting_engine import development_inputs as inputs
from test_development_inputs import seed


def outcome_source(root, *, game='2026020007', body=None):
    raw = json.dumps(body or {'id': int(game), 'gameState': 'OFF'}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    meta = {'archive': f'nhl_settlement/sources/{digest}.json', 'sha256': digest,
            'game_id': 'NHL:' + game, 'version': 'nhl-official-settlement-v1',
            'url': f'https://api-web.nhle.com/v1/gamecenter/{game}/landing',
            'fetched_at': '2026-09-30T05:00:00+00:00', 'start': '2026-09-30T02:00:00+00:00'}
    path = root / 'data' / meta['archive']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    attach(root, meta)
    return meta, path, raw


def attach(root, meta):
    with sqlite3.connect(root / 'data/engine.sqlite3') as db:
        db.execute('UPDATE system_decisions SET outcome_evidence=?',
                   (json.dumps({'score_scope': {'final_summary_source': meta}}),))


def nhl_sources(result):
    return [m for m in result['sources'] if m.get('archive', '').startswith('nhl_settlement/')]


def test_original_nhl_archive_is_exported_exactly_with_original_observation_time(tmp_path):
    seed(tmp_path)
    meta, path, raw = outcome_source(tmp_path)
    unrelated = path.parent / ('0' * 64 + '.json')
    unrelated.write_text('unreferenced source')
    before = (tmp_path / 'data/engine.sqlite3').read_bytes()
    dest = tmp_path / 'worker'
    result = inputs.export_development_inputs(tmp_path, dest)
    assert result['state'] == 'exported'
    assert nhl_sources(result) == [meta]
    assert (dest / 'data' / meta['archive']).read_bytes() == raw
    binding = next(f for f in result['files'] if f['path'] == 'data/' + meta['archive'])
    assert binding['sha256'] == meta['sha256'] and binding['bytes'] == len(raw)
    assert not (dest / 'data' / unrelated.relative_to(tmp_path / 'data')).exists()
    assert not (dest / 'data/engine.sqlite3').exists()
    assert (tmp_path / 'data/engine.sqlite3').read_bytes() == before
    assert path.read_bytes() == raw
    exported = json.loads((dest / 'data/development_inputs/system_decisions.json').read_text())
    assert exported['records'][0]['outcome_evidence']['score_scope']['final_summary_source'] == meta


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'symlink', 'parent_symlink', 'oversize'])
def test_unavailable_or_unsafe_original_source_is_explicit_and_not_substituted(tmp_path, failure, monkeypatch):
    seed(tmp_path)
    meta, path, raw = outcome_source(tmp_path)
    if failure == 'missing':
        path.unlink()
    elif failure == 'corrupt':
        path.write_bytes(b'{}')
    elif failure == 'symlink':
        other = tmp_path / 'elsewhere'; other.write_bytes(raw)
        path.unlink(); path.symlink_to(other)
    elif failure == 'parent_symlink':
        parent = path.parent; other = parent.with_name('moved')
        parent.rename(other); parent.symlink_to(other, target_is_directory=True)
    else:
        monkeypatch.setattr(inputs, 'MAX_NHL_SOURCE', len(raw) - 1)
    dest = tmp_path / 'worker'
    result = inputs.export_development_inputs(tmp_path, dest)
    assert result['state'] == 'partial' and not nhl_sources(result)
    assert any(e['kind'] == 'nhl_outcome_source' for e in result['errors'])
    assert not (dest / 'data' / meta['archive']).exists()
    assert not result['generation']['available']['nhl_settlement_sources']


@pytest.mark.parametrize('change', [
    {'archive': 'nhl_settlement/../../outside.json'},
    {'sha256': '0' * 64}, {'game_id': 'NHL:2026020006'},
    {'url': 'https://example.com/v1/gamecenter/2026020007/landing'},
    {'url': 'https://api-web.nhle.com/v1/gamecenter/2026020007/landing?token=secret'},
    {'fetched_at': '2100-01-01T00:00:00Z'}, {'fetched_at': '2026-09-30T01:00:00Z'},
    {'fetched_at': None}, {'start': []}, {'bytes': 1}, {'version': 'unknown'},
])
def test_invalid_reference_never_exports_raw_body(tmp_path, change):
    seed(tmp_path)
    meta, path, _ = outcome_source(tmp_path)
    altered = {**meta, **change}; attach(tmp_path, altered)
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert result['state'] == 'partial' and not nhl_sources(result)
    assert any(e['kind'] == 'nhl_outcome_source' for e in result['errors'])
    assert not (tmp_path / 'worker/data' / meta['archive']).exists()


def test_body_event_identity_is_checked_even_with_matching_hash(tmp_path):
    seed(tmp_path)
    meta, _, _ = outcome_source(tmp_path, body={'id': 2026020006})
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert not nhl_sources(result)
    assert any('identity mismatch' in e['reason'] for e in result['errors'])


def test_restoration_wakes_only_nhl_source_generation_without_rewriting_decisions(tmp_path):
    seed(tmp_path)
    _, path, raw = outcome_source(tmp_path)
    before = inputs.input_generation(tmp_path)
    path.unlink(); missing = inputs.input_generation(tmp_path)
    assert before['components']['nhl_settlement_sources'] != missing['components']['nhl_settlement_sources']
    for key in ('decision_history', 'stake_sources', 'scoped_models', 'validation_sources'):
        assert before['components'][key] == missing['components'][key]
    assert not missing['available']['nhl_settlement_sources']
    path.write_bytes(raw)
    assert inputs.input_generation(tmp_path) == before


def test_nhl_source_count_bound_is_explicit(tmp_path, monkeypatch):
    seed(tmp_path)
    first, _, _ = outcome_source(tmp_path)
    second, _, _ = outcome_source(tmp_path, game='2026020008')
    attach(tmp_path, [first, second])
    monkeypatch.setattr(inputs, 'MAX_BLOBS', 1)
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert len(nhl_sources(result)) == 1
    assert any(e['kind'] == 'nhl_outcome_source' and 'count bound' in e['reason'] for e in result['errors'])


def test_raw_source_with_credential_url_is_not_exported(tmp_path):
    seed(tmp_path)
    meta, _, _ = outcome_source(tmp_path, body={'id': 2026020007, 'link': 'https://user:secret@example.com/'})
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert not nhl_sources(result)
    assert not (tmp_path / 'worker/data' / meta['archive']).exists()
    assert 'user:secret' not in json.dumps(result)


@pytest.mark.parametrize('raw', [
    b'{"id":2026020007,"nested":' + b'[' * 1200 + b'0' + b']' * 1200 + b'}',
    b'{"id":2026020007,"nested":' + b'[' * 33 + b'0' + b']' * 33 + b'}',
    b'{"id":2026020007,"id":2026020007}',
    b'{"id":2026020007,"invalid":NaN}',
])
def test_malformed_bounded_source_json_is_reported_without_aborting_export(tmp_path, raw):
    seed(tmp_path)
    meta, _, _ = outcome_source(tmp_path)
    digest = hashlib.sha256(raw).hexdigest()
    meta.update(sha256=digest, archive=f'nhl_settlement/sources/{digest}.json')
    (tmp_path / 'data' / meta['archive']).write_bytes(raw)
    attach(tmp_path, meta)
    generation = inputs.input_generation(tmp_path)
    assert generation['available']['nhl_settlement_sources'] is False
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert result['state'] == 'partial' and not nhl_sources(result)
    assert any(e['kind'] == 'nhl_outcome_source' for e in result['errors'])
    assert not (tmp_path / 'worker/data' / meta['archive']).exists()
    assert (tmp_path / 'worker/data/development_inputs/manifest.json').exists()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_reference_rejects_whole_immutable_snapshot_without_aborting(tmp_path, value):
    seed(tmp_path)
    meta, _, _ = outcome_source(tmp_path)
    meta['bytes'] = value
    attach(tmp_path, meta)
    before = (tmp_path / 'data/engine.sqlite3').read_bytes()
    generation = inputs.input_generation(tmp_path)
    assert generation['available']['nhl_settlement_sources'] is False
    assert generation['available']['decision_history'] is False
    result = inputs.export_development_inputs(tmp_path, tmp_path / 'worker')
    assert result['state'] == 'partial' and not nhl_sources(result)
    assert any(e['kind'] == 'decision_history' for e in result['errors'])
    assert not (tmp_path / 'worker/data/development_inputs/system_decisions.json').exists()
    assert not (tmp_path / 'worker/data' / meta['archive']).exists()
    assert (tmp_path / 'data/engine.sqlite3').read_bytes() == before
