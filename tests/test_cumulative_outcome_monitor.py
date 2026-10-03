import copy

from betting_engine.outcome_feedback import cumulative_monitor, _proposals


def stats(pnl=3, drawdown=2, games=5):
    return {'system_decisions': {
        'cumulative_performance': {'points': [{'at': '2026-10-03T12:00:00+00:00', 'pnl': pnl}],
            'summary': {'pnl': pnl, 'current_drawdown': drawdown, 'max_drawdown': drawdown,
                        'roi': .1, 'hit_rate': .6, 'games': games, 'settled': games},
            'coverage': {'missing_settlement_time': 0}},
        'top_markets': [{'scope': 'full_game', 'market_family': 'moneyline', 'pnl': 3}],
        'by_league_market': [], 'source_coverage': {}},
        'resolution_audit': {'error_count': 0}}


def test_positive_lifetime_pnl_still_detects_drawdown_without_policy_changes():
    source = stats(); before = copy.deepcopy(source)
    report = cumulative_monitor(source)
    assert report['state'] == 'drawdown_observed'
    assert report['diagnostic_due'] is True
    assert report['summary']['pnl'] == 3
    assert report['summary']['roi'] == .1 and report['summary']['hit_rate'] == .6
    assert report['top_markets'][0]['market_family'] == 'moneyline'
    assert not report['automatic_stake_change'] and not report['automatic_model_promotion']
    assert source == before
    assert report == cumulative_monitor(source)


def test_no_curve_or_small_cohort_does_not_invent_learning_signal():
    assert cumulative_monitor({})['state'] == 'unavailable'
    assert not cumulative_monitor(stats(games=4))['diagnostic_due']
    assert not cumulative_monitor(stats(drawdown=0))['diagnostic_due']


def test_drawdown_proposal_uses_existing_economics_route_and_stable_identity():
    inputs = {'decisions': [{'family': 'a'}, {'family': 'b'}], 'bets': [], 'bet_outcomes': []}
    first = _proposals(stats(), inputs)
    second = _proposals(stats(pnl=1, drawdown=4), inputs)
    assert len(first) == len(second) == 1
    assert first[0]['kind'] == 'cumulative_drawdown'
    assert first[0]['bucket'] == 'economics'
    assert first[0]['id'] == second[0]['id']
    assert first[0]['evidence']['monitor']['curve_sha256'] != second[0]['evidence']['monitor']['curve_sha256']
    assert first[0]['evidence']['original_decision_ids'] == ['a', 'b']
    assert first[0]['profit_established'] is False
