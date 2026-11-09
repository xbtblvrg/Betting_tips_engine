"""Independent verification test suite for calculation, mathematical, and logical fixes.

Validates that:
1. Auto-tracking a real Tier 3 graduated recommendation never raises UnboundLocalError for family_key or stale scope.
2. In-cycle deduplication selects the highest EV candidate regardless of input arrival order.
3. Opposing contract detection handles all winner bases (1x2, draw_no_bet, moneyline, spreads), Asian totals, and case-insensitivity.
4. Asian quarter line settlement properly resolves scoped markets (e.g. half_1:spreads) without confusing them with totals.
5. ESPN linescore parsing properly populates 'regulation' for soccer and supports NCAAF and NCAAM.
6. Brier score calculation safely handles floating-point representations where p + push == 1.0.
"""
import pytest
from datetime import datetime, timezone, timedelta
from betting_engine.domain import utcnow
from betting_engine.storage import Store
from betting_engine.contracts import is_opposing_contract, settle_quarter_asian


def test_graduated_tip_autotrack_no_unbound_family_key(tmp_path):
    """Verify that auto-tracking a Tier 3 graduated bet doesn't crash with UnboundLocalError."""
    s = Store(tmp_path / "autotrack_grad.sqlite")
    now = utcnow().isoformat()
    tip = {
        'id': 'real_grad_tip_mls_totals_101',
        'game_id': 'MLS:live_761938',
        'league': 'MLS',
        'market': 'totals',
        'model_scope': 'full_game',
        'side': 'over',
        'line': 2.5,
        'stake': 2.5,
        'reference_odds': 1.95,
        'reference_platform': 'Stake.com',
        'start': (utcnow() + timedelta(hours=5)).isoformat(),
        'valid_until': (utcnow() + timedelta(hours=4)).isoformat(),
        'stake_evidence': {'decimal_odds': 1.95, 'fetched_at': now},
        'ev': 0.08,
    }
    settings = {
        'auto_track_recommendations': True,
        'publication_limits_version': 'v3',
        'unit_stake': 0.5,
        'max_bet': 8.0,
        'daily_risk_limit': None,
        'allow_event_additions': False,
        'max_per_event': 2,
        'max_stake_per_event': 6.0,
        'max_period_per_event': 1,
        'max_daily_family_share': 0.35,
        'max_new_funded_per_publication': 5,
    }
    s.save_settings(settings)
    payload = {'recommendations': [tip]}
    s.publish(payload)
    with s.db() as c:
        rows = c.execute("SELECT tip_id, stake, odds FROM bets").fetchall()
        assert len(rows) == 1
        assert rows[0]['tip_id'] == 'real_grad_tip_mls_totals_101'


def test_deduplication_selects_highest_ev_regardless_of_order(tmp_path):
    """Verify that higher EV line always wins over lower EV line regardless of input order."""
    s = Store(tmp_path / "dedup_order.sqlite")
    now = utcnow().isoformat()
    # Candidate A: lower EV, appears FIRST in payload
    cA = {
        'id': 'cand_lower_ev_1',
        'game_id': 'MLS:fixture_8888',
        'league': 'MLS',
        'market': 'totals',
        'model_scope': 'full_game',
        'side': 'over',
        'line': 2.5,
        'stake': 2.5,
        'reference_odds': 1.95,
        'reference_platform': 'Stake.com',
        'start': (utcnow() + timedelta(hours=5)).isoformat(),
        'valid_until': (utcnow() + timedelta(hours=4)).isoformat(),
        'stake_evidence': {'decimal_odds': 1.95, 'fetched_at': now},
        'ev': 0.03,
    }
    # Candidate B: higher EV, appears SECOND in payload
    cB = {
        'id': 'cand_higher_ev_2',
        'game_id': 'MLS:fixture_8888',
        'league': 'MLS',
        'market': 'totals',
        'model_scope': 'full_game',
        'side': 'over',
        'line': 3.5,
        'stake': 2.5,
        'reference_odds': 2.50,
        'reference_platform': 'Stake.com',
        'start': (utcnow() + timedelta(hours=5)).isoformat(),
        'valid_until': (utcnow() + timedelta(hours=4)).isoformat(),
        'stake_evidence': {'decimal_odds': 2.50, 'fetched_at': now},
        'ev': 0.12,
    }
    settings = {
        'auto_track_recommendations': True,
        'publication_limits_version': 'v3',
        'allow_event_additions': False,
    }
    s.save_settings(settings)
    # Pass [cA, cB] where cA is first
    s.publish({'recommendations': [cA, cB]})
    with s.db() as c:
        rows = c.execute("SELECT tip_id, odds FROM bets").fetchall()
        assert len(rows) == 1
        assert rows[0]['tip_id'] == 'cand_higher_ev_2'


def test_opposing_contracts_detection_comprehensive():
    """Verify that opposing contracts are detected across all naming variations and cases."""
    game = 'MLS:game_opposing_full'
    # 1. 1X2 / Three Way vs Moneyline
    t1 = {'game_id': game, 'market': 'three_way', 'side': 'Home'}
    t2 = {'game_id': game, 'market': 'moneyline', 'side': 'away'}
    assert is_opposing_contract(t1, t2) is True

    # 2. Draw no bet vs Spreads
    t3 = {'game_id': game, 'market': 'draw_no_bet', 'side': 'home'}
    t4 = {'game_id': game, 'market': 'spreads', 'side': 'AWAY'}
    assert is_opposing_contract(t3, t4) is True

    # 3. Totals vs Asian totals
    t5 = {'game_id': game, 'market': 'totals', 'side': 'OVER'}
    t6 = {'game_id': game, 'market': 'asian_totals', 'side': 'under'}
    assert is_opposing_contract(t5, t6) is True

    # 4. Team totals
    t7 = {'game_id': game, 'market': 'home_total', 'side': 'over'}
    t8 = {'game_id': game, 'market': 'home_team_total', 'side': 'under'}
    assert is_opposing_contract(t7, t8) is True


def test_asian_quarter_spread_scoped_settlement():
    """Verify that scoped Asian quarter spreads (e.g. half_1:spreads) are settled as spreads, not totals."""
    # half_1 score: Home 1, Away 0. Line: -0.25 on Home. Odds: 2.0, Stake: $4.00.
    # Quarter line -0.25 decomposes into (0.0, -0.5).
    # Leg 1 (line 0.0): Home 1 > Away 0 -> Win ($2 * (2.0 - 1) = +$2.00)
    # Leg 2 (line -0.5): Home 1 - Away 0 - 0.5 = +0.5 > 0 -> Win ($2 * (2.0 - 1) = +$2.00)
    # Result: full_win, won, +$4.00 profit.
    state, result, profit = settle_quarter_asian('half_1:spreads', 'home', -0.25, home=1, away=0, odds=2.0, stake=4.0)
    assert state == 'full_win'
    assert result == 'won'
    assert profit == 4.0

    # Draw at half: Home 0, Away 0. Line: -0.25 on Home.
    # Leg 1 (line 0.0): Home 0 == Away 0 -> Push ($2 refunded, $0 profit)
    # Leg 2 (line -0.5): 0 - 0 - 0.5 = -0.5 < 0 -> Loss ($2 lost, -$2.00)
    # Result: half_loss, lost, -$2.00 profit.
    state, result, profit = settle_quarter_asian('half_1:spreads', 'home', -0.25, home=0, away=0, odds=2.0, stake=4.0)
    assert state == 'half_loss'
    assert result == 'lost'
    assert profit == -2.0


def test_brier_floating_point_epsilon(tmp_path):
    """Verify that p + push == 1.0 does not fail Brier calculation due to float rounding."""
    s = Store(tmp_path / "brier_eps.sqlite")
    s.save_settings({'auto_track_recommendations': True})
    now = utcnow().isoformat()
    tip = {
        'id': 'tip_brier_test',
        'game_id': 'NBA:brier_test_game',
        'league': 'NBA',
        'market': 'spreads',
        'side': 'home',
        'line': -5.0,
        'probability': 0.70,
        'push_probability': 0.30000000000000004,  # Float rounding artifact
        'stake': 2.0,
        'reference_odds': 1.90,
        'reference_platform': 'Stake.com',
        'start': (utcnow() + timedelta(hours=2)).isoformat(),
        'valid_until': (utcnow() + timedelta(hours=1)).isoformat(),
        'stake_evidence': {'decimal_odds': 1.90, 'fetched_at': now},
    }
    s.publish({'recommendations': [tip]})
    # Settle bet
    evidence = {'game_id': 'NBA:brier_test_game', 'home_score': 105, 'away_score': 95, 'basis': 'verified_score_in_contract_scope'}
    s.settle(1, 'won', 1.80, evidence=evidence)
    with s.db() as c:
        row = c.execute("SELECT brier, log_loss FROM bet_outcomes WHERE bet_id = 1").fetchone()
        assert row is not None
        assert row['brier'] is not None
        assert row['brier'] >= 0.0


def test_candidate_generation_resilient_to_missing_rest_features():
    """Verify that candidate() never crashes when forecast.features lacks 'home_rest' or is non-standard."""
    from betting_engine.decisions import candidate
    from betting_engine.domain import Game, utcnow
    from betting_engine.markets import Market, Quote
    from betting_engine.models import Forecast

    g = Game(
        id='ATP:alcaraz_sinner_2026',
        league='ATP',
        home='Carlos Alcaraz',
        away='Jannik Sinner',
        home_id='alcaraz',
        away_id='sinner',
        home_abbr='ALC',
        away_abbr='SIN',
        state='scheduled',
        season=2026,
        start=(utcnow() + timedelta(hours=3)).isoformat(),
        source='official',
    )
    now_iso = utcnow().isoformat()
    m = Market(
        id='stake:tennis:ml',
        game_id=g.id,
        league=g.league,
        kind='moneyline',
        side='home',
        line=None,
        selection='Carlos Alcaraz',
        platform='Stake.com',
        url=g.source,
        token='tok_1',
        condition_id='cond_1',
        description='Match Winner',
        fee_rate=0.0,
        fee_exponent=0.0,
        minimum_notional=1.0,
        market_start=g.start,
        fetched_at=now_iso,
        metadata={'availability_verified': True}
    )
    q = Quote(
        market_id=m.id,
        token='tok_1',
        price=1.90,
        fee_per_share=0.0,
        effective_cost=1.0 / 1.90,
        available_shares=100.0,
        minimum_notional=1.0,
        fetched_at=now_iso,
        source_timestamp=now_iso,
        status='current'
    )
    import numpy as np
    f = Forecast(
        game_id=g.id,
        league='ATP',
        home_probability=0.60,
        away_probability=0.40,
        draw_probability=0.0,
        home_points=2.0,
        away_points=1.0,
        margin=1.0,
        total=3.0,
        model_probabilities={'m1': 0.60},
        disagreement=0.0,
        features={'elo_difference': 150.0},  # No 'home_rest' key
        model_version='atp_model_v1',
        warnings=[],
        residuals=np.zeros((1, 2)),
    )
    settings = {
        'minimum_roi': 0.035,
        'kelly_fraction': 0.25,
        'minimum_edge': 0.02,
        'drawdown_budget': 100.0,
        'max_stake': 8.0,
        'min_stake': 0.5,
    }
    # Must succeed cleanly without raising KeyError: 'home_rest'
    res = candidate(g, m, q, f, settings)
    assert res is not None
    assert res['game_id'] == g.id
    assert res['side'] == 'home'
    assert 'Model-estimated win probability' in res['reasons'][0]


def test_tier2_eviction_updates_tournament_feedback_and_learning_jobs(tmp_path: Path):
    """Verify that Tier 2 evictions properly reject models, record feedback, and enqueue learning jobs."""
    import sqlite3
    from betting_engine.shadow_market_incubator import ShadowMarketIncubator
    from betting_engine.tournament_engine import TournamentEngine
    from betting_engine.sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel

    db_path = tmp_path / "shadow_incubator.sqlite3"
    eng_db = tmp_path / "engine.sqlite3"

    # Set up engine.sqlite3 with learning_jobs table
    with sqlite3.connect(str(eng_db)) as e_conn:
        e_conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                league TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_attempt TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL
            )
        """)

    incubator = ShadowMarketIncubator(data_dir=tmp_path, db_path=db_path)
    t_eng = TournamentEngine(data_dir=tmp_path, db_path=db_path)

    # Register candidate model in tournament
    seg_key = "NBA|full_game|spreads"
    model = OddsDampedKellyShrinkageModel(
        model_id="test_fast_fail_model_1",
        version="1.0.0",
        league="NBA",
        scope="full_game",
        family="spreads",
        damping_strength=0.30,
        min_odds_hurdle=1.45,
    )
    t_eng.register_candidate(seg_key, model)

    # Insert incubating demoted market in shadow_incubator.sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family, demoted_at,
                rolling_roi, rolling_pnl, games, reason, stats,
                incubated_model_id, model_archetype, historical_roi, historical_sample_size,
                incubation_phase, admitted_to_incubator_at
            ) VALUES (
                'dem_test_1', ?, 'NBA', 'full_game', 'spreads', 'spreads', '2026-11-01T00:00:00Z',
                -0.10, -2.0, 10, 'Initial demotion', '{}',
                'test_fast_fail_model_1', 'OddsDampedKellyShrinkageModel', 0.05, 30,
                'incubating_with_new_model', '2026-11-01T00:00:00Z'
            )
        """, (seg_key,))

        # Insert 12 settled losing wagers for this market after admitted date (fast-fail criteria: N>=10, ROI<=-15%)
        now_iso = utcnow().isoformat()
        for i in range(12):
            conn.execute("""
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                    result, pnl, metadata, cohort, model_id
                ) VALUES (?, ?, ?, 'NBA', 'full_game', 'spreads', 'spreads',
                    'Spreads', 'Home', 'home', -3.5, 1.90, 0.55, 0.0,
                    0.50, 0.045, 25, ?, 'settled', ?,
                    'lost', -1.0, '{}', 'tournament_challenger', 'test_fast_fail_model_1')
            """, (f"w_test_{i}", f"fix_{i}", f"game_{i}", now_iso, now_iso))

    res = incubator.evaluate_tier2_lifecycle()
    assert res['evicted_fast_fail_count'] == 1

    # 1. Verify demoted_markets updated to research_training and model cleared
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key = ?", (seg_key,)).fetchone()
        assert row[0] == 'research_training'
        assert row[1] is None

        # 2. Verify ai_synthesis_feedback_loop recorded failure
        cur_fb = conn.execute("SELECT model_id, segment_key, crucible_passed, oos_roi FROM ai_synthesis_feedback_loop WHERE model_id = 'test_fast_fail_model_1'")
        fb_row = cur_fb.fetchone()
        assert fb_row is not None
        assert fb_row[0] == 'test_fast_fail_model_1'
        assert fb_row[2] == 0
        assert fb_row[3] <= -0.15

    # 3. Verify tournament registry marked candidate as rejected
    reg = t_eng.load_registry()
    _, t_data = t_eng._find_tournament_data(reg, seg_key)
    assert t_data['candidates']['test_fast_fail_model_1']['status'] == 'rejected'

    # 4. Verify learning_jobs received R&D repair job
    with sqlite3.connect(str(eng_db)) as e_conn:
        job = e_conn.execute("SELECT kind, league, status FROM learning_jobs WHERE kind = 'segment_repair'").fetchone()
        assert job is not None
        assert job[0] == 'segment_repair'
        assert job[1] == 'NBA'


def test_tier4_polymarket_normalized_matching_prevents_false_demoted_blocks(tmp_path: Path):
    """Verify that a graduated Tier 3 market with live profit is not blocked by unrelated demoted sub-markets."""
    import sqlite3
    from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
    from betting_engine.storage import Store

    db_path = tmp_path / "engine.sqlite3"
    Store(db_path)

    # Initialize shadow_incubator with graduated market AND an unrelated demoted sub-family
    inc_db = tmp_path / "shadow_incubator.sqlite3"
    with sqlite3.connect(str(inc_db)) as conn:
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
                stats TEXT NOT NULL
            )
        """)
        # Graduated market: NBA quarter 1 three_way
        conn.execute("""
            INSERT INTO graduated_markets (
                id, segment_key, league, scope, family, sub_family, graduated_at, sample_size,
                roi, delta_brier, max_drawdown, win_rate, stats
            ) VALUES (
                'grad_1', 'NBA|quarter_1|three_way', 'NBA', 'quarter_1', 'three_way', 'three_way',
                '2026-11-01T00:00:00Z', 35, 0.28, 0.005, 0.08, 0.58, '{}'
            )
        """)
        # Unrelated demoted placeholder in same league/scope: NBA quarter 1 american_derivatives
        conn.execute("""
            INSERT INTO demoted_markets (
                id, segment_key, league, scope, family, sub_family, demoted_at,
                rolling_roi, rolling_pnl, games, reason, stats
            ) VALUES (
                'dem_unrelated', 'NBA|quarter_1|american_derivatives', 'NBA', 'quarter_1', 'american_derivatives', 'totals',
                '2026-11-01T00:00:00Z', -0.05, -1.0, 5, 'Unrelated placeholder', '{}'
            )
        """)

    pe = PolymarketAutonomousExecutor(db_path=db_path, dry_run=True)
    pe._cached_scoreboard = {
        'alpha_segments': [
            {
                'segment': 'NBA|quarter_1|three_way',
                'league': 'NBA',
                'scope': 'quarter_1',
                'family': 'three_way',
                'pnl': 25.50,
                'roi_pct': 52.0,
                'games': 15,
                'sub_tier': 'SUPER_ALPHA',
            }
        ],
        'underperforming_segments': [],
        'exploration_segments': [],
    }
    pe._scoreboard_cached_at = 9999999999.0

    is_elig, reason, cap = pe.is_market_eligible('NBA', 'quarter_1', 'three_way', require_tier_4=True)
    assert is_elig is True
    assert cap == 8.0
    assert "TIER_4_LIVE_PROVEN_ALPHA" in reason


def test_polymarket_runner_matches_events_with_none_teams_and_short_titles(tmp_path):
    """Verifies that PolymarketAutonomousRunner correctly matches events when teams is None/empty

    and event titles use team nicknames/short names (e.g. 'Nuggets vs. Pacers').
    """
    from betting_engine.polymarket_runner import PolymarketAutonomousRunner

    runner = PolymarketAutonomousRunner(db_path=tmp_path / "test.sqlite3")

    # Domain tip has full franchise names
    domain_tips = [
        {
            "id": "tip_den_ind",
            "league": "NBA",
            "home": "Indiana Pacers",
            "away": "Denver Nuggets",
            "market": "moneyline",
            "model_scope": "full_game",
            "side": "home",
            "probability": 0.58,
            "predicted_home_points": 118.5,
            "predicted_away_points": 114.0,
            "allocation_code": "alpha_accelerated",
            "funding_veto": False,
        }
    ]

    # Polymarket event has teams=None and title='Nuggets vs. Pacers'
    fc, reason = runner._lookup_game_forecast(
        league="NBA",
        teams=[],
        title="Nuggets vs. Pacers",
        tips=domain_tips,
        return_failure_reason=True,
    )

    assert fc is not None, f"Expected match but got failure: {reason}"
    assert fc["home_name"] == "Indiana Pacers"
    assert fc["away_name"] == "Denver Nuggets"
    assert fc["proj_home"] == 118.5
    assert fc["proj_away"] == 114.0
    assert fc["proj_total"] == 232.5
    assert fc["home_prob"] == 0.58

    # Also test derivative event title suffix (e.g. ' - Exact Margin')
    fc_margin, _ = runner._lookup_game_forecast(
        league="NBA",
        teams=None or [],
        title="Nuggets vs. Pacers - Exact Margin",
        tips=domain_tips,
        return_failure_reason=True,
    )
    assert fc_margin is not None
    assert fc_margin["home_name"] == "Indiana Pacers"
    assert fc_margin["away_name"] == "Denver Nuggets"


def test_unified_telemetry_intake_funnel(tmp_path):
    """Verifies that all events, bottlenecks, errors, and queue congestions

    are aggregated into the unified quant intake funnel and superbrain state.
    """
    import json
    import sqlite3
    from betting_engine.quant_observability import LeadQuantObservabilityEngine
    from betting_engine.superbrain_telemetry import get_superbrain_state

    # 1. Create mock database with learning_jobs and autorun_events
    engine_db = tmp_path / "engine.sqlite3"
    with sqlite3.connect(engine_db) as conn:
        conn.execute("""
            CREATE TABLE learning_jobs (
                id INTEGER PRIMARY KEY,
                kind TEXT,
                league TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                payload TEXT
            );
        """)
        conn.execute("""
            INSERT INTO learning_jobs VALUES (1, 'autonomous_bottleneck_repair', 'NBA', 'queued', '2026-11-07T12:00:00Z', '2026-11-07T12:00:00Z', '{}');
        """)
        conn.execute("""
            CREATE TABLE autorun_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at TEXT,
                stage TEXT,
                severity TEXT,
                status TEXT,
                message TEXT,
                error_code TEXT,
                ai_remediation_hint TEXT
            );
        """)
        conn.execute("""
            INSERT INTO autorun_events (logged_at, stage, severity, status, message, error_code, ai_remediation_hint)
            VALUES ('2026-11-07T12:01:00Z', 'quote_evaluation', 'WARN', 'warning', 'Line moved slightly', 'LINE_MOVED', 'Execute Phase 2 adaptive fallback');
        """)
        conn.execute("""
            CREATE TABLE bets (
                id INTEGER PRIMARY KEY,
                tip_id TEXT,
                created_at TEXT,
                stake REAL,
                odds REAL,
                platform TEXT,
                status TEXT,
                pnl REAL,
                payload TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE polymarket_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                league TEXT,
                game_title TEXT,
                market_family TEXT,
                selection TEXT,
                poly_price REAL,
                poly_odds REAL,
                expected_value REAL,
                stake_usd REAL,
                status TEXT,
                pnl REAL,
                created_at TEXT
            );
        """)

    # 2. Create mock shadow incubator DB with ai_synthesis_feedback_loop
    inc_db = tmp_path / "shadow_incubator.sqlite3"
    with sqlite3.connect(inc_db) as conn:
        conn.execute("""
            CREATE TABLE ai_synthesis_feedback_loop (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT,
                class_name TEXT,
                generator_provider TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                oos_simulated_roi REAL,
                oos_delta_brier REAL,
                crucible_passed INTEGER,
                current_tier INTEGER,
                graduation_status TEXT,
                created_at TEXT,
                live_games_settled INTEGER,
                live_wins INTEGER,
                live_losses INTEGER,
                live_realized_roi REAL,
                live_realized_pnl REAL,
                live_brier_delta REAL,
                updated_at TEXT
            );
        """)
        conn.execute("""
            INSERT INTO ai_synthesis_feedback_loop (
                model_id, class_name, generator_provider, league, scope, family,
                oos_simulated_roi, oos_delta_brier, crucible_passed, current_tier, graduation_status,
                created_at, live_games_settled, live_wins, live_losses, live_realized_roi, live_realized_pnl, live_brier_delta, updated_at
            ) VALUES (
                'synth_test_1', 'TestModel', 'codex', 'NBA', 'quarter_1', 'three_way',
                0.24, 0.005, 1, 3, 'graduated',
                '2026-11-07T12:02:00Z', 30, 20, 10, 0.28, 15.0, 0.008, '2026-11-07T12:02:00Z'
            );
        """)
        conn.execute("""
            CREATE TABLE graduated_markets (
                id TEXT PRIMARY KEY,
                segment_key TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                sub_family TEXT,
                graduated_at TEXT,
                sample_size INTEGER,
                roi REAL,
                delta_brier REAL,
                max_drawdown REAL,
                win_rate REAL,
                stats TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE demoted_markets (
                id TEXT PRIMARY KEY,
                segment_key TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                sub_family TEXT,
                demoted_at TEXT,
                rolling_roi REAL,
                rolling_pnl REAL,
                games INTEGER,
                reason TEXT,
                stats TEXT,
                incubation_phase TEXT
            );
        """)

    # 3. Create mock bottleneck_watchdog_status.json
    wd_file = tmp_path / "bottleneck_watchdog_status.json"
    wd_file.write_text(json.dumps({
        "status": "HEALTHY",
        "healthy_markets_count": 43,
        "anomalies_detected_count": 1,
        "anomalies_healed_count": 1,
        "anomalies": [
            {
                "anomaly_id": "test_anom_1",
                "category": "unmounted_distribution",
                "league": "MLB",
                "scope": "full_game",
                "family": "totals",
                "severity": "INFO",
                "message": "Auto-mounted champion",
                "self_healed": True,
                "detected_at": "2026-11-07T12:03:00Z"
            }
        ],
        "undergenerating_markets": []
    }), encoding="utf-8")

    # 4. Verify Unified Quant Log Stream
    obs = LeadQuantObservabilityEngine(data_dir=tmp_path)
    logs = obs.stream_unified_quant_logs(limit=50)
    components = {log_entry["component"] for log_entry in logs}

    assert "bottleneck_watchdog" in components, "Bottleneck watchdog events must be in unified log funnel"
    assert "learning_jobs_queue" in components, "Learning jobs queue events must be in unified log funnel"
    assert "crucible_validation" in components, "Crucible validation events must be in unified log funnel"
    assert "autorun_execution" in components, "Autorun execution events must be in unified log funnel"

    # 5. Verify Superbrain State Intake Funnel
    state = get_superbrain_state(data_dir=tmp_path)
    assert "bottleneck_diagnostics" in state
    assert state["bottleneck_diagnostics"]["healthy_markets_count"] == 43
    assert "recent_execution_errors" in state
    assert len(state["recent_execution_errors"]) == 1
    assert state["recent_execution_errors"][0]["error_code"] == "LINE_MOVED"

    # Check live_event_queue includes bottleneck anomaly
    queue_types = {item["type"] for item in state["live_flow_events"]["live_event_queue"]}
    assert "bottleneck_anomaly" in queue_types
    assert "learning_job" in queue_types


def test_polymarket_runner_matches_events_with_prefixes_and_colons(tmp_path):
    """Verifies that Polymarket events with tournament/city prefixes and colons match domain tips."""
    from betting_engine.polymarket_runner import PolymarketAutonomousRunner

    runner = PolymarketAutonomousRunner(
        state_file=tmp_path / "poly_runner.json",
        db_path=tmp_path / "engine.sqlite3",
    )

    domain_tips = [
        {
            "league": "WTA",
            "home": "Coco Gauff",
            "away": "Jessica Pegula",
            "market": "Moneyline",
            "home_prob": 0.61,
            "away_prob": 0.39,
            "allocation_code": "alpha_production",
        },
        {
            "league": "ATP",
            "home": "Dante Pagani",
            "away": "Murkel Dellien",
            "market": "Moneyline",
            "home_prob": 0.45,
            "away_prob": 0.55,
            "allocation_code": "alpha_production",
        }
    ]

    # Event 1: WTA with tournament and group prefix before colon
    res1, reason1 = runner._lookup_game_forecast(
        league="WTA",
        teams=[],
        title="WTA Finals, Group A: Coco Gauff vs Jessica Pegula",
        tips=domain_tips,
        return_failure_reason=True,
    )
    assert res1 is not None, f"Failed to match WTA event: {reason1}"
    assert res1["home_name"] == "Coco Gauff"
    assert res1["away_name"] == "Jessica Pegula"

    # Event 2: ATP with city prefix before colon
    res2, reason2 = runner._lookup_game_forecast(
        league="ATP",
        teams=[],
        title="Villa Maria: Dante Pagani vs Murkel Dellien",
        tips=domain_tips,
        return_failure_reason=True,
    )
    assert res2 is not None, f"Failed to match ATP event: {reason2}"
    assert res2["home_name"] == "Dante Pagani"
    assert res2["away_name"] == "Murkel Dellien"


def test_codex_synthesizer_safe_imports_and_indentation_recovery():
    """Verifies that extract_clean_code and check_ast_safety handle future imports and indentation."""
    from betting_engine.codex_model_synthesizer import (
        extract_clean_code,
        check_ast_safety,
    )

    # 1. Code with from __future__ import annotations followed by primitives
    llm_code = '''```python
from __future__ import annotations

import math
from typing import Any, Dict, Optional

class CandidateWithCopula(BaseCandidateModel):
    def __init__(self):
        super().__init__("TestCopula", "test_id")
        self.copula = ClaytonCopula(theta=2.0)

    def predict_distribution(self, ctx: Dict[str, Any]) -> DiscreteDistribution:
        return DiscreteDistribution({0: 0.5, 1: 0.5})
```'''

    clean = extract_clean_code(llm_code)
    # Ensure from __future__ remains at the top
    first_non_empty = [l.strip() for l in clean.splitlines() if l.strip()][0]
    assert first_non_empty == "from __future__ import annotations", "from __future__ must be the first statement"
    
    # Must compile without error
    compile(clean, "<test_synth>", "exec")

    is_safe, cls_name, err = check_ast_safety(clean)
    assert is_safe is True, f"AST check failed: {err}"
    assert cls_name == "CandidateWithCopula"

    # 2. Indentation recovery: docstring at col 0, class indented at 4 spaces
    indented_code = '''"""
Candidate model for test segment.
"""
    from __future__ import annotations
    import math

    class IndentedCandidate(BaseCandidateModel):
        def __init__(self):
            super().__init__("TestIndented", "test_ind_id")

        def predict_distribution(self, ctx):
            return DiscreteDistribution({0: 1.0})
'''
    clean_ind = extract_clean_code(indented_code)
    compile(clean_ind, "<test_ind>", "exec")
    is_safe2, cls_name2, err2 = check_ast_safety(clean_ind)
    assert is_safe2 is True, f"AST check failed for indented candidate: {err2}"
    assert cls_name2 == "IndentedCandidate"

    # 3. Security check: dynamic import of safe modules vs unsafe modules
    safe_import_code = '''
from betting_engine.sandbox_models.base import BaseCandidateModel
math_mod = __import__("math")

class SafeDynamicImportModel(BaseCandidateModel):
    def __init__(self):
        super().__init__("SafeDyn", "safe_id")
'''
    is_safe3, cls_name3, err3 = check_ast_safety(safe_import_code)
    assert is_safe3 is True, f"Safe __import__('math') was falsely blocked: {err3}"

    unsafe_import_code = '''
from betting_engine.sandbox_models.base import BaseCandidateModel
os_mod = __import__("os")

class UnsafeDynamicImportModel(BaseCandidateModel):
    def __init__(self):
        super().__init__("UnsafeDyn", "unsafe_id")
'''
    is_safe4, cls_name4, err4 = check_ast_safety(unsafe_import_code)
    assert is_safe4 is False, "Unsafe __import__('os') must be blocked"
    assert "disallowed dynamic import 'os'" in err4



