"""Independent, deterministic EPL period-moneyline probabilities.

Expected-goal inputs must describe the target period, unless
``expected_goals_basis="per_90"`` is explicitly supplied. Full-match rates
are converted using ``remaining_minutes``; no half or period is inferred.

Optional coefficients must be estimated using chronologically separated
training data. Defaults leave supplied expected goals unchanged.

This class neither reads market prices nor submits orders. Admission,
immutable logging, portfolio sizing, daily volume, and live progression
belong to the calling engine. No ROI or validation pass is guaranteed.
"""

from math import exp, fsum, isfinite
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.special import expit
from scipy.stats import skellam

from betting_engine.sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
)


class EPLPeriodMoneylineModel(BaseCandidateModel):
    """Poisson goal difference with a positivity-preserving DC correction.

    Required context:
        home_expected, away_expected:
            Independent expected goals for the remaining target period.
            These must not be derived from bookmaker or exchange prices.

    Optional context:
        expected_goals_basis:
            "target_period" (default) or "per_90". The latter requires
            remaining_minutes, representing remaining target-period exposure.
        settlement_mode:
            "period_total" (default) or "remaining_goals".
        period_home_goals, period_away_goals:
            Goals already scored within this particular settlement period.
            Both must be supplied together. Defaults are zero.
        home_rest_days, away_rest_days:
            Nonnegative rest durations.
        home, away:
            Mappings containing positive multiplicative "attack" and
            "defense" indices. Defense > 1 means more goals conceded.
        weather:
            Mapping containing wind_speed (m/s), temp (C), and is_dome.
        venue:
            Informational only; venue effects should enter expected goals.

    Coefficients act on log expected goals. Only explicitly configured
    nonzero coefficients activate features, preventing accidental double
    counting when expected goals already incorporate those features.

    Dependence is expressed by ``dc_strength`` in (-1, 1). Positive values
    favor 0-0 and 1-1 scores. Its mapping guarantees admissible Dixon-Coles
    cell factors for every finite pair of positive scoring rates.

    Poisson tails are integrated with the Skellam distribution rather than
    discarded by a fixed score-grid cutoff.
    """

    market_anchor_mode = "shadow"
    model_weight = 1.0

    _COEFFICIENT_NAMES = frozenset(
        {
            "intercept",
            "home_advantage",
            "attack",
            "opponent_defense",
            "rest_advantage",
            "wind",
            "temperature",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_other_period_1104_084258",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "other_period",
        family: str = "moneyline",
        *,
        coefficients: Optional[Mapping[str, float]] = None,
        dc_strength: float = 0.0,
        temperature_reference_c: float = 15.0,
    ) -> None:
        if (league, scope, family) != ("EPL", "other_period", "moneyline"):
            raise ValueError(
                "This candidate supports only EPL|other_period|moneyline."
            )

        supplied = dict(coefficients or {})
        unknown = supplied.keys() - self._COEFFICIENT_NAMES
        if unknown:
            raise ValueError(f"Unknown coefficients: {sorted(unknown)}")

        self._coefficients = {
            name: self._number(supplied.get(name, 0.0), name)
            for name in self._COEFFICIENT_NAMES
        }
        self._dc_strength = self._number(dc_strength, "dc_strength")
        if not -1.0 < self._dc_strength < 1.0:
            raise ValueError("dc_strength must be strictly between -1 and 1.")

        self._temperature_reference_c = self._number(
            temperature_reference_c, "temperature_reference_c"
        )

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.market_anchor_mode = "shadow"
        self.model_weight = 1.0

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _goal_count(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _log_index(
        cls, team: Mapping[str, Any], key: str, label: str
    ) -> float:
        if key not in team:
            raise ValueError(f"Missing {label}.{key}.")
        value = cls._number(team[key], f"{label}.{key}")
        if value <= 0.0:
            raise ValueError(f"{label}.{key} must be strictly positive.")
        return float(np.log(value))

    def _log_adjustments(
        self, context: Mapping[str, Any]
    ) -> tuple[float, float]:
        c = self._coefficients
        home_terms = [c["intercept"], c["home_advantage"] / 2.0]
        away_terms = [c["intercept"], -c["home_advantage"] / 2.0]

        if c["attack"] or c["opponent_defense"]:
            home = self._mapping(context.get("home"), "home")
            away = self._mapping(context.get("away"), "away")

            if c["attack"]:
                home_terms.append(
                    c["attack"] * self._log_index(home, "attack", "home")
                )
                away_terms.append(
                    c["attack"] * self._log_index(away, "attack", "away")
                )
            if c["opponent_defense"]:
                home_terms.append(
                    c["opponent_defense"]
                    * self._log_index(away, "defense", "away")
                )
                away_terms.append(
                    c["opponent_defense"]
                    * self._log_index(home, "defense", "home")
                )

        if c["rest_advantage"]:
            home_rest = self._nonnegative(
                context.get("home_rest_days"), "home_rest_days"
            )
            away_rest = self._nonnegative(
                context.get("away_rest_days"), "away_rest_days"
            )
            effect = c["rest_advantage"] * (
                float(np.log1p(home_rest)) - float(np.log1p(away_rest))
            )
            home_terms.append(effect / 2.0)
            away_terms.append(-effect / 2.0)

        if c["wind"] or c["temperature"]:
            weather = self._mapping(context.get("weather"), "weather")
            dome = weather.get("is_dome", False)
            if not isinstance(dome, (bool, np.bool_)):
                raise ValueError("weather.is_dome must be boolean.")

            if not dome:
                if c["wind"]:
                    wind = self._nonnegative(
                        weather.get("wind_speed"), "weather.wind_speed"
                    )
                    effect = c["wind"] * float(np.log1p(wind))
                    home_terms.append(effect)
                    away_terms.append(effect)
                if c["temperature"]:
                    temperature = self._number(
                        weather.get("temp"), "weather.temp"
                    )
                    effect = c["temperature"] * (
                        temperature - self._temperature_reference_c
                    )
                    home_terms.append(effect)
                    away_terms.append(effect)

        try:
            adjustments = (fsum(home_terms), fsum(away_terms))
        except (OverflowError, ValueError) as exc:
            raise ValueError("Feature adjustments exceed numeric range.") from exc

        if not all(isfinite(value) for value in adjustments):
            raise ValueError("Feature adjustments must be finite.")
        return adjustments

    @staticmethod
    def _adjust_rate(rate: float, log_adjustment: float) -> float:
        if rate == 0.0:
            return 0.0
        log_rate = float(np.log(rate)) + log_adjustment
        try:
            adjusted = exp(log_rate)
        except OverflowError as exc:
            raise ValueError("Adjusted expected goals exceed numeric range.") from exc
        if not isfinite(adjusted) or adjusted == 0.0:
            raise ValueError("Adjusted expected goals exceed numeric precision.")
        return adjusted

    def _rates(self, context: Mapping[str, Any]) -> tuple[float, float]:
        home = self._nonnegative(context.get("home_expected"), "home_expected")
        away = self._nonnegative(context.get("away_expected"), "away_expected")

        basis = context.get("expected_goals_basis", "target_period")
        if basis == "per_90":
            minutes = self._nonnegative(
                context.get("remaining_minutes"), "remaining_minutes"
            )
            exposure = minutes / 90.0
            home *= exposure
            away *= exposure
        elif basis != "target_period":
            raise ValueError(
                "expected_goals_basis must be 'target_period' or 'per_90'."
            )

        if not isfinite(home) or not isfinite(away):
            raise ValueError("Period expected goals exceed numeric range.")

        home_adjustment, away_adjustment = self._log_adjustments(context)
        return (
            self._adjust_rate(home, home_adjustment),
            self._adjust_rate(away, away_adjustment),
        )

    def _offset(self, context: Mapping[str, Any]) -> int:
        mode = context.get("settlement_mode", "period_total")
        if mode not in {"period_total", "remaining_goals"}:
            raise ValueError(
                "settlement_mode must be 'period_total' or 'remaining_goals'."
            )

        has_home = "period_home_goals" in context
        has_away = "period_away_goals" in context
        if has_home != has_away:
            raise ValueError("Supply both period score fields together.")

        home = self._goal_count(
            context.get("period_home_goals", 0), "period_home_goals"
        )
        away = self._goal_count(
            context.get("period_away_goals", 0), "period_away_goals"
        )
        return home - away if mode == "period_total" else 0

    def _dc_mass(self, home: float, away: float) -> float:
        """Return the correction to each diagonal low-score cell.

        Standard DC factors are:
            tau00 = 1 - home * away * rho
            tau01 = 1 + home * rho
            tau10 = 1 + away * rho
            tau11 = 1 - rho

        The returned mass is -rho * home * away * exp(-home-away).
        A bounded dependence coordinate ensures every factor stays positive.
        """
        strength = self._dc_strength
        if strength == 0.0 or home == 0.0 or away == 0.0:
            return 0.0

        log_home = float(np.log(home))
        log_away = float(np.log(away))

        if strength > 0.0:
            # rho = -strength / (1 + home + away).
            log_denominator = float(
                np.logaddexp(0.0, np.logaddexp(log_home, log_away))
            )
            log_mass = (
                float(np.log(strength))
                + log_home
                + log_away
                - log_denominator
                - home
                - away
            )
            return exp(log_mass)

        # rho = (-strength) / (1 + home * away).
        # expit avoids forming a potentially overflowing product.
        product_fraction = float(expit(log_home + log_away))
        return strength * product_fraction * exp(-home - away)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for field, expected in (
            ("league", "EPL"),
            ("scope", "other_period"),
            ("family", "moneyline"),
        ):
            if field in context and context[field] != expected:
                raise ValueError(f"Context {field} must equal {expected!r}.")

        home, away = self._rates(context)
        offset = self._offset(context)

        if home == 0.0 and away == 0.0:
            probabilities = [
                float(offset > 0),
                float(offset == 0),
                float(offset < 0),
            ]
        elif home == 0.0 or away == 0.0:
            from scipy.stats import poisson

            if away == 0.0:
                threshold = -offset
                probabilities = [
                    float(poisson.sf(threshold, home)),
                    float(poisson.pmf(threshold, home)),
                    float(poisson.cdf(threshold - 1, home)),
                ]
            else:
                threshold = offset
                probabilities = [
                    float(poisson.cdf(threshold - 1, away)),
                    float(poisson.pmf(threshold, away)),
                    float(poisson.sf(threshold, away)),
                ]
        else:
            threshold = -offset
            probabilities = [
                float(skellam.sf(threshold, home, away)),
                float(skellam.pmf(threshold, home, away)),
                float(skellam.cdf(threshold - 1, home, away)),
            ]

            mass = self._dc_mass(home, away)
            corrections = [[], [], []]
            for home_goals, away_goals, signed_mass in (
                (0, 0, mass),
                (1, 1, mass),
                (0, 1, -mass),
                (1, 0, -mass),
            ):
                difference = offset + home_goals - away_goals
                index = 0 if difference > 0 else 2 if difference < 0 else 1
                corrections[index].append(signed_mass)

            probabilities = [
                fsum([probability, *changes])
                for probability, changes in zip(probabilities, corrections)
            ]

        if not all(isfinite(p) and p >= 0.0 for p in probabilities):
            raise ArithmeticError("Probability calculation failed numerically.")

        total = fsum(probabilities)
        tolerance = 256.0 * np.finfo(float).eps
        if abs(total - 1.0) > tolerance:
            raise ArithmeticError(
                f"Probability mass is inconsistent: total={total!r}."
            )

        # Correct floating-point summation error only; never floor or cap odds.
        normalized = [p / total for p in probabilities]
        return DiscreteDistribution(
            probabilities=dict(zip(("home", "draw", "away"), normalized))
        )