import math
from typing import Any, Dict, Optional, Tuple

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
except ImportError:
    # The brief says DiscreteDistribution is pre-loaded in execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel

PRIORS: Dict[str, float] = {
    "baseline_home": 112.0,        # Given by the brief.
    "baseline_away": 108.0,        # Given by the brief.
    "baseline_possessions": 98.0,  # Regulation possessions per team.
    "possessions_var": 16.0,       # Var(N): game-level pace SD of about 4.
    "pace_share": 0.5,             # Share of a total's deviation attributed to pace if pace is absent.
    "value_1": 0.09,               # Value mix of scoring possessions: P(1, 2, 3, 4 points).
    "value_2": 0.56,
    "value_3": 0.33,
    "value_4": 0.02,
    "state_feedback": 0.75,        # OU mean-reversion rate over one regulation game.
    "matchup_prior_var": 42.25,    # Var of true margin between matchups (SD 6.5).
    "input_error_var": 6.25,       # Var of the error in the input margin (SD 2.5).
    "rest_edge_points": 2.5,       # Margin swing from no rest to full recovery.
    "rest_timescale_days": 1.0,    # Recovery timescale.
    "baseline_draw_rate": 0.056,   # Recalled OT rate; sets the draw intercept.
    "draw_density_slope": 1.0,     # beta: draw odds proportional to the margin density at zero.
    "draw_tempo_slope": 0.0,       # gamma: no evidence yet beyond the tempo effect through sigma.
}


def _num(value: Any) -> Optional[float]:
    """Return a finite float, or None for missing or non-numeric input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> Optional[float]:
    number = _num(value)
    return number if number is not None and number > 0.0 else None


def _rating(game_context: Dict[str, Any], side: str, name: str) -> float:
    """Read a rating from nested ('home': {'attack': x}) or flat ('home_attack') keys."""
    nested = game_context.get(side)
    value = nested.get(name) if isinstance(nested, dict) else None
    if value is None:
        value = game_context.get(f"{side}_{name}")
    rating = _positive(value)
    return rating if rating is not None else 1.0


class NBAFullGameThreeWayModel(BaseCandidateModel):
    """Regulation-time home/draw/away model with a separately priced draw."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_005011",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._p = {**PRIORS, **(priors or {})}
        p = self._p

        weights = {1: p["value_1"], 2: p["value_2"], 3: p["value_3"], 4: p["value_4"]}
        first_moment = sum(v * w for v, w in weights.items())
        second_moment = sum(v * v * w for v, w in weights.items())
        # With P(score) = PPP / E[V | score], the possession variance is PPP * (D - PPP).
        self._dispersion = second_moment / first_moment

        self._rv_factor = self._ou_variance_factor(p["state_feedback"])

        prior_var = p["matchup_prior_var"]
        error_var = p["input_error_var"]
        self._shrink = prior_var / (prior_var + error_var)
        self._posterior_var = prior_var * error_var / (prior_var + error_var)

        # Set the intercept so the baseline game reproduces the baseline draw rate.
        mu, sigma = self._margin_moments(
            p["baseline_home"], p["baseline_away"], p["baseline_possessions"], 0.0
        )
        rate = p["baseline_draw_rate"]
        self._draw_intercept = math.log(rate / (1.0 - rate)) - p[
            "draw_density_slope"
        ] * self._log_density_at_zero(mu, sigma)

    @staticmethod
    def _ou_variance_factor(kappa: float) -> float:
        """Var(OU at T=1) / Var(random walk at T=1), which tends to 1 as kappa tends to 0."""
        return -math.expm1(-2.0 * kappa) / (2.0 * kappa) if kappa > 0.0 else 1.0

    @staticmethod
    def _log_density_at_zero(mu: float, sigma: float) -> float:
        z = mu / sigma
        return -0.5 * z * z - 0.5 * math.log(2.0 * math.pi) - math.log(sigma)

    def _possession_variance(self, ppp: float) -> float:
        variance = ppp * (self._dispersion - ppp)
        if variance <= 0.0:
            raise ValueError(f"Implausible points per possession: {ppp:.3f}")
        return variance

    def _margin_moments(
        self, home_mean: float, away_mean: float, possessions: float, rest_edge: float
    ) -> Tuple[float, float]:
        p = self._p
        prior_margin = p["baseline_home"] - p["baseline_away"]
        raw_margin = home_mean - away_mean + rest_edge
        mu = prior_margin + self._shrink * (raw_margin - prior_margin)

        ppp_home = home_mean / possessions
        ppp_away = away_mean / possessions
        scoring_var = possessions * (
            self._possession_variance(ppp_home) + self._possession_variance(ppp_away)
        )
        pace_var = (ppp_home - ppp_away) ** 2 * p["possessions_var"]
        variance = self._rv_factor * scoring_var + pace_var + self._posterior_var
        return mu, math.sqrt(variance)

    def _side_mean(self, game_context: Dict[str, Any], side: str, opponent: str) -> float:
        expected = _positive(game_context.get(f"{side}_expected"))
        if expected is not None:
            return expected
        return (
            self._p[f"baseline_{side}"]
            * _rating(game_context, side, "attack")
            * _rating(game_context, opponent, "defense")
        )

    def _possessions(self, game_context: Dict[str, Any], total: float) -> float:
        p = self._p
        home_pace = _positive(game_context.get("home_pace"))
        away_pace = _positive(game_context.get("away_pace"))
        if home_pace is not None and away_pace is not None:
            return 0.5 * (home_pace + away_pace)
        pace = _positive(game_context.get("pace"))
        if pace is not None:
            return pace
        baseline_total = p["baseline_home"] + p["baseline_away"]
        return p["baseline_possessions"] * (total / baseline_total) ** p["pace_share"]

    def _rest_edge(self, game_context: Dict[str, Any]) -> float:
        home_rest = _num(game_context.get("home_rest_days"))
        away_rest = _num(game_context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        timescale = self._p["rest_timescale_days"]
        home_recovery = -math.expm1(-max(home_rest, 0.0) / timescale)
        away_recovery = -math.expm1(-max(away_rest, 0.0) / timescale)
        return self._p["rest_edge_points"] * (home_recovery - away_recovery)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        p = self._p
        home_mean = self._side_mean(game_context, "home", "away")
        away_mean = self._side_mean(game_context, "away", "home")
        possessions = self._possessions(game_context, home_mean + away_mean)

        mu, sigma = self._margin_moments(
            home_mean, away_mean, possessions, self._rest_edge(game_context)
        )

        tempo = math.log(possessions / p["baseline_possessions"])
        log_odds = (
            self._draw_intercept
            + p["draw_density_slope"] * self._log_density_at_zero(mu, sigma)
            + p["draw_tempo_slope"] * tempo
        )
        p_draw = math.exp(log_odds) / (1.0 + math.exp(log_odds))

        # Phi(mu / sigma), computed with erfc to preserve tail accuracy.
        home_share = 0.5 * math.erfc(-mu / (sigma * math.sqrt(2.0)))
        p_home = (1.0 - p_draw) * home_share
        p_away = (1.0 - p_draw) * (1.0 - home_share)

        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )