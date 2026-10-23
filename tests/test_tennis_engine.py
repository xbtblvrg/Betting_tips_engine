"""Tests for the Tennis Quantitative Modeling Engine.

Verifies:
1. Closed-form Markov hold formula theoretical precision.
2. Tiebreak dynamic programming symmetries and boundaries.
3. Full set-score joint distribution normalization (sums to 1.0).
4. Surface Speed Index (CPI) effects across Clay, Hard, and Grass.
5. Monte Carlo match simulation convergence and line evaluations.
6. Multi-market candidate dispatch (Moneyline, Handicap, Total Games, 1st Set).
7. Crucible fatigue defense gates.
"""
import math
import pytest
from src.betting_engine.tennis_engine import (
    hold_probability,
    tiebreak_probability,
    set_score_distribution,
    calibrate_serve_probabilities,
    TOUR_BASELINES,
    SURFACE_MODIFIERS,
)
from src.betting_engine.monte_carlo_engine import (
    simulate_tennis_match,
    simulate_candidate_probability,
    TennisSimResult,
)
from src.betting_engine.ensemble_architecture import (
    blend_ensemble_probabilities,
    DEFAULT_SPORT_WEIGHTS,
)
from src.betting_engine.crucible_rules import evaluate_crucible_candidate


def test_hold_probability_theoretical_values():
    """Verifies the closed-form Markov hold formula against established tennis quant benchmarks."""
    # Boundary checks
    assert hold_probability(0.0) == 0.0
    assert hold_probability(1.0) == 1.0

    # Symmetric coin-flip point: P(Hold) must be exactly 0.50
    assert abs(hold_probability(0.50) - 0.50) < 1e-6

    # Pollard/Barnett theoretical values:
    # p = 0.60 -> ~0.7357
    assert abs(hold_probability(0.60) - 0.7357) < 0.001

    # p = 0.65 -> ~0.8296 (Average ATP hard court server)
    assert abs(hold_probability(0.65) - 0.8296) < 0.001

    # p = 0.70 -> ~0.9008 (Elite server like Hurkacz/Isner)
    assert abs(hold_probability(0.70) - 0.9008) < 0.001

    # Strict monotonicity: higher point win prob must increase hold prob
    p_values = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    holds = [hold_probability(p) for p in p_values]
    assert all(holds[i] < holds[i + 1] for i in range(len(holds) - 1))


def test_tiebreak_probability():
    """Verifies dynamic programming tiebreak calculation."""
    # Symmetrical players must produce a 50/50 tiebreak
    p_tb_sym = tiebreak_probability(0.65, 0.65)
    assert abs(p_tb_sym - 0.50) < 0.005

    # Asymmetric: Stronger server A (0.70 vs 0.60) has massive advantage in tiebreak
    p_tb_asym = tiebreak_probability(0.70, 0.60)
    assert p_tb_asym > 0.65

    # Underdog server A (0.55 vs 0.65) has lower tiebreak win probability
    p_tb_dog = tiebreak_probability(0.55, 0.65)
    assert p_tb_dog < 0.35


def test_set_score_distribution_completeness_and_normalization():
    """Verifies that the set score distribution contains all legal scores and sums to 1.0."""
    p_set_a, scores = set_score_distribution(0.65, 0.62)

    # Probabilities across all discrete set outcomes must sum to 1.0
    total_prob = sum(scores.values())
    assert abs(total_prob - 1.0) < 1e-5

    # Player A win prob is sum of scores where A has more games
    sum_a_wins = sum(v for (a, b), v in scores.items() if a > b)
    assert abs(p_set_a - sum_a_wins) < 1e-5

    # Check that legal tennis scores are present
    legal_scores = {
        (6, 0), (6, 1), (6, 2), (6, 3), (6, 4), (7, 5), (7, 6),
        (0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 7), (6, 7)
    }
    for score in legal_scores:
        assert score in scores
        assert scores[score] >= 0.0


def test_surface_speed_index_calibration():
    """Verifies CPI surface adjustments (Grass vs Hard vs Clay)."""
    # Grass must produce higher serve win prob than Hard, which is higher than Clay
    p_grass_a, p_grass_b = calibrate_serve_probabilities(tour='ATP', surface='grass')
    p_hard_a, p_hard_b = calibrate_serve_probabilities(tour='ATP', surface='hard')
    p_clay_a, p_clay_b = calibrate_serve_probabilities(tour='ATP', surface='clay')

    assert p_grass_a > p_hard_a > p_clay_a
    assert p_grass_b > p_hard_b > p_clay_b

    # WTA baseline is lower than ATP baseline
    p_wta_a, _ = calibrate_serve_probabilities(tour='WTA', surface='hard')
    p_atp_a, _ = calibrate_serve_probabilities(tour='ATP', surface='hard')
    assert p_wta_a < p_atp_a

    # Fatigue penalty strictly reduces point win prob
    p_fatigued, _ = calibrate_serve_probabilities(tour='ATP', surface='hard', fatigue_penalty_a=0.04)
    assert p_fatigued < p_hard_a


def test_monte_carlo_tennis_simulation():
    """Verifies stochastic micro-simulation of a full match (10,000 runs)."""
    res = simulate_tennis_match(
        p_serve_a=0.66,
        p_serve_b=0.62,
        best_of=3,
        target_total_line=22.5,
        target_handicap_line=-3.5,
        n_sims=10000,
        seed=42
    )

    assert isinstance(res, TennisSimResult)
    # Match win probabilities sum to 1.0
    assert abs(res.player_a_win_prob + res.player_b_win_prob - 1.0) < 0.005

    # Favorite A has advantage
    assert res.player_a_win_prob > res.player_b_win_prob

    # 1st set probabilities sum to 1.0
    assert abs(res.first_set_player_a_win_prob + res.first_set_player_b_win_prob - 1.0) < 0.005

    # Straight sets + deciding set equals 1.0 (for best of 3)
    # Either match ends 2-0 / 0-2 (straight sets) or 2-1 / 1-2 (deciding set)
    # Straight sets prob + straight sets for B + deciding sets == 1.0
    assert res.deciding_set_prob > 0.20 and res.deciding_set_prob < 0.50

    # Total games makes biological sense (mean around 22-24)
    assert 20.0 < res.mean_total_games < 26.0

    # Target lines evaluated
    assert 22.5 in res.totals_over
    assert 22.5 in res.totals_under
    assert abs(res.totals_over[22.5] + res.totals_under[22.5] - 1.0) < 0.005

    assert -3.5 in res.handicap_cover_probs
    assert 0.0 < res.handicap_cover_probs[-3.5] < 1.0


def test_simulate_candidate_probability_tennis():
    """Verifies that simulate_candidate_probability correctly dispatches Tennis candidates."""
    # Moneyline
    p_ml_home = simulate_candidate_probability(
        league='ATP',
        market='moneyline',
        side='home',
        context={'surface': 'hard', 'rating_diff': 50.0},
        n_sims=5000,
        seed=42
    )
    p_ml_away = simulate_candidate_probability(
        league='ATP',
        market='moneyline',
        side='away',
        context={'surface': 'hard', 'rating_diff': 50.0},
        n_sims=5000,
        seed=42
    )
    assert p_ml_home > 0.50
    assert p_ml_away < 0.50
    assert abs(p_ml_home + p_ml_away - 1.0) < 0.01

    # Total Games Over/Under
    p_over = simulate_candidate_probability(
        league='ATP',
        market='totals',
        side='over',
        line=22.5,
        context={'surface': 'grass'},  # Grass increases games via tiebreaks
        n_sims=5000,
        seed=42
    )
    p_under = simulate_candidate_probability(
        league='ATP',
        market='totals',
        side='under',
        line=22.5,
        context={'surface': 'grass'},
        n_sims=5000,
        seed=42
    )
    assert abs(p_over + p_under - 1.0) < 0.01

    # 1st Set Winner
    p_set1 = simulate_candidate_probability(
        league='WTA',
        market='first_set',
        side='home',
        context={'surface': 'clay', 'rating_diff': 40.0},
        n_sims=5000,
        seed=42
    )
    assert p_set1 > 0.50


def test_ensemble_tennis_weights():
    """Verifies that the multi-pillar ensemble assigns high simulation weight (45%) to Tennis."""
    assert DEFAULT_SPORT_WEIGHTS['ATP']['sim'] == 0.45
    assert DEFAULT_SPORT_WEIGHTS['WTA']['sim'] == 0.45

    pred = blend_ensemble_probabilities(
        league='ATP',
        market='moneyline',
        p_stat=0.62,
        p_sim=0.68,
        p_market=0.64,
        p_momentum=0.60
    )
    assert pred.weights_applied['sim'] == 0.45
    assert 0.60 < pred.blended_probability < 0.68


def test_crucible_tennis_fatigue_gate():
    """Verifies that deep minus-handicaps on physically exhausted players are rejected."""
    # Fatigued favorite with deep handicap (-4.5 games)
    exhausted_candidate = {
        'id': 'test_cand_1',
        'league': 'ATP',
        'market': 'spreads',
        'side': 'home',
        'line': -4.5,
        'odds': 1.95,
        'probability': 0.58,
        'ev': 0.08,
    }
    context_fatigued = {'fatigue_a': 0.05}  # Came off 3.5 hour match

    passed, reason, _ = evaluate_crucible_candidate(exhausted_candidate, context=context_fatigued)
    assert not passed
    assert 'fatigued_player_deep_handicap_excluded' in reason

    # Rested favorite with same handicap should NOT be gated
    context_rested = {'fatigue_a': 0.0}
    passed_rested, _, _ = evaluate_crucible_candidate(exhausted_candidate, context=context_rested)
    assert passed_rested


def test_tennis_contracts_and_governance_consistency():
    """Verifies that tennis is fully wired into contracts, EvidenceGuard, and knowledge cards."""
    from src.betting_engine.contracts import NO_FULL_GAME_DRAW, allocation_family
    from src.betting_engine.governance import CARD_IDS, KnowledgeAgent
    from src.betting_engine.presentation import STAKE_LEAGUE_PATHS, market_name

    # 1. Zero draw in tennis full game
    assert 'ATP' in NO_FULL_GAME_DRAW
    assert 'WTA' in NO_FULL_GAME_DRAW
    assert 'CHALLENGER' in NO_FULL_GAME_DRAW

    # 2. Allocation family for tennis moneyline & spreads
    assert allocation_family({'league': 'ATP', 'market': 'spreads', 'line': 0}) == 'moneyline'
    assert allocation_family({'league': 'WTA', 'market': 'spreads', 'line': -0.5}) == 'moneyline'

    # 3. Knowledge card wiring
    assert CARD_IDS['ATP'] == 'K14'
    assert CARD_IDS['WTA'] == 'K14'
    assert CARD_IDS['CHALLENGER'] == 'K14'

    ka = KnowledgeAgent()
    refs = ka.references('ATP')
    ref_ids = [r['id'] for r in refs]
    assert 'K14' in ref_ids
    assert 'K01' in ref_ids

    # 4. Stake paths & market display
    assert STAKE_LEAGUE_PATHS['ATP'] == 'tennis/atp'
    assert STAKE_LEAGUE_PATHS['WTA'] == 'tennis/wta'
    assert STAKE_LEAGUE_PATHS['CHALLENGER'] == 'tennis/challenger'
    assert market_name('ATP', 'moneyline') == 'Winner'
    assert market_name('ATP', 'spreads') == 'Game Handicap'
    assert market_name('ATP', 'totals') == 'Total Games'


def test_tennis_model_state_and_name_normalization():
    """Verifies that TennisModelState normalizes names and maintains player ratings."""
    from src.betting_engine.tennis_engine import TennisModelState, normalize_tennis_name

    assert normalize_tennis_name('Herbert, Pierre-Hugues') == normalize_tennis_name('Pierre-Hugues Herbert')
    assert normalize_tennis_name('Onclin, Gauthier') == normalize_tennis_name('Gauthier Onclin')

    state = TennisModelState('ATP')
    # Seeded priors check
    elo_medvedev, _ = state.get_player_elo('Daniil Medvedev')
    assert elo_medvedev > 2200.0

    elo_fritz, _ = state.get_player_elo('Herbert, Pierre-Hugues')
    assert elo_fritz < 1750.0

    # Unrated player default
    elo_unknown, n_unknown = state.get_player_elo('Random New Qualifier')
    assert elo_unknown == 1550.0
    assert n_unknown == 0


def test_tennis_league_model_forecast_with_real_players():
    """Verifies that TennisLeagueModel accurately predicts lopsided matchups rather than 50/50 coin flips."""
    from src.betting_engine.domain import Game
    from src.betting_engine.tennis_engine import TennisLeagueModel

    from datetime import datetime, timezone, timedelta
    future_start = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    g = Game(
        id='ATP:test_medvedev_shimabukuro',
        league='ATP',
        start=future_start,
        home='Daniil Medvedev',
        away='Sho Shimabukuro',
        home_id='Daniil Medvedev',
        away_id='Sho Shimabukuro',
        home_abbr='MED',
        away_abbr='SHI',
        state='scheduled',
        season=2026,
        neutral=True
    )
    model = TennisLeagueModel('ATP')
    fc = model.forecast(g)

    # Medvedev must be an overwhelming favorite (> 85%), not a 50/50 coin flip!
    assert fc.home_probability > 0.85
    assert fc.away_probability < 0.15
    assert fc.margin > 4.0

    # Test market probability pricing
    p_home_ml, _ = fc.market_probability('moneyline', 'home')
    p_away_ml, _ = fc.market_probability('moneyline', 'away')
    assert p_home_ml > 0.85
    assert p_away_ml < 0.15


def test_tennis_unrated_bayesian_shrinkage():
    """Verifies that high-uncertainty unrated matches apply Bayesian shrinkage to prevent false EV on longshots."""
    from src.betting_engine.domain import Game
    from src.betting_engine.tennis_engine import TennisLeagueModel

    from datetime import datetime, timezone, timedelta
    future_start = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    g = Game(
        id='ATP:test_unrated_qualifiers',
        league='ATP',
        start=future_start,
        home='Unknown Qualifier One',
        away='Unknown Qualifier Two',
        home_id='Unknown Qualifier One',
        away_id='Unknown Qualifier Two',
        home_abbr='UQ1',
        away_abbr='UQ2',
        state='scheduled',
        season=2026,
        neutral=True
    )
    model = TennisLeagueModel('ATP')
    fc = model.forecast(g)
    assert fc.uncertainty > 0.35


