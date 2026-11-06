import math
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
except ImportError:
    # DiscreteDistribution is documented as pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel


ANCHOR_HOME = 0.80
ANCHOR_AWAY = 0.60
FIRST_HALF_SHARE = 0.45
TAIL_MASS = 1e-12

RESIDUAL_LOG_VAR = math.log(0.12 ** 2)
RESIDUAL_LOG_VAR_PRIOR_SD = 1.0
INTERCEPT_PRIOR_SD = 0.10

FEATURE_NAMES = (
    "expected",
    "matchup",
    "pace",
    "rest",
    "travel",
    "altitude",
    "heat",
    "wind",
)
PRIOR_BETA = np.array([0.85, 0.15, 0.30, 0.03, -0.02, -0.03, -0.02, -0.01])
PRIOR_BETA_SD = np.array([0.15, 0.10, 0.15, 0.03, 0.02, 0.03, 0.02, 0.01])
FEATURE_VAR = np.array([0.09, 0.04, 0.01, 0.25, 0.50, 0.10, 0.25, 0.50])


def _num(value: Any) -> Optional[float]:
    """Return a finite float, or None if the value is unusable."""
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


def _team_stat(ctx: Dict[str, Any], side: str, stat: str) -> Optional[float]:
    team = ctx.get(side)
    nested = team.get(stat) if isinstance(team, Mapping) else None
    return _positive(nested if nested is not None else ctx.get(f"{side}_{stat}"))


def _resolve_expected(ctx: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Return home and away expected goals on the first-half scale."""
    home = _positive(ctx.get("home_expected"))
    away = _positive(ctx.get("away_expected"))

    scope = ctx.get("expected_scope")
    if scope == "half_1":
        return home, away
    if scope == "full_game":
        is_full_game = True
    else:
        observed = sum(v for v in (home, away) if v is not None)
        anchor = (ANCHOR_HOME if home is not None else 0.0) + (
            ANCHOR_AWAY if away is not None else 0.0
        )
        # The log-space midpoint between the half and full-game anchors.
        is_full_game = anchor > 0.0 and observed / anchor > FIRST_HALF_SHARE ** -0.5

    if not is_full_game:
        return home, away
    return (
        home * FIRST_HALF_SHARE if home is not None else None,
        away * FIRST_HALF_SHARE if away is not None else None,
    )


def _features(ctx: Dict[str, Any]) -> np.ndarray:
    """Build the feature vector, using NaN for anything unobserved."""
    x = np.full(len(FEATURE_NAMES), np.nan)

    _, away_expected = _resolve_expected(ctx)
    if away_expected is not None:
        x[0] = math.log(away_expected / ANCHOR_AWAY)

    attack = _team_stat(ctx, "away", "attack")
    defense = _team_stat(ctx, "home", "defense")
    if attack is not None and defense is not None:
        x[1] = math.log(attack) + math.log(defense)

    pace = _positive(ctx.get("pace_index"))
    if pace is not None:
        x[2] = math.log(pace)

    home_rest = _num(ctx.get("home_rest_days"))
    away_rest = _num(ctx.get("away_rest_days"))
    if home_rest is not None and away_rest is not None and home_rest >= 0 and away_rest >= 0:
        x[3] = math.log((1.0 + away_rest) / (1.0 + home_rest))

    travel = _num(ctx.get("away_travel_km"))
    if travel is not None and travel >= 0:
        x[4] = math.log1p(travel / 1000.0)

    venue = ctx.get("venue")
    altitude = venue.get("altitude_m") if isinstance(venue, Mapping) else None
    altitude = _num(altitude if altitude is not None else ctx.get("altitude_m"))
    if altitude is not None:
        x[5] = altitude / 1000.0

    nested_weather = ctx.get("weather")
    weather = nested_weather if isinstance(nested_weather, Mapping) else ctx
    if weather.get("is_dome") is True:
        x[6] = 0.0
        x[7] = 0.0
    else:
        temp = _num(weather.get("temp"))
        if temp is not None:
            # Smooth heat exposure, which fades out at cool temperatures.
            x[6] = float(np.logaddexp(0.0, (temp - 80.0) / 10.0))
        wind = _num(weather.get("wind_speed"))
        if wind is not None and wind >= 0:
            x[7] = math.log1p(wind)

    return x


class MlsHalf1AwayTotalModel(BaseCandidateModel):
    """Shared-frailty negative multinomial model of first-half MLS scores."""

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_043432",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._beta = PRIOR_BETA.copy()
        self._intercept = 0.0
        self._log_var = RESIDUAL_LOG_VAR

    @staticmethod
    def _rate_params(
        x: np.ndarray, beta: np.ndarray, intercept: float, log_var: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Map features to the away mean and the Gamma frailty shape."""
        observed = np.isfinite(x)
        linear = np.sum(np.where(observed, beta * np.nan_to_num(x), 0.0), axis=-1)
        mu = ANCHOR_AWAY * np.exp(intercept + linear)
        var = math.exp(log_var) + np.sum(
            np.where(observed, 0.0, beta ** 2 * FEATURE_VAR), axis=-1
        )
        return mu, 1.0 / np.expm1(var)

    def fit_map(self, rows: Iterable[Tuple[Dict[str, Any], int]]) -> bool:
        """Fit a MAP estimate from (game_context, first_half_away_goals) rows.

        Gaussian priors provide the partial pooling, so estimates stay near the
        priors when data is thin. Returns True only if the optimizer converged;
        otherwise the current parameters are left unchanged.
        """
        rows = list(rows)
        if not rows:
            return False

        x = np.array([_features(ctx) for ctx, _ in rows])
        y = np.array([goals for _, goals in rows], dtype=float)
        start = np.concatenate((self._beta, [self._intercept, self._log_var]))

        def objective(theta: np.ndarray) -> float:
            beta, intercept, log_var = theta[:-2], theta[-2], theta[-1]
            mu, kappa = self._rate_params(x, beta, intercept, log_var)
            log_likelihood = np.sum(stats.nbinom.logpmf(y, kappa, kappa / (kappa + mu)))
            penalty = (
                np.sum(((beta - PRIOR_BETA) / PRIOR_BETA_SD) ** 2)
                + (intercept / INTERCEPT_PRIOR_SD) ** 2
                + ((log_var - RESIDUAL_LOG_VAR) / RESIDUAL_LOG_VAR_PRIOR_SD) ** 2
            )
            return float(-log_likelihood + 0.5 * penalty)

        result = optimize.minimize(objective, start, method="L-BFGS-B")
        if not result.success or not np.all(np.isfinite(result.x)):
            return False

        self._beta = result.x[:-2].copy()
        self._intercept = float(result.x[-2])
        self._log_var = float(result.x[-1])
        return True

    def _home_mean(self, ctx: Dict[str, Any]) -> float:
        """Home mean for the lattice. It does not affect the away marginal."""
        home_expected, _ = _resolve_expected(ctx)
        if home_expected is None:
            return ANCHOR_HOME
        return ANCHOR_HOME * (home_expected / ANCHOR_HOME) ** float(self._beta[0])

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        mu_away, kappa = self._rate_params(
            _features(game_context), self._beta, self._intercept, self._log_var
        )
        mu_away = float(mu_away)
        kappa = float(kappa)
        mu_home = self._home_mean(game_context)

        mu_total = mu_home + mu_away
        success = kappa / (kappa + mu_total)
        away_share = mu_away / mu_total
        max_total = int(stats.nbinom.ppf(1.0 - TAIL_MASS, kappa, success))

        scores: Dict[Tuple[int, int], float] = {}
        for total in range(max_total + 1):
            total_mass = float(stats.nbinom.pmf(total, kappa, success))
            away_goals = np.arange(total + 1)
            split = stats.binom.pmf(away_goals, total, away_share)
            for away, probability in zip(away_goals, split):
                scores[(total - int(away), int(away))] = total_mass * float(probability)

        mass = sum(scores.values())
        return DiscreteDistribution.from_scores(
            {score: probability / mass for score, probability in scores.items()}
        )