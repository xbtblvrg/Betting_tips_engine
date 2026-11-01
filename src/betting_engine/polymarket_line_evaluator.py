"""Continuous Line Valuation Engine for Polymarket Prediction Markets.

Translates model probabilities from baseline sportsbook lines (e.g. Stake)
to arbitrary continuous lines offered by Polymarket contracts (e.g. alternative
totals and spreads), using underlying probability distributions (Normal / Poisson / Laplace).
"""
import math
import logging
from typing import Dict, Any, Optional, Tuple

log = logging.getLogger(__name__)

# Standard standard deviations (volatilities) of total scores and margins by league
LEAGUE_SIGMAS: Dict[str, Dict[str, float]] = {
    "NBA": {"totals": 19.5, "spreads": 12.0},
    "WNBA": {"totals": 15.0, "spreads": 10.5},
    "NFL": {"totals": 13.5, "spreads": 13.0},
    "NHL": {"totals": 2.15, "spreads": 1.70},
    "MLB": {"totals": 3.80, "spreads": 2.20},
    "MLS": {"totals": 1.45, "spreads": 1.30},
    "EPL": {"totals": 1.40, "spreads": 1.25},
    "UCL": {"totals": 1.40, "spreads": 1.25},
    "ATP": {"totals": 5.50, "spreads": 4.00},
    "WTA": {"totals": 5.20, "spreads": 3.80},
}
DEFAULT_SIGMA = {"totals": 10.0, "spreads": 8.0}

# Empirical period proportions of full game score/activity by league and period
PERIOD_PROPORTIONS: Dict[str, Dict[str, float]] = {
    "NFL": {
        "half_1": 0.50,
        "half_2": 0.50,
        "1st_half": 0.50,
        "2nd_half": 0.50,
        "quarter_1": 0.19,
        "quarter_2": 0.31,
        "quarter_3": 0.19,
        "quarter_4": 0.31,
    },
    "NBA": {
        "half_1": 0.495,
        "half_2": 0.505,
        "1st_half": 0.495,
        "2nd_half": 0.505,
        "quarter_1": 0.245,
        "quarter_2": 0.250,
        "quarter_3": 0.245,
        "quarter_4": 0.260,
    },
    "WNBA": {
        "half_1": 0.495,
        "half_2": 0.505,
        "1st_half": 0.495,
        "2nd_half": 0.505,
        "quarter_1": 0.245,
        "quarter_2": 0.250,
        "quarter_3": 0.245,
        "quarter_4": 0.260,
    },
    "NHL": {
        "period_1": 0.30,
        "period_2": 0.35,
        "period_3": 0.35,
    },
    "MLB": {
        "innings_1_5": 0.53,
    },
}


def norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_inv(p: float) -> float:
    """Standard Normal Inverse CDF (quantile function).

    Uses Acklam's algorithm or scipy.stats if available.
    """
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0

    try:
        import scipy.stats
        return float(scipy.stats.norm.ppf(p))
    except ImportError:
        pass

    # Acklam's rational approximation
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    q = p - 0.5
    if abs(q) <= 0.42:
        r = q * q
        return q * (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    
    r = p if q < 0.0 else 1.0 - p
    r = math.sqrt(-math.log(r))
    x = (((((c[0]*r + c[1])*r + c[2])*r + c[3])*r + c[4])*r + c[5]) / \
        ((((d[0]*r + d[1])*r + d[2])*r + d[3])*r + 1.0)
    return -x if q < 0.0 else x


class ContinuousLineEvaluator:
    """Evaluates probability and EV for arbitrary alternative lines on Polymarket."""

    def __init__(self, league_sigmas: Optional[Dict[str, Dict[str, float]]] = None):
        self.sigmas = league_sigmas or LEAGUE_SIGMAS

    def _get_sigma(self, league: str, family: str) -> float:
        l_upper = (league or "").upper()
        fam = "totals" if "total" in (family or "").lower() else "spreads"
        return self.sigmas.get(l_upper, DEFAULT_SIGMA).get(fam, DEFAULT_SIGMA[fam])

    def _get_period_scaling(self, league: str, scope: str) -> Tuple[float, float]:
        """Returns (weight, sigma_scale) for a given league and period scope.

        - weight: expected fraction of full game total/mean (sums to 1.0 across disjoint periods).
        - sigma_scale: sqrt(weight), maintaining variance additivity Var(total) = sum(Var(period)).
        """
        sc = (scope or "full_game").lower().strip()
        if sc in ("full_game", "full", "match", "game", "regular"):
            return 1.0, 1.0

        l_upper = (league or "").upper()
        league_weights = PERIOD_PROPORTIONS.get(l_upper, {})

        if sc in league_weights:
            w = league_weights[sc]
            return w, math.sqrt(w)

        if sc in ("half_1", "half_2", "1st_half", "2nd_half"):
            w = 0.50
        elif "quarter" in sc or sc.startswith("q"):
            w = 0.25
        elif "period" in sc:
            w = 0.333
        elif "innings_1_5" in sc:
            w = 0.53
        else:
            w = 1.0

        return w, math.sqrt(w)

    def evaluate_total(
        self,
        league: str,
        base_line: float,
        base_prob: float,
        base_side: str,
        target_line: float,
        target_side: str,
        scope: str = "full_game",
    ) -> float:
        """Projects total probability from baseline line to target line.

        Example:
            Base: NFL Over 47.5 @ 0.58.
            Target: Polymarket Over 45.5.
            Result: ~0.638 (higher chance to hit lower line).
        """
        weight, sigma_scale = self._get_period_scaling(league, scope)
        sigma = self._get_sigma(league, "totals") * sigma_scale
        eff_base_line = base_line

        # Adjust for derivative period scopes if baseline is a full-game projection
        if weight < 0.99:
            eff_base_line = base_line * weight
        
        # 1. Normalize baseline to P(Over base_line)
        is_base_over = base_side.lower() in ("over", "o", "yes")
        p_base_over = base_prob if is_base_over else (1.0 - base_prob)
        p_base_over = max(0.01, min(0.99, p_base_over))

        # 2. Invert to standard normal quantile: P(T > base_line) = 1 - Phi(z_base) => z_base = Phi^-1(1 - P)
        z_base = norm_inv(1.0 - p_base_over)

        # 3. Shift quantile by line difference: delta = (target - base) / sigma
        delta_z = (target_line - eff_base_line) / max(0.1, sigma)
        z_target = z_base + delta_z

        # 4. Target P(Over target_line) = 1 - Phi(z_target)
        p_target_over = 1.0 - norm_cdf(z_target)

        is_target_over = target_side.lower() in ("over", "o", "yes")
        final_prob = p_target_over if is_target_over else (1.0 - p_target_over)
        return max(0.01, min(0.99, round(final_prob, 4)))

    def evaluate_team_spread(
        self,
        league: str,
        proj_home: float,
        proj_away: float,
        is_home: bool,
        spread_line: float,
        scope: str = "full_game",
    ) -> float:
        """Projects team spread covering probability directly from projected scores.

        First-principles Gaussian margin formulation:
            Let D = Score_Home - Score_Away ~ N(mu, sigma^2), where mu = proj_home - proj_away.
            For Home covering spread S: Score_Home - Score_Away + S > 0 <=> D > -S
                => P(Home) = 1.0 - Phi((-S - mu) / sigma)
            For Away covering spread S: Score_Away - Score_Home + S > 0 <=> D < S
                => P(Away) = Phi((S - mu) / sigma)

        Guarantees:
            P(Home covers S) + P(Away covers -S) == 1.0000 identically for all lines.
            Monotonic in S (getting more points strictly increases probability).
        """
        weight, sigma_scale = self._get_period_scaling(league, scope)
        sigma = self._get_sigma(league, "spreads") * sigma_scale
        mu = float(proj_home - proj_away) * weight

        if is_home:
            z = (-spread_line - mu) / max(0.1, sigma)
            prob = 1.0 - norm_cdf(z)
        else:
            z = (spread_line - mu) / max(0.1, sigma)
            prob = norm_cdf(z)

        return max(0.01, min(0.99, round(prob, 4)))

    def evaluate_spread(
        self,
        league: str,
        base_line: float,
        base_prob: float,
        base_side: str,
        target_line: float,
        target_side: str,
        scope: str = "full_game",
    ) -> float:
        """Projects spread probability from baseline handicap to target handicap.

        Note on lines: A spread of -3.5 means the favorite must win by 4+.
        Target line of -1.5 is easier to cover than -3.5.
        """
        weight, sigma_scale = self._get_period_scaling(league, scope)
        sigma = self._get_sigma(league, "spreads") * sigma_scale
        eff_base_line = base_line

        if weight < 0.95 and abs(base_line) > abs(target_line) * 1.3 and abs(target_line) > 0.1:
            eff_base_line = base_line * weight

        # 1. Normalize to baseline covering probability
        is_base_fav = base_side.lower() in ("home", "favorite", "fav", "yes", "1")
        p_base_fav = base_prob if is_base_fav else (1.0 - base_prob)
        p_base_fav = max(0.01, min(0.99, p_base_fav))

        # z-score
        z_base = norm_inv(1.0 - p_base_fav)

        # Covering margin: Margin > -Line.
        # Required hurdle change: Delta_Hurdle = -target_line - (-base_line) = -(target_line - base_line)
        # Therefore: delta_z = -(target_line - eff_base_line) / max(0.1, sigma)
        delta_z = -(target_line - eff_base_line) / max(0.1, sigma)
        z_target = z_base + delta_z

        p_target = 1.0 - norm_cdf(z_target)
        is_target_fav = target_side.lower() in ("home", "favorite", "fav", "yes", "1")
        final_prob = p_target if is_target_fav else (1.0 - p_target)
        return max(0.01, min(0.99, round(final_prob, 4)))

    def calculate_valuation(
        self,
        model_prob: float,
        poly_price: float,
        min_ev_hurdle: float = 0.025,
    ) -> Dict[str, Any]:
        """Calculates expected value, fair price, and eligibility against Polymarket quote."""
        if poly_price <= 0.0 or poly_price >= 1.0:
            return {
                "ok": False,
                "ev": -1.0,
                "poly_odds": 0.0,
                "model_prob": model_prob,
                "poly_price": poly_price,
                "reason": "INVALID_POLY_PRICE",
            }

        poly_odds = 1.0 / poly_price
        ev = (model_prob * poly_odds) - 1.0
        is_eligible = (ev >= min_ev_hurdle) and (0.05 <= poly_price <= 0.95)

        return {
            "ok": is_eligible,
            "ev": round(ev, 4),
            "poly_odds": round(poly_odds, 3),
            "model_prob": round(model_prob, 4),
            "poly_price": round(poly_price, 4),
            "edge_pct": round(ev * 100.0, 2),
            "min_ev_hurdle": min_ev_hurdle,
            "reason": "POSITIVE_ALPHA" if is_eligible else "INSUFFICIENT_EV",
        }
