from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import nbinom, poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the execution environment, as specified
# by the candidate-model interface.


class NCAASecondHalfDoubleChanceModel(BaseCandidateModel):
    """
    Independent scoring model for NCAA_BASEBALL|half_2_ot|double_chance.

    Cold-start identification
    -------------------------
    The supplied segment means identify E[H]=0.80 and E[A]=0.60. An observed
    variance/mean ratio of one identifies a Poisson baseline; it does not
    identify bullpen clustering, structural zero inflation, or rest effects.
    Those effects are activated only by supplied moments or fitted parameters.

    Expected values must describe this betting segment, including its actual
    exposure and settlement definition. Full-game expected runs are invalid
    inputs. This class never silently divides them by an invented inning count.

    Optional inputs
    ---------------
    home_expected, away_expected:
        Segment run expectations, replacing the supplied cold-start means.
    home_mean_multiplier, away_mean_multiplier:
        Positive, independently estimated exposure/tempo adjustments.
    home_exposure / home_reference_exposure, and corresponding away fields:
        Optional actual/reference offensive exposure ratios.
    home_variance_to_mean, away_variance_to_mean:
        Conditional run-count dispersion indices; default to the stated
        Poisson prior. Indices below one require a different count family.
    home_zero_probability, away_zero_probability:
        Optional estimated segment zero frequencies. Together with the mean
        and dispersion, identify an admissible zero-inflated NB distribution.
    scoring_effects:
        Mapping from side to named, fitted log-mean effects:
        {
            "home": {
                "opponent_bullpen_load": {
                    "value": ...,
                    "reference": ...,
                    "coefficient": ...
                }
            },
            "away": {...}
        }
        For example, opponent rest and pitch load belong to the batting side's
        scoring equation. Weather, attack, defense and pace require fitted
        coefficients and reference values; raw features alone have no identified
        quantitative effect. Expectations already adjusted for a feature must
        not receive that effect a second time.
    settled_segment_score_probabilities:
        Optional mapping {(home_segment_runs, away_segment_runs): probability}.
        This must be an independently forecast, already normalized joint law
        of legally settled segment scores. It permits a separate fitted
        play-state model to represent skipped innings, walk-offs, mercy rules,
        extra innings and shared scoring shocks without imposing independence.
        It must not be derived from bookmaker odds.

    Mechanics limitation
    --------------------
    Segment expectations alone cannot identify state-dependent stopping.
    Marginal inning totals cannot identify walk-off overshoots either: an
    ordinary winning hit and a home run have different legal terminal scores.
    Therefore this class does not invent a half-inning stopping simulator.
    Set requires_state_dependent_stopping=True when the joint settlement law
    is required; prediction then fails explicitly if that law is unavailable.

    Output is the mutually exclusive HOME/DRAW/AWAY segment outcome law.
    The overlapping double-chance legs must be computed as H+D, A+D and H+A.
    Odds, vig and realized betting results never enter the forecast.
    """

    _PRIOR_MEANS = {"home": 0.80, "away": 0.60}

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_half_2_ot_1108_075111",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "half_2_ot",
        family: str = "double_chance",
        *,
        numerical_tolerance: float = math.sqrt(np.finfo(float).eps),
        max_support_points: int = 1_000_000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != (
            "NCAA_BASEBALL",
            "half_2_ot",
            "double_chance",
        ):
            raise ValueError("This candidate is specific to its declared segment.")

        self.numerical_tolerance = self._number(
            numerical_tolerance, "numerical_tolerance"
        )
        if not 0.0 < self.numerical_tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie strictly between 0 and 1.")
        if (
            isinstance(max_support_points, bool)
            or not isinstance(max_support_points, int)
            or max_support_points <= 0
        ):
            raise ValueError("max_support_points must be a positive integer.")
        # A computational resource budget, not a scoring/probability clamp.
        self.max_support_points = max_support_points

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite real number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite real number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        mean = self._number(
            context.get(f"{side}_expected", self._PRIOR_MEANS[side]),
            f"{side}_expected",
        )
        if mean < 0.0:
            raise ValueError("Expected runs cannot be negative.")

        multiplier = self._number(
            context.get(f"{side}_mean_multiplier", 1.0),
            f"{side}_mean_multiplier",
        )
        if multiplier <= 0.0:
            raise ValueError("Mean multipliers must be strictly positive.")
        log_adjustment = math.log(multiplier)

        exposure_key = f"{side}_exposure"
        reference_key = f"{side}_reference_exposure"
        if (exposure_key in context) != (reference_key in context):
            raise ValueError("Exposure and reference exposure must be supplied together.")
        zero_exposure = False
        if exposure_key in context:
            exposure = self._number(context[exposure_key], exposure_key)
            reference = self._number(context[reference_key], reference_key)
            if exposure < 0.0 or reference <= 0.0:
                raise ValueError("Exposure must be nonnegative and reference positive.")
            zero_exposure = exposure == 0.0
            if not zero_exposure:
                log_adjustment += math.log(exposure) - math.log(reference)

        all_effects = context.get("scoring_effects", {})
        if not isinstance(all_effects, Mapping):
            raise ValueError("scoring_effects must be a mapping.")
        effects = all_effects.get(side, {})
        if not isinstance(effects, Mapping):
            raise ValueError(f"scoring_effects[{side!r}] must be a mapping.")

        for name, specification in effects.items():
            if not isinstance(specification, Mapping):
                raise ValueError(f"Effect {name!r} must be a mapping.")
            required = {"value", "reference", "coefficient"}
            if not required.issubset(specification):
                raise ValueError(f"Effect {name!r} requires {sorted(required)}.")
            value = self._number(specification["value"], f"{name}.value")
            reference = self._number(specification["reference"], f"{name}.reference")
            coefficient = self._number(
                specification["coefficient"], f"{name}.coefficient"
            )
            log_adjustment += coefficient * (value - reference)

        if not math.isfinite(log_adjustment):
            raise ValueError("The fitted scoring adjustment is not finite.")
        if mean == 0.0 or zero_exposure:
            return 0.0

        try:
            adjusted = math.exp(math.log(mean) + log_adjustment)
        except OverflowError as exc:
            raise ValueError("Adjusted expected runs overflow floating-point range.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError("Adjusted expected runs are not numerically representable.")
        return adjusted

    def _count_law(
        self, context: Mapping[str, Any], side: str, mean: float
    ) -> Tuple[Any, float, bool]:
        """
        Return (active count law, structural-zero probability, is_poisson).

        For R = B*Y, B~Bernoulli(1-z), E[R]=m, Y~NB(k, mean=m/(1-z)):
            Var(R)/m = 1 + m*(z + 1/k)/(1-z).

        Consequently:
            1/k = (D-1)*(1-z)/m - z.
        A supplied zero frequency identifies z on the admissible interval
        [0, (D-1)/(m+D-1)]. Without it, z=0 is the identifiable NB model.
        """
        dispersion = self._number(
            context.get(f"{side}_variance_to_mean", 1.0),
            f"{side}_variance_to_mean",
        )
        if dispersion < 1.0:
            raise ValueError("Underdispersion requires an explicitly different count model.")

        zero_key = f"{side}_zero_probability"
        observed_zero = None
        if zero_key in context:
            observed_zero = self._number(context[zero_key], zero_key)
            if not 0.0 <= observed_zero <= 1.0:
                raise ValueError("Zero probabilities must lie in [0, 1].")

        if mean == 0.0:
            if observed_zero is not None and observed_zero != 1.0:
                raise ValueError("A zero run expectation requires probability one at zero.")
            return poisson(0.0), 0.0, True

        if dispersion == 1.0:
            predicted_zero = math.exp(-mean)
            if (
                observed_zero is not None
                and abs(observed_zero - predicted_zero) > self.numerical_tolerance
            ):
                raise ValueError("The supplied zero frequency contradicts Poisson moments.")
            return poisson(mean), 0.0, True

        excess = dispersion - 1.0
        maximum_zero_inflation = excess / (mean + excess)

        def make_law(z: float, boundary: bool = False) -> Any:
            active_mean = mean / (1.0 - z)
            inverse_shape = excess * (1.0 - z) / mean - z
            if boundary or inverse_shape == 0.0:
                return poisson(active_mean)
            if inverse_shape < 0.0:
                raise ArithmeticError("Invalid negative-binomial shape.")
            shape = 1.0 / inverse_shape
            success_probability = 1.0 / (1.0 + active_mean * inverse_shape)
            if not math.isfinite(shape) or not 0.0 < success_probability < 1.0:
                raise ArithmeticError("Count-law parameters exceed numerical resolution.")
            return nbinom(shape, success_probability)

        if observed_zero is None:
            return make_law(0.0), 0.0, False

        def zero_mass(z: float) -> float:
            law = make_law(z, boundary=(z == maximum_zero_inflation))
            return z + (1.0 - z) * float(law.pmf(0))

        lower_mass = zero_mass(0.0)
        upper_mass = zero_mass(maximum_zero_inflation)
        if abs(observed_zero - lower_mass) <= self.numerical_tolerance:
            return make_law(0.0), 0.0, False
        if abs(observed_zero - upper_mass) <= self.numerical_tolerance:
            return (
                make_law(maximum_zero_inflation, boundary=True),
                maximum_zero_inflation,
                False,
            )
        if not min(lower_mass, upper_mass) < observed_zero < max(
            lower_mass, upper_mass
        ):
            raise ValueError("Mean, dispersion and zero frequency are incompatible.")

        z = brentq(
            lambda candidate: zero_mass(candidate) - observed_zero,
            0.0,
            maximum_zero_inflation,
            xtol=np.nextafter(0.0, 1.0),
            rtol=4.0 * np.finfo(float).eps,
        )
        return make_law(z), float(z), False

    def _joint_outcomes(self, joint: Any) -> Tuple[float, float, float]:
        if not isinstance(joint, Mapping) or not joint:
            raise ValueError("The settled segment score law must be a nonempty mapping.")
        buckets = ([], [], [])
        for score, raw_probability in joint.items():
            if not isinstance(score, tuple) or len(score) != 2:
                raise ValueError("Joint score keys must be (home_runs, away_runs) tuples.")
            if any(
                isinstance(run, (bool, np.bool_))
                or not isinstance(run, (int, np.integer))
                or run < 0
                for run in score
            ):
                raise ValueError("Segment scores must be nonnegative integers.")
            probability = self._number(raw_probability, "joint score probability")
            if probability < 0.0:
                raise ValueError("Joint probabilities cannot be negative.")
            home, away = score
            buckets[0 if home > away else 1 if home == away else 2].append(probability)
        return tuple(math.fsum(bucket) for bucket in buckets)

    def _independent_outcomes(
        self, home_law: Any, home_zero: float, away_law: Any, away_zero: float
    ) -> Tuple[float, float, float]:
        # Sum over home scores; the away CDF/SF includes its entire support.
        # Omitted probability is bounded by the certified home marginal tail.
        quantile = float(home_law.isf(self.numerical_tolerance))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Could not construct a finite count support.")
        upper = math.ceil(quantile)
        while (
            (1.0 - home_zero) * float(home_law.sf(upper))
            > self.numerical_tolerance
        ):
            upper += 1
            if upper + 1 > self.max_support_points:
                raise ArithmeticError("Count support exceeds the computation budget.")
        if upper + 1 > self.max_support_points:
            raise ArithmeticError("Count support exceeds the computation budget.")

        scores = np.arange(upper + 1, dtype=np.int64)
        home_mass = (1.0 - home_zero) * home_law.pmf(scores)
        home_mass[0] += home_zero
        away_mass = (1.0 - away_zero) * away_law.pmf(scores)
        away_mass[0] += away_zero
        away_below = (1.0 - away_zero) * away_law.cdf(scores - 1)
        away_below += away_zero * (scores > 0)
        away_above = (1.0 - away_zero) * away_law.sf(scores)

        return (
            float(np.dot(home_mass, away_below)),
            float(np.dot(home_mass, away_mass)),
            float(np.dot(home_mass, away_above)),
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        joint = game_context.get("settled_segment_score_probabilities")
        if joint is not None:
            outcomes = self._joint_outcomes(joint)
        else:
            if game_context.get("requires_state_dependent_stopping", False):
                raise ValueError(
                    "State-dependent settlement requires an independently forecast "
                    "settled_segment_score_probabilities joint law."
                )

            home_mean = self._mean(game_context, "home")
            away_mean = self._mean(game_context, "away")
            home_law, home_zero, home_poisson = self._count_law(
                game_context, "home", home_mean
            )
            away_law, away_zero, away_poisson = self._count_law(
                game_context, "away", away_mean
            )

            if home_poisson and away_poisson:
                if home_mean == 0.0:
                    draw = math.exp(-away_mean)
                    outcomes = (0.0, draw, -math.expm1(-away_mean))
                elif away_mean == 0.0:
                    draw = math.exp(-home_mean)
                    outcomes = (-math.expm1(-home_mean), draw, 0.0)
                else:
                    margin = skellam(home_mean, away_mean)
                    outcomes = (
                        float(margin.sf(0)),
                        float(margin.pmf(0)),
                        float(margin.cdf(-1)),
                    )
            else:
                outcomes = self._independent_outcomes(
                    home_law, home_zero, away_law, away_zero
                )

        if any(not math.isfinite(p) or p < 0.0 or p > 1.0 for p in outcomes):
            raise ArithmeticError("The outcome law contains invalid probabilities.")
        total = math.fsum(outcomes)
        rounding_budget = len(outcomes) * np.finfo(float).eps
        if total <= 0.0 or abs(total - 1.0) > (
            self.numerical_tolerance + rounding_budget
        ):
            raise ArithmeticError("The outcome law failed its probability-mass audit.")

        # Only certified numerical tail loss/roundoff is normalized.
        home, draw, away = (p / total for p in outcomes)
        return DiscreteDistribution(
            probabilities={"home": home, "draw": draw, "away": away}
        )

    @staticmethod
    def double_chance_probabilities(
        probabilities: Mapping[str, float],
    ) -> Dict[str, float]:
        """Transform a mutually exclusive outcome law into overlapping contracts."""
        home = float(probabilities["home"])
        draw = float(probabilities["draw"])
        away = float(probabilities["away"])
        if any(not math.isfinite(p) or not 0.0 <= p <= 1.0 for p in (home, draw, away)):
            raise ValueError("Invalid outcome probabilities.")
        if not math.isclose(
            math.fsum((home, draw, away)),
            1.0,
            rel_tol=0.0,
            abs_tol=3.0 * np.finfo(float).eps,
        ):
            raise ValueError("Outcome probabilities must sum to one.")
        return {"1X": home + draw, "X2": away + draw, "12": home + away}

    @staticmethod
    def contract_value(probability: float, decimal_odds: float) -> Dict[str, Any]:
        """Continuous EV assessment; 1.40 is the supervisor's eligibility floor."""
        probability = NCAASecondHalfDoubleChanceModel._number(
            probability, "probability"
        )
        decimal_odds = NCAASecondHalfDoubleChanceModel._number(
            decimal_odds, "decimal_odds"
        )
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must lie in [0, 1].")
        if decimal_odds <= 1.0:
            raise ValueError("Decimal odds must exceed one.")
        expected_profit = probability * decimal_odds - 1.0
        return {
            "expected_profit_per_unit_staked": expected_profit,
            "eligible": decimal_odds >= 1.40 and expected_profit > 0.0,
        }