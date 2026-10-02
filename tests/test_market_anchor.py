import math
from types import SimpleNamespace

import pytest

from betting_engine.market_anchor import (
    anchor_status, conditional_devig_probabilities, conditional_push_free_line,
    contract_anchors, devig_probabilities, fit_model_weight, logit_blend,
)


def test_devig_normalizes_complete_two_way_market():
    result = devig_probabilities({'home': 2.0, 'away': 2.2})
    assert sum(result.values()) == pytest.approx(1.0)
    assert result['home'] > result['away']


@pytest.mark.parametrize('market', [{}, {'home': 2.0}, {'home': 1.0, 'away': 2.0},
                                    {'home': 2.0, 'away': float('nan')}])
def test_devig_fails_closed_for_incomplete_or_invalid_contract(market):
    with pytest.raises(ValueError):
        devig_probabilities(market)


def test_logit_blend_starts_at_market_and_moves_toward_model():
    market = .60
    model = .80
    assert logit_blend(model, market, 0) == pytest.approx(market)
    assert market < logit_blend(model, market, .25) < model
    assert logit_blend(model, market, 1) == pytest.approx(model)


def test_large_disagreement_is_shadow_review():
    assert anchor_status(.90, .50) == 'review'
    assert anchor_status(.58, .50) == 'usable'


def test_weight_fit_is_chronological_candidate_and_requires_independent_games():
    rows = [{'game_id': f'g{i}', 'issued_at': f'2026-01-{i+1:02d}',
             'model_probability': .8, 'market_probability': .55,
             'outcome': 1 if i % 2 == 0 else 0} for i in range(4)]
    assert fit_model_weight(rows, minimum_games=5)['state'] == 'insufficient_evidence'
    result = fit_model_weight(rows, minimum_games=4, grid_step=.25)
    assert result['state'] == 'candidate'
    assert result['weight'] in {0, .25, .5, .75, 1.0}
    assert math.isfinite(result['log_loss'])


def test_handicap_anchor_groups_opposite_signed_lines_from_one_stake_condition():
    common = {
        'game_id': 'NBA:g1', 'condition_id': 'condition-1', 'kind': 'spreads',
        'metadata': {'model_scope': 'full_game', 'stake_contract_identity': 'condition-1'},
    }
    home = SimpleNamespace(id='m-home', side='home', line=-7.5, **common)
    away = SimpleNamespace(id='m-away', side='away', line=7.5, **common)
    quotes = {
        'm-home': SimpleNamespace(effective_cost=1 / 1.90, fetched_at='2026-10-02T00:00:00Z'),
        'm-away': SimpleNamespace(effective_cost=1 / 1.90, fetched_at='2026-10-02T00:00:00Z'),
    }
    result = contract_anchors([home, away], quotes)
    assert set(result) == {'m-home', 'm-away'}
    assert result['m-home']['market_probability'] == pytest.approx(.5)
    assert result['m-away']['market_probability'] == pytest.approx(.5)


@pytest.mark.parametrize('kind,line', [('totals',5.0),('totals',5.25),('spreads',0.25),('totals',5.5)])
def test_asian_anchor_declares_push_free_conditional_mass(kind,line):
    assert conditional_push_free_line(kind,line) is True
    fair=conditional_devig_probabilities({'over':2.0,'under':2.0})
    assert fair == {'over':pytest.approx(.5),'under':pytest.approx(.5)}


def test_nhl_over_five_anchor_marks_conditional_push_mass():
    common={'game_id':'NHL:g1','condition_id':'nhl-total-5','kind':'totals',
            'metadata':{'model_scope':'full_game','stake_contract_identity':'nhl-total-5'}}
    over=SimpleNamespace(id='over',side='over',line=5.0,**common)
    under=SimpleNamespace(id='under',side='under',line=5.0,**common)
    q={'over':SimpleNamespace(effective_cost=.5,fetched_at='2026-10-02T00:00:00Z'),
       'under':SimpleNamespace(effective_cost=.5,fetched_at='2026-10-02T00:00:00Z')}
    result=contract_anchors([over,under],q)
    assert result['over']['conditional_push_free'] is True
    assert 'conditional push-free' in result['over']['market_weight_source']


def test_quarter_draw_no_bet_uses_conditional_push_free_anchor():
    common={'game_id':'NFL:g1','condition_id':'nfl-q2-dnb','kind':'quarter_2:moneyline',
            'metadata':{'model_scope':'quarter_2','stake_contract_identity':'nfl-q2-dnb',
                        'stake_market_name':'2nd Quarter - Draw No Bet'}}
    home=SimpleNamespace(id='home',side='home',line=None,**common)
    away=SimpleNamespace(id='away',side='away',line=None,**common)
    q={'home':SimpleNamespace(effective_cost=.5,fetched_at='2026-10-02T00:00:00Z'),
       'away':SimpleNamespace(effective_cost=.5,fetched_at='2026-10-02T00:00:00Z')}
    result=contract_anchors([home,away],q)
    assert result['home']['conditional_push_free'] is True
    assert 'conditional push-free' in result['home']['market_weight_source']
