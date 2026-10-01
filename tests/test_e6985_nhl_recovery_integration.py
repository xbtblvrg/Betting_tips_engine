"""Offline integration boundaries for the NHL box-download repair.

All generated responses/times below are synthetic, never source observations.
"""
from copy import deepcopy

import numpy as np
import pytest

from betting_engine.context import SportContext
from betting_engine.contracts import settlement_scores
from betting_engine.models import FormState
from betting_engine.domain import parse_time
from test_e6985_nhl_box_download_recovery import (
    EXPORT, STAMP, OfflineFeed, box, endpoint, game,
)


def test_empty_target_set_is_not_a_successful_source_or_observation():
    feed = OfflineFeed()
    report = SportContext(feed).official_hockey_season([], 2027, {'url': EXPORT})
    assert not report['ok'] and report['error']
    assert report['rows'] == report['target_rows'] == 0
    assert report['sha256'] is None and report['fetched_at'] is None
    assert not feed.calls


def test_recovery_is_scoped_to_requested_season_and_completed_nhl_games():
    target = game()
    excluded = [game('2025020001', season=2026),
                game('2026020002', state='scheduled', home_score=None, away_score=None),
                game('2026020003', state='live'), game(id='NBA:1', league='NBA')]
    before = [g.dict() for g in excluded]
    feed = OfflineFeed({endpoint(target): box(target)})
    report = SportContext(feed).official_hockey_season([target, *excluded], 2027, {})
    assert report['ok'] and report['target_rows'] == 1
    assert feed.calls == [endpoint(target)]
    assert [g.dict() for g in excluded] == before


def test_supplement_failure_retains_downloaded_and_missing_source_receipts():
    g = game()
    response = box(g)
    response['playerByGameStats']['homeTeam']['forwards'][0]['sog'] = 29
    pbp_url = endpoint(g).rsplit('/', 1)[0] + '/play-by-play'
    landing_url = endpoint(g).rsplit('/', 1)[0] + '/landing'
    feed = OfflineFeed({endpoint(g): response, pbp_url: {'id': response['id']}})
    before = g.dict()
    report, = SportContext(feed).enrich_boxes([g])
    assert not report['ok'] and report['fetched_at'] is None
    receipt = report['recovery_errors'][0]['source']
    assert receipt['sha256'] and receipt['fetched_at'] == STAMP
    assert receipt['supplements'][0]['url'] == pbp_url
    assert receipt['supplements'][0]['sha256']
    assert receipt['supplements'][1] == {'url': landing_url}
    assert g.dict() == before


def test_rejected_refresh_preserves_preexisting_box_evidence():
    g = game()
    g.context.update(box={'previous_observation': True},
                     box_source={'url': 'retained-original-source'})
    before = g.dict()
    report, = SportContext(OfflineFeed()).enrich_boxes([g])
    assert not report['ok'] and report['rows'] == 0
    assert g.dict() == before


def test_recovered_stats_enter_later_features_only_after_final_game_update():
    g = game()
    state = FormState('NHL')
    before = state.features(g).copy()
    report, = SportContext(OfflineFeed({endpoint(g): box(g)})).enrich_boxes([g])
    assert report['ok']
    np.testing.assert_array_equal(state.features(g), before)
    without_box = deepcopy(g)
    without_box.context.pop('box')
    control = FormState('NHL')
    control.update(without_box)
    state.update(g)
    later = game('2026020100', start='2026-10-03T20:00:00Z', state='scheduled',
                 home_score=None, away_score=None)
    assert not np.array_equal(state.features(later), control.features(later))
    assert state.team('BOS')['box_n'] == 1
    # Enrichment supplies no new historical publication/availability assertion.
    assert set(g.context['box_source']) == {'url', 'sha256', 'fetched_at'}


def test_full_game_recovery_cannot_fill_period_settlement_or_change_contract():
    g = game()
    contracts = [{'id': 'original-' + market, 'game_id': g.id, 'league': 'NHL',
                  'market': market, 'side': side, 'line': line,
                  'reference_odds': 1.91, 'stake': 2.5, 'probability': .57}
                 for market, side, line in [('moneyline', 'home', None),
                                            ('spreads', 'away', 1.5),
                                            ('totals', 'over', 4.5),
                                            ('period_1:totals', 'over', 1.5)]]
    original = deepcopy(contracts)
    scores = [settlement_scores(t, g) for t in contracts[:-1]]
    with pytest.raises(ValueError, match='Verified completed segment scores unavailable'):
        settlement_scores(contracts[-1], g)
    report, = SportContext(OfflineFeed({endpoint(g): box(g)})).enrich_boxes([g])
    assert report['ok']
    assert contracts == original
    assert [settlement_scores(t, g) for t in contracts[:-1]] == scores
    assert scores[0][:2] == (3, 2)  # Retain final shootout score for full game.
    with pytest.raises(ValueError, match='Verified completed segment scores unavailable'):
        settlement_scores(contracts[-1], g)
    assert 'segment_scores' not in g.context


def test_ncaaf_injury_gap_remains_explicit_after_nhl_recovery(monkeypatch):
    import betting_engine.context as context_module
    monkeypatch.setattr(context_module, 'utcnow', lambda: parse_time(STAMP))
    college = game(id='NCAAF:1', league='NCAAF', season=2026,
                   state='scheduled', start='2026-10-01T14:00:00Z',
                   home_score=None, away_score=None)
    feed = OfflineFeed()
    report, = SportContext(feed).injuries([college])
    assert not report['ok'] and report['state'] == 'not_integrated'
    assert not feed.calls and 'injuries' not in college.context
