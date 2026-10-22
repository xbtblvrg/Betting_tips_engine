"""Advanced Sports Metrics: xG, EPA, and Efficiency Differentials.

Translates raw play counts and box scores into quality-adjusted metrics:
- NHL: Danger-zone shot quality / Expected Goals (xG) share
- NFL: Expected Points Added (EPA) per play and drive success rate
- MLB: Pitcher Arsenal FIP / WHIP run-prevention index
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


def calculate_nhl_xg_share(box: Dict[str, Any]) -> Dict[str, Any]:
    """Estimates expected goals (xG) from shot quality, shooting fraction, and save rates."""
    if not box or "home" not in box or "away" not in box:
        return {"has_metrics": False, "home_xg": 3.0, "away_xg": 3.0, "home_xg_share": 0.5}

    h = box["home"]
    a = box["away"]
    sf_h = float(h.get("shots_for", 30.0))
    sf_a = float(a.get("shots_for", 30.0))
    ppg_h = float(h.get("power_play_goals", 0.5))
    ppg_a = float(a.get("power_play_goals", 0.5))
    save_h = float(h.get("save_fraction", 0.905))
    save_a = float(a.get("save_fraction", 0.905))

    # Baseline 5v5 expected conversion rate ~8.2%, elevated on power plays
    xg_home = (sf_h * 0.082) + (ppg_h * 0.45) + ((1.0 - save_a) * sf_h * 0.15)
    xg_away = (sf_a * 0.082) + (ppg_a * 0.45) + ((1.0 - save_h) * sf_a * 0.15)
    total_xg = xg_home + xg_away

    return {
        "has_metrics": True,
        "home_xg": round(xg_home, 2),
        "away_xg": round(xg_away, 2),
        "home_xg_share": round(xg_home / total_xg, 3) if total_xg > 0 else 0.5,
        "total_expected_goals": round(total_xg, 2)
    }


def calculate_nfl_epa_efficiency(box: Dict[str, Any]) -> Dict[str, Any]:
    """Estimates net EPA per play and passing efficiency differentials."""
    if not box or "home" not in box or "away" not in box:
        return {"has_metrics": False, "home_epa_per_play": 0.0, "away_epa_per_play": 0.0, "net_edge": 0.0}

    h = box["home"]
    a = box["away"]
    ypp_h = float(h.get("yards_per_play", 5.4))
    ypp_a = float(a.get("yards_per_play", 5.4))
    third_h = float(h.get("third_down_fraction", 0.40))
    third_a = float(a.get("third_down_fraction", 0.40))
    to_h = float(h.get("turnovers_per_play", 0.02))
    to_a = float(a.get("turnovers_per_play", 0.02))

    # Proxy EPA per play: (Yards/play - baseline 5.4) * 0.06 + (3rdDown - 0.40) * 0.35 - (TO% - 0.02) * 2.0
    epa_h = (ypp_h - 5.4) * 0.06 + (third_h - 0.40) * 0.35 - (to_h - 0.02) * 2.0
    epa_a = (ypp_a - 5.4) * 0.06 + (third_a - 0.40) * 0.35 - (to_a - 0.02) * 2.0
    net_edge = round(epa_h - epa_a, 3)

    return {
        "has_metrics": True,
        "home_epa_per_play": round(epa_h, 3),
        "away_epa_per_play": round(epa_a, 3),
        "net_home_epa_edge": net_edge,
        "implied_spread_adjustment": round(net_edge * 28.0, 1)  # ~65 plays per game
    }
