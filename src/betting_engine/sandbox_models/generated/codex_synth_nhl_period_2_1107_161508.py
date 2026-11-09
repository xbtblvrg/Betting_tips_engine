from collections.abc import Mapping
import math
from typing import Any, Dict, Optional

import numpy as np
from scipy.special import gammaln, logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NHLPeriod2ExactTotalModel(BaseCandidateModel):
    """
    Independent, pre-period second-period scoring model.

    Cold-start assumptions
    ----------------------
    Home and away second-period mean goals are 1.02 and 0.92. With no
    observed dispersion evidence, goals follow independent Poisson counting
    processes. These assumptions define a prior, not demonstrated betting
    profitability or out-of-sample calibration.

    Optional shared exposure uncertainty
    ------------------------------------
    Conditional on a shared exposure Z:
        H | Z ~ Poisson(home_mean * Z)
        A | Z ~ Poisson(away_mean * Z)

    For E[Z] = 1 and Var[Z] = v, total goals have
        E[N] = mu
        Var[N] = mu + v * mu**2.

    Gamma exposure therefore gives a negative-binomial total, with
    shape = 1 / v. At v = 0 the model is exactly Poisson. Conditional on
    N, H is binomial with success probability home_mean / mu.

    Context contract
    ----------------
    * home_expected / away_expected: second-period means, never full-game
      means. Supplied means supersede attack/defense construction.
    * home / away: optional mappings containing dimensionless 'attack' and
      'defense' multipliers. Defense means goals-conceded exposure: larger
      values increase the opponent's expected goals.
    * pace_multiplier: positive relative second-period exposure. Applied
      once to both means; supplied expected means must exclude this adjustment.
    * home_rest_days / away_rest_days: optional nonnegative rest days.
      Rest adjustments require an externally estimated coefficient and an
      explicit reference rest level. The cold-start rest effect is zero.
    * shared_exposure_variance: nonnegative variance v; alternatively,
      total_dispersion_index: total-goals variance / mean, at least one.
      Supply at most one. Neither is inferred from betting prices.

    All inputs must be available before period two begins. This is not a
    remaining-time live model. Weather and venue have no invented effects.

    The returned score mapping is {(home_goals, away_goals): probability}.
    Its exact-total marginal is obtained by summing home_goals + away_goals.
    Support truncation is governed only by numerical tail accuracy.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_2_1107_161508",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_2",
        family: str = "exact_total",
        *,
        home_baseline: float = 1.02,
        away_baseline: float = 0.92,
        rest_log_coefficient: float = 0.0,
        rest_reference_days: Optional[float] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        if (league, scope, family) != ("NHL", "period_2", "exact_total"):
            raise ValueError("This model supports only NHL|period_2|exact_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_baseline = self._nonnegative("home_baseline", home_baseline)
        self.away_baseline = self._nonnegative("away_baseline", away_baseline)
        self.rest_log_coefficient = self._finite(
            "rest_log_coefficient", rest_log_coefficient
        )
        self.rest_reference_days = (
            None
            if rest_reference_days is None
            else self._nonnegative("rest_reference_days", rest_reference_days)
        )

        if self.rest_log_coefficient != 0 and self.rest_reference_days is None:
            raise ValueError(
                "An estimated rest effect requires rest_reference_days."
            )

        # Numerical precision policy, not an empirical or betting threshold.
        if tail_tolerance is None:
            tail_tolerance = float(np.finfo(float).eps ** 0.75)
        self.tail_tolerance = self._finite("tail_tolerance", tail_tolerance)
        if not np.finfo(float).eps <= self.tail_tolerance < 1:
            raise ValueError(
                "tail_tolerance must be at least machine epsilon and below one."
            )

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        # Optional operational memory budget; exceeding it raises, never clips.
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(name: str, value: Any) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, name: str, value: Any) -> float:
        result = cls._finite(name, value)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, name: str, value: Any) -> float:
        result = cls._finite(name, value)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _team(context: Mapping, side: str) -> Mapping:
        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise ValueError(f"{side} must be a mapping.")
        return team

    def _mean(self, context: Mapping, side: str, pace: float) -> float:
        expected_key = f"{side}_expected"
        baseline = self.home_baseline if side == "home" else self.away_baseline

        if expected_key in context:
            mean = self._nonnegative(expected_key, context[expected_key])
            log_mean = -math.inf if mean == 0 else math.log(mean)
        else:
            opponent = "away" if side == "home" else "home"
            attack = self._positive(
                f"{side}.attack", self._team(context, side).get("attack", 1.0)
            )
            defense = self._positive(
                f"{opponent}.defense",
                self._team(context, opponent).get("defense", 1.0),
            )
            log_mean = (
                -math.inf
                if baseline == 0
                else math.log(baseline) + math.log(attack) + math.log(defense)
            )

        rest_key = f"{side}_rest_days"
        rest = (
            self._nonnegative(rest_key, context[rest_key])
            if rest_key in context
            else None
        )
        if self.rest_log_coefficient != 0:
            if rest is None:
                raise ValueError(f"{rest_key} is required for the rest adjustment.")
            log_mean += self.rest_log_coefficient * (
                math.log1p(rest) - math.log1p(self.rest_reference_days)
            )

        log_mean += math.log(pace)
        if log_mean == -math.inf:
            return 0.0
        if not math.isfinite(log_mean):
            raise ValueError(f"{side} adjusted log-mean is not finite.")
        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError(f"{side} adjusted mean exceeds numeric range.") from exc
        if not math.isfinite(mean) or mean == 0:
            raise ValueError(f"{side} adjusted mean exceeds numeric range.")
        return mean

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "NHL"),
            ("scope", "period_2"),
            ("family", "exact_total"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"{key} must be {expected!r}.")

        pace = self._positive(
            "pace_multiplier", game_context.get("pace_multiplier", 1.0)
        )
        home_mean = self._mean(game_context, "home", pace)
        away_mean = self._mean(game_context, "away", pace)
        mu = home_mean + away_mean
        if not math.isfinite(mu):
            raise ValueError("Total expected goals exceeds numeric range.")

        has_variance = "shared_exposure_variance" in game_context
        has_index = "total_dispersion_index" in game_context
        if has_variance and has_index:
            raise ValueError(
                "Supply shared_exposure_variance or total_dispersion_index, "
                "not both."
            )

        variance = self._nonnegative(
            "shared_exposure_variance",
            game_context.get("shared_exposure_variance", 0.0),
        )
        dispersion = self._finite(
            "total_dispersion_index",
            game_context.get("total_dispersion_index", 1.0),
        )
        if dispersion < 1:
            raise ValueError(
                "Underdispersion requires a separately justified counting model."
            )

        if mu == 0:
            if has_index and dispersion != 1:
                raise ValueError("A zero mean cannot have nonzero count variance.")
            return DiscreteDistribution.from_scores({(0, 0): 1.0})

        if has_index:
            variance = (dispersion - 1.0) / mu
        if not math.isfinite(variance):
            raise ValueError("Implied exposure variance exceeds numeric range.")

        if variance == 0:
            total_law = poisson(mu=mu)
        else:
            shape = 1.0 / variance
            excess = variance * mu
            if not math.isfinite(shape) or not math.isfinite(excess):
                raise ValueError("Exposure parameters exceed numeric range.")
            success = 1.0 / (1.0 + excess)
            if shape <= 0 or not 0 < success < 1:
                raise ValueError(
                    "Exposure parameters cannot be represented accurately."
                )
            total_law = nbinom(n=shape, p=success)

        upper = float(total_law.isf(self.tail_tolerance))
        if not math.isfinite(upper) or upper < 0:
            raise ArithmeticError("Could not determine a finite scoring support.")
        max_total = int(math.ceil(upper))

        # Verify the bound instead of trusting inverse-CDF rounding.
        while True:
            omitted_mass = float(total_law.sf(max_total))
            if not math.isfinite(omitted_mass):
                raise ArithmeticError("Nonfinite scoring tail probability.")
            if omitted_mass <= self.tail_tolerance:
                break
            max_total += 1

        one_sided = home_mean == 0 or away_mean == 0
        cells = (
            max_total + 1
            if one_sided
            else (max_total + 1) * (max_total + 2) // 2
        )
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Requested accuracy needs {cells} score cells; "
                f"configured budget is {self.max_score_cells}."
            )

        totals = np.arange(max_total + 1, dtype=np.float64)
        log_total_mass = np.asarray(total_law.logpmf(totals), dtype=np.float64)
        if np.any(np.isnan(log_total_mass)):
            raise ArithmeticError("Invalid total-goals probability mass.")
        normalizer = float(logsumexp(log_total_mass))
        if not math.isfinite(normalizer):
            raise ArithmeticError("Scoring support has no representable mass.")
        total_mass = np.exp(log_total_mass - normalizer)

        scores = {}
        if one_sided:
            for total, mass in enumerate(total_mass):
                score = (total, 0) if away_mean == 0 else (0, total)
                scores[score] = float(mass)
        else:
            log_home_share = math.log(home_mean) - math.log(mu)
            log_away_share = math.log(away_mean) - math.log(mu)

            for total, mass in enumerate(total_mass):
                home_goals = np.arange(total + 1, dtype=np.float64)
                away_goals = total - home_goals
                log_split = (
                    gammaln(total + 1)
                    - gammaln(home_goals + 1)
                    - gammaln(away_goals + 1)
                    + home_goals * log_home_share
                    + away_goals * log_away_share
                )
                split = np.exp(log_split - logsumexp(log_split))
                for home, conditional_mass in enumerate(split):
                    scores[(home, total - home)] = float(mass * conditional_mass)

        retained_mass = math.fsum(scores.values())
        if not math.isfinite(retained_mass) or retained_mass <= 0:
            raise ArithmeticError("Invalid joint score distribution.")
        scores = {
            score: probability / retained_mass
            for score, probability in scores.items()
            if probability > 0
        }
        return DiscreteDistribution.from_scores(scores)