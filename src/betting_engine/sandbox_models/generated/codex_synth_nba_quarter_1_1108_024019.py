from typing import Any, Dict, Optional, Tuple
import math

import numpy as np
from scipy.special import gammaln
from scipy.stats import binom, nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter1ExactScoreModel(BaseCandidateModel):
    """
    Deterministic, bookmaker-independent first-quarter exact-score model.

    Cold-start identification
    -------------------------
    The supplied means, 28 and 27, and variance/mean prior of one identify
    independent Poisson marginals as a transparent starting assumption.
    They do not identify possession variability, scoring-event composition,
    dependence, rest effects, or an achievable betting ROI.

    Optional evidence-supported extensions
    --------------------------------------
    Without shared pace uncertainty, each marginal may have its own observed
    dispersion index D >= 1:

        Var(S_i) = mu_i * D_i
        k_i = mu_i / (D_i - 1).

    This is obtained by mixing a Poisson scoring intensity with a gamma
    distribution. D == 1 uses the exact Poisson limit.

    With supplied shared_pace_variance = v > 0:

        Z ~ Gamma(shape=1/v, scale=v)
        S_home | Z ~ Poisson(mu_home * Z)
        S_away | Z ~ Poisson(mu_away * Z).

    Consequently:

        Var(S_i) = mu_i + v * mu_i**2
        Cov(S_home, S_away) = v * mu_home * mu_away.

    The exact joint law is evaluated as a negative-binomial total followed
    by a binomial allocation. No quadrature, simulation, or odds enter it.
    Shared pace variance must come from independent domain data; it is not
    invented from the cold-start sample.

    Context contract
    ----------------
    home_expected / away_expected:
        Nonnegative expected FIRST-QUARTER points. By default these are
        already conditioned on situational features.

    expected_features_applied:
        Boolean, default True. Set False only when supplied expected values
        still require the adjustments described below. Missing expected
        values always start at the supplied empirical baseline and receive
        available adjustments.

    home / away:
        Optional dictionaries containing positive attack and defense
        ratings. Ratings are divided by attack_reference / defense_reference,
        provided either inside the team dictionary or at context level.
        Defense is an opponent-points-allowed rating: larger means weaker
        defense. Unreferenced ratings are rejected.

    pace / pace_reference:
        Positive pace and its reference, expressed in identical units.

    home_rest_days / away_rest_days:
        Nonnegative rest. To affect predictions these additionally require
        rest_reference_days and rest_log_mean_coefficient, either prefixed
        with the applicable side or supplied globally. The coefficient is
        an externally estimated log-mean change per rest day.

    dispersion_index:
        Optional common marginal variance/mean index, default one.
        home_dispersion_index / away_dispersion_index override it.
        Indices below one require a different, fitted scoring mechanism
        and are rejected instead of silently clamped.

    shared_pace_variance:
        Optional nonnegative variance of the unit-mean shared pace factor.
        If positive, explicitly supplied marginal dispersion indices must
        agree with its implied moments.

    Numerical truncation
    --------------------
    Returned probabilities condition on a finite retained lattice. Its
    omitted probability is bounded by tail_probability, up to numerical
    precision. This is numerical approximation, not a betting filter.
    max_score_cells is an optional resource budget; exceeding it raises.

    Fit all optional parameters using training-only game data. The reported
    historical ROI and Brier benchmarks are not fitted model parameters.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_1_1108_024019",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_1",
        family: str = "exact_score",
        *,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("NBA", "quarter_1", "exact_score"):
            raise ValueError("This model supports only NBA|quarter_1|exact_score.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_probability must be strictly between zero and one.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")

        self.tail_probability = tolerance
        self.max_score_cells = max_score_cells

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
    def _team(context: Dict[str, Any], side: str) -> Dict[str, Any]:
        team = context.get(side, {})
        if not isinstance(team, dict):
            raise ValueError(f"{side} must be a dictionary.")
        return team

    @classmethod
    def _rating_factor(
        cls,
        context: Dict[str, Any],
        side: str,
        rating: str,
    ) -> float:
        team = cls._team(context, side)
        if rating not in team:
            return 1.0

        reference_name = f"{rating}_reference"
        reference = team.get(reference_name, context.get(reference_name))
        if reference is None:
            raise ValueError(f"{side}.{rating} requires {reference_name}.")

        numerator = cls._positive(team[rating], f"{side}.{rating}")
        denominator = cls._positive(reference, reference_name)
        return math.log(numerator) - math.log(denominator)

    def _mean(self, context: Dict[str, Any], side: str) -> float:
        key = f"{side}_expected"
        baseline = {"home": 28.0, "away": 27.0}[side]
        supplied = key in context
        mean = self._nonnegative(context.get(key, baseline), key)

        already_conditioned = context.get("expected_features_applied", True)
        if not isinstance(already_conditioned, bool):
            raise ValueError("expected_features_applied must be boolean.")
        if supplied and already_conditioned:
            return mean

        opponent = "away" if side == "home" else "home"
        log_adjustment = 0.0
        for team_side, rating in ((side, "attack"), (opponent, "defense")):
            if rating in self._team(context, team_side):
                log_adjustment += self._rating_factor(context, team_side, rating)

        if "pace" in context:
            if "pace_reference" not in context:
                raise ValueError("pace requires pace_reference.")
            log_adjustment += (
                math.log(self._positive(context["pace"], "pace"))
                - math.log(self._positive(context["pace_reference"], "pace_reference"))
            )

        coefficient = context.get(
            f"{side}_rest_log_mean_coefficient",
            context.get("rest_log_mean_coefficient"),
        )
        rest_key = f"{side}_rest_days"
        if rest_key in context:
            rest = self._nonnegative(context[rest_key], rest_key)
            if coefficient is not None:
                reference = context.get(
                    f"{side}_rest_reference_days",
                    context.get("rest_reference_days"),
                )
                if reference is None:
                    raise ValueError("A rest coefficient requires rest_reference_days.")
                reference = self._nonnegative(reference, "rest_reference_days")
                coefficient = self._number(coefficient, "rest_log_mean_coefficient")
                log_adjustment += coefficient * (rest - reference)
        elif coefficient is not None:
            raise ValueError(f"A rest coefficient requires {rest_key}.")

        if not math.isfinite(log_adjustment):
            raise ValueError("Situational adjustments exceed floating-point range.")
        if mean == 0.0:
            return 0.0
        try:
            adjusted = math.exp(math.log(mean) + log_adjustment)
        except OverflowError as exc:
            raise ValueError("Adjusted expected score exceeds numerical range.") from exc
        if not math.isfinite(adjusted) or adjusted == 0.0:
            raise ValueError("Adjusted expected score exceeds numerical range.")
        return adjusted

    def _dispersion(self, context: Dict[str, Any], side: str) -> float:
        value = context.get(
            f"{side}_dispersion_index", context.get("dispersion_index", 1.0)
        )
        result = self._number(value, f"{side}_dispersion_index")
        if result < 1.0:
            raise ValueError("This intensity-mixture model requires dispersion >= 1.")
        return result

    @staticmethod
    def _law(mean: float, dispersion: float) -> Any:
        if mean == 0.0 or dispersion == 1.0:
            return poisson(mean)
        shape = mean / (dispersion - 1.0)
        probability = 1.0 / dispersion
        if not math.isfinite(shape) or shape <= 0.0 or probability <= 0.0:
            raise ValueError("Negative-binomial parameters exceed numerical range.")
        return nbinom(shape, probability)

    @staticmethod
    def _upper_bound(law: Any, tail: float) -> int:
        quantile = float(law.isf(tail))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Unable to determine a finite score-support bound.")
        upper = int(math.ceil(quantile))
        while True:
            remainder = float(law.sf(upper))
            if not math.isfinite(remainder):
                raise ArithmeticError("Nonfinite score-tail probability.")
            if remainder <= tail:
                return upper
            upper += 1

    def _check_budget(self, cells: int) -> None:
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise ValueError(
                f"Required score lattice has {cells} cells; "
                f"configured budget is {self.max_score_cells}."
            )

    def _independent_scores(
        self, means: Tuple[float, float], dispersions: Tuple[float, float]
    ) -> Dict[Tuple[int, int], float]:
        laws = tuple(self._law(mu, d) for mu, d in zip(means, dispersions))
        marginal_tail = self.tail_probability / len(laws)
        bounds = tuple(self._upper_bound(law, marginal_tail) for law in laws)
        self._check_budget((bounds[0] + 1) * (bounds[1] + 1))

        masses = tuple(
            np.asarray(law.pmf(np.arange(bound + 1)), dtype=float)
            for law, bound in zip(laws, bounds)
        )
        return {
            (home, away): float(ph * pa)
            for home, ph in enumerate(masses[0])
            for away, pa in enumerate(masses[1])
        }

    def _shared_pace_scores(
        self, means: Tuple[float, float], variance: float
    ) -> Dict[Tuple[int, int], float]:
        total_mean = math.fsum(means)
        if total_mean == 0.0:
            return {(0, 0): 1.0}

        total_dispersion = 1.0 + total_mean * variance
        if not math.isfinite(total_dispersion):
            raise ValueError("Shared-pace total variance exceeds numerical range.")

        law = self._law(total_mean, total_dispersion)
        upper = self._upper_bound(law, self.tail_probability)
        home_share = means[0] / total_mean
        if means[0] == 0.0 or means[1] == 0.0:
            self._check_budget(upper + 1)
            return {
                ((0, total) if means[0] == 0.0 else (total, 0)):
                    float(law.pmf(total))
                for total in range(upper + 1)
            }

        self._check_budget((upper + 1) * (upper + 2) // 2)
        total_mass = np.asarray(law.pmf(np.arange(upper + 1)), dtype=float)
        scores: Dict[Tuple[int, int], float] = {}
        for total, mass in enumerate(total_mass):
            allocation = binom.pmf(np.arange(total + 1), total, home_share)
            for home, conditional_mass in enumerate(allocation):
                scores[(home, total - home)] = float(mass * conditional_mass)
        return scores

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, dict):
            raise TypeError("game_context must be a dictionary.")

        for key, expected in (
            ("league", "NBA"),
            ("scope", "quarter_1"),
            ("family", "exact_score"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        means = (
            self._mean(game_context, "home"),
            self._mean(game_context, "away"),
        )
        dispersions = (
            self._dispersion(game_context, "home"),
            self._dispersion(game_context, "away"),
        )
        pace_variance = self._nonnegative(
            game_context.get("shared_pace_variance", 0.0),
            "shared_pace_variance",
        )

        if pace_variance > 0.0:
            for side, mean, observed in zip(("home", "away"), means, dispersions):
                supplied = (
                    f"{side}_dispersion_index" in game_context
                    or "dispersion_index" in game_context
                )
                implied = 1.0 + mean * pace_variance
                if not math.isfinite(implied):
                    raise ValueError("Implied marginal dispersion is nonfinite.")
                if supplied and not math.isclose(
                    observed,
                    implied,
                    rel_tol=math.sqrt(np.finfo(float).eps),
                    abs_tol=np.finfo(float).eps,
                ):
                    raise ValueError(
                        f"{side} dispersion {observed} conflicts with shared-pace "
                        f"implied dispersion {implied}."
                    )
            scores = self._shared_pace_scores(means, pace_variance)
        else:
            scores = self._independent_scores(means, dispersions)

        if any(not math.isfinite(p) or p < 0.0 for p in scores.values()):
            raise ArithmeticError("Score lattice contains invalid probability mass.")
        retained = math.fsum(scores.values())
        numerical_tolerance = math.sqrt(np.finfo(float).eps)
        if (
            retained <= 0.0
            or retained > 1.0 + numerical_tolerance
            or retained < 1.0 - self.tail_probability - numerical_tolerance
        ):
            raise ArithmeticError("Score lattice failed retained-mass verification.")

        normalized = {score: mass / retained for score, mass in scores.items()}
        return DiscreteDistribution.from_scores(normalized)