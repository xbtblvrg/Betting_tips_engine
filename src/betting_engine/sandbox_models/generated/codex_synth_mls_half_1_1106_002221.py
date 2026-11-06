import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # The sandbox pre-loads DiscreteDistribution into execution scope.


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """Bivariate first-half goal model with a shared Gamma rate frailty.

    Given frailty G with mean 1 and variance phi:
        N_H | G ~ Poisson(G * lambda_H)
        N_A | G ~ Poisson(G * lambda_A)

    This implies:
        N_H + N_A ~ NegBin(mean=lambda_H + lambda_A, r=1/phi)
        N_A | N_H + N_A = t ~ Binomial(t, lambda_A / (lambda_H + lambda_A))
        Marginal N_A ~ NegBin(mean=lambda_A, r=1/phi)
        P(N_A = 0) = (1 + phi * lambda_A) ** (-1 / phi)
        Var(N_A) / E(N_A) = 1 + phi * lambda_A

    The observed dispersion index of 1.000 gives phi = 0, the independent Poisson limit.
    Use estimate_phi once settled outcomes exist.

    Elasticities on signals that may already inform *_expected are deliberately small
    to limit double counting. Missing or invalid signals contribute no adjustment.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_002221",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
        home_anchor: float = 0.80,
        away_anchor: float = 0.60,
        phi: float = 0.0,
        expected_elasticity: float = 0.85,
        matchup_elasticity: float = 0.10,
        pace_elasticity: float = 0.50,
        rest_effect: float = 0.04,
        rest_recovery_days: float = 3.0,
        altitude_effect_per_km: float = 0.03,
        tail_mass: float = 1e-12,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if home_anchor <= 0.0 or away_anchor <= 0.0:
            raise ValueError("Anchors must be positive.")
        if phi < 0.0:
            raise ValueError("phi must be non-negative.")
        if rest_recovery_days <= 0.0:
            raise ValueError("rest_recovery_days must be positive.")
        if not 0.0 < tail_mass < 1.0:
            raise ValueError("tail_mass must lie in (0, 1).")

        self.home_anchor = home_anchor
        self.away_anchor = away_anchor
        self.phi = phi
        self.expected_elasticity = expected_elasticity
        self.matchup_elasticity = matchup_elasticity
        self.pace_elasticity = pace_elasticity
        self.rest_effect = rest_effect
        self.rest_recovery_days = rest_recovery_days
        self.altitude_effect_per_km = altitude_effect_per_km
        self.tail_mass = tail_mass

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        lambda_home, lambda_away = self._team_rates(game_context)
        return DiscreteDistribution.from_scores(self._score_lattice(lambda_home, lambda_away))

    @staticmethod
    def estimate_phi(counts: Sequence[int], means: Sequence[float]) -> float:
        """Return the marginal-likelihood estimate of phi, or 0 if Poisson fits as well."""
        y = np.asarray(counts, dtype=float)
        mu = np.asarray(means, dtype=float)
        if y.size == 0 or y.size != mu.size or np.any(y < 0.0) or np.any(mu <= 0.0):
            raise ValueError("Need matching non-empty counts and positive means.")

        def negative_log_likelihood(log_phi: float) -> float:
            phi = math.exp(log_phi)
            return -float(np.sum(stats.nbinom.logpmf(y, 1.0 / phi, 1.0 / (1.0 + phi * mu))))

        # These bounds limit the numerical search only. They do not constrain forecasts.
        result = optimize.minimize_scalar(
            negative_log_likelihood, bounds=(-12.0, 3.0), method="bounded"
        )
        poisson_nll = -float(np.sum(stats.poisson.logpmf(y, mu)))
        return math.exp(float(result.x)) if float(result.fun) < poisson_nll else 0.0

    def _team_rates(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home_expected = self._positive(context.get("home_expected"))
        away_expected = self._positive(context.get("away_expected"))

        log_home = math.log(self.home_anchor) + self.expected_elasticity * self._log_ratio(
            home_expected, self.home_anchor
        )
        log_away = math.log(self.away_anchor) + self.expected_elasticity * self._log_ratio(
            away_expected, self.away_anchor
        )

        league_attack = self._positive(context.get("league_attack")) or 1.0
        league_defense = self._positive(context.get("league_defense")) or 1.0
        log_home += self.matchup_elasticity * (
            self._log_ratio(self._team_metric(context, "home", "attack"), league_attack)
            + self._log_ratio(self._team_metric(context, "away", "defense"), league_defense)
        )
        log_away += self.matchup_elasticity * (
            self._log_ratio(self._team_metric(context, "away", "attack"), league_attack)
            + self._log_ratio(self._team_metric(context, "home", "defense"), league_defense)
        )

        pace = self._positive(context.get("pace", context.get("tempo")))
        pace_term = self.pace_elasticity * self._log_ratio(
            pace, self._positive(context.get("league_pace"))
        )
        log_home += pace_term
        log_away += pace_term

        rest_term = self._rest_term(context)
        log_home -= rest_term
        log_away += rest_term

        log_away -= self.altitude_effect_per_km * self._altitude_km(context)
        return math.exp(log_home), math.exp(log_away)

    def _score_lattice(
        self, lambda_home: float, lambda_away: float
    ) -> Dict[Tuple[int, int], float]:
        total_rate = lambda_home + lambda_away
        if self.phi > 0.0:
            total_goals = stats.nbinom(1.0 / self.phi, 1.0 / (1.0 + self.phi * total_rate))
        else:
            total_goals = stats.poisson(total_rate)

        # The lattice grows with the rate, so tail mass is not lost to a fixed goal cap.
        max_total = int(total_goals.isf(self.tail_mass)) + 1
        away_share = lambda_away / total_rate

        scores: Dict[Tuple[int, int], float] = {}
        for total in range(max_total + 1):
            total_probability = float(total_goals.pmf(total))
            for away_goals in range(total + 1):
                scores[(total - away_goals, away_goals)] = total_probability * float(
                    stats.binom.pmf(away_goals, total, away_share)
                )

        mass = sum(scores.values())
        return {score: probability / mass for score, probability in scores.items()}

    def _rest_term(self, context: Mapping[str, Any]) -> float:
        """Return the away log-rate effect, using saturating recovery from fatigue."""
        home_rest = self._non_negative(context.get("home_rest_days"))
        away_rest = self._non_negative(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        home_fatigue = math.exp(-home_rest / self.rest_recovery_days)
        away_fatigue = math.exp(-away_rest / self.rest_recovery_days)
        return self.rest_effect * (home_fatigue - away_fatigue)

    def _altitude_km(self, context: Mapping[str, Any]) -> float:
        venue = context.get("venue")
        altitude = venue.get("altitude_m") if isinstance(venue, Mapping) else None
        if altitude is None:
            altitude = context.get("altitude_m")
        altitude_m = self._non_negative(altitude)
        return altitude_m / 1000.0 if altitude_m is not None else 0.0

    def _team_metric(
        self, context: Mapping[str, Any], side: str, metric: str
    ) -> Optional[float]:
        team = context.get(side)
        value = team.get(metric) if isinstance(team, Mapping) else None
        if value is None:
            value = context.get(f"{side}_{metric}")
        return self._positive(value)

    @staticmethod
    def _log_ratio(value: Optional[float], reference: Optional[float]) -> float:
        if value is None or reference is None:
            return 0.0
        return math.log(value / reference)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number > 0.0 else None

    @classmethod
    def _non_negative(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number >= 0.0 else None