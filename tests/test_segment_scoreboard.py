import sqlite3
import json
import pytest
from betting_engine.segment_scoreboard import compute_segment_scoreboard, segment_key_from_payload


def create_in_memory_db():
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE system_decisions (
            family TEXT,
            tip_id TEXT,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        )
    """)
    return conn


def test_segment_key_from_payload():
    tip = {'league': 'NFL', 'market': 'spreads', 'side': 'home', 'line': -3.5}
    assert segment_key_from_payload(tip) == 'NFL|full_game|spreads'
    
    period_tip = {'league': 'NHL', 'market': 'period_1:three_way', 'side': 'draw'}
    assert segment_key_from_payload(period_tip) == 'NHL|period_1|period_1:three_way'
    
    assert segment_key_from_payload({}) is None
    assert segment_key_from_payload({'league': 'NBA'}) is None


def test_compute_segment_scoreboard_classification(tmp_path, monkeypatch):
    test_hist = tmp_path / 'test_tier_history.json'
    test_log = tmp_path / 'test_tier_transitions.jsonl'
    import betting_engine.tier_state_machine as tsm
    monkeypatch.setattr(tsm, 'HISTORY_FILE', test_hist)
    monkeypatch.setattr(tsm, 'TRANSITIONS_LOG', test_log)
    conn = create_in_memory_db()
    
    # 1. Add winning NFL spread bets (ALPHA_BOOST)
    for i in range(5):
        payload = {
            'id': f'nfl_{i}',
            'league': 'NFL',
            'market': 'spreads',
            'stake': 2.0,
            'game_id': f'game_nfl_{i}'
        }
        conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (f'nfl_{i}', f'nfl_{i}', '2026-10-01', json.dumps(payload), 'won', 1.80, '{}'))
        
    # 2. Add losing NHL total bets (UNDERPERFORMING)
    for i in range(5):
        payload = {
            'id': f'nhl_{i}',
            'league': 'NHL',
            'market': 'totals',
            'stake': 2.0,
            'game_id': f'game_nhl_{i}'
        }
        conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (f'nhl_{i}', f'nhl_{i}', '2026-10-01', json.dumps(payload), 'lost', -2.00, '{}'))
        
    # 3. Add neutral/small sample bets (EXPLORATION)
    payload_exp = {
        'id': 'exp_1',
        'league': 'WNBA',
        'market': 'moneyline',
        'stake': 1.0,
        'game_id': 'game_wnba_1'
    }
    conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ('exp_1', 'exp_1', '2026-10-01', json.dumps(payload_exp), 'won', 0.90, '{}'))
    
    scoreboard = compute_segment_scoreboard(conn)
    
    assert scoreboard['counts']['alpha'] == 1
    assert scoreboard['counts']['underperforming'] == 1
    assert scoreboard['counts']['exploration'] == 1
    
    alpha = scoreboard['alpha_segments'][0]
    assert alpha['segment'] == 'NFL|full_game|spreads'
    assert alpha['tier'] == 'ALPHA_BOOST'
    assert alpha['stake_cap'] in (5.0, 8.0)
    assert alpha['pnl'] == 9.0  # 5 * 1.80
    
    under = scoreboard['underperforming_segments'][0]
    assert under['segment'] == 'NHL|full_game|totals'
    assert under['tier'] == 'UNDERPERFORMING'
    assert under['stake_cap'] in (0.5, 1.0)
    assert under['pnl'] == -10.0  # 5 * -2.00

    assert under['min_ev'] == 0.035
    
    exp = scoreboard['exploration_segments'][0]
    assert exp['segment'] == 'WNBA|full_game|moneyline'
    assert exp['tier'] == 'EXPLORATION'
