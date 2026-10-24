"""Unit tests for Autonomous Model Discovery Engine and Advanced Literature Archetypes."""
from pathlib import Path
import sqlite3
import pytest

from betting_engine.autonomous_model_discovery import AutonomousModelDiscoveryEngine
from betting_engine.historical_trainer import HistoricalCandidateTrainer
from betting_engine.sandbox_models.archetypes.dixon_coles_soccer import DixonColesSoccerModel
from betting_engine.sandbox_models.archetypes.skellam_differential import SkellamDifferentialModel
from betting_engine.tournament_engine import TournamentEngine


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_incubator.sqlite3"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE demoted_markets (
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
            stats TEXT NOT NULL,
            incubated_model_id TEXT,
            model_archetype TEXT,
            model_params TEXT,
            historical_roi REAL,
            historical_brier_delta REAL,
            historical_sample_size INT DEFAULT 0,
            incubation_phase TEXT DEFAULT "research_training",
            admitted_to_incubator_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE incubated_wagers (
            id TEXT PRIMARY KEY,
            fixture_id TEXT,
            game_id TEXT,
            league TEXT,
            scope TEXT,
            family TEXT,
            sub_family TEXT,
            raw_market_name TEXT,
            selection TEXT,
            side TEXT DEFAULT 'home',
            line REAL,
            odds REAL,
            model_prob REAL,
            push_prob REAL DEFAULT 0.0,
            market_prob REAL,
            ev REAL,
            dynamic_sample_power_req INT DEFAULT 25,
            captured_at TEXT,
            status TEXT DEFAULT 'open',
            settled_at TEXT,
            result TEXT,
            pnl REAL,
            brier_model REAL,
            brier_market REAL,
            metadata TEXT,
            cohort TEXT DEFAULT 'gap_market',
            model_id TEXT DEFAULT 'baseline',
            model_version TEXT DEFAULT 'v1.0'
        )
    """)
    # Insert sample demoted market in Tier 1
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family, demoted_at,
            rolling_roi, rolling_pnl, games, reason, stats, incubation_phase
        ) VALUES (
            'test_demoted_1', 'EPL|full_game|totals', 'EPL', 'full_game', 'totals', 'totals',
            '2026-10-24T00:00:00Z', -0.05, -2.5, 10, 'underperforming', '{}', 'research_training'
        )
    """)
    conn.commit()
    conn.close()
    return db_file


def test_dixon_coles_soccer_model_mechanics():
    """Verifies that Dixon-Coles adjusts low-scoring probabilities accurately."""
    model = DixonColesSoccerModel(
        model_id="test_dixon",
        league="EPL",
        scope="full_game",
        family="totals",
        low_score_rho=-0.10,
        home_advantage=1.12,
    )
    dist = model.predict_distribution({'home_expected': 1.6, 'away_expected': 1.1})
    assert dist.home_expected > 0.5
    assert dist.away_expected > 0.5
    assert 0.0 < dist.p_home < 1.0
    assert 0.0 < dist.p_away < 1.0

    # Test tau adjustments
    tau_00 = DixonColesSoccerModel.tau_adjustment(0, 0, 1.5, 1.2, -0.10)
    assert tau_00 > 1.0  # Negative rho increases 0-0 probability
    tau_10 = DixonColesSoccerModel.tau_adjustment(1, 0, 1.5, 1.2, -0.10)
    assert tau_10 < 1.0  # Negative rho reduces 1-0 probability


def test_skellam_differential_model_mechanics():
    """Verifies that Skellam distribution models discrete goal/run differentials."""
    model = SkellamDifferentialModel(
        model_id="test_skellam",
        league="MLS",
        scope="full_game",
        family="spreads",
        draw_inflation_eta=0.05,
    )
    dist = model.predict_distribution({'home_expected': 1.5, 'away_expected': 1.2})
    assert dist.home_expected > 0.5
    assert dist.away_expected > 0.5
    spread_p = dist.spread_prob(-0.5, 'home')
    assert 0.0 < spread_p < 1.0


def test_autonomous_model_discovery_catalog(temp_db: Path):
    """Verifies that AutonomousModelDiscoveryEngine discovers all 10 archetypes."""
    engine = AutonomousModelDiscoveryEngine(db_path=temp_db)
    archetypes = engine.list_available_archetypes()
    assert len(archetypes) >= 10
    names = [a['archetype_name'] for a in archetypes]
    assert 'DixonColesSoccerModel' in names
    assert 'SkellamDifferentialModel' in names
    assert 'MarkovChainTennisModel' in names
    assert 'WeibullRenewalHazardModel' in names
    assert 'ExtremeValueGumbelModel' in names


def test_autonomous_model_discovery_scan_and_summary(temp_db: Path):
    """Verifies scanning unresolved Tier 1 markets and reporting discovery summaries."""
    engine = AutonomousModelDiscoveryEngine(db_path=temp_db)
    unresolved = engine.scan_unresolved_tier1_markets()
    assert len(unresolved) == 1
    assert unresolved[0]['segment_key'] == 'EPL|full_game|totals'

    summary = engine.get_discovery_summary()
    assert summary['total_archetypes_in_library'] >= 10
    assert summary['tier1_unresolved_count'] == 1
    assert summary['tier2_incubating_count'] == 0
