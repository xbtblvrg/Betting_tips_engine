"""Read-only complete-cohort Statistics boundary, child and HTTP regressions."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from betting_engine.statistics_worker import StatisticsRenderer, build_statistics_response, read_snapshot
from test_statistics import fixtures

AS_OF = datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc)  # October 1 in Budapest.
TABLES = {
    'bets': 'id INTEGER PRIMARY KEY, tip_id TEXT, created_at TEXT, stake REAL, odds REAL, platform TEXT, status TEXT, pnl REAL, payload TEXT',
    'system_decisions': 'family TEXT PRIMARY KEY, tip_id TEXT, issued_at TEXT, status TEXT, pnl REAL, payload TEXT, outcome_evidence TEXT',
    'bet_outcomes': 'bet_id INTEGER PRIMARY KEY, league TEXT, market TEXT, model_version TEXT, result TEXT, probability REAL, push_probability REAL, brier REAL, log_loss REAL, evidence TEXT',
}


def _insert(connection, table, row):
    keys = list(row)
    values = [json.dumps(row[k]) if isinstance(row[k], (dict, list)) else row[k] for k in keys]
    connection.execute(f"INSERT OR IGNORE INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", values)


def database(tmp_path, rows=()):
    project = tmp_path / 'project'
    data = project / 'data'
    data.mkdir(parents=True)
    path = data / 'engine.sqlite3'
    connection = sqlite3.connect(path)
    try:
        for table, columns in TABLES.items():
            connection.execute(f'CREATE TABLE {table} ({columns})')
        connection.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
        connection.execute('INSERT INTO settings VALUES (?,?)', ('bankroll', '{"value":null}'))
        connection.execute('CREATE TABLE learning_jobs (id TEXT PRIMARY KEY, state TEXT)')
        connection.execute("INSERT INTO learning_jobs VALUES ('outcome_review','queued')")
        for row in rows:
            for table, item in zip(TABLES, row):
                if item is not None:
                    _insert(connection, table, item)
        connection.commit()
    finally:
        connection.close()
    (project / 'config').mkdir()
    (project / 'config/settings.json').write_text('{"max_bet":5,"daily_risk_limit":null}')
    (data / 'learning_worker.json').write_text('{"jobs":{"outcome_review":{"state":"queued"}}}')
    return path, data, project


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def report(path, data, **filters):
    return build_statistics_response(path, data, filters, now=AS_OF)


def cohort(bid, start, league='NHL', source='assumed'):
    row = fixtures(bid, game=f'game-{bid}')
    for entry in row[:2]:
        p = entry['payload']
        p.update(start=start, league=league)
        p['tracking_source'] = {'assumed': 'auto_assumed_placed', 'reported': 'user_reported_placed'}.get(source, 'other')
        if source == 'verified':
            p['execution_verified'] = True
        if source == 'manual':
            p.pop('tracking_source')
            p['ledger_contract'] = 'manual'
    row[2]['league'] = league
    return row


def test_snapshot_and_worker_include_every_ticket_beyond_500_without_side_effects(tmp_path):
    path, data, project = database(tmp_path, [fixtures(i, game=f'g{i}') for i in range(1, 602)])
    before = fingerprints(project)
    snapshot = read_snapshot(path)
    assert {k: len(v) for k, v in snapshot.items()} == {'bets': 601, 'system_decisions': 601, 'bet_outcomes': 601}
    assert isinstance(snapshot['bets'][0]['payload'], dict)
    assert isinstance(snapshot['system_decisions'][0]['outcome_evidence'], dict)
    assert isinstance(snapshot['bet_outcomes'][0]['evidence'], dict)
    result = report(path, data)
    for section in ('ticket_ledger', 'system_decisions'):
        summary = result[section]['summary']
        assert (summary['count'], summary['settled'], summary['settled_games']) == (601, 601, 601)
        assert summary['settled_stake'] == 1202 and summary['pnl'] == 1803
    assert len(result['ticket_ledger']['closed_tickets']) == 601
    assert fingerprints(project) == before


@pytest.mark.parametrize('period,expected,cutoff', [
    ('today', {2}, '2026-10-01'),
    ('7d', {1, 2, 4}, '2026-09-25'),
    ('30d', {1, 2, 4, 5, 6}, '2026-09-02'),
    ('all', set(range(1, 9)), None),
])
def test_budapest_match_start_boundaries_and_unknown_dates(tmp_path, period, expected, cutoff):
    starts = ['2026-09-30T21:59:59Z', '2026-09-30T22:00:00Z', '2026-10-01T22:00:00Z',
              '2026-09-24T22:00:00Z', '2026-09-24T21:59:59Z', '2026-09-01T22:00:00Z',
              '2026-09-01T21:59:59Z', None]
    path, data, _ = database(tmp_path, [cohort(i, start) for i, start in enumerate(starts, 1)])
    result = report(path, data, period=period)
    assert {r['id'] for r in result['ticket_ledger']['closed_tickets']} == expected
    assert result['filters']['date_from'] == cutoff
    assert result['filters']['date_to'] == ('2026-10-01' if period != 'all' else None)
    assert result['filters']['timezone'] == 'Europe/Budapest'
    assert result['filters']['excluded_unknown_match_dates'] == {
        'tickets': int(period != 'all'), 'decisions': int(period != 'all')}


@pytest.mark.parametrize('period', ['today', '7d', '30d'])
def test_naive_or_malformed_start_has_no_invented_timezone_in_date_cohort(tmp_path, period):
    rows = [cohort(1, '2026-09-30T22:05:00'), cohort(2, 'not-a-date'),
            cohort(3, '2026-10-01T00:05:00+02:00')]
    path, data, _ = database(tmp_path, rows)
    selected = report(path, data, period=period)
    assert [r['id'] for r in selected['ticket_ledger']['closed_tickets']] == [3]
    assert selected['filters']['excluded_unknown_match_dates'] == {'tickets': 2, 'decisions': 2}
    all_rows = report(path, data)
    assert all_rows['ticket_ledger']['summary']['count'] == 3
    assert all_rows['system_decisions']['summary']['count'] == 3
    assert {r['id'] for r in all_rows['ticket_ledger']['closed_tickets'] if r['start'] is None} == {1, 2}


@pytest.mark.parametrize('source', ['assumed', 'reported', 'verified', 'manual', 'unknown'])
def test_execution_source_and_league_filters_share_canonical_classifier(tmp_path, source):
    names = ['assumed', 'reported', 'verified', 'manual', 'unknown']
    rows = [cohort(i, '2026-09-30T22:05:00Z', source=name) for i, name in enumerate(names, 1)]
    rows.append(cohort(6, '2026-09-30T22:05:00Z', league='NFL', source=source))
    path, data, _ = database(tmp_path, rows)
    result = report(path, data, league='NHL', execution=source, period='today')
    assert [r['id'] for r in result['ticket_ledger']['closed_tickets']] == [names.index(source) + 1]
    assert result['ticket_ledger']['closed_tickets'][0]['execution_source'] == source
    assert result['system_decisions']['summary']['count'] == 1
    assert result['available_leagues'] == ['NFL', 'NHL']


def test_reported_duplicate_money_does_not_resize_or_duplicate_original_decision(tmp_path):
    first = fixtures()
    duplicate = deepcopy(first)
    duplicate[0].update(id=2, stake=1, odds=2, pnl=1)
    duplicate[0]['payload']['tracking_source'] = 'user_reported_placed'
    duplicate[2]['bet_id'] = 2
    path, data, _ = database(tmp_path, [first, duplicate])
    all_rows = report(path, data)
    assert all_rows['ticket_ledger']['summary']['pnl'] == 4
    assert all_rows['ticket_ledger']['summary']['count'] == 2
    assert all_rows['system_decisions']['summary']['count'] == 1
    selected = report(path, data, execution='reported')
    assert selected['ticket_ledger']['summary']['count'] == 1
    assert selected['ticket_ledger']['summary']['settled_stake'] == 1
    assert selected['ticket_ledger']['summary']['pnl'] == 1
    assert selected['system_decisions']['summary']['settled_stake'] == 2
    assert selected['system_decisions']['summary']['pnl'] == 3


def test_orphans_remain_global_and_filtered_outcomes_do_not_become_false_orphans(tmp_path):
    a = cohort(1, '2026-09-30T22:05:00Z', 'NHL')
    b = cohort(2, '2026-09-30T22:05:00Z', 'NFL')
    orphan = deepcopy(a[2]); orphan['bet_id'] = 9999
    path, data, _ = database(tmp_path, [a, b, (None, None, orphan)])
    entire = report(path, data)
    assert entire['resolution_audit']['counts']['orphan_outcome'] == 1
    assert entire['global_integrity']['orphan_bet_ids'] == [9999]
    selected = report(path, data, league='NHL')
    assert selected['ticket_ledger']['summary']['count'] == 1
    assert selected['resolution_audit']['counts'].get('orphan_outcome', 0) == 0
    assert selected['global_integrity']['orphan_outcome_count'] == 1
    assert selected['global_integrity']['orphan_bet_ids'] == [9999]


@pytest.mark.parametrize('table,column', [('bets', 'payload'), ('system_decisions', 'payload'),
    ('system_decisions', 'outcome_evidence'), ('bet_outcomes', 'evidence')])
def test_malformed_stored_json_fails_without_substituting_successful_empty_totals(tmp_path, table, column):
    path, data, project = database(tmp_path, [fixtures()])
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'UPDATE {table} SET {column}=?', ('{"truncated":',)); connection.commit()
    finally:
        connection.close()
    before = fingerprints(project)
    with pytest.raises(ValueError, match='decoded dictionaries'):
        report(path, data)
    assert fingerprints(project) == before


def test_missing_database_read_does_not_create_database_or_directories(tmp_path):
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        read_snapshot(missing)
    assert not missing.exists()


def test_single_read_transaction_cannot_mix_before_and_after_settlement(tmp_path, monkeypatch):
    row = fixtures(status='open', pnl=None)
    path, _, _ = database(tmp_path, [(row[0], row[1], None)])
    original = sqlite3.connect
    setup = original(path); setup.execute('PRAGMA journal_mode=WAL'); setup.close()
    writer_ran = []

    def connect(*args, **kwargs):
        assert kwargs['uri'] is True and args[0].endswith('?mode=ro')
        reader = original(*args, **kwargs)
        def trace(statement):
            if statement == 'SELECT * FROM system_decisions':
                writer = original(path)
                try:
                    writer.execute("UPDATE bets SET status='won',pnl=3")
                    writer.execute("UPDATE system_decisions SET status='won',pnl=3")
                    _insert(writer, 'bet_outcomes', fixtures()[2]); writer.commit(); writer_ran.append(True)
                finally:
                    writer.close()
        reader.set_trace_callback(trace)
        return reader

    monkeypatch.setattr(sqlite3, 'connect', connect)
    snapshot = read_snapshot(path)
    assert writer_ran == [True]
    assert snapshot['bets'][0]['status'] == snapshot['system_decisions'][0]['status'] == 'open'
    assert snapshot['bet_outcomes'] == []
    # The concurrent writer committed, but none of its three changes leaked into this snapshot.
    check = original(path)
    try:
        assert check.execute('SELECT status FROM bets').fetchone()[0] == 'won'
        assert check.execute('SELECT count(*) FROM bet_outcomes').fetchone()[0] == 1
    finally:
        check.close()


def test_actual_statistics_child_is_fresh_and_preserves_every_project_file(tmp_path):
    path, data, project = database(tmp_path, [fixtures()])
    async def run():
        renderer = StatisticsRenderer()
        try:
            before = fingerprints(project)
            first = await renderer.get(path, data, {}, '{}')
            assert json.loads(first.body)['ticket_ledger']['summary']['count'] == 1
            assert gzip.decompress(first.gzip_body) == first.body
            assert first.actionable_until is None and first.version == 0
            assert fingerprints(project) == before
            connection = sqlite3.connect(path)
            try:
                for table, row in zip(TABLES, fixtures(2)):
                    _insert(connection, table, row)
                connection.commit()
            finally:
                connection.close()
            changed = fingerprints(project)
            second = await renderer.get(path, data, {}, '{}')
            assert json.loads(second.body)['ticket_ledger']['summary']['count'] == 2
            assert fingerprints(project) == changed
        finally:
            await renderer.close()
    asyncio.run(run())


def test_statistics_worker_has_no_runtime_import_or_project_write_permission(tmp_path):
    path, data, project = database(tmp_path, [fixtures()])
    output = tmp_path / 'output'; output.mkdir(); before = fingerprints(project)
    script = '''
import os,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def audit(event,args):
    if event=='import' and args[0] in ('betting_engine.engine','betting_engine.server','betting_engine.storage','betting_engine.dashboard_worker','betting_engine.dashboard_payload'):
        raise AssertionError('Statistics worker imported runtime: '+args[0])
    if event=='open' and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(root) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
            raise AssertionError('Statistics worker attempted project write')
    if event=='sqlite3.connect' and not str(args[0]).endswith('?mode=ro'):
        raise AssertionError('Statistics worker attempted writable SQLite')
sys.addaudithook(audit)
from betting_engine.statistics_worker import main
sys.argv=['worker',sys.argv[2],sys.argv[3],sys.argv[4],'{}']
main()
'''
    completed = subprocess.run([sys.executable, '-c', script, str(project), str(path), str(data), str(output)],
                               capture_output=True, text=True, timeout=12)
    assert completed.returncode == 0, completed.stderr
    assert json.loads((output / 'response.json').read_text())['ticket_ledger']['summary']['pnl'] == 3
    assert fingerprints(project) == before


@pytest.fixture
def api(tmp_path, monkeypatch):
    from betting_engine import server, providers
    from fastapi.testclient import TestClient
    path, data, project = database(tmp_path, [fixtures()])
    monkeypatch.setattr(server, 'engine', SimpleNamespace(store=SimpleNamespace(path=path)))
    monkeypatch.setattr(providers, 'DATA', data)
    monkeypatch.setattr(server, 'statistics_renderer', StatisticsRenderer())
    # No lifespan: an HTTP read must not start the betting loop in this fixture.
    return TestClient(server.app), server, path, project


@pytest.mark.parametrize('query', ['period=yesterday', 'period=0', 'execution=confirmed',
    'league=nhl', 'league=NHL%3BDROP', 'league=' + 'X'*21])
def test_api_invalid_filters_never_start_statistics_job_or_mutate_inputs(api, monkeypatch, query):
    client, server, _, project = api
    class Forbidden:
        async def get(self, *args):
            raise AssertionError('Invalid query started a child')
    monkeypatch.setattr(server, 'statistics_renderer', Forbidden())
    before = fingerprints(project)
    response = client.get('/api/statistics?' + query)
    assert response.status_code == 422
    assert fingerprints(project) == before


@pytest.mark.parametrize('encoding', ['identity', 'gzip;q=0', 'gzip'])
def test_actual_statistics_get_preserves_ledger_settings_jobs_and_encoding(api, encoding):
    client, _, _, project = api
    before = fingerprints(project)
    response = client.get('/api/statistics?league=NHL&execution=assumed', headers={'Accept-Encoding': encoding})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['ticket_ledger']['summary']['pnl'] == 3
    assert result['system_decisions']['summary']['count'] == 1
    assert result['filters']['league'] == 'NHL' and result['filters']['execution'] == 'assumed'
    assert response.headers['cache-control'] == 'no-store'
    assert 'accept-encoding' in response.headers['vary'].lower()
    assert response.headers['content-encoding'] == ('gzip' if encoding == 'gzip' else 'identity')
    assert fingerprints(project) == before


def test_api_malformed_next_generation_is_503_not_cached_success_or_zero_pnl(api):
    client, _, path, project = api
    assert client.get('/api/statistics').status_code == 200
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE bets SET payload='{truncated'"); connection.commit()
    finally:
        connection.close()
    before = fingerprints(project)
    response = client.get('/api/statistics')
    assert response.status_code == 503
    assert 'ticket_ledger' not in response.json()
    assert fingerprints(project) == before


@pytest.mark.parametrize('name', ['statistics.py', 'statistics_worker.py', 'outcome_feedback.py'])
def test_statistics_and_outcome_modules_are_protected_from_automatic_developer_edits(name):
    from betting_engine.development import validate_changes
    path = 'src/betting_engine/' + name
    with pytest.raises(ValueError, match='Protected file changed'):
        validate_changes({path: 'original'}, {path: 'changed', 'tests/new_statistics_test.py': 'new'})
