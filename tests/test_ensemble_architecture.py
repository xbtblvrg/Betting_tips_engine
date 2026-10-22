"""Tests and Empirical Evaluation for Five-Pillar Multi-Model Ensemble Architecture.

Verifies:
1. Multi-sport Monte Carlo simulators (MLB, NFL, Soccer) execute coherently.
2. Logit-space Bayesian Model Averaging (BMA) blends predictions without distortion.
3. Self-improving walk-forward weight calibration minimizes out-of-sample Brier loss.
4. Divergence / Anomaly guard detects model disagreement.
"""
import pytest
import numpy as np

from betting_engine.monte_carlo_engine import (
    simulate_mlb_match,
    simulate_nfl_match,
    simulate_soccer_match,
    MLBSimResult,
    NFLSimResult,
    SoccerSimResult,
)
from betting_engine.ensemble_architecture import (
    blend_ensemble_probabilities,
    calibrate_ensemble_weights_walk_forward,
    EnsemblePrediction,
    load_ensemble_weights,
)


def test_mlb_simulation_coherence_and_f5():
    """Verifies MLB stochastic simulation across Inning 1, F5, and full game."""
    res = simulate_mlb_match(
        home_starter_fip=3.40,
        away_starter_fip=4.50,
        wind_speed_mph=14.0,
        wind_bearing='outfield',
        n_sims=5000,
        seed=101
    )
    assert isinstance(res, MLBSimResult)

    # Home has superior starter (FIP 3.40 vs 4.50) -> should win more often
    assert res.home_win_prob > res.away_win_prob
    assert res.f5_home_win_prob > res.f5_away_win_prob

    # NRFI/YRFI run prob in Inning 1 (high outfield wind elevates YRFI)
    assert 0.30 <= res.first_inning_run_prob <= 0.75

    # Full game totals monotonicity
    assert res.totals_over[7.5] > res.totals_over[8.5]


def test_nfl_simulation_and_key_numbers():
    """Verifies NFL simulation naturally produces clusters on key numbers 3 and 7."""
    res = simulate_nfl_match(
        home_net_epa=0.08,
        away_net_epa=-0.04,
        total_line_prior=45.5,
        n_sims=5000,
        seed=202
    )
    assert isinstance(res, NFLSimResult)

    # Key number rate: In NFL, ~14-16% of games end by 3 points and ~8-10% by 7 points
    assert res.key_number_3_rate > 0.08, f"Key number 3 rate was {res.key_number_3_rate:.3f}"
    assert res.key_number_7_rate > 0.05, f"Key number 7 rate was {res.key_number_7_rate:.3f}"

    # Spread cover check: Home cover should be higher than away cover for +3.5 line
    assert res.spread_cover_probs[-3.0] <= res.spread_cover_probs[3.0]


def test_soccer_simulation_and_btts():
    """Verifies Soccer simulation for 1X2, Asian Handicap, and BTTS."""
    res = simulate_soccer_match(
        home_expected_goals=1.80,
        away_expected_goals=1.10,
        n_sims=5000,
        seed=303
    )
    assert isinstance(res, SoccerSimResult)

    # 1X2 sums to 1.0
    sum_1x2 = res.home_win_prob + res.draw_prob + res.away_win_prob
    assert abs(sum_1x2 - 1.0) < 0.005

    # BTTS probabilities sum to 1.0
    sum_btts = res.btts_yes_prob + res.btts_no_prob
    assert abs(sum_btts - 1.0) < 0.005


def test_ensemble_logit_blending_and_divergence_guard():
    """Verifies logit-space Bayesian blending and divergence detection."""
    # 1. When models agree
    pred_agree = blend_ensemble_probabilities(
        league='NBA',
        market='moneyline',
        p_stat=0.62,
        p_sim=0.64,
        p_market=0.63
    )
    assert isinstance(pred_agree, EnsemblePrediction)
    assert 0.62 <= pred_agree.blended_probability <= 0.64
    assert not pred_agree.has_high_divergence
    assert pred_agree.divergence_score < 0.05

    # 2. When models strongly diverge (e.g. simulation detects trap)
    pred_div = blend_ensemble_probabilities(
        league='NHL',
        market='totals',
        p_stat=0.68,   # Naive model claims Over is 68%
        p_sim=0.48,    # Simulation detects 48% due to empty-net/low-pace
        p_market=0.51
    )
    assert pred_div.has_high_divergence
    assert pred_div.divergence_score >= 0.14
    # Blended probability is pulled safely down toward center
    assert pred_div.blended_probability < 0.60


def test_ensemble_walk_forward_weight_calibration():
    """Verifies that out-of-time walk forward calibration selects weights that minimize Brier score."""
    # Synthetic dataset where simulation (p_sim) is much more accurate than naive stat (p_stat)
    rng = np.random.default_rng(777)
    observations = []

    for _ in range(50):
        true_p = rng.uniform(0.3, 0.7)
        won = bool(rng.random() < true_p)

        # Simulation is close to true_p
        p_sim = float(np.clip(true_p + rng.normal(0, 0.03), 0.05, 0.95))
        # Stat model has high noise
        p_stat = float(np.clip(true_p + rng.normal(0, 0.15), 0.05, 0.95))

        observations.append({
            'status': 'won' if won else 'lost',
            'p_stat': p_stat,
            'p_sim': p_sim,
            'p_market': true_p,
            'market': 'moneyline'
        })

    calibrated_weights = calibrate_ensemble_weights_walk_forward(observations, league='NHL')
    assert isinstance(calibrated_weights, dict)
    assert 'sim' in calibrated_weights
    # Higher weight given to the superior simulation model
    assert calibrated_weights['sim'] >= 0.40
