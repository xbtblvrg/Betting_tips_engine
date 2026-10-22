"""Market Devigging and Consensus Fair Value Evaluator.

Extracts true probabilities from sportsbook odds by removing overround (vig)
using multiplicative and power/Shin devig methods. Evaluates Closing Line Value
(CLV) and sharp market consensus.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def multiplicative_devig(odds_list: List[float]) -> List[float]:
    """Basic proportional devigging by dividing by total bookmaker overround."""
    if not odds_list or any(o <= 1.0 for o in odds_list):
        return []
    implied = [1.0 / o for o in odds_list]
    total_implied = sum(implied)
    if total_implied <= 0:
        return []
    return [round(p / total_implied, 4) for p in implied]


def power_devig(odds_list: List[float], max_iter: int = 25) -> List[float]:
    """Power/Shin method devigging, accounting for favourite-longshot bias."""
    if len(odds_list) != 2 or any(o <= 1.0 for o in odds_list):
        return multiplicative_devig(odds_list)

    p1_imp = 1.0 / odds_list[0]
    p2_imp = 1.0 / odds_list[1]
    
    # Binary search for exponent k such that (p1_imp)^k + (p2_imp)^k = 1.0
    low, high = 0.5, 3.0
    k = 1.0
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        val = (p1_imp ** mid) + (p2_imp ** mid)
        if abs(val - 1.0) < 1e-5:
            k = mid
            break
        elif val > 1.0:
            low = mid
        else:
            high = mid

    p1_fair = p1_imp ** k
    p2_fair = p2_imp ** k
    total = p1_fair + p2_fair
    return [round(p1_fair / total, 4), round(p2_fair / total, 4)]


def evaluate_market_edge(
    offered_odds: float,
    counter_odds: float,
    model_probability: float
) -> Dict[str, Any]:
    """Evaluates whether an offered price has genuine value against the devigged market."""
    if offered_odds <= 1.0 or counter_odds <= 1.0:
        return {"has_edge": False, "fair_odds": 0.0, "market_fair_prob": 0.0, "edge_pct": 0.0}

    devigged = power_devig([offered_odds, counter_odds])
    if not devigged:
        return {"has_edge": False, "fair_odds": 0.0, "market_fair_prob": 0.0, "edge_pct": 0.0}

    market_p = devigged[0]
    fair_odds = round(1.0 / market_p, 3) if market_p > 0 else 0.0

    # True edge between our model probability and the market's true probability
    edge_pct = round((model_probability - market_p) * 100.0, 2)
    ev_pct = round((offered_odds * model_probability - 1.0) * 100.0, 2)

    return {
        "has_edge": edge_pct > 1.5,
        "market_fair_prob": market_p,
        "fair_odds": fair_odds,
        "model_prob": round(model_probability, 4),
        "model_market_edge_pct": edge_pct,
        "expected_value_pct": ev_pct,
        "vig_pct": round(((1.0 / offered_odds + 1.0 / counter_odds) - 1.0) * 100.0, 2)
    }
