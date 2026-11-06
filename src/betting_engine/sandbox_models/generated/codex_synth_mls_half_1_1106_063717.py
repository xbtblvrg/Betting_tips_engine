import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

from scipy.stats import binom, nbinom

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    # The execution scope pre-loads DiscreteDistribution.
    pass


def _number(value: Any) -> Optional[float]:
    """Return value as a finite float, or None if it is not a usable number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, or None."""
    result = _number(value)
    return result if result is not None and result > 0.0 else None


class MLSHalf1AwayTotalCoxModel(BaseCandidateModel):
    """Gamma-frailty Cox count model for MLS first-half away goals."""

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_063717",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
        base_home: float = 0.80,
        base_away: float = 0.60,
        dispersion_shape: float = 10.0,
        signal_log_variance: float = 0.09,
        noise_log_variance: float = 0.03,
        exposure_coef: float = 0.08,
        pace_elasticity: float = 0.25,
        rest_coef: float = 0.06,
        rest_recovery_days: float = 3.0,
        travel_coef_per_1000km: float = -0.01,
        altitude_coef_per_1000m: float = -0.03,
        tail_tolerance: float = 1e-10,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.base_home = base_home
        self.base_away = base_away
        self.dispersion_shape = dispersion_shape
        self.input_reliability = signal_log_variance / (
            signal_log_variance + noise_log_variance
        )
        self.exposure_coef = exposure_coef
        self.pace_elasticity = pace_elasticity
        self.rest_coef = rest_coef
        self.rest_recovery_days = rest_recovery_days
        self.travel_coef_per_1000km = travel_coef_per_1000km
        self.altitude_coef_per_1000m = altitude_coef_per_1000m
        self.tail_tolerance = tail_tolerance

    @staticmethod
    def _rating(ctx: Mapping, side: str, key: str) -> Optional[float]:
        """Read a rating from ctx[side][key] or from ctx['<side>_<key>']."""
        nested = ctx.get(side)
        if isinstance(nested, Mapping) and key in nested:
            return _number(nested.get(key))
        return _number(ctx.get(f"{side}_{key}"))

    def _exposure(self, ctx: Mapping, attacker: str, defender: str) -> float:
        """Return a bounded attack-versus-defense log-rate adjustment.

        The difference is the same under 0-centered and 1-centered ratings,
        and tanh keeps the response smooth when the rating scale is unknown.
        """
        attack = self._rating(ctx, attacker, "attack")
        defense = self._rating(ctx, defender, "defense")
        if attack is None or defense is None:
            return 0.0
        return self.exposure_coef * math.tanh(attack - defense)

    def _pace(self, ctx: Mapping) -> float:
        pace = _positive(ctx.get("pace"))
        league_pace = _positive(ctx.get("league_pace"))
        if pace is None or league_pace is None:
            return 0.0
        return self.pace_elasticity * math.log(pace / league_pace)

    def _recovery(self, days: float) -> float:
        """Return fatigue recovery, which saturates exponentially with rest."""
        return 1.0 - math.exp(-max(days, 0.0) / self.rest_recovery_days)

    def _rest_edge(self, ctx: Mapping) -> float:
        """Return the away-minus-home recovery log-rate adjustment."""
        home_rest = _number(ctx.get("home_rest_days"))
        away_rest = _number(ctx.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        return self.rest_coef * (
            self._recovery(away_rest) - self._recovery(home_rest)
        )

    def _away_burden(self, ctx: Mapping) -> float:
        """Return the away-only log-rate adjustment for travel and altitude."""
        burden = 0.0
        travel_km = _number(ctx.get("away_travel_km"))
        if travel_km is not None:
            burden += self.travel_coef_per_1000km * max(travel_km, 0.0) / 1000.0

        venue = ctx.get("venue")
        altitude_m = None
        if isinstance(venue, Mapping):
            altitude_m = _number(venue.get("altitude_m"))
        if altitude_m is None:
            altitude_m = _number(ctx.get("altitude_m"))
        if altitude_m is not None:
            burden += self.altitude_coef_per_1000m * max(altitude_m, 0.0) / 1000.0
        return burden

    def _anchored_log_rate(self, expected: Any, base: float) -> float:
        """Shrink the input expectation toward the baseline in log space."""
        value = _positive(expected)
        if value is None:
            return math.log(base)
        return math.log(base) + self.input_reliability * math.log(value / base)

    def _rates(self, ctx: Mapping) -> Tuple[float, float]:
        """Return the home and away first-half mean goal rates."""
        pace = self._pace(ctx)
        rest_edge = self._rest_edge(ctx)

        log_home = (
            self._anchored_log_rate(ctx.get("home_expected"), self.base_home)
            + self._exposure(ctx, "home", "away")
            + pace
            - rest_edge
        )
        log_away = (
            self._anchored_log_rate(ctx.get("away_expected"), self.base_away)
            + self._exposure(ctx, "away", "home")
            + pace
            + rest_edge
            + self._away_burden(ctx)
        )
        return math.exp(log_home), math.exp(log_away)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, Mapping) else {}
        mu_home, mu_away = self._rates(ctx)

        mu_total = mu_home + mu_away
        away_share = mu_away / mu_total
        shape = self.dispersion_shape
        success_prob = shape / (shape + mu_total)

        # The lattice extends until only numerical tail mass remains. This is
        # truncation for computation, not a modeling cap on goals.
        max_total = int(
            nbinom.ppf(1.0 - self.tail_tolerance, shape, success_prob)
        )

        scores: Dict[Tuple[int, int], float] = {}
        for total in range(max_total + 1):
            total_prob = float(nbinom.pmf(total, shape, success_prob))
            for away_goals in range(total + 1):
                scores[(total - away_goals, away_goals)] = total_prob * float(
                    binom.pmf(away_goals, total, away_share)
                )

        mass = sum(scores.values())
        scores = {score: prob / mass for score, prob in scores.items()}
        return DiscreteDistribution.from_scores(scores)