"""Unit tests for the autonomous sandbox model infrastructure and archetypes."""
import math
import pytest

from betting_engine.sandbox_models.base import DiscreteDistribution, BaseCandidateModel
from betting_engine.sandbox_models.validator import validate_candidate_model
from betting_engine.sandbox_models.archetypes.negative_binomial import NegativeBinomialOverdispersionModel
from betting_engine.sandbox_models.archetypes.situational_fatigue import SituationalFatigueModel
from betting_engine.sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel
from betting_engine.sandbox_models.archetypes.bivariate_copula import BivariateCopulaHazardModel


def test_discrete_distribution_normalization_and_methods():
    scores = {(1, 0): 0.3, (0, 0): 0.2, (0, 1): 0.5}
    dist = DiscreteDistribution.from_scores(scores)

    assert math.isclose(dist.p_home, 0.3, abs_tol=1e-3)
    assert math.isclose(dist.p_draw, 0.2, abs_tol=1e-3)
    assert math.isclose(dist.p_away, 0.5, abs_tol=1e-3)

    # Totals: line = 0.5
    # (1, 0) sum=1 > 0.5, (0, 1) sum=1 > 0.5 -> total over = 0.8
    assert math.isclose(dist.total_prob(0.5, 'over'), 0.8, abs_tol=1e-3)
    assert math.isclose(dist.total_prob(0.5, 'under'), 0.2, abs_tol=1e-3)

    # Spread: line = -0.5 for home
    # home win (1, 0): 1 - 0 - 0.5 = 0.5 > 0 -> prob = 0.3
    assert math.isclose(dist.spread_prob(-0.5, 'home'), 0.3, abs_tol=1e-3)

    # Spread: line = +0.5 for away
    # (0, 0): 0 - 0 + 0.5 > 0 (prob 0.2)
    # (0, 1): 1 - 0 + 0.5 > 0 (prob 0.5)
    # Total away +0.5 cover prob = 0.7
    assert math.isclose(dist.spread_prob(0.5, 'away'), 0.7, abs_tol=1e-3)


def test_negative_binomial_archetype_validation():
    model = NegativeBinomialOverdispersionModel(league='NHL', scope='period_3', family='totals')
    context = {'home_expected': 1.1, 'away_expected': 0.9}

    passed, errors = validate_candidate_model(model, context)
    assert passed, f"Validation failed: {errors}"

    prob, ev = model.evaluate_market('totals', 'over', 1.5, 2.10, context)
    assert 0.01 <= prob <= 0.99
    assert math.isclose(ev, round(prob * 2.10 - 1.0, 4))


def test_situational_fatigue_archetype_validation():
    model = SituationalFatigueModel(league='NBA', scope='full_game', family='spreads')
    # Test with back-to-back for home team
    context_b2b = {'home_expected': 112.0, 'away_expected': 110.0, 'home_rest_days': 0, 'away_rest_days': 2}
    context_rested = {'home_expected': 112.0, 'away_expected': 110.0, 'home_rest_days': 3, 'away_rest_days': 0}

    passed, errors = validate_candidate_model(model, context_b2b)
    assert passed, f"Validation failed: {errors}"

    dist_b2b = model.predict_distribution(context_b2b)
    dist_rested = model.predict_distribution(context_rested)

    # Home team should have higher expected points when rested than on b2b
    assert dist_rested.home_expected > dist_b2b.home_expected


def test_odds_damped_kelly_archetype_validation():
    model = OddsDampedKellyShrinkageModel(league='NBA', scope='full_game', family='spreads')
    context = {'home_expected': 115.0, 'away_expected': 105.0}

    passed, errors = validate_candidate_model(model, context)
    assert passed, f"Validation failed: {errors}"

    # For heavy favorite (odds = 1.25), probability should shrink towards implied prob (1/1.25 = 0.8)
    p_damped, ev_damped = model.evaluate_market('moneyline', 'home', None, 1.25, context)
    assert 0.01 <= p_damped <= 0.99


def test_bivariate_copula_archetype_validation():
    model = BivariateCopulaHazardModel(league='MLS', scope='full_game', family='totals', correlation_rho=0.25)
    context = {'home_expected': 1.6, 'away_expected': 1.2}

    passed, errors = validate_candidate_model(model, context)
    assert passed, f"Validation failed: {errors}"

    dist = model.predict_distribution(context)
    assert math.isclose(dist.p_home + dist.p_away + dist.p_draw, 1.0, abs_tol=1e-3)


def test_zero_inflated_poisson_archetype_validation():
    from betting_engine.sandbox_models.archetypes.zero_inflated import ZeroInflatedPoissonModel
    model = ZeroInflatedPoissonModel(league='EPL', scope='full_game', family='totals', zero_inflation_pi=0.15)
    context = {'home_expected': 1.3, 'away_expected': 1.0}

    passed, errors = validate_candidate_model(model, context)
    assert passed, f"Validation failed: {errors}"

    dist = model.predict_distribution(context)
    # 0-0 probability should be enhanced by zero-inflation
    p_0_0 = dist.scores.get((0, 0), 0.0)
    assert p_0_0 > 0.05

    # Test high-scoring sport (e.g. NBA)
    model_nba = ZeroInflatedPoissonModel(league='NBA', scope='full_game', family='totals', zero_inflation_pi=0.05)
    context_nba = {'home_expected': 110.0, 'away_expected': 105.0}
    passed_nba, errors_nba = validate_candidate_model(model_nba, context_nba)
    assert passed_nba, f"NBA ZIP Validation failed: {errors_nba}"
    dist_nba = model_nba.predict_distribution(context_nba)
    assert dist_nba.home_expected > 90.0


def test_evaluate_market_away_side_and_team_name_resolution():
    model = NegativeBinomialOverdispersionModel(league='NHL', scope='full_game', family='spreads')
    ctx = {
        'home_expected': 3.5,
        'away_expected': 2.0,
        'home_team': 'Edmonton Oilers',
        'away_team': 'Calgary Flames',
    }

    # Edmonton is favorite (3.5 vs 2.0). Away team (Calgary) has lower win prob
    p_home, _ = model.evaluate_market('moneyline', 'Edmonton Oilers', None, 1.50, ctx, side='home')
    p_away, _ = model.evaluate_market('moneyline', 'Calgary Flames', None, 2.70, ctx, side='away')

    assert p_home > p_away
    assert 0.01 <= p_home <= 0.99
    assert 0.01 <= p_away <= 0.99
    assert math.isclose(p_home + p_away + model.predict_distribution(ctx).p_draw, 1.0, abs_tol=1e-3)

    # Away spread: +1.5
    p_away_spread, _ = model.evaluate_market('spreads', 'Calgary Flames +1.5', 1.5, 1.95, ctx, side='away')
    assert p_away_spread > p_away  # With +1.5 goals, cover probability should exceed outright win prob


def test_team_total_and_derivative_evaluation():
    scores = {
        (2, 1): 0.4,
        (1, 0): 0.3,
        (0, 0): 0.1,
        (0, 2): 0.2,
    }
    dist = DiscreteDistribution.from_scores(scores)

    # Home total over 1.5: (2, 1) has home=2 > 1.5 -> prob = 0.4
    assert math.isclose(dist.team_total_prob(1.5, 'over', 'home'), 0.4, abs_tol=1e-3)
    # Home total under 1.5: (1, 0), (0, 0), (0, 2) has home < 1.5 -> prob = 0.6
    assert math.isclose(dist.team_total_prob(1.5, 'under', 'home'), 0.6, abs_tol=1e-3)

    # Away total over 0.5: (2, 1) away=1, (0, 2) away=2 -> prob = 0.6
    assert math.isclose(dist.team_total_prob(0.5, 'over', 'away'), 0.6, abs_tol=1e-3)
    # Away total under 0.5: (1, 0), (0, 0) -> prob = 0.4
    assert math.isclose(dist.team_total_prob(0.5, 'under', 'away'), 0.4, abs_tol=1e-3)

    # BTTS (both score): (2, 1) has h>0 and a>0 -> prob = 0.4
    assert math.isclose(dist.both_score_prob('yes'), 0.4, abs_tol=1e-3)
    assert math.isclose(dist.both_score_prob('no'), 0.6, abs_tol=1e-3)

    # Double chance:
    # 1X (h >= a): (2, 1) h>a, (1, 0) h>a, (0, 0) h=a -> prob = 0.4 + 0.3 + 0.1 = 0.8
    assert math.isclose(dist.double_chance_prob('1x'), 0.8, abs_tol=1e-3)
    # X2 (a >= h): (0, 0) h=a, (0, 2) a>h -> prob = 0.1 + 0.2 = 0.3
    assert math.isclose(dist.double_chance_prob('x2'), 0.3, abs_tol=1e-3)
    # 12 (h != a): (2, 1), (1, 0), (0, 2) -> prob = 0.4 + 0.3 + 0.2 = 0.9
    assert math.isclose(dist.double_chance_prob('12'), 0.9, abs_tol=1e-3)

    # Candidate model evaluate_market integration
    model = NegativeBinomialOverdispersionModel(league='EPL', scope='full_game', family='totals')
    ctx = {'home_expected': 2.0, 'away_expected': 1.0}

    p_ht_over, _ = model.evaluate_market('home_total', 'Arsenal Over 1.5', 1.5, 1.85, ctx, side='over')
    p_tot_over, _ = model.evaluate_market('totals', 'Over 1.5', 1.5, 1.30, ctx, side='over')
    # Full game total over 1.5 should be strictly greater than just home total over 1.5
    assert p_tot_over > p_ht_over


