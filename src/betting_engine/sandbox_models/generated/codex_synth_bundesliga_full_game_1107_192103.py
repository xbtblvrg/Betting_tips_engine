from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional

import numpy as np
from scipy.stats import skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class BundesligaFullGameMoneylineModel(BaseCandidateModel):
    """
    Independent, pre-match, regulation-time three-outcome model.

    Cold-start derivation
    ---------------------
    Let each team's scoring process have a finite integrated intensity and
    conditionally independent infinitesimal scoring opportunities. The resulting
    goal counts are independent Poisson variables. Their difference determines
    home/draw/away settlement, including a genuine discrete draw probability.

    The supplied home/away means already contain the prior home advantage.
    No additional home intercept is applied. With no observations identifying
    dispersion, correlation, or feature effects, these parameters are not
    invented. This is a baseline architecture, not evidence of profitable or
    calibrated out-of-sample predictions.

    Input contract
    --------------
    home_expected, away_expected:
        Optional nonnegative pre-match goal expectations. Each overrides that
        side's baseline and attack/defense adjustments. Subsequent explicit
        tempo and fitted covariate adjustments still apply, so callers must
        avoid supplying an expectation already adjusted for those inputs.

    home, away:
        Optional mappings with dimensionless positive `attack` and `defense`
        multipliers. Attack > 1 increases scoring; defense > 1 means more goals
        conceded. These must be normalized relative to the population used for
        the baseline means, not raw ratings or unidentified exposure counts.
        Their default is the multiplicative identity.

    tempo_multiplier:
        Optional positive common scoring-intensity multiplier. Alternatively,
        supply positive `pace` and `reference_pace`; their ratio is used.
        Supplying both representations is an error.

    covariate_coefficients:
        Constructor-only, externally estimated log-intensity coefficients,
        separately keyed by "home" and "away". Supported covariates:
          * rest_difference: home_rest_days - away_rest_days
          * wind_speed: wind_speed, with zero effective wind inside a dome
          * temperature_difference: temp - reference_temp
        Coefficients must be estimated using only information available before
        each prediction. Missing inputs for an active coefficient are errors.
        There are no default rest or weather effects. Venue identifiers alone
        have no numerically identifiable effect.

    All inputs must describe information available before kickoff. Bookmaker
    prices and odds-derived probabilities are never used. Odds eligibility
    (decimal odds >= 1.40) and expected-value decisions belong downstream:
    expected return per unit stake = outcome_probability * decimal_odds - 1.

    Numerical tails are evaluated by the Skellam distribution, avoiding an
    arbitrarily truncated score lattice. Invalid inputs or failed numerical
    evaluations raise errors rather than returning a fabricated forecast.
    """

    BASE_HOME_EXPECTED = 1.60
    BASE_AWAY_EXPECTED = 1.20

    _SUPPORTED_COVARIATES = frozenset(
        {"rest_difference", "wind_speed", "temperature_difference"}
    )

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_full_game_1107_192103",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "full_game",
        family: str = "moneyline",
        *,
        covariate_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
    ) -> None:
        if (league, scope, family) != (
            "BUNDESLIGA",
            "full_game",
            "moneyline",
        ):
            raise ValueError("This model supports BUNDESLIGA|full_game|moneyline.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        supplied = (
            {} if covariate_coefficients is None else covariate_coefficients
        )
        if not isinstance(supplied, Mapping):
            raise TypeError("covariate_coefficients must be a mapping.")
        if set(supplied) - {"home", "away"}:
            raise ValueError("Coefficient side keys must be 'home' or 'away'.")

        # Copy and freeze configuration so later caller mutations cannot alter
        # an existing model's predictions.
        frozen = {}
        for side in ("home", "away"):
            coefficients = supplied.get(side, {})
            if not isinstance(coefficients, Mapping):
                raise TypeError(f"{side} coefficients must be a mapping.")
            unknown = set(coefficients) - self._SUPPORTED_COVARIATES
            if unknown:
                raise ValueError(f"Unsupported {side} covariates: {unknown!r}")

            entries = []
            for name in sorted(coefficients):
                value = self._number(
                    coefficients[name], f"{side} coefficient {name}"
                )
                if value != 0.0:
                    entries.append((name, value))
            frozen[side] = tuple(entries)

        self._home_coefficients = frozen["home"]
        self._away_coefficients = frozen["away"]

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise TypeError(f"{name} must be a finite numeric scalar.")
        if not np.isscalar(value):
            raise TypeError(f"{name} must be a finite numeric scalar.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite real number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _required(context: Mapping[str, Any], key: str) -> Any:
        if key not in context or context[key] is None:
            raise ValueError(f"Missing required pre-match feature: {key}")
        return context[key]

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise TypeError(f"{side} must be a mapping.")
        return team

    def _base_log_intensity(
        self, context: Mapping[str, Any], side: str
    ) -> Optional[float]:
        expected_key = f"{side}_expected"
        if expected_key in context:
            expected = self._nonnegative(
                context[expected_key], expected_key
            )
            # A supplied structural zero stays zero under multiplicative effects.
            return None if expected == 0.0 else math.log(expected)

        opponent = "away" if side == "home" else "home"
        baseline = (
            self.BASE_HOME_EXPECTED
            if side == "home"
            else self.BASE_AWAY_EXPECTED
        )
        attack = self._positive(
            self._team(context, side).get("attack", 1.0),
            f"{side}.attack",
        )
        defense = self._positive(
            self._team(context, opponent).get("defense", 1.0),
            f"{opponent}.defense",
        )
        return math.fsum(
            (math.log(baseline), math.log(attack), math.log(defense))
        )

    def _tempo_log_multiplier(self, context: Mapping[str, Any]) -> float:
        has_multiplier = "tempo_multiplier" in context
        has_pace = "pace" in context or "reference_pace" in context

        if has_multiplier and has_pace:
            raise ValueError(
                "Supply tempo_multiplier or pace/reference_pace, not both."
            )
        if has_multiplier:
            return math.log(
                self._positive(context["tempo_multiplier"], "tempo_multiplier")
            )
        if has_pace:
            pace = self._positive(self._required(context, "pace"), "pace")
            reference = self._positive(
                self._required(context, "reference_pace"), "reference_pace"
            )
            return math.log(pace) - math.log(reference)
        return 0.0

    def _covariate(self, context: Mapping[str, Any], name: str) -> float:
        if name == "rest_difference":
            home_rest = self._nonnegative(
                self._required(context, "home_rest_days"), "home_rest_days"
            )
            away_rest = self._nonnegative(
                self._required(context, "away_rest_days"), "away_rest_days"
            )
            result = home_rest - away_rest
        elif name == "wind_speed":
            is_dome = self._required(context, "is_dome")
            if not isinstance(is_dome, (bool, np.bool_)):
                raise TypeError("is_dome must be boolean.")
            result = (
                0.0
                if is_dome
                else self._nonnegative(
                    self._required(context, "wind_speed"), "wind_speed"
                )
            )
        elif name == "temperature_difference":
            temperature = self._number(
                self._required(context, "temp"), "temp"
            )
            reference = self._number(
                self._required(context, "reference_temp"), "reference_temp"
            )
            result = temperature - reference
        else:
            raise ValueError(f"Unsupported covariate: {name}")

        if not math.isfinite(result):
            raise ValueError(f"Covariate {name} exceeds numeric range.")
        return result

    def _intensities(
        self, context: Mapping[str, Any]
    ) -> tuple[float, float]:
        tempo_log = self._tempo_log_multiplier(context)
        coefficients_by_side = (
            ("home", self._home_coefficients),
            ("away", self._away_coefficients),
        )
        active_names = {
            name
            for _, coefficients in coefficients_by_side
            for name, _ in coefficients
        }
        features = {
            name: self._covariate(context, name)
            for name in sorted(active_names)
        }

        intensities = []
        for side, coefficients in coefficients_by_side:
            baseline_log = self._base_log_intensity(context, side)
            if baseline_log is None:
                intensities.append(0.0)
                continue

            terms = [baseline_log, tempo_log]
            for name, coefficient in coefficients:
                term = coefficient * features[name]
                if not math.isfinite(term):
                    raise ValueError(
                        f"{side} covariate contribution exceeds numeric range."
                    )
                terms.append(term)

            try:
                log_intensity = math.fsum(terms)
                intensity = math.exp(log_intensity)
            except (OverflowError, ValueError) as exc:
                raise ValueError(
                    f"{side} intensity exceeds numeric range."
                ) from exc

            if not math.isfinite(intensity) or intensity <= 0.0:
                raise ValueError(
                    f"{side} positive intensity cannot be represented."
                )
            intensities.append(intensity)

        return intensities[0], intensities[1]

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_mean, away_mean = self._intensities(game_context)

        if home_mean == 0.0 and away_mean == 0.0:
            home_probability, draw_probability, away_probability = (
                0.0,
                1.0,
                0.0,
            )
        elif away_mean == 0.0:
            draw_probability = math.exp(-home_mean)
            home_probability = -math.expm1(-home_mean)
            away_probability = 0.0
        elif home_mean == 0.0:
            draw_probability = math.exp(-away_mean)
            home_probability = 0.0
            away_probability = -math.expm1(-away_mean)
        else:
            # Margin = home goals - away goals. Evaluate each event directly:
            # home: margin >= 1; draw: margin == 0; away: margin <= -1.
            home_probability = float(
                skellam.sf(0, home_mean, away_mean)
            )
            draw_probability = float(
                skellam.pmf(0, home_mean, away_mean)
            )
            away_probability = float(
                skellam.cdf(-1, home_mean, away_mean)
            )

        probabilities = (
            home_probability,
            draw_probability,
            away_probability,
        )
        if any(
            not math.isfinite(value) or value < 0.0 or value > 1.0
            for value in probabilities
        ):
            raise ArithmeticError("Invalid Skellam probability evaluation.")

        total = math.fsum(probabilities)
        # A machine-precision-derived tolerance detects numerical failure;
        # it neither clips forecasts nor imposes an economic decision threshold.
        tolerance = math.sqrt(np.finfo(float).eps)
        if total <= 0.0 or abs(total - 1.0) > tolerance:
            raise ArithmeticError(
                f"Outcome probabilities failed conservation of mass: {total!r}"
            )

        return DiscreteDistribution(
            probabilities={
                "home": home_probability / total,
                "draw": draw_probability / total,
                "away": away_probability / total,
            }
        )