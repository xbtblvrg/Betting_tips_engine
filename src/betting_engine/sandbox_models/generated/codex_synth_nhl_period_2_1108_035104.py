from typing import Any, Dict, Mapping, Optional
import math

import numpy as np
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some candidate runners inject this type into the execution namespace.
if "DiscreteDistribution" not in globals():
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NHLPeriod2ExactScoreModel(BaseCandidateModel):
    """
    Period-only exact-score model with Poisson marginals.

    Let X = A + C and Y = B + C, where the three components are
    independent Poisson variables. Then

        E[X] = Var[X] = mu_home
        E[Y] = Var[Y] = mu_away
        Cov[X, Y] = lambda_common.

    The supplied means identify the marginals, but do not identify
    covariance. Cold-start covariance therefore defaults to zero.
    Positive dependence must come from independently estimated P2 data;
    sports narratives alone do not identify its magnitude. A common
    component is a statistical representation, not simultaneous goals.

    Context contract
    ----------------
    home_expected, away_expected:
        Expected goals for the SECOND PERIOD ONLY. Defaults: 1.02, 0.92.
        Full-game expectations must be converted upstream.

    pace_ratio:
        Positive multiplicative exposure relative to the reference period.
        Defaults to the multiplicative identity.

    home, away:
        Optional mappings containing attack_ratio and defense_ratio.
        A defense_ratio above one means more goals conceded. Home scoring
        uses home.attack_ratio * away.defense_ratio, and conversely.
        Unqualified attack/defense ratings are not interpreted because their
        units and direction are unspecified.

    home_penalty_exposure_ratio, away_penalty_exposure_ratio:
        Positive, side-specific scoring-intensity ratios, calibrated
        upstream. Raw penalty counts are not goal-intensity multipliers.

    rate_coefficients:
        Optional mapping:
            {"home": {"feature": coefficient, ...},
             "away": {"feature": coefficient, ...}}
        Coefficients must be fitted on prior, disjoint training data.

    rate_features:
        Finite, centered numerical covariates for rate_coefficients.
        A coefficient named "rest" instead uses log1p(rest_days) minus
        log1p(reference_rest_days), separately for each side. Both rest
        inputs must be supplied when that coefficient is used.

    period_1_home_goals, period_1_away_goals, score_state_coefficient:
        Optional fitted score-state adjustment. It changes allocation
        between teams while preserving their combined expected goals.
        A positive coefficient shifts scoring share toward the team
        trailing after period one.

    p2_covariance:
        Conditional covariance at the FINAL situational means. Must lie
        in [0, min(mu_home, mu_away)].

    p2_reference_covariance:
        Alternative covariance at the supplied empirical reference means.
        Its fraction of the maximum feasible covariance is transported
        to the situational means. This transport is a modeling assumption.
        Do not supply both covariance fields.

    All intensity multipliers must describe residual effects not already
    included in home_expected/away_expected, to avoid double counting.

    Numerical truncation has an explicit omitted-mass budget, unrelated
    to betting thresholds. The returned finite distribution is conditioned
    on its retained rectangle; its total-variation error is at most that
    budget. No bookmaker information enters prediction.

    There are no settled wagers establishing calibration, predictive
    skill, profitability, or superiority to the supplied benchmarks.
    """

    HOME_REFERENCE_MEAN = 1.02
    AWAY_REFERENCE_MEAN = 0.92

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_2_1108_035104",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_2",
        family: str = "exact_score",
        *,
        tail_mass_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        if (league, scope, family) != ("NHL", "period_2", "exact_score"):
            raise ValueError("This model supports only NHL|period_2|exact_score.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        if tail_mass_tolerance is None:
            tail_mass_tolerance = math.sqrt(np.finfo(np.float64).eps)

        tolerance = self._number(
            tail_mass_tolerance, "tail_mass_tolerance"
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_mass_tolerance must lie strictly in (0, 1).")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, (bool, np.bool_))
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")

        self.tail_mass_tolerance = tolerance
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise TypeError(f"{name} must be a finite real number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError(f"{name} must be a finite real number.") from exc
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
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _goal_count(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer goal count.")
        return int(result)

    def _mean(self, context: Mapping, side: str, opponent: str) -> float:
        reference = (
            self.HOME_REFERENCE_MEAN
            if side == "home"
            else self.AWAY_REFERENCE_MEAN
        )
        expected = self._nonnegative(
            context.get(f"{side}_expected", reference),
            f"{side}_expected",
        )

        team = self._mapping(context.get(side, {}), side)
        opposing_team = self._mapping(context.get(opponent, {}), opponent)

        factors = (
            self._positive(context.get("pace_ratio", 1.0), "pace_ratio"),
            self._positive(
                team.get("attack_ratio", 1.0), f"{side}.attack_ratio"
            ),
            self._positive(
                opposing_team.get("defense_ratio", 1.0),
                f"{opponent}.defense_ratio",
            ),
            self._positive(
                context.get(f"{side}_penalty_exposure_ratio", 1.0),
                f"{side}_penalty_exposure_ratio",
            ),
        )

        coefficients_by_side = self._mapping(
            context.get("rate_coefficients", {}), "rate_coefficients"
        )
        if set(coefficients_by_side) - {"home", "away"}:
            raise ValueError("rate_coefficients accepts only home and away.")

        coefficients = self._mapping(
            coefficients_by_side.get(side, {}),
            f"rate_coefficients.{side}",
        )
        features = self._mapping(
            context.get("rate_features", {}), "rate_features"
        )
        terms = [math.log(factor) for factor in factors]

        for feature, raw_coefficient in coefficients.items():
            coefficient = self._number(
                raw_coefficient, f"rate_coefficients.{side}.{feature}"
            )
            if feature == "rest":
                rest_key = f"{side}_rest_days"
                reference_key = f"{side}_reference_rest_days"
                if rest_key not in context or reference_key not in context:
                    raise ValueError(
                        f"A rest coefficient requires {rest_key} and "
                        f"{reference_key}."
                    )
                rest = self._nonnegative(context[rest_key], rest_key)
                reference_rest = self._nonnegative(
                    context[reference_key], reference_key
                )
                feature_value = math.log1p(rest) - math.log1p(reference_rest)
            else:
                if feature not in features:
                    raise ValueError(f"Missing rate_features[{feature!r}].")
                feature_value = self._number(
                    features[feature], f"rate_features.{feature}"
                )

            term = coefficient * feature_value
            if not math.isfinite(term):
                raise ValueError("A rate adjustment exceeded numerical range.")
            terms.append(term)

        if expected == 0.0:
            return 0.0

        try:
            mean = math.exp(math.fsum([math.log(expected), *terms]))
        except (OverflowError, ValueError) as exc:
            raise ValueError("Expected goals exceeded numerical range.") from exc

        if not math.isfinite(mean) or mean == 0.0:
            raise ValueError("Expected goals exceeded floating-point range.")
        return mean

    def _means(self, context: Mapping):
        home = self._mean(context, "home", "away")
        away = self._mean(context, "away", "home")

        if "score_state_coefficient" not in context:
            return home, away

        coefficient = self._number(
            context["score_state_coefficient"], "score_state_coefficient"
        )
        required = ("period_1_home_goals", "period_1_away_goals")
        if any(key not in context for key in required):
            raise ValueError(
                "score_state_coefficient requires both period-one goal counts."
            )

        home_goals = self._goal_count(context[required[0]], required[0])
        away_goals = self._goal_count(context[required[1]], required[1])
        adjustment = coefficient * (away_goals - home_goals)
        if not math.isfinite(adjustment):
            raise ValueError("Score-state adjustment exceeded numerical range.")

        if home == 0.0 or away == 0.0:
            return home, away

        total = home + away
        if not math.isfinite(total):
            raise ValueError("Combined expected goals exceeded numerical range.")

        log_odds = math.log(home) - math.log(away) + adjustment
        if not math.isfinite(log_odds):
            raise ValueError("Adjusted scoring allocation is not finite.")

        # Stable allocation: preserve the total without clipping log odds.
        smaller_ratio = math.exp(-abs(log_odds))
        smaller_mean = total * (smaller_ratio / (1.0 + smaller_ratio))
        larger_mean = total - smaller_mean
        if smaller_mean == 0.0:
            raise ValueError("Score allocation underflowed floating-point range.")

        return (
            (larger_mean, smaller_mean)
            if log_odds >= 0.0
            else (smaller_mean, larger_mean)
        )

    def _common_mean(self, context: Mapping, home: float, away: float) -> float:
        conditional = "p2_covariance" in context
        reference = "p2_reference_covariance" in context
        if conditional and reference:
            raise ValueError("Supply only one covariance specification.")

        feasible = min(home, away)
        if conditional:
            covariance = self._nonnegative(
                context["p2_covariance"], "p2_covariance"
            )
            if covariance > feasible:
                raise ValueError(
                    "p2_covariance exceeds min(home_mean, away_mean)."
                )
            return covariance

        if reference:
            covariance = self._nonnegative(
                context["p2_reference_covariance"], "p2_reference_covariance"
            )
            reference_limit = min(
                self.HOME_REFERENCE_MEAN, self.AWAY_REFERENCE_MEAN
            )
            if covariance > reference_limit:
                raise ValueError(
                    "Reference covariance exceeds its mathematical bound."
                )
            return (covariance / reference_limit) * feasible

        return 0.0

    @staticmethod
    def _cutoff(mean: float, tail_budget: float) -> int:
        if mean == 0.0:
            return 0

        # Find the smallest cutoff satisfying the actual Poisson survival
        # bound. This avoids inverse-CDF failures near machine precision.
        lower = -1
        upper = max(1, math.ceil(mean))
        while float(poisson.sf(upper, mean)) > tail_budget:
            lower = upper
            upper = 2 * upper + 1

        while upper - lower > 1:
            middle = (lower + upper) // 2
            if float(poisson.sf(middle, mean)) <= tail_budget:
                upper = middle
            else:
                lower = middle
        return upper

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        context = self._mapping(game_context, "game_context")
        for key, expected in (
            ("league", "NHL"),
            ("scope", "period_2"),
            ("family", "exact_score"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        home, away = self._means(context)
        common = self._common_mean(context, home, away)

        # A union bound controls omitted mass for every feasible covariance.
        marginal_budget = self.tail_mass_tolerance / 2.0
        if marginal_budget == 0.0:
            raise ValueError("The requested tail tolerance is too small.")

        home_cutoff = self._cutoff(home, marginal_budget)
        away_cutoff = self._cutoff(away, marginal_budget)
        cells = (home_cutoff + 1) * (away_cutoff + 1)

        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Distribution requires {cells} score cells; configured "
                f"resource budget is {self.max_score_cells}. "
                "No probability or expected-goal clamp was applied."
            )

        home_counts = np.arange(home_cutoff + 1)
        away_counts = np.arange(away_cutoff + 1)

        log_home = poisson.logpmf(home_counts, home - common)
        log_away = poisson.logpmf(away_counts, away - common)
        log_joint = np.full(
            (home_cutoff + 1, away_cutoff + 1), -np.inf, dtype=np.float64
        )

        # P(X=x,Y=y) sums over all feasible values of the common count.
        common_cutoff = min(home_cutoff, away_cutoff) if common > 0.0 else 0
        log_common = poisson.logpmf(
            np.arange(common_cutoff + 1), common
        )

        for count in range(common_cutoff + 1):
            contribution = (
                log_common[count]
                + log_home[: home_cutoff + 1 - count, None]
                + log_away[None, : away_cutoff + 1 - count]
            )
            block = log_joint[count:, count:]
            np.logaddexp(block, contribution, out=block)

        log_retained_mass = float(logsumexp(log_joint))
        if not math.isfinite(log_retained_mass):
            raise ArithmeticError("Failed to construct finite score mass.")

        probabilities = np.exp(log_joint - log_retained_mass)
        normalizer = math.fsum(float(p) for p in probabilities.flat)
        if not math.isfinite(normalizer) or normalizer <= 0.0:
            raise ArithmeticError("Invalid score probability normalization.")

        scores = {
            (int(h), int(a)): float(probabilities[h, a]) / normalizer
            for h in range(home_cutoff + 1)
            for a in range(away_cutoff + 1)
            if probabilities[h, a] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    @classmethod
    def contract_value(cls, probability: float, decimal_odds: float) -> Dict[str, Any]:
        """
        Value a unit-stake exact-score contract without altering prediction.

        The 1.40 minimum is the explicitly requested eligibility rule.
        There is no upper odds cap, edge threshold, or bookmaker blending.
        """
        probability = cls._nonnegative(probability, "probability")
        if probability > 1.0:
            raise ValueError("probability must not exceed one.")

        decimal_odds = cls._positive(decimal_odds, "decimal_odds")
        if decimal_odds < 1.0:
            raise ValueError("Decimal odds must be at least one.")

        expected_profit = probability * decimal_odds - 1.0
        return {
            "probability": probability,
            "decimal_odds": decimal_odds,
            "expected_profit_per_unit": expected_profit,
            "eligible": decimal_odds >= 1.40 and expected_profit > 0.0,
        }