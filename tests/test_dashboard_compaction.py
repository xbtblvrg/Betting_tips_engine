from betting_engine.dashboard_payload import _compact_dashboard_tip


def test_non_actionable_research_rows_drop_repeated_evidence_tree():
    row = {
        'id': 'research', 'game_id': 'NBA:g', 'league': 'NBA', 'market': 'totals',
        'stake': 0.0, 'selection': 'Over 220.5',
        'decision_guard': {'passed': False, 'errors': ['review'],
                          'input_readiness': {'raw': 'x' * 10000},
                          'knowledge': [{'raw': 'x' * 10000}]},
        'input_readiness': {'raw': 'x' * 10000},
        'game_context': {'raw': 'x' * 10000},
        'model_probabilities': {'raw': 'x' * 10000},
        'reasons': ['reason'], 'warnings': ['warning'],
    }
    compact = _compact_dashboard_tip(row)
    assert len(str(compact)) < 3000
    assert compact['id'] == 'research'
    assert compact['decision_guard'] == {'passed': False, 'errors': ['review']}
    assert compact['input_readiness'] == {}
    assert compact['game_context'] == {}


def test_actionable_tip_keeps_full_execution_evidence():
    row = {'id': 'live', 'stake': 2.0, 'decision_guard': {'raw': 'evidence'}}
    assert _compact_dashboard_tip(row) is row

