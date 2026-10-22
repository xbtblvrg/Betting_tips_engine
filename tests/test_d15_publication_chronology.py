"""Frozen publications cannot establish observations before they existed."""
from copy import deepcopy

import pytest

from betting_engine.requirement_acceptance import publication_limits


def publication():
    return {'version': 7, 'created_at': '2026-10-01T10:00:00Z', 'payload': {
        'opportunity_inventory': {'fixtures': [{'matched': True, 'counts': [{
            'observed': 4, 'supported': 4, 'active_standard': 4,
            'swish_unverified': 0, 'fetched_at': '2026-10-01T09:59:30Z',
        }]}]}}}


def test_older_quotes_do_not_make_a_future_publication_available():
    snapshot = publication()
    before = deepcopy(snapshot)
    report = publication_limits(snapshot, as_of='2026-10-01T09:59:59Z')
    assert report['state'] == 'limit'
    assert report['reasons'] == ['publication_after_as_of']
    assert 'observed_outcomes' not in report
    assert report['currently_executable_instructions'] is None
    assert snapshot == before


@pytest.mark.parametrize('time', [None, '', '2026-10-01T10:00:00', 'invalid'])
def test_unknown_publication_time_is_never_inferred_from_quotes(time):
    snapshot = publication()
    snapshot['created_at'] = time
    report = publication_limits(snapshot, as_of='2026-10-01T10:01:00Z')
    assert report['state'] == 'limit'
    assert report['temporal_evidence'] == {
        'state': 'unknown', 'reason': 'publication_time_missing_or_invalid'}
    # Retain raw evidence, without calling it chronologically available.
    assert report['observed_outcomes'] == 4
    assert report['currently_executable_instructions'] is None


def test_source_fetched_after_publication_cannot_support_a_fresh_count():
    snapshot = publication()
    snapshot['payload']['opportunity_inventory']['fixtures'][0]['counts'][0][
        'fetched_at'] = '2026-10-01T10:00:01Z'
    report = publication_limits(snapshot, as_of='2026-10-01T10:00:02Z')
    assert report['state'] == 'limit'
    assert report['source_rows_after_publication'] == 1
    assert report['expired_or_invalid_count_rows'] == 1
    assert 'quote_counts_expired_or_invalid' in report['reasons']


@pytest.mark.parametrize(('as_of', 'expired'), [
    ('2026-10-01T10:04:30Z', False), ('2026-10-01T10:04:30.000001Z', True),
])
def test_quote_lifetime_stays_at_300_seconds(as_of, expired):
    report = publication_limits(publication(), as_of=as_of)
    assert report['temporal_evidence']['state'] == 'observed_by_cutoff'
    assert ('quote_counts_expired_or_invalid' in report['reasons']) == expired
    assert report['state'] == ('limit' if expired else 'observed_only')
    assert report['currently_executable_instructions'] is None


def test_missing_fixture_field_is_not_an_empty_valid_calendar():
    snapshot = publication()
    del snapshot['payload']['opportunity_inventory']['fixtures']
    report = publication_limits(snapshot, as_of='2026-10-01T10:01:00Z')
    assert report['reasons'] == ['invalid_fixture_inventory']
    assert 'observed_fixture_count' not in report
    snapshot['payload']['opportunity_inventory']['fixtures'] = []
    report = publication_limits(snapshot, as_of='2026-10-01T10:01:00Z')
    assert report['observed_fixture_count'] == 0
    assert report['reasons'] == ['no_observed_fixtures']
    assert report['state'] == 'limit'
