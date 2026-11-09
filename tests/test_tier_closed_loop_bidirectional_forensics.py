"""Forensic Unit Test Suite for Bidirectional Closed-Loop Tier Progression.

Verifies end-to-end forward and backward transitions:
- Tier 1 (R&D Edge Discovery) -> Tier 2 (Shadow Incubator)
- Tier 2 -> Tier 1 (Fast-Fail Eviction to R&D)
- Tier 2 -> Tier 3 (Crucible Re-Graduation)
- Tier 3 -> Tier 4 (Polymarket Proven Alpha)
- Tier 4 -> Tier 1 / Tier 2 (Live Drawdown Suspension & Demotion)
"""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import pytest

from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.unified_market_universe import UnifiedMarketUniverse, MarketTier
from betting_engine.polymarket_executor import PolymarketAutonomousExecutor


@pytest.fixture
def isolated_env():
    test_dir = Path(tempfile.mkdtemp())
    eng_db = test_dir / "engine.sqlite3"
    inc_db = test_dir / "shadow_incubator.sqlite3"

    inc = ShadowMarketIncubator(data_dir=test_dir, db_path=inc_db)

    with sqlite3.connect(str(eng_db)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                league TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_attempt TEXT,
                attempts INTEGER DEFAULT 0,
                payload TEXT,
                result TEXT,
                last_error TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_decisions (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                market TEXT NOT NULL,
                action TEXT NOT NULL,
                pnl REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                issued_at TEXT NOT NULL,
                payload TEXT
            )
        """)
        conn.commit()

    yield test_dir, inc, eng_db, inc_db

    shutil.rmtree(test_dir)


def test_bidirectional_closed_loop_lifecycle(isolated_env):
    test_dir, inc, eng_db, inc_db = isolated_env

    # -------------------------------------------------------------
    # 1. Tier 1 -> Tier 2 Forward Transition
    # -------------------------------------------------------------
    with sqlite3.connect(str(inc_db)) as conn:
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family,
                demoted_at, rolling_roi, rolling_pnl, games, reason, incubation_phase, stats
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'test_1', 'NBA|quarter_1|totals', 'NBA', 'quarter_1', 'totals', 'totals',
            '2026-11-01T00:00:00Z', -0.10, -5.0, 10, 'Live test demotion', 'research_training', '{}'
        ))
        conn.commit()

    # Simulate Candidate Discovery & Bridge Admission to Tier 2
    with sqlite3.connect(str(inc_db)) as conn:
        conn.execute("""
            UPDATE demoted_markets
            SET incubated_model_id = ?,
                model_archetype = ?,
                historical_roi = ?,
                historical_sample_size = ?,
                incubation_phase = 'incubating_with_new_model',
                admitted_to_incubator_at = ?
            WHERE segment_key = ?
        """, ('test_model_v1', 'PoissonRegressionModel', 0.055, 120, '2026-11-02T00:00:00Z', 'NBA|quarter_1|totals'))
        conn.commit()

    u = UnifiedMarketUniverse(data_dir=test_dir)
    roster = u.build_universe_roster(force_refresh=True)
    p = roster.get('NBA|quarter_1|totals')
    assert p is not None
    assert p.tier == int(MarketTier.SHADOW_INCUBATOR)
    assert p.stake_cap == 0.0

    # -------------------------------------------------------------
    # 2. Tier 2 -> Tier 1 Backward Transition (Fast-Fail Eviction)
    # -------------------------------------------------------------
    with sqlite3.connect(str(inc_db)) as conn:
        for i in range(10):
            conn.execute("""
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family, raw_market_name, selection,
                    odds, model_prob, market_prob, ev, dynamic_sample_power_req, captured_at, status,
                    settled_at, result, pnl, brier_model, brier_market, cohort
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f'w_{i}', f'fix_{i}', f'g_{i}', 'NBA', 'quarter_1', 'totals', 'totals', 'Total Points', 'Over 55.5',
                1.90, 0.55, 0.50, 0.045, 25, '2026-11-02T10:00:00Z', 'settled',
                '2026-11-03T10:00:00Z', 'lost', -1.0, 0.30, 0.25, 'core_challenger'
            ))
        conn.commit()

    life_res = inc.evaluate_tier2_lifecycle(as_of='2026-11-03T12:00:00Z')
    assert life_res['evicted_fast_fail_count'] == 1

    with sqlite3.connect(str(inc_db)) as conn:
        r = conn.execute(
            'SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key = ?',
            ('NBA|quarter_1|totals',)
        ).fetchone()
        assert r[0] == 'research_training'
        assert r[1] is None

    with sqlite3.connect(str(eng_db)) as conn:
        job = conn.execute(
            'SELECT kind, league, payload FROM learning_jobs WHERE kind = ?',
            ('segment_repair',)
        ).fetchone()
        assert job is not None
        p_data = json.loads(job[2])
        assert p_data['segment'] == 'NBA|quarter_1|totals'

    # -------------------------------------------------------------
    # 3. Tier 2 -> Tier 3 Forward Transition (Crucible Re-Graduation)
    # -------------------------------------------------------------
    with sqlite3.connect(str(inc_db)) as conn:
        conn.execute("""
            UPDATE demoted_markets
            SET incubated_model_id = ?,
                model_archetype = ?,
                historical_roi = ?,
                historical_sample_size = ?,
                incubation_phase = 'incubating_with_new_model',
                admitted_to_incubator_at = ?,
                demoted_at = ?
            WHERE segment_key = ?
        """, ('champ_v2', 'CopulaModel', 0.08, 150, '2026-11-04T00:00:00Z', '2026-11-01T00:00:00Z', 'NBA|quarter_1|totals'))
        conn.execute('DELETE FROM incubated_wagers')
        for i in range(25):
            pnl_val = 0.90 if i < 18 else -1.0
            res_val = 'won' if i < 18 else 'lost'
            conn.execute("""
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family, raw_market_name, selection,
                    odds, model_prob, market_prob, ev, dynamic_sample_power_req, captured_at, status,
                    settled_at, result, pnl, brier_model, brier_market, cohort
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f'w_win_{i}', f'fix_win_{i}', f'g_win_{i}', 'NBA', 'quarter_1', 'totals', 'totals', 'Total Points', 'Over 55.5',
                1.90, 0.58, 0.50, 0.09, 25, '2026-11-04T10:00:00Z', 'settled',
                '2026-11-05T10:00:00Z', res_val, pnl_val, 0.20, 0.24, 'core_challenger'
            ))
        conn.commit()

    grad_list = inc.evaluate_graduation(as_of='2026-11-05T12:00:00Z')
    assert len(grad_list) >= 1
    assert grad_list[0]['segment_key'] == 'NBA|quarter_1|totals'

    with sqlite3.connect(str(inc_db)) as conn:
        g_row = conn.execute(
            'SELECT segment_key, roi, win_rate FROM graduated_markets WHERE segment_key = ?',
            ('NBA|quarter_1|totals',)
        ).fetchone()
        assert g_row is not None
        d_row = conn.execute(
            'SELECT 1 FROM demoted_markets WHERE segment_key = ?',
            ('NBA|quarter_1|totals',)
        ).fetchone()
        assert d_row is None

    roster = u.build_universe_roster(force_refresh=True)
    p3 = roster.get('NBA|quarter_1|totals')
    assert p3 is not None
    assert p3.tier == int(MarketTier.PRODUCTION_GRADUATED)
    assert p3.stake_cap >= 0.50

    # -------------------------------------------------------------
    # 4. Tier 3 -> Tier 4 Forward Transition (Polymarket Proven Alpha)
    # -------------------------------------------------------------
    with sqlite3.connect(str(eng_db)) as conn:
        for i in range(12):
            payload = {
                'league': 'NBA',
                'model_scope': 'quarter_1',
                'period': 'quarter_1',
                'market': 'quarter_1:totals',
                'stake': 2.0,
                'game_id': f'live_g_{i}'
            }
            conn.execute("""
                INSERT INTO system_decisions (
                    id, game_id, market, action, pnl, status, issued_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (f'dec_{i}', f'live_g_{i}', 'quarter_1:totals', 'recommend', 1.80, 'won', '2026-11-06T00:00:00Z', json.dumps(payload)))
        conn.commit()

    pm_exec = PolymarketAutonomousExecutor(db_path=eng_db)
    is_elig, reason, cap = pm_exec.is_market_eligible('NBA', 'quarter_1', 'totals', require_tier_4=True)
    assert is_elig is True
    assert cap >= 3.50

    # -------------------------------------------------------------
    # 5. Tier 4 -> Tier 1 Backward Transition (Live Drawdown Demotion)
    # -------------------------------------------------------------
    with sqlite3.connect(str(eng_db)) as conn:
        for i in range(15):
            payload = {
                'league': 'NBA',
                'model_scope': 'quarter_1',
                'period': 'quarter_1',
                'market': 'quarter_1:totals',
                'stake': 5.0,
                'game_id': f'live_loss_g_{i}'
            }
            conn.execute("""
                INSERT INTO system_decisions (
                    id, game_id, market, action, pnl, status, issued_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (f'dec_loss_{i}', f'live_loss_g_{i}', 'quarter_1:totals', 'recommend', -5.0, 'lost', '2026-11-07T00:00:00Z', json.dumps(payload)))
        conn.commit()

    PolymarketAutonomousExecutor._cached_scoreboard = None
    PolymarketAutonomousExecutor._scoreboard_cached_at = 0.0
    is_elig_post_loss, reason_loss, cap_loss = pm_exec.is_market_eligible('NBA', 'quarter_1', 'totals', require_tier_4=True)
    assert is_elig_post_loss is False
    assert cap_loss == 0.0

    roster_loss = u.build_universe_roster(force_refresh=True)
    p_loss = roster_loss.get('NBA|quarter_1|totals')
    assert p_loss.tier == int(MarketTier.RESEARCH_RND)
    assert p_loss.stake_cap == 0.0


def test_high_stake_variance_wobble_preserves_tier_3_tourniquet(isolated_env):
    """Verifies that high-stake losses (e.g. $5 bets, 2 losses = -$10) DO NOT instant-demote to Tier 1.
    
    A 2-unit wobble is strictly managed by Phase 1 Capital Defense Tourniquet ($0.50 clamp).
    Only sustained statistical breakdown across N >= 8 games with units <= -4.0U demotes to Tier 1 ($0.00).
    """
    test_dir, inc, eng_db, inc_db = isolated_env

    # 1. Market graduates into Tier 3
    with sqlite3.connect(str(inc_db)) as conn:
        conn.execute("""
            INSERT INTO graduated_markets (
                id, segment_key, league, scope, family, sub_family,
                graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'grad_nba_tot', 'NBA|quarter_1|totals', 'NBA', 'quarter_1', 'totals', 'totals',
            '2026-11-01T00:00:00Z', 30, 0.065, 0.002, 0.12, 0.60, '{}'
        ))
        conn.commit()

    u = UnifiedMarketUniverse(data_dir=test_dir)

    # 2. Market experiences a high-stake 2-loss wobble ($5.00 stake, -$10.00 loss across 4 games: 2 wins, 2 losses)
    # Total PnL = 2 * (+4.50) + 2 * (-5.00) = -$1.00 (or -0.2 units)
    # Or let's test 4 games with 3 losses at $5.00 stake = -$7.00 loss (-1.4 units)
    with sqlite3.connect(str(eng_db)) as conn:
        # Game 1: Win at $5.00 stake
        conn.execute("""
            INSERT INTO system_decisions (id, game_id, market, action, pnl, status, issued_at, payload)
            VALUES ('d1', 'g1', 'quarter_1:totals', 'recommend', 4.50, 'won', '2026-11-02T00:00:00Z', ?)
        """, (json.dumps({'league': 'NBA', 'model_scope': 'quarter_1', 'market': 'quarter_1:totals', 'stake': 5.0, 'game_id': 'g1'}),))
        # Games 2, 3, 4: Losses at $5.00 stake (PnL = -5.00 each)
        for i in (2, 3, 4):
            conn.execute("""
                INSERT INTO system_decisions (id, game_id, market, action, pnl, status, issued_at, payload)
                VALUES (?, ?, 'quarter_1:totals', 'recommend', -5.00, 'lost', '2026-11-02T01:00:00Z', ?)
            """, (f'd{i}', f'g{i}', json.dumps({'league': 'NBA', 'model_scope': 'quarter_1', 'market': 'quarter_1:totals', 'stake': 5.0, 'game_id': f'g{i}'})))
        conn.commit()

    # Net PnL = +4.50 - 15.00 = -$10.50 (in dollar terms this is > $10 loss!)
    # But in units: staked = $20.00, avg_stake = $5.00, units = -10.50 / 5.0 = -2.1 units across 4 games.
    # Under the ANTI-CONSTANT MANDATE:
    # A $10 loss at $5 stake over 4 games MUST NOT instant-demote to Tier 1!
    # It must be protected by Phase 1 Capital Defense Tourniquet ($0.50 clamp)!
    roster = u.build_universe_roster(force_refresh=True)
    p = roster.get('NBA|quarter_1|totals')
    assert p is not None
    assert p.tier == int(MarketTier.PRODUCTION_GRADUATED), f"Expected Tier 3 (Phase 1 Tourniquet), got Tier {p.tier} ({p.phase_detail})"
    assert p.stake_cap == 0.50, f"Expected $0.50 tourniquet clamp, got ${p.stake_cap}"
    assert "Capital Defense Tourniquet" in p.phase_detail

    # 3. Now simulate sustained statistical breakdown across N >= 8 games (adding 6 more consecutive losses)
    with sqlite3.connect(str(eng_db)) as conn:
        for i in range(5, 11):
            conn.execute("""
                INSERT INTO system_decisions (id, game_id, market, action, pnl, status, issued_at, payload)
                VALUES (?, ?, 'quarter_1:totals', 'recommend', -5.00, 'lost', '2026-11-03T00:00:00Z', ?)
            """, (f'd{i}', f'g{i}', json.dumps({'league': 'NBA', 'model_scope': 'quarter_1', 'market': 'quarter_1:totals', 'stake': 5.0, 'game_id': f'g{i}'})))
        conn.commit()

    # Now N = 10 games, 9 losses, units <= -8.0U, ROI <= -70% across 10 games!
    # This is a genuine, statistically significant failure -> Demoted to Tier 1 R&D quarantine ($0.00).
    roster_demoted = u.build_universe_roster(force_refresh=True)
    p_dem = roster_demoted.get('NBA|quarter_1|totals')
    assert p_dem is not None
    assert p_dem.tier == int(MarketTier.RESEARCH_RND), f"Expected Tier 1 (Quarantine), got Tier {p_dem.tier}"
    assert p_dem.stake_cap == 0.00, f"Expected $0.00 quarantine cap, got ${p_dem.stake_cap}"
    assert "Quarantined" in p_dem.phase_detail

