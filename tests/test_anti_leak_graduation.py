import json
import sqlite3
import pytest
from pathlib import Path
from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.unified_market_universe import UnifiedMarketUniverse, MarketTier

def test_losing_live_market_cannot_graduate(tmp_path: Path):
    """Anti-Leak Invariant: A market with live settled losses must never graduate into Tier 3,
    even if it has dozens of profitable shadow wagers."""
    incubator = ShadowMarketIncubator(data_dir=tmp_path)
    engine_db = tmp_path / "engine.sqlite3"

    # Setup engine db with a losing settled live bet
    with sqlite3.connect(str(engine_db)) as conn_eng:
        conn_eng.execute("""
            CREATE TABLE system_decisions (
                payload TEXT,
                status TEXT,
                pnl REAL,
                issued_at TEXT
            )
        """)
        dec_payload = {
            'league': 'NBA',
            'market': 'spreads',
            'scope': 'half_1',
            'selection': 'Boston Celtics -4.5',
            'stake': 5.0
        }
        conn_eng.execute(
            "INSERT INTO system_decisions VALUES (?, 'lost', -5.0, '2026-10-30T10:00:00Z')",
            (json.dumps(dec_payload),)
        )

    # Setup 30 profitable shadow wagers in incubator for NBA|half_1|spreads
    with incubator._get_connection() as conn_inc:
        batch = []
        for i in range(30):
            batch.append((
                f"w_{i}", "g1", "g1", "NBA", "half_1", "spreads", "spreads",
                "Spread", "Boston Celtics -4.5", "home", -4.5, 1.95, 0.60, 0.0,
                0.51, 0.17, 25, "2026-10-29T10:00:00Z", "settled", "2026-10-29T12:00:00Z",
                "won", 0.95, 0.16, 0.24, "{}", "tournament_challenger"
            ))
        conn_inc.executemany("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)

    # Run graduation evaluation
    graduated = incubator.evaluate_graduation()
    # Must NOT graduate because live PnL is -$5.00
    assert not any(g['segment_key'] == 'NBA|half_1|spreads' for g in graduated)

def test_demoted_market_requires_post_demotion_wagers(tmp_path: Path):
    """Prime Directive 6 & 7: Demoted market cannot use pre-demotion wagers to graduate."""
    incubator = ShadowMarketIncubator(data_dir=tmp_path)

    # Demote market at 2026-10-30T12:00:00Z
    with incubator._get_connection() as conn_inc:
        conn_inc.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family,
                demoted_at, rolling_roi, rolling_pnl, games, reason, stats, incubation_phase
            ) VALUES ('d1', 'MLS|half_1|totals', 'MLS', 'half_1', 'totals', 'totals',
                      '2026-10-30T12:00:00Z', -0.30, -6.0, 10, 'Live drawdown', '{}', 'research_training')
        """)

        # Add 30 pre-demotion wagers settled at 2026-10-29
        batch = []
        for i in range(30):
            batch.append((
                f"w_pre_{i}", "g1", "g1", "MLS", "half_1", "totals", "totals",
                "Over 1.5", "Over", "over", 1.5, 2.05, 0.58, 0.0,
                0.48, 0.18, 25, "2026-10-29T10:00:00Z", "settled", "2026-10-29T12:00:00Z",
                "won", 1.05, 0.17, 0.26, "{}", "core_challenger"
            ))
        conn_inc.executemany("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)

    # Evaluate graduation: pre-demotion wagers must be ignored, cannot graduate
    graduated = incubator.evaluate_graduation()
    assert not any(g['segment_key'] == 'MLS|half_1|totals' for g in graduated)

    # Now add 26 post-demotion wagers settled at 2026-10-31T08:00:00Z
    with incubator._get_connection() as conn_inc:
        batch_post = []
        for i in range(26):
            batch_post.append((
                f"w_post_{i}", "g2", "g2", "MLS", "half_1", "totals", "totals",
                "Over 1.5", "Over", "over", 1.5, 2.05, 0.58, 0.0,
                0.48, 0.18, 25, "2026-10-31T06:00:00Z", "settled", "2026-10-31T08:00:00Z",
                "won", 1.05, 0.17, 0.26, "{}", "core_challenger"
            ))
        conn_inc.executemany("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch_post)

    # Now with 26 post-demotion wagers proving edge and zero live losses, it successfully re-graduates!
    graduated_post = incubator.evaluate_graduation()
    assert any(g['segment_key'] == 'MLS|half_1|totals' for g in graduated_post)

def test_unified_universe_quarantines_past_live_losses(tmp_path: Path):
    """Prime Directive 6: UnifiedMarketUniverse must quarantine markets with past live losses to Tier 2 ($0.00)."""
    umu = UnifiedMarketUniverse(data_dir=tmp_path)
    engine_db = tmp_path / "engine.sqlite3"
    incubator_db = tmp_path / "shadow_incubator.sqlite3"

    # Setup engine db with past live loss
    with sqlite3.connect(str(engine_db)) as conn_eng:
        conn_eng.execute("""
            CREATE TABLE system_decisions (
                payload TEXT,
                status TEXT,
                pnl REAL,
                issued_at TEXT
            )
        """)
        dec = {'league': 'WNBA', 'market': 'moneyline', 'scope': 'full_game', 'stake': 6.20}
        conn_eng.execute("INSERT INTO system_decisions VALUES (?, 'lost', -6.20, '2026-10-30T10:00:00Z')", (json.dumps(dec),))

    # Setup incubator db with market in graduated_markets
    with sqlite3.connect(str(incubator_db)) as conn_inc:
        conn_inc.execute("""
            CREATE TABLE graduated_markets (
                id TEXT PRIMARY KEY, segment_key TEXT, league TEXT, scope TEXT,
                family TEXT, sub_family TEXT, graduated_at TEXT, sample_size INT,
                roi REAL, delta_brier REAL, max_drawdown REAL, win_rate REAL, stats TEXT
            )
        """)
        conn_inc.execute("""
            INSERT INTO graduated_markets VALUES (
                'g1', 'WNBA|full_game|moneyline', 'WNBA', 'full_game', 'moneyline', 'moneyline',
                '2026-10-31T00:00:00Z', 50, 0.10, 0.01, 0.05, 0.60, '{}'
            )
        """)

    roster = umu.build_universe_roster()
    wnba_prof = roster.get('WNBA|full_game|moneyline')
    assert wnba_prof is not None
    # Must be quarantined to Tier 2 (SHADOW_INCUBATOR) with $0.00 stake cap!
    assert wnba_prof.tier == int(MarketTier.SHADOW_INCUBATOR)
    assert wnba_prof.stake_cap == 0.00
    assert "Quarantine" in wnba_prof.phase_detail
