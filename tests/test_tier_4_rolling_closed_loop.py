"""Anti-Tautological Verification Suite: Tier 4 Rolling Closed-Loop & Scale-Invariant Units.

Validates:
1. Zero Hardcoded Clamps (Commandment 3): Thresholds scale dynamically in risk units (PnL / avg_stake).
2. Live Proven Alpha Rolling Quorum (Commandment 6 & User Directive):
   - Tier 4 admission requires N >= 10 games, rolling ROI >= +2.0%, rolling units >= +0.5U with active model.
   - Ancient all-time profit CANNOT mask recent rolling drawdown.
3. Closed-Loop Degradation & Immediate Suspension:
   - If a market in Tier 3 on Stake suffers rolling degradation, it drops to Capital Defense ($0.50 clamp)
     or R&D demotion ($0.00).
   - Polymarket order generation is instantly suspended to $0.00 virtual stakes.
4. Demoted Markets Exclusion:
   - Any market present in demoted_markets table is strictly quarantined ($0.00) on Polymarket.
"""
import json
import sqlite3
import pytest
from pathlib import Path

from betting_engine.tier_state_machine import classify_tier
from betting_engine.segment_scoreboard import compute_segment_scoreboard
from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
from betting_engine.storage import Store


def test_scale_invariant_risk_units():
    """Verifies that tier classification uses risk units rather than static dollar amounts."""
    # Scenario A: Low-stake exploration ($0.50 avg stake)
    # A loss of -$2.00 is 4 units of loss (severe) -> Capital Defense
    low_stake_loss = classify_tier(
        games_count=4,
        pnl=-2.00,
        roi=-1.0,
        staked=2.00,
        rolling_games=4,
        rolling_pnl=-2.00,
        rolling_roi=-1.0,
        rolling_staked=2.00,
    )
    assert low_stake_loss['tier'] == 'UNDERPERFORMING'
    assert low_stake_loss['sub_tier'] == 'CAPITAL_DEFENSE'

    # Scenario B: High-stake Kelly alpha ($8.00 avg stake)
    # A single lost bet of -$8.00 over 6 games is only 1 unit of loss (-1.0U) with positive overall ROI
    # At 6 games, staked=$48, pnl=+$8.00 (roi=+16.7%), rolling 4 games: staked=$32, pnl=+$4.00
    high_stake_minor_variance = classify_tier(
        games_count=6,
        pnl=8.00,
        roi=0.167,
        staked=48.00,
        rolling_games=4,
        rolling_pnl=4.00,
        rolling_roi=0.125,
        rolling_staked=32.00,
    )
    # Remains in Solid/Super Alpha without false static dollar demotion
    assert high_stake_minor_variance['tier'] == 'ALPHA_BOOST'


def test_rolling_drawdown_overrides_ancient_all_time_profit():
    """Verifies that recent rolling drawdown prevents Alpha Boost even if all-time PnL is positive."""
    # Ancient profit: +$20.00 over 50 games.
    # Recent rolling 10 games: -$8.00 PnL (rolling ROI -40.0%, units = -4.0U)
    result = classify_tier(
        games_count=50,
        pnl=20.00,
        roi=0.20,
        staked=100.00,
        rolling_games=10,
        rolling_pnl=-8.00,
        rolling_roi=-0.40,
        rolling_staked=20.00,
    )
    # Must be demoted to Capital Defense due to recent rolling breakdown!
    assert result['tier'] == 'UNDERPERFORMING'
    assert result['sub_tier'] == 'CAPITAL_DEFENSE'
    assert result['stake_cap'] == 0.50


def test_polymarket_demoted_markets_quarantine(tmp_path):
    """Verifies that any market in demoted_markets table is strictly blocked from Polymarket trading."""
    db_path = tmp_path / "engine.sqlite3"
    inc_db_path = tmp_path / "shadow_incubator.sqlite3"
    log_path = tmp_path / "orders.jsonl"
    Store(db_path)

    # Populate shadow_incubator with demoted NCAAF spreads
    with sqlite3.connect(inc_db_path) as inc_conn:
        inc_conn.execute("""
            CREATE TABLE IF NOT EXISTS demoted_markets (
                id TEXT PRIMARY KEY,
                segment_key TEXT NOT NULL UNIQUE,
                league TEXT NOT NULL,
                scope TEXT NOT NULL,
                family TEXT NOT NULL,
                sub_family TEXT NOT NULL,
                demoted_at TEXT NOT NULL,
                rolling_roi REAL NOT NULL,
                rolling_pnl REAL NOT NULL,
                games INT NOT NULL,
                reason TEXT NOT NULL,
                stats TEXT NOT NULL
            )
        """)
        inc_conn.execute("""
            INSERT INTO demoted_markets VALUES (
                'demote_1', 'NCAAF|full_game|spreads', 'NCAAF', 'full_game', 'spreads', 'spreads',
                '2026-11-05T00:00:00Z', -0.29, -5.19, 20, 'Rolling drawdown', '{}'
            )
        """)

    pe = PolymarketAutonomousExecutor(db_path=db_path, log_path=log_path, dry_run=True)

    is_elig, reason, cap = pe.is_market_eligible("NCAAF", "full_game", "spreads", require_tier_4=True)
    assert is_elig is False
    assert cap == 0.0
    assert "DEMOTED_IN_SHADOW_INCUBATOR" in reason


def test_polymarket_tier_4_rolling_quorum(tmp_path):
    """Verifies that Tier 4 Polymarket entry requires N >= 10 and Rolling ROI >= +2.0%."""
    db_path = tmp_path / "engine.sqlite3"
    log_path = tmp_path / "orders.jsonl"
    Store(db_path)

    pe = PolymarketAutonomousExecutor(db_path=db_path, log_path=log_path, dry_run=True)

    # 1. Market with rolling N=12, Rolling ROI=+5.5%, Rolling Units=+1.8U -> Qualifies for Tier 4!
    pe._cached_scoreboard = {
        "alpha_segments": [
            {
                "segment": "NBA|full_game|spreads",
                "league": "NBA",
                "scope": "full_game",
                "family": "spreads",
                "pnl": 15.00,
                "roi_pct": 8.0,
                "games": 30,
                "rolling_games": 12,
                "rolling_pnl": 5.50,
                "rolling_roi_pct": 5.5,
                "rolling_units": 1.8,
                "sub_tier": "SOLID_ALPHA",
            }
        ],
        "exploration_segments": [
            # 2. Market with rolling N=5, Rolling ROI=+15.0% (early probation, insufficient rolling sample)
            {
                "segment": "NFL|full_game|totals",
                "league": "NFL",
                "scope": "full_game",
                "family": "totals",
                "pnl": 3.00,
                "roi_pct": 15.0,
                "games": 5,
                "rolling_games": 5,
                "rolling_pnl": 3.00,
                "rolling_roi_pct": 15.0,
                "rolling_units": 1.5,
                "sub_tier": "EXPLORATION",
            }
        ],
        "underperforming_segments": [],
    }
    pe._scoreboard_cached_at = 9999999999.0

    # NBA spreads qualifies
    is_nba, r_nba, cap_nba = pe.is_market_eligible("NBA", "full_game", "spreads", require_tier_4=True)
    assert is_nba is True
    assert cap_nba >= 3.50
    assert "TIER_4_LIVE_PROVEN_ALPHA" in r_nba

    # NFL totals has insufficient rolling sample (N=5 < 10) -> Rejected from Tier 4
    is_nfl, r_nfl, cap_nfl = pe.is_market_eligible("NFL", "full_game", "totals", require_tier_4=True)
    assert is_nfl is False
    assert cap_nfl == 0.0
    assert "PROBATION_INSUFFICIENT_SAMPLE" in r_nfl
