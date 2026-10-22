"""Quantitative Player Injury Impact Model.

Transforms raw injury reports (ESPN/SportsDataverse) into position-weighted
team strength adjustments (points/spread/totals penalties) instead of passive
text display.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# Baseline point-spread impact by key position/sport when player is OUT or IR
POSITION_WEIGHTS: Dict[str, Dict[str, float]] = {
    "NFL": {
        "QB": 4.5,   # Starting Quarterback absence is the largest single-player impact
        "LT": 1.2,   # Left tackle pass protection
        "WR": 0.8,   # Primary wide receiver
        "CB": 0.8,   # Shutdown cornerback
        "DE": 0.9,   # Edge rusher
        "RB": 0.5,   # Running back
        "TE": 0.5,
        "K": 0.4,
    },
    "NBA": {
        "PG": 2.2,   # Primary ballhandler / playmaker
        "C": 1.8,    # Rim protector / rebound anchor
        "SG": 1.4,
        "SF": 1.6,   # Two-way wing
        "PF": 1.3,
    },
    "WNBA": {
        "PG": 2.5,
        "C": 2.2,
        "G": 1.5,
        "F": 1.6,
    },
    "NHL": {
        "G": 0.55,   # Goalie change measured in expected goal differential (~0.4 - 0.7 goals)
        "C": 0.25,   # 1st line center
        "D": 0.20,   # Top pairing defenseman
        "RW": 0.15,
        "LW": 0.15,
    },
    "MLB": {
        "SP": 0.75,  # Starting pitcher (runs allowed per game delta)
        "OF": 0.15,
        "1B": 0.12,
        "3B": 0.12,
        "SS": 0.10,
        "C": 0.12,
        "2B": 0.08,
    }
}


def calculate_injury_impact(league: str, injuries: Dict[str, Any]) -> Dict[str, Any]:
    """Computes quantitative team penalty points from verified OUT/IR absences."""
    weights = POSITION_WEIGHTS.get(league, {})
    if not weights or not injuries:
        return {
            "has_impact": False,
            "home_penalty_points": 0.0,
            "away_penalty_points": 0.0,
            "net_home_advantage": 0.0,
            "key_absences": {"home": [], "away": []}
        }

    home_injuries = injuries.get("home", []) or []
    away_injuries = injuries.get("away", []) or []

    def evaluate_side(side_injuries: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]]]:
        penalty = 0.0
        key_players = []
        for p in side_injuries:
            status = str(p.get("status", "")).upper()
            if status not in ("OUT", "IR", "INJURED_RESERVE", "SUSPENSION", "15DAYIL", "60DAYIL"):
                continue

            pos = str(p.get("athlete_position", "")).upper()
            # Match position prefix/exact
            weight = weights.get(pos, 0.0)
            if not weight:
                for k, w in weights.items():
                    if k in pos:
                        weight = w
                        break

            if weight > 0:
                penalty += weight
                key_players.append({
                    "name": p.get("athlete_display_name", "Unknown"),
                    "position": pos,
                    "status": status,
                    "estimated_point_value": weight
                })
        # Bounded team cap so single team penalty cannot exceed reasonable maximum
        max_cap = 7.0 if league == "NFL" else (5.0 if league in ("NBA", "WNBA") else 1.5)
        return min(max_cap, penalty), key_players

    home_pen, home_keys = evaluate_side(home_injuries)
    away_pen, away_keys = evaluate_side(away_injuries)

    # Net impact from home perspective (positive = away team more impaired -> home advantage)
    net_adv = round(away_pen - home_pen, 2)

    return {
        "has_impact": (home_pen > 0 or away_pen > 0),
        "home_penalty_points": round(home_pen, 2),
        "away_penalty_points": round(away_pen, 2),
        "net_home_advantage": net_adv,
        "key_absences": {
            "home": home_keys,
            "away": away_keys
        }
    }
