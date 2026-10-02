from datetime import datetime, timezone

import pytest

from betting_engine.calibration_uncertainty import build_snapshot, summary_for
from betting_engine.contracts import market_stress


AS_OF = '2026-10-02T12:00:00+00:00'


def row(game, *, status='won', issued='2026-09-20T12:00:00+00:00',
        settled='2026-09-21T12:00:00+00:00', probability=.60,
        odds=2.0, market='moneyline', line=None, model='m-v1'):
    return {
        'status': status,
        'issued_at': issued,
        'outcome_evidence': {'settled_at': settled},
        'payload': {
            'game_id': game, 'league': 'NBA', 'market': market, 'line': line,
            'reference_odds': odds, 'probability': probability,
            'push_probability': 0.0, 'model_version': model,
        },
    }


def test_snapshot_is_prospective_and_game_clustered():
    rows = [row('g1'), row('g1', status='lost'), row('g2', status='lost')]
    snapshot = build_snapshot(rows, {'m-v1': '2026-09-01T00:00:00+00:00'}, AS_OF,
                              minimum_games=2)
    segment = next(iter(snapshot['segments'].values()))
    assert segment['independent_games'] == 2
    assert segment['observations'] == 3
    assert segment['state'] == 'measured'
    assert snapshot['snapshot_sha256']


def test_rows_before_training_or_after_as_of_are_excluded():
    rows = [
        row('before', issued='2026-08-01T00:00:00+00:00'),
        row('future', settled='2026-10-03T00:00:00+00:00'),
        row('prospective'),
    ]
    snapshot = build_snapshot(rows, {'m-v1': '2026-09-01T00:00:00+00:00'}, AS_OF,
                              minimum_games=30)
    segment = next(iter(snapshot['segments'].values()))
    assert segment['independent_games'] == 1
    assert segment['state'] == 'insufficient_evidence'
    assert segment['uncertainty'] == 0


def test_summary_missing_segment_fails_closed_and_stress_keeps_legacy_floor():
    snapshot = build_snapshot([], {}, AS_OF, minimum_games=30)
    summary = summary_for(snapshot, 'NHL', 'totals', 6.5, 1.9)
    assert summary['state'] == 'insufficient_evidence'
    assert market_stress('totals', 'NHL', 6.5, .01, summary) == pytest.approx(.06)


def test_measured_uncertainty_can_raise_stress_but_never_lower_floor():
    calibration = {'state': 'measured', 'uncertainty': .12}
    assert market_stress('totals', 'NHL', 6.5, .01, calibration) == pytest.approx(.12)
    assert market_stress('moneyline', 'NBA', None, .40,
                         {'state': 'measured', 'uncertainty': .01}) == pytest.approx(.20)

