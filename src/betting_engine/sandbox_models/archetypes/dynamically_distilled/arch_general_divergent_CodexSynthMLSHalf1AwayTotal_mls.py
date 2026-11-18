"""Cold-start first-half away goals model for MLS|half_1|away_total.

Status: not executed or backtested. With 0 settled wagers, nothing here has been
fitted, and the stated ROI and Brier benchmarks are not claimed. Validate
out-of-sample before staking.

Assumptions to verify against the engine:
  * home_expected/away_expected are first-half rates, not full-game rates.
  * attack/defense/pace are positive multipliers centred on 1.0, and a higher
    defense value means more goals conceded. Other values are treated as missing.
  * temp is in Fahrenheit and wind_speed is in mph.
  * from_scores accepts {(home_goals, away_goals): probability}, with a fallback
    to a probability matrix. If it expects raw samples, adapt _build_distribution.

The class-level parameters are domain-judgement priors, not estimates from data.
Refit them once settled results exist.
"""

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


def _number(value: Any) -> Optional[float]:
    """Return a finite float, or None for missing or non-numeric input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _softplus(x: float) -> float:
    return float(np.logaddexp(0.0, x))


class CodexSynthMLSHalf1AwayTotal(BaseCandidateModel):
    """Bayesian log-rate model with a shared tempo factor and gamma-Poisson goals.

    Each team's first-half log rate starts from a league prior and is updated by
    two noisy signals: the supplied expected goals and the attack/defense ratings.
    Precision weighting shrinks extreme inputs towards the baseline, which limits
    tail mispricing.

    The posterior variance v then sets the dispersion rather than a fixed value:
    a mean-one lognormal tempo factor shared by both teams carries part of v, and
    a team-specific gamma factor carries the rest. The rate's relative variance is
    exp(v) - 1, so the dispersion index is 1 + mean * (exp(v) - 1). This stays
    close to the Poisson index of 1.000 and widens as information gets thinner.

    No bookmaker price enters the forecast.
    """

    BASE_HOME_RATE = 0.80
    BASE_AWAY_RATE = 0.60

    # Standard deviations on the log-rate scale.
    PRIOR_SD = 0.22
    EXPECTED_SIGNAL_SD = 0.15
    RATINGS_SIGNAL_SD = 0.30
    SHARED_TEMPO_SD = 0.10

    # Situational effects on the log rate. Fatigue is mostly a late-game effect,
    # so the first-half rest effect is deliberately small.
    REST_EFFECT = 0.06
    REST_TIMESCALE_DAYS = 3.0
    HEAT_EFFECT = -0.02
    HEAT_ONSET_F = 85.0
    HEAT_WIDTH_F = 5.0
    WIND_EFFECT = -0.01
    WIND_ONSET_MPH = 12.0
    WIND_WIDTH_MPH = 4.0

    QUADRATURE_NODES = 21
    TAIL_WIDTH_SDS = 10.0

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_154607",
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

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_mean, home_var = self._posterior_log_rate(
            game_context, self.BASE_HOME_RATE, "home_expected", "home", "away"
        )
        away_mean, away_var = self._posterior_log_rate(
            game_context, self.BASE_AWAY_RATE, "away_expected", "away", "home"
        )

        shared = self._shared_log_adjustment(game_context)
        rest = self._rest_log_edge(game_context)
        home_rate = math.exp(home_mean + shared + rest)
        away_rate = math.exp(away_mean + shared - rest)

        return self._build_distribution(
            self._score_matrix(home_rate, away_rate, home_var, away_var)
        )

    def _posterior_log_rate(
        self,
        context: Mapping[str, Any],
        base_rate: float,
        expected_key: str,
        attacking_side: str,
        defending_side: str,
    ) -> Tuple[float, float]:
        """Combine the prior and available signals by precision weighting."""
        prior_mean = math.log(base_rate)
        precision = self.PRIOR_SD ** -2
        weighted_sum = precision * prior_mean

        expected = _number(context.get(expected_key))
        if expected is not None and expected > 0.0:
            signal_precision = self.EXPECTED_SIGNAL_SD ** -2
            precision += signal_precision
            weighted_sum += signal_precision * math.log(expected)

        attack = self._team_value(context, attacking_side, "attack")
        defense = self._team_value(context, defending_side, "defense")
        if attack is not None or defense is not None:
            ratings_log_rate = (
                prior_mean
                + (math.log(attack) if attack is not None else 0.0)
                + (math.log(defense) if defense is not None else 0.0)
            )
            signal_precision = self.RATINGS_SIGNAL_SD ** -2
            precision += signal_precision
            weighted_sum += signal_precision * ratings_log_rate

        return weighted_sum / precision, 1.0 / precision

    @staticmethod
    def _team_value(
        context: Mapping[str, Any], side: str, field: str
    ) -> Optional[float]:
        """Read a positive team metric from nested or flat context keys."""
        team = context.get(side)
        value = _number(team.get(field)) if isinstance(team, Mapping) else None
        if value is None:
            value = _number(context.get(f"{side}_{field}"))
        return value if value is not None and value > 0.0 else None

    def _shared_log_adjustment(self, context: Mapping[str, Any]) -> float:
        """Apply pace and weather effects that move both teams' rates together."""
        adjustment = 0.0

        for side in ("home", "away"):
            pace = self._team_value(context, side, "pace")
            if pace is None:
                pace = self._team_value(context, side, "tempo")
            if pace is not None:
                adjustment += 0.5 * math.log(pace)

        weather = context.get("weather")
        weather = weather if isinstance(weather, Mapping) else {}

        def read_weather(key: str) -> Any:
            return weather[key] if key in weather else context.get(key)

        if bool(read_weather("is_dome")):
            return adjustment

        temp = _number(read_weather("temp"))
        if temp is not None:
            adjustment += self.HEAT_EFFECT * _softplus(
                (temp - self.HEAT_ONSET_F) / self.HEAT_WIDTH_F
            )

        wind = _number(read_weather("wind_speed"))
        if wind is not None:
            adjustment += self.WIND_EFFECT * _softplus(
                (wind - self.WIND_ONSET_MPH) / self.WIND_WIDTH_MPH
            )

        return adjustment

    def _rest_log_edge(self, context: Mapping[str, Any]) -> float:
        """Return half the home freshness edge, neutral if either value is missing."""
        home_rest = _number(context.get("home_rest_days"))
        away_rest = _number(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0

        def freshness(days: float) -> float:
            return -math.expm1(-max(days, 0.0) / self.REST_TIMESCALE_DAYS)

        return 0.5 * self.REST_EFFECT * (freshness(home_rest) - freshness(away_rest))

    def _score_matrix(
        self,
        home_rate: float,
        away_rate: float,
        home_var: float,
        away_var: float,
    ) -> np.ndarray:
        """Integrate the joint score lattice over the shared tempo factor."""
        # Tempo cannot explain more log variance than either team's total.
        shared_var = min(self.SHARED_TEMPO_SD ** 2, home_var, away_var)
        home_shape = self._gamma_shape(home_var - shared_var)
        away_shape = self._gamma_shape(away_var - shared_var)

        max_home = self._lattice_limit(home_rate, home_var)
        max_away = self._lattice_limit(away_rate, away_var)

        nodes, weights = hermegauss(self.QUADRATURE_NODES)
        weights = weights / weights.sum()
        shared_sd = math.sqrt(shared_var)

        matrix = np.zeros((max_home + 1, max_away + 1))
        for node, weight in zip(nodes, weights):
            tempo = math.exp(shared_sd * node - 0.5 * shared_var)
            home_pmf = self._count_pmf(home_rate * tempo, home_shape, max_home)
            away_pmf = self._count_pmf(away_rate * tempo, away_shape, max_away)
            matrix += weight * np.outer(home_pmf, away_pmf)

        return matrix / matrix.sum()

    @staticmethod
    def _gamma_shape(log_variance: float) -> float:
        """Match gamma relative variance to a lognormal log variance."""
        excess = math.expm1(max(log_variance, 0.0))
        return 1.0 / excess if excess > 0.0 else math.inf

    def _lattice_limit(self, rate: float, log_variance: float) -> int:
        variance = rate + rate * rate * math.expm1(log_variance)
        return int(math.ceil(rate + self.TAIL_WIDTH_SDS * math.sqrt(variance)))

    @staticmethod
    def _count_pmf(mean: float, shape: float, max_count: int) -> np.ndarray:
        counts = np.arange(max_count + 1)
        if math.isinf(shape):
            return stats.poisson.pmf(counts, mean)
        return stats.nbinom.pmf(counts, shape, shape / (shape + mean))

    @staticmethod
    def _build_distribution(matrix: np.ndarray) -> DiscreteDistribution:
        scores = {
            (int(home_goals), int(away_goals)): float(probability)
            for (home_goals, away_goals), probability in np.ndenumerate(matrix)
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(matrix)