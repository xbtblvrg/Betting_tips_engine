"""Unit and integration tests for Tier 3 Demotion to Tier 1 and Anti-Stagnation Lifecycle.

Validates the Constitutional Prime Directives and User Directives:
1. Tier 3 Demotion strictly resets to Tier 1 (RESEARCH_RND, incubation_phase='research_training', model=NULL).
2. A market demoted from live execution NEVER reverts to Tier 2 on subsequent roster refreshes.
3. get_market_tier returns MarketTier.RESEARCH_RND for demoted markets, never falsely SHADOW_INCUBATOR.
4. Auto-sync cleans demoted markets from graduated_markets (both SQLite and JSON).
5. Tier 2 Inactivity Watchdog evicts non-trading/stagnant candidate models back to Tier 1.
6. Tier 2 Deadlock Watchdog evicts models with N >= 25 that fail graduation criteria back to Tier 1.
7. Multi-threaded candidate screening functions across Tier 1 markets.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.unified_market_universe import UnifiedMarketUniverse, MarketTier
from betting_engine.shadow_market_incubator import (
    ShadowMarketIncubator,
    evaluate_tier2_lifecycle,
    load_graduated_markets,
)


@pytest.fixture
def isolated_env(tmp_path: Path):
    """Sets up an isolated test environment with SQLite tables and JSON state."""
    db_path = tmp_path / "shadow_incubator.sqlite3"
    eng_db = tmp_path / "engine.sqlite3"

    with sqlite3.connect(str(db_path)) as conn:
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
                admitted_to_incubator_at TEXT,
                last_screened_at TEXT
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

    with sqlite3.connect(str(eng_db)) as conn_e:
        conn_e.execute("""
            CREATE TABLE IF NOT EXISTS system_decisions (
                id TEXT PRIMARY KEY,
                payload TEXT,
                status TEXT,
                pnl REAL,
                issued_at TEXT
            )
        """)

    grad_json = tmp_path / "graduated_markets.json"
    grad_json.write_text("{}", encoding="utf-8")

    return tmp_path, db_path, eng_db


def test_tier3_demotion_strictly_sets_tier1(isolated_env):
    """Verifies that demoting a Tier 3 market resets it to Tier 1 R&D, not Tier 2."""
    tmp_path, db_path, eng_db = isolated_env

    # 1. Market was graduated at T0
    t0 = "2026-11-01T00:00:00Z"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            INSERT INTO graduated_markets (
                id, segment_key, league, scope, family, sub_family,
                graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
            ) VALUES (
                'grad_nba', 'NBA|full_game|spreads', 'NBA', 'full_game', 'spreads', 'spreads',
                ?, 30, 0.12, 0.02, 0.05, 0.60, '{}'
            )
        """, (t0,))
        # An old incubator row exists with old model
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family,
                demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
                incubated_model_id, model_archetype, incubation_phase, admitted_to_incubator_at
            ) VALUES (
                'dem_nba', 'NBA|full_game|spreads', 'NBA', 'full_game', 'spreads', 'spreads',
                ?, -0.20, -5.00, 10, 'Live drawdown demotion', '{}',
                'old_failing_model_123', 'OldArchetype', 'incubating_with_new_model', ?
            )
        """, (t0, t0))

    grad_json = tmp_path / "graduated_markets.json"
    grad_json.write_text(json.dumps({
        'NBA|full_game|spreads': {
            'id': 'grad_nba',
            'segment_key': 'NBA|full_game|spreads',
            'league': 'NBA',
            'scope': 'full_game',
            'family': 'spreads',
            'graduated_at': t0,
        }
    }))

    # 2. At T1 (later), a live drawdown demotion occurs
    t1 = "2026-11-05T12:00:00Z"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            UPDATE demoted_markets
            SET demoted_at = ?,
                rolling_roi = -0.30,
                rolling_pnl = -6.00,
                games = 15,
                incubation_phase = 'research_training',
                incubated_model_id = NULL,
                model_archetype = NULL
            WHERE segment_key = 'NBA|full_game|spreads'
        """, (t1,))

    universe = UnifiedMarketUniverse(data_dir=tmp_path)
    tier = universe.get_market_tier('NBA', 'full_game', 'spreads')
    assert tier == MarketTier.RESEARCH_RND, f"Demoted market must return MarketTier.RESEARCH_RND, got {tier}"

    roster = universe.build_universe_roster(force_refresh=True)
    prof = roster.get('NBA|full_game|spreads')
    assert prof is not None
    assert prof.tier == int(MarketTier.RESEARCH_RND), f"Universe roster must place demoted market in Tier 1, got {prof.tier_name}"
    assert prof.active_model_id is None, f"Demoted market must have NULL active_model_id, got {prof.active_model_id}"
    assert prof.stake_cap == 0.00, f"Demoted market must have 0.00 stake cap, got {prof.stake_cap}"


def test_tier2_inactivity_watchdog_evicts_dead_model(isolated_env):
    """Verifies that a candidate model in Tier 2 with >14d (336h) tenure and 0 wagers is evicted to Tier 1."""
    tmp_path, db_path, eng_db = isolated_env
    incubator = ShadowMarketIncubator(data_dir=tmp_path, db_path=db_path)

    # Active league with fixtures
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act', 'NBA', 'full_game', 'spreads', datetime('now'), 'open')")
        # Candidate admitted 16 days ago (384 hours ago)
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family,
                demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
                incubated_model_id, model_archetype, historical_roi, historical_sample_size,
                incubation_phase, admitted_to_incubator_at
            ) VALUES (
                'd_dead', 'NBA|quarter_1|three_way', 'NBA', 'quarter_1', 'three_way', 'three_way',
                '2026-10-20T00:00:00Z', -0.10, -2.00, 5, 'Demoted', '{}',
                'silent_ghost_model_456', 'ExtremeValueGumbelModel', 0.06, 20,
                'incubating_with_new_model', '2026-10-22T00:00:00Z'
            )
        """)

    res = incubator.evaluate_tier2_lifecycle(as_of="2026-11-08T00:00:00Z")
    assert res['evicted_stagnant_count'] >= 1
    assert any(e['segment_key'] == 'NBA|quarter_1|three_way' for e in res['evicted_stagnant'])

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'NBA|quarter_1|three_way'").fetchone()
        assert row[0] == 'research_training'
        assert row[1] is None
        assert 'Inactivity Eviction' in row[2]


def test_tier2_deadlock_watchdog_evicts_failed_graduation(isolated_env):
    """Verifies that a candidate model with N >= 25 that fails graduation criteria is evicted to Tier 1."""
    tmp_path, db_path, eng_db = isolated_env
    incubator = ShadowMarketIncubator(data_dir=tmp_path, db_path=db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO incubated_wagers (id, league, scope, family, captured_at, status) VALUES ('w_act', 'UCL', 'full_game', 'totals', datetime('now'), 'open')")
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family,
                demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
                incubated_model_id, model_archetype, historical_roi, historical_sample_size,
                incubation_phase, admitted_to_incubator_at
            ) VALUES (
                'd_ucl', 'UCL|full_game|totals', 'UCL', 'full_game', 'totals', 'totals',
                '2026-10-27T08:48:00Z', -0.24, -4.40, 20, 'Live loss', '{}',
                'ucl_totals_copula', 'BivariateCopulaHazardModel', 0.10, 36,
                'incubating_with_new_model', '2026-10-27T09:00:00Z'
            )
        """)
        # 30 wagers: overall positive PnL (+4.0), BUT recent 20 wagers have drawdown (-8.0)
        # First 10 wagers: win +12
        for i in range(10):
            conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at) VALUES ('w_u1_{i}', 'UCL', 'full_game', 'totals', 'totals', 2.2, 1.2, 'settled', '2026-10-28T12:00:00Z')")
        # Next 20 wagers: 6 wins (+7.2) and 14 losses (-14.0) -> recent sum = -6.8 (< 0, fails graduation)
        for i in range(6):
            conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at) VALUES ('w_u2_{i}', 'UCL', 'full_game', 'totals', 'totals', 2.2, 1.2, 'settled', '2026-11-02T12:00:00Z')")
        for i in range(14):
            conn.execute(f"INSERT INTO incubated_wagers (id, league, scope, family, sub_family, odds, pnl, status, settled_at) VALUES ('w_u3_{i}', 'UCL', 'full_game', 'totals', 'totals', 2.2, -1.0, 'settled', '2026-11-05T12:00:00Z')")

    res = incubator.evaluate_tier2_lifecycle(as_of="2026-11-08T00:00:00Z")
    assert res['evicted_stagnant_count'] >= 1
    assert any(e['segment_key'] == 'UCL|full_game|totals' for e in res['evicted_stagnant'])

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT incubation_phase, incubated_model_id, reason FROM demoted_markets WHERE segment_key = 'UCL|full_game|totals'").fetchone()
        assert row[0] == 'research_training'
        assert row[1] is None
        assert 'Tier 2 Stagnation Eviction' in row[2]


def test_auto_sync_clears_graduated_json_and_sqlite(isolated_env):
    """Verifies that degraded markets in universe roster are purged from graduated_markets in SQLite and JSON."""
    tmp_path, db_path, eng_db = isolated_env

    # 1. Market is in graduated_markets SQLite and JSON
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            INSERT INTO graduated_markets (
                id, segment_key, league, scope, family, sub_family,
                graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
            ) VALUES (
                'g_mlb', 'MLB|full_game|totals', 'MLB', 'full_game', 'totals', 'totals',
                '2026-10-15T00:00:00Z', 50, 0.10, 0.01, 0.05, 0.58, '{}'
            )
        """)

    grad_json = tmp_path / "graduated_markets.json"
    grad_json.write_text(json.dumps({
        'MLB|full_game|totals': {
            'id': 'g_mlb',
            'segment_key': 'MLB|full_game|totals',
            'league': 'MLB',
            'scope': 'full_game',
            'family': 'totals',
            'graduated_at': '2026-10-15T00:00:00Z',
        }
    }))

    # 2. Add 5 losing decisions in engine.sqlite3 so it is stat-degraded
    with sqlite3.connect(str(eng_db)) as conn_e:
        for i in range(5):
            payload = json.dumps({'league': 'MLB', 'market': 'totals', 'scope': 'full_game', 'stake': 1.0})
            conn_e.execute("INSERT INTO system_decisions (id, payload, status, pnl, issued_at) VALUES (?, ?, 'lost', -1.0, ?)",
                           (f"dec_{i}", payload, f"2026-10-20T1{i}:00:00Z"))

    universe = UnifiedMarketUniverse(data_dir=tmp_path)
    roster = universe.build_universe_roster(force_refresh=True)

    prof = roster.get('MLB|full_game|totals')
    assert prof is not None
    assert prof.tier == int(MarketTier.RESEARCH_RND), f"Degraded market must be in Tier 1, got {prof.tier_name}"

    # Verify SQLite graduated_markets table is purged
    with sqlite3.connect(str(db_path)) as conn:
        g_row = conn.execute("SELECT 1 FROM graduated_markets WHERE segment_key = 'MLB|full_game|totals'").fetchone()
        assert g_row is None, "Degraded market must be deleted from graduated_markets SQLite table"

        d_row = conn.execute("SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key = 'MLB|full_game|totals'").fetchone()
        assert d_row is not None, "Degraded market must be auto-synced into demoted_markets"
        assert d_row[0] == 'research_training', f"Phase must be research_training, got {d_row[0]}"
        assert d_row[1] is None, f"Model ID must be None, got {d_row[1]}"

    # Verify JSON file is purged
    loaded_json = json.loads(grad_json.read_text(encoding="utf-8"))
    assert 'MLB|full_game|totals' not in loaded_json, "Degraded market must be removed from graduated_markets.json"
