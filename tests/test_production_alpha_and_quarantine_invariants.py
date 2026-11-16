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
