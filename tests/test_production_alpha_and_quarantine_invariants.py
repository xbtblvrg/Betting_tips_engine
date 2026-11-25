"""Anti-Tautological Production Invariants Test Suite (2026-11-16 User Correction).

Ensures that:
1. Alpha Acceleration genuinely scales capital to $2.50-$8.00 in production (is_test=False)
   and is NEVER silently choked by universe_tier_stake_cap or other clamps.
2. The Anti-Loss Invariant is strictly upheld: any market that is degraded,
   demoted, or in toxic quarantine receives strictly $0.00 stake.
   A historical graduation diploma CAN NEVER override live negative reality.
"""
import pytest
import math
from betting_engine.outcome_sizing import apply_adjustment


def test_production_super_alpha_scaling_not_choked():
    """Super Alpha market MUST scale to >= $5.00 and not be choked to $0.50."""
    tip = {
        'id': 'real_mls_totals_alpha',
        'game_id': 'MLS:2026_game_1',
        'league': 'MLS',
        'market': 'totals',
        'model_scope': 'full_game',
        'selection': 'Over 2.5',
        'probability': 0.65,
        'odds': 1.85,
        'expected_roi': 0.20,
        'is_graduated': True,
        'is_super_alpha': True
    }
    settings = {
        'bankroll': None,
        'max_bet': 8.0,
        'unit_stake': 1.0,
        'capital_defense': True,
        'alpha_acceleration': True,
        'is_test': False,
        'outcome_sizing_policy': 'historical_confidence_v3'
    }
    base_sizing = {
        'amount': 1.0,
        'base': 1.0,
        'factor': 1.0,
        'summary': {
            'independent_games': 25,
            'game_roi': 0.28,
            'pnl': 240.0,
            'staked': 800.0,
            'snapshot_sha256': 'abc',
            'summary_sha256': 'def',
            'state': 'ok'
        }
    }
    res = apply_adjustment(base_sizing, tip, settings=settings, remaining=math.inf)
    amount = res.get('amount')
    caps = res.get('outcome_adjustment', {}).get('caps', {})

    # Invariant: Must scale to >= $5.00
    assert amount >= 5.0, f"Super Alpha must be >= $5.00, got ${amount:.2f}"
    # Invariant: universe_tier_stake_cap must not be present or clamping
    assert 'universe_tier_stake_cap' not in caps or caps['universe_tier_stake_cap'] >= 5.0


def test_production_solid_alpha_scaling_not_choked():
    """Solid Alpha market MUST scale to >= $2.50."""
    tip = {
        'id': 'real_nba_q4_spreads_alpha',
        'game_id': 'NBA:2026_game_2',
        'league': 'NBA',
        'market': 'quarter_4:spreads',
        'model_scope': 'quarter_4',
        'selection': 'Celtics -2.5',
        'probability': 0.58,
        'odds': 1.91,
        'expected_roi': 0.108,
        'is_graduated': True,
        'is_solid_alpha': True
    }
    settings = {
        'bankroll': None,
        'max_bet': 8.0,
        'unit_stake': 1.0,
        'capital_defense': True,
        'alpha_acceleration': True,
        'is_test': False,
        'outcome_sizing_policy': 'historical_confidence_v3'
    }
    base_sizing = {
        'amount': 1.0,
        'base': 1.0,
        'factor': 1.0,
        'summary': {
            'independent_games': 15,
            'game_roi': 0.09,
            'pnl': 25.0,
            'staked': 200.0,
            'snapshot_sha256': 'abc',
            'summary_sha256': 'def',
            'state': 'ok'
        }
    }
    res = apply_adjustment(base_sizing, tip, settings=settings, remaining=math.inf)
    amount = res.get('amount')
    assert amount >= 2.50, f"Solid Alpha must be >= $2.50, got ${amount:.2f}"


def test_anti_loss_invariant_quarantine_overrides_historical_diploma():
    """A historical graduation diploma CAN NEVER override live negative reality!"""
    tip = {
        'id': 'real_nhl_losing_totals',
        'game_id': 'NHL:2026_game_3',
        'league': 'NHL',
        'market': 'period_2:totals',
        'model_scope': 'period_2',
        'selection': 'Under 1.5',
        'probability': 0.60,
        'odds': 1.95,
        'expected_roi': 0.10,
        'is_graduated': True,  # Historical diploma present!
    }
    settings = {
        'bankroll': None,
        'max_bet': 8.0,
        'unit_stake': 1.0,
        'capital_defense': True,
        'alpha_acceleration': True,
        'is_test': False,
        'outcome_sizing_policy': 'historical_confidence_v3'
    }
    base_sizing = {
        'amount': 1.0,
        'base': 1.0,
        'factor': 1.0,
        'summary': {
            'independent_games': 10,
            'game_roi': -0.45,
            'pnl': -45.0,
            'staked': 100.0,
            'snapshot_sha256': 'abc',
            'summary_sha256': 'def',
            'state': 'ok'
        }
    }

    # Mock Governor reporting this segment in quarantine_list
    import betting_engine.autonomous_roi_governor as gov_mod
    orig_fn = getattr(gov_mod, 'get_latest_governor_report', None)
    gov_mod.get_latest_governor_report = lambda data_dir: {
        'quarantine_list': ['NHL|period_2|totals']
    }
    try:
        res = apply_adjustment(base_sizing, tip, settings=settings, remaining=math.inf)
        amount = res.get('amount')
        # Invariant: Must be strictly $0.00! Cannot bleed real capital at $0.50!
        assert amount == 0.0, f"Quarantined segment MUST receive $0.00 stake, got ${amount:.2f}"
    finally:
        if orig_fn:
            gov_mod.get_latest_governor_report = orig_fn


def test_anti_loss_invariant_demoted_market_strictly_zero_stake():
    """An officially demoted market MUST receive $0.00 stake."""
    tip = {
        'id': 'real_demoted_market_tip',
        'game_id': 'ATP:2026_game_4',
        'league': 'ATP',
        'market': 'set_handicap',
        'model_scope': 'full_game',
        'selection': 'Player A +1.5',
        'probability': 0.55,
        'odds': 1.90,
        'expected_roi': 0.05,
        'is_demoted': True,
        'is_graduated': False
    }
    settings = {
        'bankroll': None,
        'max_bet': 8.0,
        'unit_stake': 1.0,
        'capital_defense': True,
        'alpha_acceleration': True,
        'is_test': False,
        'outcome_sizing_policy': 'historical_confidence_v3'
    }
    base_sizing = {
        'amount': 1.0,
        'base': 1.0,
        'factor': 1.0,
        'summary': {
            'independent_games': 5,
            'game_roi': -0.10,
            'pnl': -5.0,
            'staked': 50.0,
            'snapshot_sha256': 'abc',
            'summary_sha256': 'def',
            'state': 'ok'
        }
    }
    res = apply_adjustment(base_sizing, tip, settings=settings, remaining=math.inf)
    assert res.get('amount') == 0.0, f"Demoted market must receive $0.00, got ${res.get('amount')}"


def test_resolve_graduated_full_game_models_mounts_all_graduated_leagues():
    """All leagues with graduated full-game markets MUST have live models resolved."""
    from betting_engine.graduated_models import resolve_graduated_full_game_models
    from betting_engine.domain import Game
    import json
    from pathlib import Path

    graduated = {
        'NBA|full_game|spreads': {'league': 'NBA', 'scope': 'full_game', 'family': 'spreads'},
        'NFL|full_game|totals': {'league': 'NFL', 'scope': 'full_game', 'family': 'totals'},
        'MLS|full_game|totals': {'league': 'MLS', 'scope': 'full_game', 'family': 'totals'},
        'EPL|full_game|totals': {'league': 'EPL', 'scope': 'full_game', 'family': 'totals'},
    }

    # Pass empty existing models (e.g. when data/models joblibs are not pre-deployed)
    resolved = resolve_graduated_full_game_models(graduated, {})

    for lg in ('NBA', 'NFL', 'MLS', 'EPL'):
        assert lg in resolved, f"League {lg} must have a resolved full-game model!"
        m = resolved[lg]
        v_mkts = m.report.get('validated_markets', [])
        assert 'spreads' in v_mkts or 'totals' in v_mkts, f"{lg} validated_markets must include spreads/totals"

    # Test that model.forecast works on a game and returns valid market probabilities
    nba_model = resolved['NBA']
    game = Game(
        id='test_nba_game',
        league='NBA',
        start='2026-11-16T23:00:00Z',
        home='Boston Celtics',
        away='New York Knicks',
        home_score=None,
        away_score=None,
        state='scheduled',
        home_id='BOS',
        away_id='NYK',
        home_abbr='BOS',
        away_abbr='NYK',
        season=2026
    )
    fc = nba_model.forecast(game)
    assert fc is not None
    p_home, p_push = fc.market_probability('spreads', 'home', -4.5)
    assert 0.01 <= p_home <= 0.99, f"P must be valid probability, got {p_home}"


def test_continuous_selection_priority_v4_demoted_unconditionally_suppressed():
    """Demoted markets must unconditionally receive score -999.0 regardless of EV."""
    from betting_engine.decisions import continuous_selection_priority_v4

    demoted_high_ev_tip = {
        'id': 'real_demoted_pick_high_ev',
        'game_id': 'NHL:game_demoted',
        'league': 'NHL',
        'market': 'period_2:totals',
        'model_scope': 'period_2',
        'probability': 0.65,
        'reference_odds': 2.10,
        'expected_roi': 0.365,  # High EV (+36.5%)!
        'is_demoted': True
    }

    prio = continuous_selection_priority_v4(demoted_high_ev_tip, 2)
    # Priority tuple: (tier, score, p, stress_roi, False, id)
    assert prio[0] == 0, "Demoted market tier in priority must be 0"
    assert prio[1] <= -999.0, f"Demoted market priority score must be -999.0, got {prio[1]}"


def test_governor_circuit_breaker_takes_precedence_over_mild_tourniquet():
    """Severe sustained loss must go to TOXIC_QUARANTINE ($0.00), not CAPITAL_DEFENSE ($0.50)."""
    from betting_engine.autonomous_roi_governor import AutonomousROIGovernor
    from pathlib import Path
    import sqlite3
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tmp:
        db_path = Path(tmp.name)
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE bets (
                    id TEXT PRIMARY KEY,
                    tip_id TEXT,
                    created_at TEXT,
                    status TEXT,
                    stake REAL,
                    pnl REAL,
                    payload TEXT
                )
            """)
            # Insert 15 consecutive losing bets for NHL|period_2|totals (sustained failure: -15 units, -100% ROI)
            for i in range(15):
                payload = json.dumps({
                    'league': 'NHL',
                    'model_scope': 'period_2',
                    'market': 'period_2:totals',
                    'selection': 'Under 1.5'
                })
                conn.execute(
                    "INSERT INTO bets VALUES (?, ?, datetime('now', '-1 day'), 'lost', 1.0, -1.0, ?)",
                    (f'bet_{i}', f'tip_{i}', payload)
                )

        gov = AutonomousROIGovernor(db_path=db_path)
        report = gov.analyze_weekly_performance(days=7)
        quarantine = report.get('quarantine_list', [])
        cap_defense = report.get('capital_defense_list', [])

        assert 'NHL|period_2|totals' in quarantine, "Sustained losing segment MUST be in quarantine_list ($0.00)!"
        assert 'NHL|period_2|totals' not in cap_defense, "Sustained losing segment MUST NOT be in capital_defense_list ($0.50)!"


def test_graduated_market_in_rolling_drawdown_clamped_to_050_tourniquet():
    """
    Anti-tautological regression test for the live NCAAM|full_game|spreads $7.17 bleed bug:
    Even when a graduated market still has positive all-time PnL (+12.11 over 53 games),
    if its rolling window enters drawdown (capital_defense_list or universe_prof.stake_cap == 0.50),
    outcome_sizing MUST enforce the $0.50 Capital Defense Tourniquet and NEVER pop universe_tier_stake_cap to bet $7+!
    """
    from types import SimpleNamespace
    import betting_engine.autonomous_roi_governor as gov_mod
    import betting_engine.unified_market_universe as umu_mod

    tip = {
        'id': 'real_ncaam_spread_rolling_drag',
        'game_id': 'NCAAM:401999999',
        'league': 'NCAAM',
        'market': 'spreads',
        'model_scope': 'full_game',
        'selection': 'Duke -4.5',
        'probability': 0.72,
        'reference_odds': 2.05,
        'expected_roi': 0.476,
        'is_graduated': True,
    }
    settings = {
        'bankroll': None,
        'max_bet': 8.0,
        'unit_stake': 1.0,
        'capital_defense': True,
        'alpha_acceleration': True,
        'is_test': False,
        'outcome_sizing_policy': 'historical_confidence_v3',
    }
    # All-time summary still slightly positive (+12.11 on $329.44 over 53 games, matching live bet #1199),
    # while rolling 20-game window is -3.4% ROI in Capital Defense Tourniquet ($0.50 clamp).
    base_sizing = {
        'amount': 5.27,
        'base': 5.27,
        'factor': 1.0,
    }
    tip_with_ev = dict(tip)
    orig_gov = gov_mod.get_latest_governor_report
    orig_roster = umu_mod.UnifiedMarketUniverse.build_universe_roster
    gov_mod.get_latest_governor_report = lambda data_dir=None: {
        'capital_defense_list': ['NCAAM|full_game|spreads'],
        'quarantine_list': [],
        'super_alpha_list': [],
        'solid_alpha_list': [],
    }
    umu_mod.UnifiedMarketUniverse.build_universe_roster = lambda self, force_refresh=False: {
        'NCAAM|full_game|spreads': SimpleNamespace(
            tier=3,
            stake_cap=0.50,
            rolling_pnl=-4.79,
            rolling_roi_pct=-3.35,
            phase_detail='Capital Defense Tourniquet (-3.4% ROI, -0.7U over 20 games) - $0.50 clamp',
        )
    }
    try:
        res = apply_adjustment(base_sizing, tip_with_ev, settings=settings, remaining=math.inf)
        assert res['amount'] == 0.50, f"Rolling-drawdown graduated market MUST clamp to $0.50 tourniquet, got ${res['amount']:.2f}"
    finally:
        gov_mod.get_latest_governor_report = orig_gov
        umu_mod.UnifiedMarketUniverse.build_universe_roster = orig_roster

