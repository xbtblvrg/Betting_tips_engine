"""Mathematical Invariant and Regression Tests for Continuous Line Valuation."""
import pytest
from betting_engine.polymarket_line_evaluator import (
    ContinuousLineEvaluator,
    norm_cdf,
    norm_inv,
)


def test_norm_cdf_and_inv_invariants():
    """Verifies round-trip identity of normal CDF and quantile function."""
    for p in [0.05, 0.20, 0.50, 0.80, 0.95]:
        z = norm_inv(p)
        p_reconstructed = norm_cdf(z)
        assert pytest.approx(p, abs=1e-3) == p_reconstructed


def test_continuous_totals_monotonicity():
    """Verifies that probability decreases monotonically as total line increases for OVER."""
    evaluator = ContinuousLineEvaluator()
    base_line = 47.5
    base_prob = 0.55  # Over 47.5 is 55%

    p_lower = evaluator.evaluate_total("NFL", base_line, base_prob, "over", 44.5, "over")
    p_exact = evaluator.evaluate_total("NFL", base_line, base_prob, "over", 47.5, "over")
    p_higher = evaluator.evaluate_total("NFL", base_line, base_prob, "over", 50.5, "over")

    # Invariant: Over 44.5 must be MORE likely than Over 47.5, which is MORE likely than Over 50.5
    assert p_lower > p_exact > p_higher
    assert pytest.approx(p_exact, abs=1e-3) == base_prob

    # Invariant: Complementarity P(Over L) + P(Under L) == 1.0
    p_under = evaluator.evaluate_total("NFL", base_line, base_prob, "over", 44.5, "under")
    assert pytest.approx(p_lower + p_under, abs=1e-3) == 1.0


def test_continuous_spread_monotonicity():
    """Verifies that giving fewer points or getting more points increases cover probability."""
    evaluator = ContinuousLineEvaluator()
    base_line = -3.5
    base_prob = 0.52

    # Line of -1.5 is easier to cover than -3.5
    p_easier = evaluator.evaluate_spread("NBA", base_line, base_prob, "home", -1.5, "home")
    p_exact = evaluator.evaluate_spread("NBA", base_line, base_prob, "home", -3.5, "home")
    p_harder = evaluator.evaluate_spread("NBA", base_line, base_prob, "home", -6.5, "home")

    assert p_easier > p_exact > p_harder
    assert pytest.approx(p_exact, abs=1e-3) == base_prob

    # Invariant: Complementarity P(Fav covers L) + P(Underdog covers -L) == 1.0
    p_dog = evaluator.evaluate_spread("NBA", base_line, base_prob, "home", -1.5, "underdog")
    assert pytest.approx(p_easier + p_dog, abs=1e-3) == 1.0


def test_contract_valuation_ev_hurdle():
    """Verifies that EV is mathematically accurate: EV = P * (1 / Price) - 1.0."""
    evaluator = ContinuousLineEvaluator()
    
    # Prob 0.60, Price 0.50 (Odds 2.0) => EV = 0.60 * 2.0 - 1 = +0.20 (+20%)
    val = evaluator.calculate_valuation(model_prob=0.60, poly_price=0.50, min_ev_hurdle=0.025)
    assert val["ok"] is True
    assert val["ev"] == 0.20
    assert val["poly_odds"] == 2.0
    assert val["edge_pct"] == 20.0

    # Prob 0.50, Price 0.52 (Odds 1.923) => EV = 0.50 * (1/0.52) - 1 = -0.0385 (Negative EV)
    val_neg = evaluator.calculate_valuation(model_prob=0.50, poly_price=0.52, min_ev_hurdle=0.025)
    assert val_neg["ok"] is False
    assert val_neg["ev"] < 0.0


def test_evaluate_team_spread_first_principles():
    """Verifies that evaluate_team_spread obeys probability axioms and exact cover conditions."""
    evaluator = ContinuousLineEvaluator()

    # Home = 110.15 (Magic), Away = 113.02 (Celtics), Celtics favored by 2.87
    ph, pa = 110.15, 113.02

    # 1. Monotonicity: Getting more points increases cover probability
    p_dog_deep = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=12.5)
    p_dog_mid = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=5.5)
    p_dog_neg = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=-12.5)

    assert p_dog_deep > p_dog_mid > p_dog_neg
    # Underdog giving 12.5 points against a stronger team must have small probability (< 15%)
    assert p_dog_neg < 0.15
    # Underdog receiving 12.5 points must have high probability (> 75%)
    assert p_dog_deep > 0.75

    # 2. Complementarity Axiom: P(Home covers S) + P(Away covers -S) == 1.0000
    for s in [-15.5, -10.5, -5.5, -0.5, 0.0, 0.5, 5.5, 10.5, 15.5]:
        p_h = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=True, spread_line=s)
        p_a = evaluator.evaluate_team_spread("NBA", ph, pa, is_home=False, spread_line=-s)
        assert pytest.approx(p_h + p_a, abs=1e-3) == 1.0

