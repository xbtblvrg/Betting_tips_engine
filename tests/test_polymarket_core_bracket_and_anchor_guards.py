"""Mathematical and Algorithmic Invariant Tests for Polymarket Core Price Bracket,
Anchor Line Proximity Guard, and NCAA College Mascot Resolution.
"""
import pytest
from betting_engine.polymarket_executor import PolymarketAutonomousExecutor, PreflightResult
from betting_engine.polymarket_line_evaluator import ContinuousLineEvaluator
from betting_engine.polymarket_service import _canonical_match_key, _normalize_name


def test_preflight_core_price_bracket_guard():
    """Verifies that preflight_check strictly rejects tail underdogs and heavy favorites."""
    pe = PolymarketAutonomousExecutor(dry_run=True)

    # 1. Price < 0.35 (Tail Underdog) must be rejected
    low_price_book = {
        "asks": [{"price": "0.22", "size": "100.0"}]
    }
    res_low = pe.preflight_check(
        token_id="tok_low_price",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.30,  # EV = (0.30 / 0.22) - 1 = +36.4%
        min_ev=0.040,
        mock_book=low_price_book,
    )
    assert res_low.ok is False
    assert "PRICE_OUTSIDE_CORE_BRACKET" in res_low.reason
    assert res_low.eff_price < 0.35

    # 2. Price > 0.65 (Heavy Favorite with poor risk/reward) must be rejected
    high_price_book = {
        "asks": [{"price": "0.68", "size": "100.0"}]
    }
    res_high = pe.preflight_check(
        token_id="tok_high_price",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.80,  # EV = (0.80 / 0.68) - 1 = +17.6%
        min_ev=0.040,
        mock_book=high_price_book,
    )
    assert res_high.ok is False
    assert "PRICE_OUTSIDE_CORE_BRACKET" in res_high.reason
    assert res_high.eff_price > 0.65

    # 3. Core price within [0.35, 0.65] with EV >= 0.040 passes
    core_book = {
        "asks": [{"price": "0.50", "size": "100.0"}]
    }
    res_core = pe.preflight_check(
        token_id="tok_core_price",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.55,  # EV = (0.55 / 0.50) - 1 = +10.0%
        min_ev=0.040,
        mock_book=core_book,
    )
    assert res_core.ok is True
    assert res_core.reason == "PREFLIGHT_PASSED"
    assert 0.35 <= res_core.eff_price <= 0.65


def test_preflight_post_slippage_ev_hurdle_enforcement():
    """Verifies that preflight_check enforces the upgraded 4.0% post-slippage EV hurdle."""
    pe = PolymarketAutonomousExecutor(dry_run=True)

    # Price = 0.50, Model Prob = 0.515 => EV = +3.0% (< 4.0% hurdle)
    marginal_book = {
        "asks": [{"price": "0.50", "size": "100.0"}]
    }
    res_marginal = pe.preflight_check(
        token_id="tok_marginal",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.515,  # EV = (0.515 / 0.50) - 1 = +3.0%
        min_ev=0.040,
        mock_book=marginal_book,
    )
    assert res_marginal.ok is False
    assert "INSUFFICIENT_EV_AFTER_SLIPPAGE" in res_marginal.reason

    # Slippage pushing EV from 5.0% to 2.0% must be rejected
    thin_book = {
        "asks": [
            {"price": "0.50", "size": "2.0"},   # $1.00
            {"price": "0.54", "size": "100.0"},  # remaining fills at 0.54
        ]
    }
    res_slipped = pe.preflight_check(
        token_id="tok_slipped",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.525,
        min_ev=0.040,
        mock_book=thin_book,
    )
    assert res_slipped.ok is False
    assert "INSUFFICIENT_EV_AFTER_SLIPPAGE" in res_slipped.reason


def test_anchor_line_proximity_guard_rejects_distant_alternates():
    """Verifies that ContinuousLineEvaluator with max_sigma_shift=0.35 rejects distant alternate lines."""
    evaluator = ContinuousLineEvaluator()
    # Celtics projected by 3 points over Magic (mu = -3.0, NBA spreads sigma = 12.0)
    ph, pa = 110.0, 113.0

    # Line of +3.5 (underdog receiving 3.5 pts vs consensus 3.0): z = (-3.5 - (-3.0)) / 12.0 = -0.5 / 12.0 = -0.042 <= 0.35
    p_close = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=3.5, max_sigma_shift=0.35)
    assert p_close is not None
    assert 0.0 < p_close < 1.0

    # Distant alternate line of -15.5 (favorite by 15.5 when projected to lose by 3): z = 1.54 > 0.35
    p_distant = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=-15.5, max_sigma_shift=0.35)
    assert p_distant is None, "Extreme alternate line -15.5 must be rejected by anchor proximity guard"

    # Distant alternate line of +18.5 (receiving 18.5 pts when projected to lose by only 3): z = -1.29 < -0.35
    p_dog_distant = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=18.5, max_sigma_shift=0.35)
    assert p_dog_distant is None, "Extreme alternate line +18.5 must be rejected by anchor proximity guard"


def test_ncaa_mascot_stripping_canonical_matcher():
    """Verifies that _canonical_match_key accurately strips common college mascots."""
    pairs = [
        ("Penn State Nittany Lions", "pennstate"),
        ("Tulsa Golden Hurricane", "tulsa"),
        ("Iowa State Cyclones", "iowastate"),
        ("Montana State Bobcats", "montanastate"),
        ("Texas Southern Tigers", "texassouthern"),
        ("Cal Poly Mustangs", "calpoly"),
        ("UIC Flames", "uic"),
        ("UT Martin Skyhawks", "utmartin"),
    ]
    for raw_name, expected in pairs:
        norm = _normalize_name(raw_name)
        canon = _canonical_match_key(norm)
        assert canon == expected, f"Failed for {raw_name}: got {canon}, expected {expected}"
