"""Cold-start first-half score model for MLS|half_1|away_total.

Plan: combine the situational inputs into a posterior for each side's log
scoring rate, then integrate over the remaining uncertainty to produce a
first-half score matrix. Bookmaker odds are not used.

Status: this code has not been run or backtested. With 0 settled wagers, no
out-of-sample skill can be claimed, including against the +19.1% ROI benchmark.
Keep it in shadow or paper evaluation until it has settled results.

Mechanics:
- Each side's log rate combines a baseline prior, the supplied expected goals
  and attack/defense ratings, weighted by precision. Missing inputs drop out,
  which widens the posterior automatically.
- Conditional on the rates, goals are Poisson. Log-normal uncertainty on the
  rates preserves the mean and adds only about 1.5% dispersion to away goals
  when expected goals and ratings are present, consistent with the 1.000 prior.
- A shared tempo factor adds positive correlation between home and away goals.

The prompt asks for zero hardcoded constants, but no data exists to fit these
values. The PRIOR_*, *_VAR and *_EFFECT values are therefore judgmental domain
priors, not estimates, and should be refit once results settle. Only the
0.80/0.60 baselines come from the brief. No odds caps or probability clamps are
used.

Unverified integration assumptions (base.py was not available):
- BaseCandidateModel.__init__ accepts model_id, version, league, scope and
  family as keywords.
- DiscreteDistribution.from_scores accepts {(home_goals, away_goals): prob}.
- home_expected and away_expected are first-half rates, not full-game rates.
- Attack and defense are multipliers centered on 1.0, and a higher defense value
  means conceding MORE. An opposite convention would invert the defense signal.
- Temperature is in Fahrenheit and wind speed is in mph.
- Tempo is read from pace_factor or tempo_factor, a multiplier centered on 1.0.
"""

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # The brief says execution scope pre-loads this name.


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """Precision-weighted, mixed-Poisson first-half score model."""

    PRIOR_HOME_RATE = 0.80
    PRIOR_AWAY_RATE = 0.60

    # Variances on the log-rate scale.
    PRIOR_VAR = 0.25**2  # Between-match spread around the baseline.
    EXPECTED_VAR = 0.15**2  # Error in the supplied expected goals.
    RATING_VAR = 0.30**2  # Wide because ratings likely overlap with expected.
    SHARED_TEMPO_VAR = 0.10**2  # Match-wide tempo shock.

    # Situational effects on the log-rate scale.
    RATING_SATURATION = 0.50
    REST_EFFECT = 0.06
    REST_RECOVERY_DAYS = 3.0
    HEAT_EFFECT = 0.02
    HEAT_ONSET_F = 80.0
    HEAT_SCALE_F = 10.0
    WIND_EFFECT = 0.0015

    QUADRATURE_NODES = 9
    TAIL_MASS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1107_014500",
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
        nodes, weights = np.polynomial.hermite_e.hermegauss(self.QUADRATURE_NODES)
        self._nodes = nodes
        self._weights = weights / weights.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        home_mean, home_var, away_mean, away_var = self._rate_posteriors(game_context)
        return DiscreteDistribution.from_scores(
            self._score_matrix(home_mean, home_var, away_mean, away_var)
        )

    def _rate_posteriors(
        self, context: Dict[str, Any]
    ) -> Tuple[float, float, float, float]:
        home_attack, home_defense = self._ratings(context, "home")
        away_attack, away_defense = self._ratings(context, "away")

        tempo = self._tempo_offset(context)
        rest = self._rest_offset(context)
        home_prior = math.log(self.PRIOR_HOME_RATE) + tempo + rest
        away_prior = math.log(self.PRIOR_AWAY_RATE) + tempo - rest

        home_mean, home_var = self._posterior(
            home_prior,
            self._positive(context.get("home_expected")),
            self._rating_offset(home_attack, away_defense),
        )
        away_mean, away_var = self._posterior(
            away_prior,
            self._positive(context.get("away_expected")),
            self._rating_offset(away_attack, home_defense),
        )
        return home_mean, home_var, away_mean, away_var

    def _posterior(
        self,
        prior_mean: float,
        expected: Optional[float],
        rating_offset: Optional[float],
    ) -> Tuple[float, float]:
        """Combine the available log-rate estimates by precision."""
        estimates = [(prior_mean, 1.0 / self.PRIOR_VAR)]
        if expected is not None:
            estimates.append((math.log(expected), 1.0 / self.EXPECTED_VAR))
        if rating_offset is not None:
            estimates.append((prior_mean + rating_offset, 1.0 / self.RATING_VAR))

        precision = sum(weight for _, weight in estimates)
        mean = sum(value * weight for value, weight in estimates) / precision
        return mean, 1.0 / precision

    def _score_matrix(
        self, home_mean: float, home_var: float, away_mean: float, away_var: float
    ) -> Dict[Tuple[int, int], float]:
        """Integrate over shared tempo and each side's rate uncertainty."""
        largest_node = float(self._nodes.max())
        largest_log_rate = max(
            home_mean + largest_node * (math.sqrt(home_var) + math.sqrt(self.SHARED_TEMPO_VAR)),
            away_mean + largest_node * (math.sqrt(away_var) + math.sqrt(self.SHARED_TEMPO_VAR)),
        )
        max_goals = int(poisson.isf(self.TAIL_MASS, math.exp(largest_log_rate))) + 1
        goals = np.arange(max_goals + 1)

        matrix = np.zeros((max_goals + 1, max_goals + 1))
        for node, weight in zip(self._nodes, self._weights):
            shared_shock = math.sqrt(self.SHARED_TEMPO_VAR) * node
            home_pmf = self._mixed_pmf(home_mean, home_var, shared_shock, goals)
            away_pmf = self._mixed_pmf(away_mean, away_var, shared_shock, goals)
            matrix += weight * np.outer(home_pmf, away_pmf)

        matrix /= matrix.sum()
        return {
            (home_goals, away_goals): float(matrix[home_goals, away_goals])
            for home_goals in goals.tolist()
            for away_goals in goals.tolist()
        }

    def _mixed_pmf(
        self, mean: float, variance: float, shared_shock: float, goals: np.ndarray
    ) -> np.ndarray:
        """Return a Poisson pmf mixed over log-normal rate uncertainty."""
        # The -0.5 * total variance term keeps E[rate] equal to exp(mean).
        total_variance = variance + self.SHARED_TEMPO_VAR
        rates = np.exp(
            mean - 0.5 * total_variance + math.sqrt(variance) * self._nodes + shared_shock
        )
        return self._weights @ poisson.pmf(goals[None, :], rates[:, None])

    def _ratings(
        self, context: Dict[str, Any], side: str
    ) -> Tuple[Optional[float], Optional[float]]:
        nested = context.get(side)
        nested = nested if isinstance(nested, Mapping) else {}
        attack = nested.get("attack", context.get(f"{side}_attack"))
        defense = nested.get("defense", context.get(f"{side}_defense"))
        return self._positive(attack), self._positive(defense)

    def _rating_offset(
        self, attack: Optional[float], opponent_defense: Optional[float]
    ) -> Optional[float]:
        """Return a smoothly saturated log multiplier, or None without ratings."""
        ratings = [value for value in (attack, opponent_defense) if value is not None]
        if not ratings:
            return None
        return sum(self._saturate(math.log(value)) for value in ratings)

    def _rest_offset(self, context: Dict[str, Any]) -> float:
        """Return the home-side rest edge, with diminishing returns per extra day."""
        home_rest = self._non_negative(context.get("home_rest_days"))
        away_rest = self._non_negative(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        # Split the edge so each side moves by half of the differential.
        return 0.5 * self.REST_EFFECT * (
            math.exp(-away_rest / self.REST_RECOVERY_DAYS)
            - math.exp(-home_rest / self.REST_RECOVERY_DAYS)
        )

    def _tempo_offset(self, context: Dict[str, Any]) -> float:
        """Return the match-wide log-rate shift from pace, heat and wind."""
        offset = 0.0

        pace = self._positive(context.get("pace_factor", context.get("tempo_factor")))
        if pace is not None:
            offset += self._saturate(math.log(pace))

        weather = context.get("weather")
        weather = weather if isinstance(weather, Mapping) else {}
        if bool(weather.get("is_dome", context.get("is_dome", False))):
            return offset

        temperature = self._finite(weather.get("temp", context.get("temp")))
        if temperature is not None:
            excess_heat = (temperature - self.HEAT_ONSET_F) / self.HEAT_SCALE_F
            offset -= self.HEAT_EFFECT * float(np.logaddexp(0.0, excess_heat))

        wind = self._non_negative(weather.get("wind_speed", context.get("wind_speed")))
        if wind is not None:
            offset -= self.WIND_EFFECT * wind

        return offset

    def _saturate(self, log_multiplier: float) -> float:
        """Limit extreme inputs smoothly without introducing a hard clamp."""
        scale = self.RATING_SATURATION
        return scale * math.tanh(log_multiplier / scale)

    @staticmethod
    def _finite(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._finite(value)
        return number if number is not None and number > 0.0 else None

    @classmethod
    def _non_negative(cls, value: Any) -> Optional[float]:
        number = cls._finite(value)
        return number if number is not None and number >= 0.0 else None