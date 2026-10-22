"""Offline component/registry integration; synthetic clocks are not source times."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from betting_engine.component_health import audit_components


NOW = datetime(2026, 10, 1, 0, 4, 41, 669250, tzinfo=timezone.utc)
PUBLICATION_COMPONENTS = {
    'Recommendation publication', 'Independent sports sources', 'EvidenceGuard',
    'Model forecasts', 'Inning and period models', 'Stake fixture endpoints',
    'NCAA discovery', 'Market implementation',
}


def publication(stamp=None):
    return {
        'generated_at': NOW.isoformat() if stamp is None else stamp,
        'models': {'NHL': {'version': 'unchanged-model'}},
        'period_models': {'NHL:period_1': {'version': 'unchanged-period-model'}},
        'status': {
            'data_sources': [{'source': 'NHL/2026', 'ok': True,
                             'fetched_at': '2026-09-28T12:00:00Z',
                             'source_published_at': None}],
            'governance': {'guard': {'checked': 2, 'passed': 1, 'rejected': 1}},
        },
        'api_audit': {'fixtures': [{'responses': {'odds': {
            'sha256': 'synthetic-source-hash', 'archive': 'synthetic-archive',
        }}}]},
    }


def audit(payload, now=NOW, catalog=None):
    report = audit_components(payload, {'checked_at': now.isoformat()},
                              catalog or {'checked_at': now.isoformat(), 'issues': []},
                              {'checked_at': now.isoformat()}, now)
    return report, {c['component']: c for c in report['checks']}


@pytest.mark.parametrize('seconds,expected', [(0, 'fresh'), (300, 'fresh'),
                                              (300.000001, 'stale'), (-0.000001, 'future')])
def test_expired_publication_cannot_become_healthy_from_new_poll(seconds, expected):
    stamp = (NOW - timedelta(seconds=seconds)).isoformat()
    report, checks = audit(publication(stamp))
    for name in PUBLICATION_COMPONENTS:
        proof = checks[name]['publication_freshness']
        assert proof == {'timestamp_field': 'generated_at', 'observed_at': stamp,
                         'age_seconds': seconds, 'maximum_age_seconds': 300,
                         'state': expected}
        if expected == 'fresh':
            assert checks[name]['state'] == ('observed' if name == 'Market implementation' else 'healthy')
        else:
            assert checks[name]['state'] in {'stale', 'degraded'}
            assert 'Publication evidence is ' + expected in checks[name]['detail']
    # Separate clocks: old publication evidence does not age a newer catalogue.
    assert checks['Full market catalogue']['state'] == 'healthy'
    assert checks['Learning worker']['state'] == 'healthy'
    assert report['checked_at'] == NOW.isoformat()


@pytest.mark.parametrize('stamp,expected', [(None, 'missing'), ('invalid', 'invalid'),
                                          ('', 'invalid'), (True, 'invalid'),
                                          (123, 'invalid'), ({}, 'invalid')])
def test_missing_or_malformed_publication_time_fails_closed_without_crashing(stamp, expected):
    payload = publication()
    payload['generated_at'] = stamp
    report, checks = audit(payload)
    assert report['state'] == 'degraded'
    proof = checks['Independent sports sources']['publication_freshness']
    assert proof['state'] == expected and proof['age_seconds'] is None
    assert proof['observed_at'] == stamp
    assert checks['Independent sports sources']['state'] == 'stale'
    json.dumps(report, allow_nan=False)


def test_expired_evidence_retains_failures_and_does_not_claim_empty_valid_calendar():
    payload = publication((NOW - timedelta(minutes=20)).isoformat())
    failure = {'source': 'NCAAF/injuries', 'ok': False, 'state': 'not_integrated',
               'error': 'College availability data is not yet an implemented model input.'}
    payload['status']['data_sources'].append(failure)
    payload['status']['operations'] = {'gaps': [{'action': 'market_research'}]}
    payload['api_audit'] = {'fixtures': []}
    before = deepcopy(payload)
    _, checks = audit(payload)
    assert checks['Independent sports sources']['state'] == 'degraded'
    assert checks['Independent sports sources']['source_failures'] == [failure]
    assert checks['Stake fixture endpoints']['state'] == 'stale'
    assert checks['Market implementation']['state'] == 'needs_implementation'
    assert payload == before
    checks['Independent sports sources']['source_failures'][0]['error'] = 'modified'
    checks['Independent sports sources']['publication_freshness']['observed_at'] = 'modified'
    assert payload == before
    assert checks['Model forecasts']['publication_freshness']['observed_at'] == payload['generated_at']


def test_publication_time_is_not_assigned_to_independent_source_availability():
    payload = publication()
    before = deepcopy(payload)
    _, checks = audit(payload)
    assert checks['Independent sports sources']['state'] == 'healthy'
    assert payload == before
    assert payload['status']['data_sources'][0]['source_published_at'] is None
    assert 'source_published_at' not in checks['Independent sports sources']['publication_freshness']


@pytest.mark.parametrize('seconds,expected', [(1800, 'fresh'), (1800.000001, 'stale'), (-1, 'future')])
def test_catalogue_uses_unchanged_independent_freshness_window(seconds, expected):
    catalog = {'checked_at': (NOW - timedelta(seconds=seconds)).isoformat(),
               'fixtures_fetched': 107, 'discovered_fixtures': 107,
               'complete_endpoint_responses': 214, 'issues': []}
    _, checks = audit(publication(), catalog=catalog)
    row = checks['Full market catalogue']
    assert row['freshness']['state'] == expected
    assert row['freshness']['maximum_age_seconds'] == 1800
    assert row['state'] == ('healthy' if expected == 'fresh' else 'degraded')
    assert '107/107' in row['detail'] and row['issues'] == []
    if expected != 'fresh':
        assert 'Catalogue evidence is ' + expected in row['detail']


def test_catalogue_missing_and_invalid_time_differ_from_endpoint_failure():
    issue = {'reason': 'stake_fixture_endpoint_error', 'fixture': 'synthetic-fixture'}
    for catalog, expected in [({'issues': [issue]}, 'missing'),
                              ({'checked_at': 42, 'issues': [issue]}, 'invalid')]:
        _, checks = audit(publication(), catalog=catalog)
        row = checks['Full market catalogue']
        assert row['freshness']['state'] == expected
        assert row['issues'] == [issue]
        assert 'stake_fixture_endpoint_error' in row['detail']
        assert row['state'] == 'degraded'


def test_new_health_poll_does_not_resolve_durable_gap_from_expired_success(tmp_path):
    # Exercise the existing protected engineering consumer without modifying it.
    from test_engineering_loop import run, registry, evidence

    payload = publication()
    payload['status']['data_sources'][0]['ok'] = False
    components, _ = audit(payload)
    values = evidence(NOW)
    run(tmp_path, NOW, values=(payload, values[1], components))
    identity = 'component:Independent sports sources'
    original = deepcopy(registry(tmp_path)['gaps'][identity])
    assert original['state'] == 'open'

    # An old success observed by a new poll used to close this failure.
    payload['status']['data_sources'][0]['ok'] = True
    later = NOW + timedelta(seconds=301)
    components, _ = audit(payload, later)
    values = evidence(later)
    run(tmp_path, later, values=(payload, values[1], components))
    pending = registry(tmp_path)['gaps'][identity]
    assert pending['state'] == 'open'
    assert pending['first_seen'] == original['first_seen']
    assert pending['due_at'] == original['due_at']

    # Only a later fresh report of success may resolve this component gap.
    payload['generated_at'] = (later + timedelta(seconds=1)).isoformat()
    components, _ = audit(payload, later + timedelta(seconds=1))
    values = evidence(later + timedelta(seconds=1))
    run(tmp_path, later + timedelta(seconds=1), values=(payload, values[1], components))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'resolved'


def test_component_http_response_exposes_evidence_clock_without_ledger_writes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server, providers
    import betting_engine.component_health as health

    payload = publication((NOW - timedelta(minutes=20)).isoformat())
    before = deepcopy(payload)
    # The endpoint only receives a read-only publication stub, never a database.
    monkeypatch.setattr(server, 'engine', SimpleNamespace(store=SimpleNamespace(latest=lambda: payload)))
    monkeypatch.setattr(providers, 'DATA', tmp_path)
    monkeypatch.setattr(health, 'utcnow', lambda: NOW)
    client = TestClient(server.app)  # No lifespan / engine loop.
    response = client.get('/api/component-health')
    assert response.status_code == 200
    checks = {c['component']: c for c in response.json()['checks']}
    assert checks['Independent sports sources']['state'] == 'stale'
    assert checks['Independent sports sources']['publication_freshness']['age_seconds'] == 1200
    assert checks['Full market catalogue']['freshness']['state'] == 'missing'
    assert payload == before and not list(tmp_path.iterdir())
