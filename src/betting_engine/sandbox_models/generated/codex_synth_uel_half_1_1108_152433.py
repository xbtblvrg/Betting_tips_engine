from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.stats import binom, nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


# DiscreteDistribution is supplied by the execution environment, as specified
# by the candidate-model interface.


class UELHalf1TotalsModel(BaseCandidateModel):
    """
    Independent first-half scoring model for UEL totals.

    Cold-start identification
    -------------------------
    The supplied first-half means identify E[H]=0.80 and E[A]=0.60.
    With no observed excess dispersion, conditionally independent goal arrivals
    imply H~Poisson(mu_h), A~Poisson(mu_a).

    Optional, independently estimated excess dispersion is represented by a
    shared match-intensity multiplier Z with E[Z]=1 and Var[Z]=v:

        H | Z ~ Poisson(mu_h * Z)
        A | Z ~ Poisson(mu_a * Z)

    A gamma mixing distribution gives
        Var(T)/E[T] = 1 + v * E[T], T=H+A,
        H | T ~ Binomial(T, mu_h / E[T]).

    This is a modeling assumption, not a uniquely identified consequence of
    two moments. With v=0, the model reduces exactly to independent Poissons.

    Context contract
    ----------------
    home_expected, away_expected:
        Optional nonnegative expected goals for THIS FIRST HALF.
        Missing values use the supplied empirical baseline means.

    home, away:
        Optional mappings containing positive residual `attack_factor` and
        `defense_factor` multipliers. A larger defense_factor means greater
        defensive vulnerability. Alternatively, `attack` and `defense` can
        be supplied alongside positive `attack_reference` and
        `defense_reference` values to form dimensionless ratios.
        Unreferenced raw ratings are intentionally not interpreted.

    tempo_factor, home_exposure_factor, away_exposure_factor:
        Optional positive residual multipliers, neutral when absent.

    log_rate_adjustments:
        Optional mapping keyed by "home" and/or "away", each holding a list:
            {
                "feature": "home_rest_days",
                "reference": <training-time centering value>,
                "coefficient": <externally fitted log-rate slope>,
                "scale": <optional positive training-time scale>
            }
        Dotted feature paths such as "weather.wind_speed" are supported.
        Coefficients must be fitted using information available before kickoff.
        The model invents no rest, weather, venue, or league coefficients.

    shared_intensity_variance:
        Optional nonnegative, independently estimated Var[Z].

    dispersion_index:
        Optional independently estimated total-goal variance/mean ratio.
        Must be >=1 for this model. Its default is the supplied Poisson prior.
        dispersion_reference_mean optionally identifies the mean at which that
        index was estimated; otherwise the current total mean is used.

    tail_probability_tolerance:
        Optional numerical accuracy target in (0,1), not a betting filter.
        Defaults to machine epsilon. Score matrices condition on their finite
        support; omitted probability is bounded by this tolerance. Binary
        valuation uses the untruncated analytical distribution.

    max_score_cells:
        Optional caller-supplied resource budget. Exceeding it raises an error;
        the implementation never silently truncates to fit a resource cap.

    All multipliers and adjustments are RESIDUAL effects. Do not apply effects
    already incorporated into supplied expected-goal inputs a second time.

    No market odds, prices, wager outcomes, or bookmaker probabilities enter
    the forecast. Cold-start out-of-sample calibration is not established by
    this implementation and must be evaluated on subsequent held-out games.
    """

    BASELINE_HOME = 0.80
    BASELINE_AWAY = 0.60
    MINIMUM_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1108_152433",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "totals",
    ) -> None:
        if (league, scope, family) != ("UEL", "half_1", "totals"):
            raise ValueError("This model supports only UEL|half_1|totals.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
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
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _lookup(context: Mapping[str, Any], path: str) -> Any:
        if not isinstance(path, str) or not path:
            raise ValueError("Feature paths must be nonempty strings.")
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Required feature {path!r} is missing.")
            value = value[component]
        return value

    @classmethod
    def _team_factor(
        cls, team: Mapping[str, Any], name: str, label: str
    ) -> float:
        factor_key = f"{name}_factor"
        reference_key = f"{name}_reference"

        if factor_key in team:
            if name in team or reference_key in team:
                raise ValueError(f"{label}.{name} has ambiguous representations.")
            return cls._positive(team[factor_key], f"{label}.{factor_key}")

        if reference_key in team:
            if name not in team:
                raise ValueError(f"{label}.{name} is required with its reference.")
            value = cls._positive(team[name], f"{label}.{name}")
            reference = cls._positive(
                team[reference_key], f"{label}.{reference_key}"
            )
            return cls._positive(value / reference, f"{label}.{name} ratio")

        return 1.0

    @classmethod
    def _means(cls, context: Mapping[str, Any]) -> Tuple[float, float]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "UEL"),
            ("scope", "half_1"),
            ("family", "totals"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        teams = {}
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, Mapping):
                raise ValueError(f"{side} must be a mapping.")
            teams[side] = team

        tempo = cls._positive(context.get("tempo_factor", 1.0), "tempo_factor")
        adjustments = context.get("log_rate_adjustments", {})
        if not isinstance(adjustments, Mapping):
            raise ValueError("log_rate_adjustments must be a mapping.")
        if set(adjustments) - {"home", "away"}:
            raise ValueError("Unknown side in log_rate_adjustments.")

        means = []
        for side, opponent, baseline in (
            ("home", "away", cls.BASELINE_HOME),
            ("away", "home", cls.BASELINE_AWAY),
        ):
            base = cls._nonnegative(
                context.get(f"{side}_expected", baseline), f"{side}_expected"
            )
            factors = (
                tempo,
                cls._positive(
                    context.get(f"{side}_exposure_factor", 1.0),
                    f"{side}_exposure_factor",
                ),
                cls._team_factor(teams[side], "attack", side),
                cls._team_factor(teams[opponent], "defense", opponent),
            )
            terms = [math.log(factor) for factor in factors]
            specifications = adjustments.get(side, ())
            if not isinstance(specifications, (list, tuple)):
                raise ValueError(f"log_rate_adjustments.{side} must be a list.")

            for specification in specifications:
                if not isinstance(specification, Mapping):
                    raise ValueError("Each rate adjustment must be a mapping.")
                if not {"feature", "reference", "coefficient"} <= specification.keys():
                    raise ValueError(
                        "Rate adjustments require feature, reference, coefficient."
                    )
                feature = specification["feature"]
                raw = cls._lookup(context, feature)
                if isinstance(raw, (bool, np.bool_)):
                    raw = int(raw)
                value = cls._number(raw, feature)
                reference = cls._number(specification["reference"], "reference")
                coefficient = cls._number(
                    specification["coefficient"], "coefficient"
                )
                scale = cls._positive(specification.get("scale", 1.0), "scale")
                terms.append(
                    cls._number(
                        coefficient * ((value - reference) / scale),
                        f"{side} log-rate adjustment",
                    )
                )

            if base == 0:
                means.append(0.0)
                continue

            try:
                mean = math.exp(math.fsum([math.log(base), *terms]))
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"{side} expected goals exceed numeric range.") from exc
            if not math.isfinite(mean) or mean <= 0:
                raise ValueError(f"{side} expected goals exceed numeric range.")
            means.append(mean)

        return means[0], means[1]

    @classmethod
    def _law(
        cls, context: Mapping[str, Any]
    ) -> Tuple[float, float, Optional[Any]]:
        home, away = cls._means(context)
        total = home + away
        if not math.isfinite(total):
            raise ValueError("Total expected goals exceed numeric range.")

        has_variance = "shared_intensity_variance" in context
        has_index = "dispersion_index" in context

        if has_variance and (has_index or "dispersion_reference_mean" in context):
            raise ValueError("Supply one dispersion parameterization.")
        if "dispersion_reference_mean" in context and not has_index:
            raise ValueError("dispersion_reference_mean requires dispersion_index.")

        if has_variance:
            variance = cls._nonnegative(
                context["shared_intensity_variance"],
                "shared_intensity_variance",
            )
        else:
            index = cls._positive(
                context.get("dispersion_index", 1.0), "dispersion_index"
            )
            if index < 1:
                raise ValueError("Underdispersion requires a different model.")
            reference = None
            if "dispersion_reference_mean" in context:
                reference = cls._positive(
                    context["dispersion_reference_mean"],
                    "dispersion_reference_mean",
                )
            if index == 1:
                variance = 0.0
            else:
                reference = total if reference is None else reference
                if reference <= 0:
                    raise ValueError(
                        "Excess dispersion requires a positive reference mean."
                    )
                variance = cls._positive(
                    (index - 1.0) / reference, "shared intensity variance"
                )

        if total == 0:
            return home, away, None
        if variance == 0:
            return home, away, poisson(mu=total)

        shape = 1.0 / variance
        excess = total * variance
        probability = 1.0 / (1.0 + excess)
        if (
            not math.isfinite(shape)
            or shape <= 0
            or not math.isfinite(excess)
            or not 0 < probability < 1
        ):
            raise ValueError("Dispersion parameters exceed numeric resolution.")
        return home, away, nbinom(n=shape, p=probability)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        home, away, law = self._law(game_context)
        if law is None:
            return DiscreteDistribution.from_scores({(0, 0): 1.0})

        tolerance = self._positive(
            game_context.get(
                "tail_probability_tolerance", np.finfo(np.float64).eps
            ),
            "tail_probability_tolerance",
        )
        if tolerance >= 1:
            raise ValueError("tail_probability_tolerance must be below one.")

        quantile = float(law.isf(tolerance))
        if not math.isfinite(quantile) or quantile < 0:
            raise ArithmeticError("Unable to determine finite score support.")
        maximum_total = math.ceil(quantile)
        while True:
            tail = float(law.sf(maximum_total))
            if not math.isfinite(tail):
                raise ArithmeticError("Nonfinite total-goal tail probability.")
            if tail <= tolerance:
                break
            maximum_total += 1

        cell_count = (
            maximum_total + 1
            if home == 0 or away == 0
            else (maximum_total + 1) * (maximum_total + 2) // 2
        )
        if "max_score_cells" in game_context:
            budget = self._positive(game_context["max_score_cells"], "max_score_cells")
            if not budget.is_integer():
                raise ValueError("max_score_cells must be an integer.")
            if cell_count > int(budget):
                raise MemoryError(
                    f"Required score support has {cell_count} cells; "
                    f"caller budget is {int(budget)}."
                )

        home_share = home / (home + away)
        scores: Dict[Tuple[int, int], float] = {}

        for total in range(maximum_total + 1):
            total_mass = float(law.pmf(total))
            if not math.isfinite(total_mass) or total_mass < 0:
                raise ArithmeticError("Invalid total-goal probability.")
            if total_mass == 0:
                continue
            if home == 0:
                scores[(0, total)] = total_mass
            elif away == 0:
                scores[(total, 0)] = total_mass
            else:
                allocations = binom.pmf(
                    np.arange(total + 1), total, home_share
                )
                if np.any(~np.isfinite(allocations)) or np.any(allocations < 0):
                    raise ArithmeticError("Invalid conditional score probabilities.")
                allocation_mass = math.fsum(float(p) for p in allocations)
                if allocation_mass <= 0:
                    raise ArithmeticError("Conditional score probability vanished.")
                for home_goals, conditional in enumerate(allocations):
                    probability = total_mass * float(conditional) / allocation_mass
                    if probability > 0:
                        scores[(home_goals, total - home_goals)] = probability

        retained_mass = math.fsum(scores.values())
        if not math.isfinite(retained_mass) or retained_mass <= 0:
            raise ArithmeticError("Score distribution has no finite probability mass.")

        normalized = {
            score: probability / retained_mass
            for score, probability in scores.items()
        }
        return DiscreteDistribution.from_scores(normalized)

    def binary_total_probability(
        self,
        game_context: Dict[str, Any],
        line: float,
        comparison: str,
    ) -> float:
        """
        Probability of a binary YES contract under explicit settlement rules.

        comparison is "gt", "ge", "lt", or "le". This accommodates arbitrary real
        thresholds and integer-line equality without inventing push rules.
        Asian totals and quarter-line split settlements are separate contracts.
        """
        threshold = self._number(line, "line")
        if comparison not in {"gt", "ge", "lt", "le"}:
            raise ValueError("comparison must be gt, ge, lt, or le.")

        _, _, law = self._law(game_context)
        if law is None:
            return float(
                {
                    "gt": 0 > threshold,
                    "ge": 0 >= threshold,
                    "lt": 0 < threshold,
                    "le": 0 <= threshold,
                }[comparison]
            )

        if comparison == "gt":
            probability = float(law.sf(math.floor(threshold)))
        elif comparison == "ge":
            probability = float(law.sf(math.ceil(threshold) - 1))
        elif comparison == "lt":
            probability = float(law.cdf(math.ceil(threshold) - 1))
        else:
            probability = float(law.cdf(math.floor(threshold)))

        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ArithmeticError("Invalid binary settlement probability.")
        return probability

    def value_binary_contract(
        self,
        game_context: Dict[str, Any],
        *,
        line: float,
        comparison: str,
        executable_price: float,
        buy_yes: bool = True,
        acquisition_cost_per_share: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Value a fully collateralized binary contract paying one unit on a win.

        executable_price is the actual executable price of the selected YES or
        NO token, including size-dependent slippage. acquisition_cost_per_share
        supplies externally calculated additional fees/costs; no venue fee
        schedule is assumed. The selected token's price is never used to fit
        the scoring distribution.

        Eligibility uses the requested gross decimal-odds floor and strictly
        positive net expectation, with no odds ceiling or probability filter.
        """
        price = self._positive(executable_price, "executable_price")
        if price > 1:
            raise ValueError("A binary token price must not exceed its unit payout.")
        if not isinstance(buy_yes, (bool, np.bool_)):
            raise ValueError("buy_yes must be boolean.")
        additional_cost = self._nonnegative(
            acquisition_cost_per_share, "acquisition_cost_per_share"
        )
        cost = price + additional_cost
        if not math.isfinite(cost):
            raise ValueError("Acquisition cost exceeds numeric range.")

        inverse = {"gt": "le", "ge": "lt", "lt": "ge", "le": "gt"}
        if comparison not in inverse:
            raise ValueError("comparison must be gt, ge, lt, or le.")
        selected_comparison = comparison if buy_yes else inverse[comparison]
        probability = self.binary_total_probability(
            game_context, line, selected_comparison
        )

        gross_decimal_odds = 1.0 / price
        expected_profit = probability - cost
        return {
            "win_probability": probability,
            "gross_decimal_odds": gross_decimal_odds,
            "acquisition_cost_per_share": cost,
            "expected_profit_per_share": expected_profit,
            "expected_roi": expected_profit / cost,
            "eligible": bool(
                gross_decimal_odds >= self.MINIMUM_DECIMAL_ODDS
                and expected_profit > 0
            ),
        }