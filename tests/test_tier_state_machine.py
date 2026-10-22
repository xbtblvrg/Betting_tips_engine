import json
import sqlite3
import pytest

from betting_engine.tier_state_machine import (
    classify_tier,
    process_tier_transitions,
    load_tier_transitions,
    diagnose_promotion,
    diagnose_demotion
)
from betting_engine.segment_scoreboard import compute_segment_scoreboard


def create_mock_db():
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
    conn.execute("""
        CREATE TABLE learning_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            league TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            next_attempt TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL,
            result TEXT,
            last_error TEXT
        )
    """)
    return conn


def test_classify_tier_boundaries():
    # 1. Capital Defense
    cap_def = classify_tier(games_count=4, pnl=-5.0, roi=-0.15)
    assert cap_def['tier'] == 'UNDERPERFORMING'
    assert cap_def['sub_tier'] == 'CAPITAL_DEFENSE'
    assert cap_def['stake_cap'] == 0.50
    assert cap_def['min_ev'] == 0.035
    assert 'clamp' in cap_def['scaling_target']

    # 2. Solid Alpha
    solid = classify_tier(games_count=3, pnl=2.5, roi=0.08)
    assert solid['tier'] == 'ALPHA_BOOST'
    assert solid['sub_tier'] == 'SOLID_ALPHA'
    assert solid['stake_cap'] == 3.5
    assert '2.50' in solid['scaling_target']

    # 3. Super Alpha
    super_a = classify_tier(games_count=6, pnl=12.0, roi=0.25)
    assert super_a['tier'] == 'ALPHA_BOOST'
    assert super_a['sub_tier'] == 'SUPER_ALPHA'
    assert super_a['stake_cap'] == 8.0
    assert '5.00' in super_a['scaling_target'] or '8.00' in super_a['scaling_target']

    # 4. Exploration (neutral / emerging)
    exp = classify_tier(games_count=2, pnl=1.0, roi=0.10)
    assert exp['tier'] == 'EXPLORATION'
    assert exp['sub_tier'] == 'EXPLORATION'
    assert exp['stake_cap'] == 2.0


def test_dynamic_transition_and_forensics(tmp_path, monkeypatch):
    # Route history & log to temp dir for isolated test
    test_hist = tmp_path / 'test_tier_history.json'
    test_log = tmp_path / 'test_tier_transitions.jsonl'
    import betting_engine.tier_state_machine as tsm
    monkeypatch.setattr(tsm, 'HISTORY_FILE', test_hist)
    monkeypatch.setattr(tsm, 'TRANSITIONS_LOG', test_log)

    conn = create_mock_db()

    # Initial state: NFL spreads in EXPLORATION
    test_hist.write_text(json.dumps({
        'NFL|full_game|spreads': {
            'tier': 'EXPLORATION',
            'canonical_tier': 'EXPLORATION',
            'updated_at': '2026-10-01T00:00:00Z',
            'pnl': 0.0,
            'roi_pct': 0.0,
            'games': 1
        },
        'NHL|full_game|totals': {
            'tier': 'EXPLORATION',
            'canonical_tier': 'EXPLORATION',
            'updated_at': '2026-10-01T00:00:00Z',
            'pnl': 0.0,
            'roi_pct': 0.0,
            'games': 1
        }
    }))

    # Now evaluate segments:
    # 1. NFL spreads promoted to SUPER_ALPHA
    # 2. NHL totals demoted to CAPITAL_DEFENSE
    evaluated = [
        {
            'segment': 'NFL|full_game|spreads',
            'league': 'NFL',
            'scope': 'full_game',
            'family': 'spreads',
            'sub_tier': 'SUPER_ALPHA',
            'tier': 'ALPHA_BOOST',
            'pnl': 14.50,
            'roi_pct': 48.3,
            'games': 6,
            'wins': 5,
            'losses': 1,
            'scaling_target': '$5.00–$8.00 super-alpha scaling'
        },
        {
            'segment': 'NHL|full_game|totals',
            'league': 'NHL',
            'scope': 'full_game',
            'family': 'totals',
            'sub_tier': 'CAPITAL_DEFENSE',
            'tier': 'UNDERPERFORMING',
            'pnl': -12.00,
            'roi_pct': -24.0,
            'games': 5,
            'wins': 1,
            'losses': 4,
            'scaling_target': '$1.00 flat clamp'
        }
    ]

    new_trans, history = process_tier_transitions(conn, evaluated)

    assert len(new_trans) == 2

    # Check promotion event
    promo = next(t for t in new_trans if t['segment'] == 'NFL|full_game|spreads')
    assert promo['transition_type'] == 'PROMOTION'
    assert promo['previous_tier'] == 'EXPLORATION'
    assert promo['new_tier'] == 'SUPER_ALPHA'
    assert 'Super Alpha unlocked' in promo['diagnostic_reason']
    assert promo['repair_job_id'] is None

    # Check demotion event
    demote = next(t for t in new_trans if t['segment'] == 'NHL|full_game|totals')
    assert demote['transition_type'] == 'DEMOTION'
    assert demote['previous_tier'] == 'EXPLORATION'
    assert demote['new_tier'] == 'CAPITAL_DEFENSE'
    assert 'Demoted' in demote['diagnostic_reason'] or 'Deteriorated' in demote['diagnostic_reason']
    assert demote['repair_job_id'] is not None

    # Verify R&D repair job was enqueued into learning_jobs table
    cur = conn.cursor()
    cur.execute("SELECT id, kind, league, status, payload FROM learning_jobs WHERE kind = 'segment_repair'")
    jobs = cur.fetchall()
    assert len(jobs) == 1
    job = jobs[0]
    assert job[1] == 'segment_repair'
    assert job[2] == 'NHL'
    assert job[3] == 'queued'
    payload = json.loads(job[4])
    assert payload['segment'] == 'NHL|full_game|totals'
    assert payload['new_tier'] == 'CAPITAL_DEFENSE'


def test_scoreboard_with_transitions_integration(tmp_path, monkeypatch):
    test_hist = tmp_path / 'test_tier_history.json'
    test_log = tmp_path / 'test_tier_transitions.jsonl'
    import betting_engine.tier_state_machine as tsm
    monkeypatch.setattr(tsm, 'HISTORY_FILE', test_hist)
    monkeypatch.setattr(tsm, 'TRANSITIONS_LOG', test_log)

    conn = create_mock_db()

    # Pre-populate history so a transition triggers
    test_hist.write_text(json.dumps({
        'NFL|full_game|spreads': {
            'tier': 'SOLID_ALPHA',
            'canonical_tier': 'ALPHA_BOOST',
            'updated_at': '2026-10-01T00:00:00Z',
            'pnl': 3.0,
            'roi_pct': 10.0,
            'games': 3
        }
    }))

    # Insert 6 winning NFL spread decisions (promotes to SUPER_ALPHA)
    for i in range(6):
        payload = {
            'id': f'nfl_{i}',
            'league': 'NFL',
            'market': 'spreads',
            'stake': 2.0,
            'reference_odds': 2.0,
            'game_id': f'game_nfl_{i}'
        }
        conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (f'nfl_{i}', f'nfl_{i}', '2026-10-01', json.dumps(payload), 'won', 2.0, '{}'))

    scoreboard = compute_segment_scoreboard(conn)

    assert scoreboard['counts']['super_alpha'] == 1
    assert scoreboard['counts']['alpha'] == 1
    alpha_seg = scoreboard['alpha_segments'][0]
    assert alpha_seg['sub_tier'] == 'SUPER_ALPHA'
    assert alpha_seg['stake_cap'] == 8.0

    # Verify transition was recorded
    assert len(scoreboard['recent_transitions']) == 1
    trans = scoreboard['recent_transitions'][0]
    assert trans['segment'] == 'NFL|full_game|spreads'
    assert trans['previous_tier'] == 'SOLID_ALPHA'
    assert trans['new_tier'] == 'SUPER_ALPHA'
    assert trans['transition_type'] == 'PROMOTION'


def test_dual_window_patched_segment_agile_unclamping():
    """Verifies that patched segments winning in their rolling window escape Capital Defense."""
    # 1. Unpatched negative segment stays clamped in Capital Defense
    unpatched = classify_tier(
        games_count=25,
        pnl=-20.0,
        roi=-0.25,
        rolling_games=6,
        rolling_pnl=3.5,
        rolling_roi=0.18,
        has_deployed_patch=False
    )
    assert unpatched['tier'] == 'UNDERPERFORMING'
    assert unpatched['sub_tier'] == 'CAPITAL_DEFENSE'
    assert unpatched['stake_cap'] == 0.50

    # 2. Patched segment with winning rolling cohort graduates to Solid Alpha
    patched_recovering = classify_tier(
        games_count=25,
        pnl=-20.0,
        roi=-0.25,
        rolling_games=6,
        rolling_pnl=3.5,
        rolling_roi=0.18,
        has_deployed_patch=True
    )
    assert patched_recovering['tier'] == 'ALPHA_BOOST'
    assert patched_recovering['sub_tier'] == 'SOLID_ALPHA'
    assert patched_recovering['stake_cap'] == 3.5
    assert patched_recovering['recovery_accelerated'] is True
    assert 'Post-repair Solid Alpha active' in patched_recovering['action']

    # 3. Patched segment with dominant rolling cohort graduates to Super Alpha
    patched_super = classify_tier(
        games_count=25,
        pnl=-20.0,
        roi=-0.25,
        rolling_games=7,
        rolling_pnl=8.0,
        rolling_roi=0.30,
        has_deployed_patch=True
    )
    assert patched_super['tier'] == 'ALPHA_BOOST'
    assert patched_super['sub_tier'] == 'SUPER_ALPHA'
    assert patched_super['stake_cap'] == 8.0
    assert patched_super['recovery_accelerated'] is True
    assert 'Post-repair Super Alpha active' in patched_super['action']

    # 4. Patched segment that relapses in its rolling window remains in Capital Defense
    patched_relapsed = classify_tier(
        games_count=25,
        pnl=-20.0,
        roi=-0.25,
        rolling_games=6,
        rolling_pnl=-3.0,
        rolling_roi=-0.15,
        has_deployed_patch=True
    )
    assert patched_relapsed['tier'] == 'UNDERPERFORMING'
    assert patched_relapsed['sub_tier'] == 'CAPITAL_DEFENSE'
    assert patched_relapsed['stake_cap'] == 0.50
