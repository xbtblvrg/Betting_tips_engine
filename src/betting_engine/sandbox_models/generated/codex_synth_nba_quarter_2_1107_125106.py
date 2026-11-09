from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox runners inject DiscreteDistribution into the execution scope.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter2WinnerTotalModel(BaseCandidateModel):
    """
    Joint Q2 score model with a shared, integer-valued possession count.

    Conditional on N, team scores are independent sums of N possessions whose
    point support is {0, 1, 2, 3}. Winner and total probabilities must therefore
    be priced from the SAME returned score distribution.

    Cold-start assumptions:
      * Expected scores: home=28, away=27, supplied empirical anchors.
      * Possessions per TEAM: 24.5, the midpoint of the supplied 24--25
        mechanics hypothesis. This is an explicit, replaceable prior.
      * Without observed possession variability, N uses its minimum-variance
        integer distribution. A negative-binomial count is not identifiable
        from the supplied score means and dispersion index alone.
      * Without a shot mix, maximum-entropy possession probabilities match
        the requested mean and team-score variance/mean index of 1.

    An observed shot mix takes precedence over the score-dispersion prior.
    Inconsistent moments raise an error rather than silently clipping them.

    Context schema:
      home_expected, away_expected:
          Baseline Q2 scoring expectations before explicit adjustments below.
      home, away:
          Optional dictionaries containing attack_multiplier and
          defense_concession_multiplier. All multipliers are normalized to 1;
          larger defense_concession_multiplier means more points conceded.
      pace_multiplier:
          Multiplies BOTH expected scores and possession mean.
      q2_possessions_mean:
          Possessions per team before pace/rest adjustments.
      q2_possessions_variance:
          Absolute variance after all adjustments.
      q2_pace_cv, q2_rest_pace_cv:
          Optional independent latent pace coefficients of variation.
          Their squared values add to the count variance divided by mean^2.
      q2_reference_rest_days, q2_rest_pace_log_coefficient:
          Optional externally fitted rest-to-pace relationship.
      home_rest_days, away_rest_days:
          Required when a nonzero rest coefficient is supplied.
      home_rest_efficiency_log_coefficient,
      away_rest_efficiency_log_coefficient:
          Optional externally fitted rest-to-efficiency relationships.
      home_q2_point_probabilities, away_q2_point_probabilities:
          Four weights for 0, 1, 2, 3 points per possession. Exponential
          tilting preserves their relative structure while matching means.
          They must describe possession outcomes, not field-goal attempt mix.
      home_score_dispersion_index, away_score_dispersion_index:
          Used only when the corresponding possession mix is absent.

    For team possession means m_h, m_a and variances v_h, v_a:
      E[S_i] = E[N] m_i
      Var(S_i) = E[N] v_i + Var(N) m_i^2
      Cov(S_h, S_a) = Var(N) m_h m_a

    The common-count approximation excludes unequal final possession counts
    and possessions worth more than three points. Supply validated Q2 inputs
    before treating this candidate as a calibrated production forecast.
    No historical skill or positive ROI is implied by a cold-start prior.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1107_125106",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "winner_total",
        *,
        prior_home_expected: float = 28.0,
        prior_away_expected: float = 27.0,
        prior_possessions_mean: float = 24.5,
        prior_dispersion_index: float = 1.0,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.prior_home_expected = self._number(
            prior_home_expected, "prior_home_expected", nonnegative=True
        )
        self.prior_away_expected = self._number(
            prior_away_expected, "prior_away_expected", nonnegative=True
        )
        self.prior_possessions_mean = self._number(
            prior_possessions_mean, "prior_possessions_mean", positive=True
        )
        self.prior_dispersion_index = self._number(
            prior_dispersion_index, "prior_dispersion_index", nonnegative=True
        )
        self.tail_tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._number(tail_tolerance, "tail_tolerance", positive=True)
        )
        if self.tail_tolerance >= 1:
            raise ValueError("tail_tolerance must be less than 1.")
        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        positive: bool = False,
        nonnegative: bool = False,
    ) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0:
            raise ValueError(f"{name} must be positive.")
        if nonnegative and result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _integer_variance(mean: float) -> float:
        fraction = mean - math.floor(mean)
        return fraction * (1.0 - fraction)

    @staticmethod
    def _roundoff(*values: float) -> float:
        return math.ulp(max(1.0, *(abs(v) for v in values))) * 32

    @classmethod
    def _maximum_entropy_mix(cls, mean: float, variance: float) -> np.ndarray:
        """Solve the four-point moment problem using one free coordinate."""
        if not 0 <= mean <= 3:
            raise ValueError("Expected possession points must lie in [0, 3].")

        lower_variance = cls._integer_variance(mean)
        upper_variance = mean * (3.0 - mean)
        tolerance = cls._roundoff(mean, variance, upper_variance)

        if variance < lower_variance - tolerance or variance > upper_variance + tolerance:
            raise ValueError(
                "Requested score dispersion is incompatible with the possession "
                "mean/count variance and the {0,1,2,3} scoring support. "
                "Supply observed possession probabilities or consistent moments."
            )

        if abs(variance - lower_variance) <= tolerance:
            result = np.zeros(4)
            low = math.floor(mean)
            fraction = mean - low
            result[low] = 1.0 - fraction
            if fraction:
                result[low + 1] = fraction
            return result

        if abs(variance - upper_variance) <= tolerance:
            return np.array([1.0 - mean / 3.0, 0.0, 0.0, mean / 3.0])

        second_moment = variance + mean * mean
        a = (second_moment - mean) / 2.0
        b = 2.0 * mean - second_moment
        c = 1.0 - 1.5 * mean + second_moment / 2.0

        # With t=p_3, p=(c-t, b+3t, a-3t, t).
        left = max(0.0, -b / 3.0)
        right = min(a / 3.0, c)
        if right < left:
            raise ArithmeticError("Possession moment feasibility lost numerically.")

        def probabilities(t: float) -> np.ndarray:
            return np.array([c - t, b + 3.0 * t, a - 3.0 * t, t])

        def derivative(t: float) -> float:
            if t <= left:
                return -math.inf
            if t >= right:
                return math.inf
            p = probabilities(t)
            if p[1] <= 0 or p[3] <= 0:
                return -math.inf
            if p[0] <= 0 or p[2] <= 0:
                return math.inf
            return (
                math.log(p[3])
                + 3.0 * math.log(p[1])
                - 3.0 * math.log(p[2])
                - math.log(p[0])
            )

        t = (
            left
            if right == left
            else brentq(
                derivative,
                left,
                right,
                xtol=np.finfo(float).tiny,
                rtol=8.0 * np.finfo(float).eps,
            )
        )
        result = probabilities(t)
        if np.any(result < -tolerance):
            raise ArithmeticError("Negative possession probability.")
        # Remove only floating-point cancellation at a feasible boundary.
        result[result < 0] = 0.0
        return result / result.sum()

    @classmethod
    def _tilted_mix(cls, weights: Any, mean: float) -> np.ndarray:
        if isinstance(weights, Mapping):
            weights = [weights.get(k, weights.get(str(k), 0.0)) for k in range(4)]
        try:
            p = np.asarray(weights, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError("Possession weights must contain four numbers.") from exc
        if p.shape != (4,) or not np.all(np.isfinite(p)) or np.any(p < 0):
            raise ValueError("Possession weights must be four finite nonnegative numbers.")
        if not np.any(p > 0):
            raise ValueError("At least one possession weight must be positive.")

        support = np.flatnonzero(p > 0).astype(float)
        if mean < support[0] or mean > support[-1]:
            raise ValueError("Expected possession points lie outside shot-mix support.")

        result = np.zeros(4)
        if mean == support[0] or mean == support[-1]:
            result[int(mean)] = 1.0
            return result

        log_weights = np.log(p[p > 0])

        def tilted(theta: float) -> np.ndarray:
            logits = log_weights + theta * support
            q = np.exp(logits - logits.max())
            return q / q.sum()

        def residual(theta: float) -> float:
            return float(tilted(theta) @ support) - mean

        left, right = -1.0, 1.0
        while residual(left) > 0:
            left *= 2.0
        while residual(right) < 0:
            right *= 2.0
        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).tiny,
            rtol=8.0 * np.finfo(float).eps,
        )
        result[support.astype(int)] = tilted(theta)
        return result

    def _count_probabilities(self, mean: float, variance: float) -> np.ndarray:
        minimum = self._integer_variance(mean)
        tolerance = self._roundoff(mean, variance)
        if variance < minimum - tolerance:
            raise ValueError("Possession variance is below the integer-support minimum.")

        floor_mean = math.floor(mean)
        fraction = mean - floor_mean

        if variance <= minimum + tolerance:
            result = np.zeros(math.ceil(mean) + 1)
            result[floor_mean] = 1.0 - fraction
            if fraction:
                result[floor_mean + 1] = fraction
            return result

        if variance <= mean:
            # Both components have the same mean, so variance mixes linearly.
            poisson_weight = (variance - minimum) / (mean - minimum)
            count_law = poisson(mu=mean)
        else:
            # Gamma-mixed Poisson: Var(N)=mean+mean^2/shape.
            shape = mean * mean / (variance - mean)
            probability = mean / variance
            if not math.isfinite(shape) or shape <= 0 or not 0 < probability < 1:
                raise ValueError("Possession negative-binomial parameters are unresolved.")
            count_law = nbinom(n=shape, p=probability)
            poisson_weight = 1.0

        quantile = count_law.isf(self.tail_tolerance / poisson_weight)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Could not resolve possession-count tail.")
        maximum = max(math.ceil(mean), int(math.ceil(float(quantile))))
        while poisson_weight * float(count_law.sf(maximum)) > self.tail_tolerance:
            maximum += 1

        cells = (3 * maximum + 1) ** 2
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Joint score support requires {cells} cells; "
                f"configured limit is {self.max_score_cells}."
            )

        result = poisson_weight * count_law.pmf(np.arange(maximum + 1))
        if poisson_weight < 1.0:
            result[floor_mean] += (1.0 - poisson_weight) * (1.0 - fraction)
            if fraction:
                result[floor_mean + 1] += (1.0 - poisson_weight) * fraction

        retained = float(result.sum())
        if (
            not math.isfinite(retained)
            or retained <= 0
            or abs(1.0 - retained) > self.tail_tolerance + self._roundoff(retained)
        ):
            raise ArithmeticError("Possession-count probability mass check failed.")
        # Conditioning on the retained count support changes event
        # probabilities by at most the discarded tail probability.
        return result / retained

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        context = game_context

        def number(key: str, default: float, **conditions: Any) -> float:
            return self._number(context.get(key, default), key, **conditions)

        pace = number("pace_multiplier", 1.0, positive=True)
        rest_pace_beta = number("q2_rest_pace_log_coefficient", 0.0)
        home_rest_beta = number("home_rest_efficiency_log_coefficient", 0.0)
        away_rest_beta = number("away_rest_efficiency_log_coefficient", 0.0)

        home_rest_delta = away_rest_delta = 0.0
        if any(beta != 0 for beta in (rest_pace_beta, home_rest_beta, away_rest_beta)):
            required = (
                "q2_reference_rest_days",
                "home_rest_days",
                "away_rest_days",
            )
            if any(key not in context for key in required):
                raise ValueError("Rest adjustments require rest days and a reference.")
            reference = number("q2_reference_rest_days", 0.0, nonnegative=True)
            home_rest_delta = number("home_rest_days", 0.0, nonnegative=True) - reference
            away_rest_delta = number("away_rest_days", 0.0, nonnegative=True) - reference

        try:
            pace *= math.exp(
                rest_pace_beta * (home_rest_delta + away_rest_delta) / 2.0
            )
            home_rest_factor = math.exp(home_rest_beta * home_rest_delta)
            away_rest_factor = math.exp(away_rest_beta * away_rest_delta)
        except OverflowError as exc:
            raise ValueError("Rest adjustment overflowed; inspect fitted coefficients.") from exc

        pace = self._number(pace, "adjusted pace", positive=True)
        home = context.get("home", {})
        away = context.get("away", {})
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise ValueError("home and away features must be mappings.")

        def multiplier(team: Mapping[str, Any], key: str) -> float:
            return self._number(team.get(key, 1.0), key, positive=True)

        expected_home = (
            number("home_expected", self.prior_home_expected, nonnegative=True)
            * multiplier(home, "attack_multiplier")
            * multiplier(away, "defense_concession_multiplier")
            * pace
            * home_rest_factor
        )
        expected_away = (
            number("away_expected", self.prior_away_expected, nonnegative=True)
            * multiplier(away, "attack_multiplier")
            * multiplier(home, "defense_concession_multiplier")
            * pace
            * away_rest_factor
        )
        expected_home = self._number(expected_home, "adjusted home expectation", nonnegative=True)
        expected_away = self._number(expected_away, "adjusted away expectation", nonnegative=True)
        possession_mean = self._number(
            number("q2_possessions_mean", self.prior_possessions_mean, positive=True) * pace,
            "adjusted possession mean",
            positive=True,
        )

        if "q2_possessions_variance" in context:
            if "q2_pace_cv" in context or "q2_rest_pace_cv" in context:
                raise ValueError("Supply either absolute possession variance or pace CVs.")
            possession_variance = number("q2_possessions_variance", 0.0, nonnegative=True)
        else:
            pace_cv = number("q2_pace_cv", 0.0, nonnegative=True)
            rest_cv = number("q2_rest_pace_cv", 0.0, nonnegative=True)
            possession_variance = (
                self._integer_variance(possession_mean)
                + possession_mean**2 * (pace_cv**2 + rest_cv**2)
            )
            possession_variance = self._number(
                possession_variance, "derived possession variance", nonnegative=True
            )

        def team_mix(side: str, expectation: float) -> np.ndarray:
            mean = expectation / possession_mean
            if not 0 <= mean <= 3:
                raise ValueError(f"{side} expectation exceeds possession scoring support.")
            mix_key = f"{side}_q2_point_probabilities"
            if mix_key in context:
                return self._tilted_mix(context[mix_key], mean)
            index = number(
                f"{side}_score_dispersion_index",
                self.prior_dispersion_index,
                nonnegative=True,
            )
            possession_score_variance = (
                index * expectation - possession_variance * mean**2
            ) / possession_mean
            return self._maximum_entropy_mix(mean, possession_score_variance)

        home_mix = team_mix("home", expected_home)
        away_mix = team_mix("away", expected_away)
        counts = self._count_probabilities(possession_mean, possession_variance)

        maximum_count = len(counts) - 1
        dimension = 3 * maximum_count + 1
        if self.max_score_cells is not None and dimension**2 > self.max_score_cells:
            raise MemoryError("Joint score support exceeds max_score_cells.")

        joint = np.zeros((dimension, dimension), dtype=float)
        conditional_home = np.ones(1)
        conditional_away = np.ones(1)

        for count, weight in enumerate(counts):
            if weight > 0:
                size = len(conditional_home)
                # Rowwise accumulation avoids a second full-size outer product.
                for home_score, home_probability in enumerate(conditional_home):
                    if home_probability > 0:
                        joint[home_score, :size] += (
                            weight * home_probability * conditional_away
                        )
            if count < maximum_count:
                conditional_home = np.convolve(conditional_home, home_mix)
                conditional_away = np.convolve(conditional_away, away_mix)

        mass = float(joint.sum())
        if not math.isfinite(mass) or mass <= 0 or np.any(joint < 0):
            raise ArithmeticError("Invalid joint score distribution.")
        if abs(mass - 1.0) > self.tail_tolerance + self._roundoff(mass):
            raise ArithmeticError("Joint score convolution lost probability mass.")
        joint /= mass

        scores: Dict[Tuple[int, int], float] = {
            (int(home_score), int(away_score)): float(joint[home_score, away_score])
            for home_score, away_score in zip(*np.nonzero(joint))
        }
        return DiscreteDistribution.from_scores(scores)