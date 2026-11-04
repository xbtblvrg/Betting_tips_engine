"""Tests for Historical Candidate Trainer and Demoted Market Incubation Bridge."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.historical_trainer import HistoricalCandidateTrainer, CandidateTrainingResult
from betting_engine.demoted_market_bridge import DemotedMarketIncubationBridge
from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.tournament_engine import TournamentEngine


@pytest.fixture
def temp_incubator_db(tmp_path: Path):
    db_file = tmp_path / "shadow_incubator.sqlite3"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incubated_wagers (
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
    conn.execute("""
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
        CREATE TABLE IF NOT EXISTS graduated_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
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

    # Populate 30 historical settled wagers for NFL|full_game|spreads with line=-1.5 (home expected margin +3.0)
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(30):
        # 75% win rate produces positive ROI on odds 2.05
        is_win = (i % 4 != 0)
        res = 'won' if is_win else 'lost'
        pnl_val = 1.05 if is_win else -1.0
        conn.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                selection, side, line, odds, model_prob, market_prob, ev,
                captured_at, status, result, pnl, brier_model, brier_market
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'settled', ?, ?, ?, ?)
        """, (
            f"wager_{i}", f"fix_{i}", f"game_{i}", "NFL", "full_game", "spreads", "spreads",
            "Home -1.5", "home", -1.5, 2.05, 0.62, 0.48, 0.20,
            f"2026-10-10T1{i%9}:00:00Z", res, pnl_val, 0.18, 0.26
        ))

    # Add one demoted market in research_training phase
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family,
            demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
            incubation_phase
        ) VALUES (
            'demote_test_1', 'NFL|full_game|spreads', 'NFL', 'full_game', 'spreads', 'spreads',
            ?, -0.25, -5.0, 20, 'Continuous Review deficit', '{}', 'research_training'
        )
    """, (now_iso,))

    conn.commit()
    conn.close()
    return db_file


def test_historical_candidate_trainer_training(temp_incubator_db: Path):
    trainer = HistoricalCandidateTrainer(db_path=temp_incubator_db)
    result = trainer.train_and_screen_demoted_market('NFL|full_game|spreads', min_samples=10)
    assert result is not None
    assert result.segment_key == 'NFL|full_game|spreads'
    assert result.oos_sample_size > 0
    assert result.oos_simulated_roi > 0.0
    assert result.is_qualified is True
    assert result.model_archetype in (
        'BivariateCopulaHazardModel',
        'NegativeBinomialOverdispersionModel',
        'OddsDampedKellyShrinkageModel',
        'SituationalFatigueModel',
        'ZeroInflatedPoissonModel'
    )


def test_demoted_market_incubation_bridge(temp_incubator_db: Path, tmp_path: Path):
    bridge = DemotedMarketIncubationBridge(data_dir=tmp_path, db_path=temp_incubator_db)
    sync_res = bridge.synchronize_demoted_markets(max_train_per_run=10)
    assert sync_res['status'] == 'success'
    assert sync_res['newly_admitted_count'] == 1

    # Verify database was updated
    conn = sqlite3.connect(str(temp_incubator_db))
    row = conn.execute("""
        SELECT incubation_phase, incubated_model_id, model_archetype, historical_roi, admitted_to_incubator_at
        FROM demoted_markets WHERE id = 'demote_test_1'
    """).fetchone()
    conn.close()

    assert row[0] == 'incubating_with_new_model'
    assert row[1] is not None
    assert row[2] is not None
    assert row[3] is not None and row[3] > 0.0
    assert row[4] is not None


def test_incubator_status_exposes_model_and_edge(temp_incubator_db: Path, tmp_path: Path):
    bridge = DemotedMarketIncubationBridge(data_dir=tmp_path, db_path=temp_incubator_db)
    bridge.synchronize_demoted_markets(max_train_per_run=10)

    incubator = ShadowMarketIncubator(data_dir=tmp_path)
    status = incubator.get_incubator_status()
    assert 'demoted_markets' in status
    assert len(status['demoted_markets']) == 1
    dm = status['demoted_markets'][0]
    assert dm['incubation_phase'] == 'incubating_with_new_model'
    assert dm['incubated_model_id'] is not None
    assert dm['historical_roi_pct'] is not None


def test_screen_candidate_against_incumbent():
    """Verifies that a challenger candidate only passes if it beats incumbent by >= 1.5% ROI and +0.001 Brier."""
    trainer = HistoricalCandidateTrainer()
    cand = CandidateTrainingResult(
        segment_key='NBA|full_game|spreads',
        model_id='challenger_model_1',
        model_archetype='OddsDampedKellyShrinkageModel',
        oos_simulated_roi=0.060,  # 6.0%
        oos_delta_brier=0.0035,
        oos_max_drawdown=0.10,
    )

    # 1. Beating a lower incumbent (3.0% ROI, 0.0010 Brier) -> Pass
    passed, reasons = trainer.screen_candidate_against_incumbent(
        candidate_result=cand,
        incumbent_roi=0.030,
        incumbent_delta_brier=0.0010,
    )
    assert passed is True
    assert reasons == []

    # 2. Failing against a high incumbent (5.5% ROI -> needs >= 7.0%) -> Reject
    passed_fail, reasons_fail = trainer.screen_candidate_against_incumbent(
        candidate_result=cand,
        incumbent_roi=0.055,
        incumbent_delta_brier=0.0010,
    )
    assert passed_fail is False
    assert any("does not exceed incumbent" in r for r in reasons_fail)


def test_fast_track_composite_power_graduation(temp_incubator_db: Path, tmp_path: Path):
    """Verifies that a low-cadence market with N_live=12 and N_oos=200 graduates via Composite Power."""
    # Insert 12 winning live wagers for UCL|full_game|totals
    conn = sqlite3.connect(str(temp_incubator_db))
    # Register historical OOS backing in demoted_markets
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family,
            demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
            historical_sample_size, historical_roi, historical_brier_delta, incubation_phase
        ) VALUES (
            'dem_ucl_1', 'UCL|full_game|totals', 'UCL', 'full_game', 'totals', 'totals',
            '2026-10-01T00:00:00Z', -0.10, -2.0, 10, 'initial', '{}',
            200, 0.08, 0.005, 'incubating_with_new_model'
        )
    """)

    for i in range(12):
        is_win = (i % 5 != 0)  # ~80% win rate
        res = 'won' if is_win else 'lost'
        pnl_val = 0.95 if is_win else -1.0
        conn.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                selection, side, line, odds, model_prob, market_prob, ev,
                captured_at, settled_at, status, result, pnl, brier_model, brier_market
            ) VALUES (?, ?, ?, 'UCL', 'full_game', 'totals', 'totals',
                'Over 2.5', 'home', 2.5, 1.95, 0.60, 0.49, 0.15,
                '2026-10-20T10:00:00Z', '2026-10-20T12:00:00Z', 'settled', ?, ?, 0.18, 0.25)
        """, (f"ucl_{i}", f"ucl_fix_{i}", f"ucl_g_{i}", res, pnl_val))
    conn.commit()
    conn.close()

    incubator = ShadowMarketIncubator(data_dir=tmp_path)
    grad_records = incubator.evaluate_graduation(as_of="2026-10-25T12:00:00Z")

    # The UCL market should graduate via Fast-Track Composite Power (Power = 12 + 0.20*200 = 52 >= 50)
    ucl_grads = [g for g in grad_records if g['segment_key'] == 'UCL|full_game|totals']
    assert len(ucl_grads) == 1
    assert ucl_grads[0]['stats']['cohort'] == 'fast_track_composite'
    assert ucl_grads[0]['stats']['sample_size'] == 12


def test_get_default_segment_context_all_scopes_and_sports():
    """Validates game partition scaling invariants across all leagues and scopes."""
    from betting_engine.historical_trainer import get_default_segment_context

    # 1. Basketball Quarters: All quarters must be exactly 0.25 of full game (28.0 vs 112.0)
    for q in ('quarter_1', 'quarter_2', 'quarter_3', 'quarter_4', 'quarter_4_ot'):
        ctx = get_default_segment_context('NBA', q)
        assert ctx['home_expected'] == 28.0, f"Expected 28.0 for NBA {q}, got {ctx['home_expected']}"
        assert ctx['away_expected'] == 27.0, f"Expected 27.0 for NBA {q}, got {ctx['away_expected']}"

    # 2. Basketball Halves: Exactly 0.50 of full game
    for h in ('half_1', 'half_2'):
        ctx = get_default_segment_context('NBA', h)
        assert ctx['home_expected'] == 56.0
        assert ctx['away_expected'] == 54.0

    # 3. Tennis Sets: 0.45 of match (5.76 vs 12.8 total match games)
    for p in ('period_1', 'period_2'):
        ctx = get_default_segment_context('ATP', p)
        assert ctx['home_expected'] == 5.76
        assert ctx['away_expected'] == 5.31

    # 4. Baseball Innings
    ctx_1_5 = get_default_segment_context('MLB', 'innings_1_5')
    assert ctx_1_5['home_expected'] == 2.53  # 4.6 * 0.55
    ctx_1 = get_default_segment_context('MLB', 'inning_1')
    assert ctx_1['home_expected'] == 0.69    # 4.6 * 0.15

    # 5. Full Game Invariant: Scale must be exactly 1.0
    ctx_full = get_default_segment_context('NBA', 'full_game')
    assert ctx_full['home_expected'] == 112.0
    assert ctx_full['away_expected'] == 108.0

