"""Unit tests for quantitative mathematical primitives and copula lattices.

Enforces Anti-Tautological verification: tests validate against independent
mathematical invariants (probability axioms, Fréchet-Hoeffding bounds,
monotonicity, Stein's shrinkage properties, and Kelly bounds).
"""
import math
import numpy as np
import pytest
from scipy.stats import poisson

from betting_engine.sandbox_models.quant_primitives import (
    ClaytonCopula,
    GumbelCopula,
    FrankCopula,
    GaussianCopula,
    build_copula_score_lattice,
    WeibullRenewalHazard,
    ZeroInflatedPoisson,
    ZeroInflatedNegativeBinomial,
    JamesSteinShrinkage,
    EmpiricalBayesPoissonRate,
    KalmanRatingTracker,
    TemperatureScaler,
    continuous_fractional_kelly,
)


def test_clayton_copula_axioms():
    copula = ClaytonCopula(theta=2.0)
    # Fréchet-Hoeffding bounds: max(u + v - 1, 0) <= C(u, v) <= min(u, v)
    u_vals = [0.1, 0.3, 0.7, 0.9]
    v_vals = [0.2, 0.5, 0.8]
    for u in u_vals:
        for v in v_vals:
            c = copula.cdf(u, v)
            assert 0.0 <= c <= min(u, v), f"Violated upper Fréchet bound: C({u}, {v}) = {c}"
            assert c >= max(u + v - 1.0, 0.0) - 1e-6, f"Violated lower Fréchet bound: C({u}, {v}) = {c}"
    
    # Boundary conditions: C(u, 1) = u, C(1, v) = v, C(u, 0) = 0
    assert abs(copula.cdf(0.65, 1.0) - 0.65) < 1e-4
    assert abs(copula.cdf(1.0, 0.42) - 0.42) < 1e-4
    assert copula.cdf(0.5, 0.0) == 0.0
    assert copula.lower_tail_dependence > 0.0


def test_gumbel_copula_axioms():
    copula = GumbelCopula(theta=1.8)
    # Boundary conditions
    assert abs(copula.cdf(0.7, 1.0) - 0.7) < 1e-3
    assert abs(copula.cdf(1.0, 0.35) - 0.35) < 1e-3
    # Upper tail dependence should be strictly positive
    assert 0.0 < copula.upper_tail_dependence < 1.0


def test_frank_copula_symmetry():
    copula = FrankCopula(theta=2.5)
    # Symmetry: C(u, v) == C(v, u)
    assert abs(copula.cdf(0.3, 0.8) - copula.cdf(0.8, 0.3)) < 1e-9
    assert 0.0 <= copula.cdf(0.5, 0.5) <= 0.5


def test_gaussian_copula_independence_at_zero_rho():
    copula = GaussianCopula(rho=0.0)
    u, v = 0.4, 0.6
    # When rho = 0, C(u, v) = u * v (independence)
    assert abs(copula.cdf(u, v) - (u * v)) < 1e-5


def test_build_copula_score_lattice_probability_invariant():
    # Generate marginal Poisson PMFs
    pmf_h = [poisson.pmf(i, 2.3) for i in range(8)]
    pmf_a = [poisson.pmf(i, 1.6) for i in range(8)]
    
    copula = ClaytonCopula(theta=1.5)
    lattice = build_copula_score_lattice(pmf_h, pmf_a, copula)
    
    # Probability axiom 1: All probabilities must be non-negative
    for (h, a), p in lattice.items():
        assert p >= 0.0, f"Negative probability at ({h}, {a}): {p}"
    
    # Probability axiom 2: Joint distribution must sum strictly to 1.0
    total_prob = sum(lattice.values())
    assert abs(total_prob - 1.0) < 1e-7, f"Total probability mass {total_prob} != 1.0"


def test_weibull_renewal_hazard_properties():
    # shape = 1.5 > 1.0 -> positive duration dependence (aging)
    hazard_model = WeibullRenewalHazard(shape=1.5, scale=20.0)
    h_early = hazard_model.hazard(5.0)
    h_late = hazard_model.hazard(15.0)
    assert h_late > h_early, "Weibull with shape > 1.0 must have increasing hazard"
    
    # Survival function must be strictly decreasing
    assert hazard_model.survival(0.0) == 1.0
    assert hazard_model.survival(10.0) > hazard_model.survival(20.0) > 0.0


def test_zero_inflated_poisson_invariants():
    zip_dist = ZeroInflatedPoisson(mu=1.8, pi=0.25)
    pmf = zip_dist.generate_pmf_vector(max_k=10)
    
    # Excess zeros: P(0) must exceed standard Poisson(0)
    standard_p0 = math.exp(-1.8)
    assert pmf[0] > standard_p0
    
    # All non-negative
    assert all(p >= 0.0 for p in pmf)
    # Sum of probabilities converges to 1.0
    assert abs(sum(pmf) - 1.0) < 1e-2
    # Variance must exceed Poisson variance due to zero inflation mixture
    assert zip_dist.variance > zip_dist.expected_value


def test_zero_inflated_negative_binomial():
    zinb = ZeroInflatedNegativeBinomial(mu=2.0, alpha=0.3, pi=0.15)
    pmf = zinb.generate_pmf_vector(max_k=10)
    assert pmf[0] > 0.0
    assert all(p >= 0.0 for p in pmf)
    assert abs(sum(pmf) - 1.0) < 1e-2


def test_james_stein_shrinkage():
    # For p >= 3, James-Stein shrinks extreme estimates toward target
    estimates = [10.0, 2.0, -4.0, 15.0, 1.0]
    shrunk = JamesSteinShrinkage.shrink(estimates, target=0.0, sigma_sq=2.0)
    assert len(shrunk) == len(estimates)
    # Absolute values should be shrunk towards 0
    for orig, s in zip(estimates, shrunk):
        assert abs(s) <= abs(orig) + 1e-6


def test_kalman_rating_tracker():
    tracker = KalmanRatingTracker(initial_rating=1500.0, initial_uncertainty=50.0, process_variance=10.0, observation_variance=40.0)
    init_r, init_u = tracker.current_state
    assert init_r == 1500.0
    
    # Predict step expands uncertainty
    tracker.predict(rest_decay_factor=1.0)
    _, p_pred = tracker.current_state
    assert p_pred > init_u
    
    # Observe high performance (1600.0)
    updated_r = tracker.update(1600.0)
    assert 1500.0 < updated_r < 1600.0
    _, p_post = tracker.current_state
    # Measurement update must contract uncertainty
    assert p_post < p_pred


def test_temperature_scaler():
    scaler = TemperatureScaler(temperature=2.0)
    orig_probs = [0.8, 0.15, 0.05]
    scaled = scaler.scale_probabilities(orig_probs)
    
    # Invariant: Must sum to 1.0
    assert abs(sum(scaled) - 1.0) < 1e-7
    # Temperature > 1 increases entropy: highest probability must decrease towards uniform
    assert scaled[0] < orig_probs[0]
    assert scaled[2] > orig_probs[2]


def test_continuous_fractional_kelly_bounds():
    # Negative EV must result in $0.00
    assert continuous_fractional_kelly(win_probability=0.40, decimal_odds=2.0) == 0.0
    
    # Positive EV must be strictly bounded in [$0.50, $8.00]
    small_edge_stake = continuous_fractional_kelly(win_probability=0.55, decimal_odds=2.0)
    assert 0.50 <= small_edge_stake <= 8.00
    
    huge_edge_stake = continuous_fractional_kelly(win_probability=0.90, decimal_odds=3.0)
    assert huge_edge_stake == 8.00  # Clamped at $8.00 max
