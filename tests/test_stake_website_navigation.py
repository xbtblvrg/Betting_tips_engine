"""Website routes must not be invented from internal Stake group names."""
from copy import deepcopy

import pytest

from betting_engine.presentation import english_payload, stake_market


@pytest.mark.parametrize('away,home,slug,selection', [
    ('Edmonton Oilers', 'Vancouver Canucks', '46728056-vancouver-canucks-edmonton-oilers', 'Draw or Edmonton Oilers'),
    ('Los Angeles Kings', 'Colorado Avalanche', '46728057-colorado-avalanche-los-angeles-kings', 'Draw or Los Angeles Kings'),
])
def test_real_double_chance_groups_do_not_become_winner_tabs(away, home, slug, selection):
    tip = {'league': 'NHL', 'market': 'regulation:double_chance', 'model_scope': 'regulation',
           'home': home, 'away': away, 'selection': selection, 'side': 'draw_away',
           'reference_contract_description': 'Double Chance', 'reference_platform': 'Stake.com',
           'reference_url': 'https://stake.com/sports/ice-hockey/usa/nhl/' + slug,
           'probability': .61, 'stake': 1.23, 'reference_odds': 2.04,
           'stake_evidence': {'market_groups': [{'id': 'edf549f2-51ae-4d49-bb5d-6c3cb3c731ad', 'name': 'winner'}]}}
    original = deepcopy(tip)
    result = english_payload(tip)
    nav = result['stake_market']
    assert nav['tab'] is None and nav['navigation_source'] is None
    assert nav['navigation_status'] == 'website_tab_unconfirmed'
    assert nav['event_url'] == tip['reference_url']
    assert nav['name'] == 'Double Chance'
    assert 'Excludes overtime and shootouts' in nav['scope_note']
    assert nav['market_groups'][0]['name'] == 'winner'
    assert nav['availability_verified'] is True
    for key in ('selection', 'market', 'model_scope', 'stake', 'reference_odds', 'probability'):
        assert result[key] == original[key]
    assert tip == original


@pytest.mark.parametrize('group', ['winner', 'handicap', 'total', 'threeway', 'other', 'outright', 'unknown private group'])
def test_nhl_internal_groups_are_not_visible_tabs(group):
    nav = stake_market({'league': 'NHL', 'market': 'regulation:double_chance',
                        'stake_evidence': {'market_groups': [{'id': 'raw', 'name': group}]}})
    assert nav['tab'] is None
    assert nav['market_groups'][0]['name'] == group


@pytest.mark.parametrize('league,group,tab', [
    ('NHL', 'main', 'Main'), ('NHL', 'periods', 'Periods'), ('NHL', 'goals', 'Goals'),
    ('NHL', 'combo', 'Combo'), ('MLB', 'innings 1 to 5', 'Innings 1 to 5'),
    ('NFL', 'quarters', 'Quarters'), ('WNBA', 'half', 'Half'), ('NBA', 'main', 'Main'),
])
def test_recognized_existing_navigation_is_preserved(league, group, tab):
    nav = stake_market({'league': league, 'market': 'totals',
                        'stake_evidence': {'market_groups': [{'id': 'g', 'name': group}]}})
    assert nav['tab'] == tab
    assert nav['navigation_status'] == 'recognized_group_route'


@pytest.mark.parametrize('league', ['NBA', 'WNBA', 'NHL', 'MLB', 'NFL', 'NCAAF'])
def test_unknown_group_is_never_titlecased_into_a_website_tab(league):
    nav = stake_market({'league': league, 'market': 'totals',
                        'stake_evidence': {'market_groups': [{'id': 'g', 'name': 'new internal grouping'}]}})
    assert nav['tab'] is None


def test_known_route_is_selected_after_an_internal_group_without_inventing_main():
    tip = {'league': 'NHL', 'market': 'period_1:double_chance',
           'stake_evidence': {'market_groups': [{'id': 'w', 'name': 'winner'}, {'id': 'p', 'name': 'periods'}]}}
    assert stake_market(tip)['tab'] == 'Periods'
    tip['stake_evidence']['market_groups'].append({'id': 'm', 'name': 'main'})
    assert stake_market(tip)['tab'] == 'Main'


def test_nested_historical_presentation_repairs_navigation_without_rewriting_record():
    record = {'payload': {'league': 'NHL', 'market': 'regulation:double_chance', 'selection': 'Draw or Los Angeles Kings',
                          'stake_market': {'tab': 'Winner'},
                          'stake_evidence': {'market_groups': [{'id': 'w', 'name': 'winner'}]}, 'stake': 2.34}}
    original = deepcopy(record)
    assert english_payload(record)['payload']['stake_market']['tab'] is None
    assert record == original
