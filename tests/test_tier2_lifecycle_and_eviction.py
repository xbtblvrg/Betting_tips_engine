"""Unit tests for Tier 2 Lifecycle & Eviction Watchdog Engine.

Validates:
1. Reachability Guard: Evicts and permanently purges phantom/unsupported contracts (e.g. basketball both_score, odd_even) from demoted_markets.
2. Off-Season & Hiatus Protection: Preserves legitimate models in leagues with zero recent/upcoming fixtures without eviction or penalty.
3. Fast-Fail Live Eviction: Resets candidate models suffering severe drawdowns (N >= 10, ROI <= -15%) back to Tier 1 ('research_training', model=NULL).
4. Stagnation Eviction: Evicts candidate models failing to reach +3.5% ROI after full trial (N >= 25) back to Tier 1.
5. Deadlock Reset: Eliminates unverified legacy rows (N=0, ROI <= 0) back to Tier 1 for real AI training.
6. Legitimate Progression: Retains healthy incubating models and smoothly graduates winning cohorts into Tier 3.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.contracts import is_supported_stake_contract
from betting_engine.shadow_market_incubator import (
    ShadowMarketIncubator,
    evaluate_tier2_lifecycle,
    _init_db,
)


@pytest.fixture
def test_env(tmp_path: Path):
    """Sets up an isolated incubator test database and data directory."""
    db_path = tmp_path / "shadow_incubator.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demoted_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT UNIQUE NOT NULL,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            demoted_at TEXT NOT NULL,
            rolling_roi REAL NOT NULL,
            rolling_pnl REAL NOT NULL,
            games INT NOT NULL,
            reason TEXT NOT NULL,
            stats TEXT NOT NULL,
            incubated_model_id TEXT,
            model_archetype TEXT,
            model_params TEXT,
            historical_roi REAL,
            historical_brier_delta REAL,
            historical_sample_size INT DEFAULT 0,
            incubation_phase TEXT DEFAULT 'research_training',
            admitted_to_incubator_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incubated_wagers (
            id TEXT PRIMARY KEY,
            fixture_id TEXT,
            game_id TEXT,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT,
            raw_market_name TEXT,
            selection TEXT,
            side TEXT,
            line REAL,
            odds REAL,
            model_prob REAL,
            push_prob REAL,
            market_prob REAL,
            ev REAL,
            dynamic_sample_power_req INT,
            captured_at TEXT,
            status TEXT,
            settled_at TEXT,
            result TEXT,
            pnl REAL,
            brier_model REAL,
            brier_market REAL,
            metadata TEXT,
            cohort TEXT,
            model_id TEXT,
            model_version TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graduated_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT UNIQUE NOT NULL,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            graduated_at TEXT NOT NULL,
            sample_size INT NOT NULL,
            roi REAL NOT NULL,
            delta_brier REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            win_rate REAL NOT NULL,
            stats TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    incubator = ShadowMarketIncubator(data_dir=tmp_path, db_path=db_path)
    return tmp_path, db_path, incubator


def test_phantom_contract_eviction(test_env):
    """Verifies that unoffered/phantom markets (e.g. basketball both_score, odd_even) are permanently purged from demoted_markets."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    # Insert phantom market
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_phantom_1', 'NBA|full_game|both_score', 'NBA', 'full_game', 'both_score', 'both_score',
            '2026-10-31T00:00:00Z', -0.50, -5.00, 10, 'Demoted test', '{}',
            'fake_model_123', 'SomeArchetype', 0.05, 20, 'incubating_with_new_model', '2026-10-31T00:00:00Z'
        )
    """)
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_phantom_count'] == 1
    assert 'NBA|full_game|both_score' in res['evicted_phantom']

    # Verify permanently purged from demoted_markets (row no longer exists)
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'NBA|full_game|both_score'").fetchone()
    conn.close()
    assert row is None


def test_unverified_deadlock_reset(test_env):
    """Verifies that legacy rows with 0% historical ROI and 0 samples are reset to Tier 1 (research_training, model=NULL)."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    # Insert active league wager so league is in-season
    conn.execute("""
        INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status)
        VALUES ('w_active', 'NBA', 'full_game', 'spreads', datetime('now'), 'open')
    """)
    # Insert legacy unverified market
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_legacy_1', 'NBA|half_1|spreads', 'NBA', 'half_1', 'spreads', 'spreads',
            '2026-10-28T00:00:00Z', -0.20, -4.00, 10, 'Legacy test', '{}',
            'codex_synth_nba_half_1_1028_000000', 'AsymmetricLaplaceSpreadModel', 0.0, 0,
            'incubating_with_new_model', '2026-10-28T00:00:00Z'
        )
    """)
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_unverified_count'] == 1
    assert 'NBA|half_1|spreads' in res['evicted_unverified']

    # Verify in DB
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'NBA|half_1|spreads'").fetchone()
    conn.close()
    assert row[0] == 'research_training'
    assert row[1] is None
    assert 'Tier 2 Deadlock Reset' in row[2]


def test_fast_fail_live_eviction(test_env):
    """Verifies that models suffering severe live losses (N >= 10, ROI <= -15%) are evicted to Tier 1 immediately."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    # Active league
    conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act', 'EPL', 'full_game', 'totals', datetime('now'), 'open')")

    # Legitimate candidate admitted 2 days ago
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_fast_fail', 'EPL|full_game|totals', 'EPL', 'full_game', 'totals', 'totals',
            '2026-11-01T00:00:00Z', -0.10, -2.00, 10, 'Test', '{}',
            'epl_totals_model_v1', 'DixonColesSoccerModel', 0.08, 50,
            'incubating_with_new_model', '2026-11-01T00:00:00Z'
        )
    """)
    # 10 settled losing wagers post-admission (ROI = -100%, PnL = -$10.00)
    for i in range(10):
        conn.execute(f"""
            INSERT INTO incubated_wagers (
                id, league, scope, family, sub_family, odds, pnl, status, settled_at, captured_at
            ) VALUES (
                'w_lost_{i}', 'EPL', 'full_game', 'totals', 'totals', 2.0, -1.0, 'settled',
                '2026-11-02T12:00:00Z', '2026-11-02T10:00:00Z'
            )
        """)
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_fast_fail_count'] == 1
    ev_info = res['evicted_fast_fail'][0]
    assert ev_info['segment_key'] == 'EPL|full_game|totals'
    assert ev_info['n_live'] == 10
    assert ev_info['live_roi'] == -1.0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'EPL|full_game|totals'").fetchone()
    conn.close()
    assert row[0] == 'research_training'
    assert row[1] is None
    assert 'Tier 2 Fast-Fail Eviction' in row[2]


def test_off_season_dormancy_protection(test_env):
    """Verifies that legitimate models in off-season leagues (0 recent fixtures) are NOT evicted."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    # League is in off-season (no wagers in incubated_wagers in last 14 days)
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_dormant', 'WNBA|full_game|totals', 'WNBA', 'full_game', 'totals', 'totals',
            '2026-10-15T00:00:00Z', -0.05, -1.00, 10, 'Test', '{}',
            'wnba_totals_proven_model', 'NegativeBinomialOverdispersionModel', 0.12, 60,
            'incubating_with_new_model', '2026-10-20T00:00:00Z'
        )
    """)
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['dormant_count'] == 1
    assert 'WNBA|full_game|totals' in res['dormant_markets']
    assert res['evicted_fast_fail_count'] == 0
    assert res['evicted_stagnant_count'] == 0
    assert res['evicted_unverified_count'] == 0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key = 'WNBA|full_game|totals'").fetchone()
    conn.close()
    # Stays safely in Tier 2!
    assert row[0] == 'incubating_with_new_model'
    assert row[1] == 'wnba_totals_proven_model'


def test_stagnation_eviction(test_env):
    """Verifies that models with 25+ settled live wagers that fail the +3.5% ROI hurdle are evicted to Tier 1."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act2', 'NHL', 'full_game', 'totals', datetime('now'), 'open')")

    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_stagnant', 'NHL|full_game|totals', 'NHL', 'full_game', 'totals', 'totals',
            '2026-10-20T00:00:00Z', -0.05, -1.00, 10, 'Test', '{}',
            'nhl_totals_model_v2', 'WeibullRenewalHazardModel', 0.05, 40,
            'incubating_with_new_model', '2026-10-20T00:00:00Z'
        )
    """)
    # 25 wagers: 12 wins (odds 2.0 -> +$12) and 13 losses (-$13) -> Net PnL -$1.00, ROI = -4.0% (not fast-fail, but stagnant/unprofitable)
    for i in range(12):
        conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at, captured_at) VALUES ('w_win_{i}', 'NHL', 'full_game', 'totals', 'totals', 2.0, 1.0, 'settled', '2026-10-25T12:00:00Z', '2026-10-25T10:00:00Z')")
    for i in range(13):
        conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at, captured_at) VALUES ('w_loss_{i}', 'NHL', 'full_game', 'totals', 'totals', 2.0, -1.0, 'settled', '2026-10-26T12:00:00Z', '2026-10-26T10:00:00Z')")
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_stagnant_count'] == 1
    ev_info = res['evicted_stagnant'][0]
    assert ev_info['segment_key'] == 'NHL|full_game|totals'
    assert ev_info['n_live'] == 25
    assert ev_info['live_pnl'] == -1.0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'NHL|full_game|totals'").fetchone()
    conn.close()
    assert row[0] == 'research_training'
    assert row[1] is None
    assert 'Tier 2 Stagnation Eviction' in row[2]


def test_retained_healthy_model(test_env):
    """Verifies that a healthy model (positive ROI, N=15, on track) is retained in Tier 2."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act3', 'NBA', 'full_game', 'spreads', datetime('now'), 'open')")

    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_healthy', 'NBA|full_game|spreads', 'NBA', 'full_game', 'spreads', 'spreads',
            '2026-11-01T00:00:00Z', -0.05, -1.00, 10, 'Test', '{}',
            'nba_spreads_healthy_v1', 'SkellamDifferentialModel', 0.06, 50,
            'incubating_with_new_model', '2026-11-01T00:00:00Z'
        )
    """)
    # 15 wagers: 10 wins (odds 1.95 -> +0.95), 5 losses (-1.0) -> PnL +$4.50, ROI +30%
    for i in range(10):
        conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at, captured_at) VALUES ('w_hwin_{i}', 'NBA', 'full_game', 'spreads', 'spreads', 1.95, 0.95, 'settled', '2026-11-03T12:00:00Z', '2026-11-03T10:00:00Z')")
    for i in range(5):
        conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at, captured_at) VALUES ('w_hloss_{i}', 'NBA', 'full_game', 'spreads', 'spreads', 1.95, -1.0, 'settled', '2026-11-03T12:00:00Z', '2026-11-03T10:00:00Z')")
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['retained_count'] == 1
    assert 'NBA|full_game|spreads' in res['retained_markets']
    assert res['evicted_fast_fail_count'] == 0
    assert res['evicted_stagnant_count'] == 0
    assert res['evicted_unverified_count'] == 0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key = 'NBA|full_game|spreads'").fetchone()
    conn.close()
    assert row[0] == 'incubating_with_new_model'
    assert row[1] == 'nba_spreads_healthy_v1'


def test_evict_orphaned_model_without_id_or_archetype(test_env):
    """Verifies that an orphaned market with NULL model_id or archetype is reset to Tier 1 R&D."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act4', 'NBA', 'full_game', 'totals', datetime('now'), 'open')")
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games,
            reason, stats, incubated_model_id, model_archetype, historical_roi, historical_sample_size,
            incubation_phase, admitted_to_incubator_at
        ) VALUES (
            'd_orphan', 'NBA|full_game|totals', 'NBA', 'full_game', 'totals', 'totals',
            '2026-11-01T00:00:00Z', -0.10, -5.00, 20, 'Test', '{}',
            NULL, NULL, 0.04, 30,
            'incubating_with_new_model', '2026-11-01T00:00:00Z'
        )
    """)
    conn.commit()
    conn.close()

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_unverified_count'] == 1
    assert 'NBA|full_game|totals' in res['evicted_unverified']

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'NBA|full_game|totals'").fetchone()
    conn.close()
    assert row[0] == 'research_training'
    assert row[1] is None
    assert 'Tier 2 Integrity Reset' in row[2]


def test_virtual_drawdown_barrier_prevents_graduation(test_env):
    """Verifies that an active drawdown in recent virtual wagers prevents Crucible graduation."""
    data_dir, db_path, incubator = test_env
    conn = sqlite3.connect(str(db_path))

    # Market has 30 wagers: first 20 won big, but last 10 lost in a row
    # Total PnL: 20 * 1.5 - 10 * 1.0 = +20.0 (overall ROI = 20/30 = +66.7%), but recent 10 PnL = -10.0!
    for i in range(20):
        conn.execute(f"""
            INSERT INTO incubated_wagers (
                id, league, scope, family, sub_family, odds, pnl, brier_model, brier_market,
                dynamic_sample_power_req, status, settled_at, cohort
            ) VALUES (
                'w_early_{i}', 'EPL', 'full_game', 'totals', 'totals', 2.50, 1.50, 0.15, 0.25,
                25, 'settled', '2026-11-01T12:00:00Z', 'core_challenger'
            )
        """)
    for i in range(10):
        conn.execute(f"""
            INSERT INTO incubated_wagers (
                id, league, scope, family, sub_family, odds, pnl, brier_model, brier_market,
                dynamic_sample_power_req, status, settled_at, cohort
            ) VALUES (
                'w_late_{i}', 'EPL', 'full_game', 'totals', 'totals', 2.50, -1.00, 0.25, 0.20,
                25, 'settled', '2026-11-02T12:00:00Z', 'core_challenger'
            )
        """)
    conn.commit()
    conn.close()

    grads = incubator.evaluate_graduation()
    # Must NOT graduate because recent virtual wagers are experiencing an active drawdown!
    assert not any(g['segment_key'] == 'EPL|full_game|totals' for g in grads)

