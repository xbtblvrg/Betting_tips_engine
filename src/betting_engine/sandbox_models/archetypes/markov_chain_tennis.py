"""Markov Chain Tennis Service-Hold Model Archetype.

Analytically derives tennis game totals, set handicaps, and match outcomes
from first-principles service-hold transition probabilities.
Specialized for ATP, WTA, and Challenger tennis contracts.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from ..base import BaseCandidateModel, DiscreteDistribution


def _game_hold_prob(p_point: float) -> float:
    """Computes probability of winning a service game given point-win probability p."""
    p = max(0.01, min(0.99, p_point))
    q = 1.0 - p
    # Standard Markov tennis game formula
    prob_deuce = 20.0 * (p ** 3) * (q ** 3)
    p_deuce_win = (p ** 2) / max(1e-6, (p ** 2 + q ** 2))
    p_pre_deuce = (p ** 4) * (1.0 + 4.0 * q + 10.0 * (q ** 2))
    return min(0.999, max(0.001, p_pre_deuce + prob_deuce * p_deuce_win))


def _set_score_distribution(p_hold_a: float, p_hold_b: float) -> Dict[Tuple[int, int], float]:
    """Computes exact PMF of set scores (e.g. (6, 4): 0.18, (7, 6): 0.14)."""
    # DP over games up to tiebreak
    dp: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    for total_games in range(12):
        next_dp: Dict[Tuple[int, int], float] = {}
        for (g_a, g_b), prob in dp.items():
            if (g_a >= 6 and g_a - g_b >= 2) or (g_b >= 6 and g_b - g_a >= 2):
                next_dp[(g_a, g_b)] = next_dp.get((g_a, g_b), 0.0) + prob
                continue
            # Server rotates every game (player A serves on even total_games)
            p_server_wins = p_hold_a if (total_games % 2 == 0) else (1.0 - p_hold_b)
            # A wins game
            next_dp[(g_a + 1, g_b)] = next_dp.get((g_a + 1, g_b), 0.0) + prob * p_server_wins
            # B wins game
            next_dp[(g_a, g_b + 1)] = next_dp.get((g_a, g_b + 1), 0.0) + prob * (1.0 - p_server_wins)
        dp = next_dp

    # If reached 6-6, tiebreak decides
    if (6, 6) in dp:
        p_66 = dp.pop((6, 6))
        # Tiebreak win probability (approx 50/50 weighted by hold difference)
        p_tb_a = max(0.2, min(0.8, 0.5 + (p_hold_a - p_hold_b) * 0.75))
        dp[(7, 6)] = dp.get((7, 6), 0.0) + p_66 * p_tb_a
        dp[(6, 7)] = dp.get((6, 7), 0.0) + p_66 * (1.0 - p_tb_a)

    return {k: v for k, v in dp.items() if (k[0] >= 6 and k[0] - k[1] >= 2) or (k[1] >= 6 and k[1] - k[0] >= 2) or k in ((7, 6), (6, 7))}


class MarkovChainTennisModel(BaseCandidateModel):
    """Candidate model deriving tennis games and sets from point-by-point Markov transitions."""

    def __init__(
        self,
        model_id: str = "archetype_markov_tennis_v1",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "full_game",
        family: str = "totals",
        serve_point_prob_a: float = 0.64,
        serve_point_prob_b: float = 0.62,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['player_a_rank', 'player_b_rank'],
            metadata=metadata or {'serve_a': serve_point_prob_a, 'serve_b': serve_point_prob_b},
        )
        self.serve_point_prob_a = max(0.50, min(0.78, serve_point_prob_a))
        self.serve_point_prob_b = max(0.50, min(0.78, serve_point_prob_b))

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        # Check context adjustments
        p_a = float(game_context.get('home_serve_win_pct') or self.serve_point_prob_a)
        p_b = float(game_context.get('away_serve_win_pct') or self.serve_point_prob_b)

        hold_a = _game_hold_prob(p_a)
        hold_b = _game_hold_prob(p_b)

        set_pmf = _set_score_distribution(hold_a, hold_b)

        # For a full game (best of 3 sets), aggregate games won across 2 or 3 sets
        scores: Dict[Tuple[int, int], float] = {}
        if self.scope == 'period_1' or 'set_1' in self.scope:
            # Single set score
            for (ga, gb), prob in set_pmf.items():
                scores[(ga, gb)] = prob
        else:
            # 2-3 sets: convolve set PMFs
            for (ga1, gb1), p1 in set_pmf.items():
                win_a_set1 = (ga1 > gb1)
                for (ga2, gb2), p2 in set_pmf.items():
                    win_a_set2 = (ga2 > gb2)
                    if win_a_set1 == win_a_set2:
                        # Match ended in 2 sets (2-0 or 0-2)
                        k = (ga1 + ga2, gb1 + gb2)
                        scores[k] = scores.get(k, 0.0) + p1 * p2
                    else:
                        # 3rd set deciding
                        for (ga3, gb3), p3 in set_pmf.items():
                            k = (ga1 + ga2 + ga3, gb1 + gb2 + gb3)
                            scores[k] = scores.get(k, 0.0) + p1 * p2 * p3

        return DiscreteDistribution.from_scores(scores)
