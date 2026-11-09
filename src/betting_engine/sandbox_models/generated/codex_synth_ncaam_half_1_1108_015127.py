from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import xlogy
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAMFirstHalfThreeWayModel(BaseCandidateModel):
    """
    Independent, possession-based first-half three-way model.

    Conditional on a shared possession count N, each team's possession outcomes
    are independent categorical draws on {0, 1, 2, 3}. The margin distribution
    preserves the integer scoring lattice and the covariance induced by N.

    Cold-start assumptions, all exposed as constructor parameters:
      * Expected scores: 40.0 and 37.5, supplied in the task.
      * Possession-count prior: uniform on integers 33 through 36, supplied
        by the domain hypothesis. This is a prior, not observed calibration.
      * Score variance/mean: 1.0, the supplied unobserved dispersion prior.
      * Possession probabilities maximize entropy subject to the requested
        score mean and variance.

    The scoring moments alone cannot identify possession count or scoring-event
    frequencies. Actual pregame possession estimates and fitted outcome
    probabilities should replace these priors when available. No out-of-sample
    profitability or empirical draw-rate calibration is implied.

    Context contract:
      home_expected, away_expected:
        Final, pregame first-half score expectations. When supplied, these take
        precedence over attack, defense, pace, and rest adjustments.
      possession_mean or pace:
        Expected possessions PER TEAM in the first half.
      possession_variance:
        Variance of the shared possession count. Requires possession_mean/pace.
      possession_count_probabilities:
        Alternative mapping {integer_count: probability}; cannot be combined
        with possession_mean, pace, or possession_variance.
      home, away:
        Optional mappings containing dimensionless attack_ratio and
        defense_ratio. A larger defense_ratio means MORE points allowed.
        Unnormalized attack/defense ratings are deliberately not interpreted.
      home_rest_days, away_rest_days:
        Used only when rest_log_ppp_coefficient is fitted and nonzero.
      rest_log_ppp_coefficient, rest_reference_days:
        Optional fitted rest adjustment and its training reference.
      home_dispersion_index, away_dispersion_index:
        Requested unconditional score variance divided by score mean.
      home_possession_probabilities, away_possession_probabilities:
        Optional fitted distributions over 0, 1, 2, 3 points. Exponentially
        tilted to the requested scoring mean; these replace dispersion priors.
      home_edge_reliability:
        Optional externally estimated reliability in [0, 1]. Shrinks the
        expected home/away scoring ratio toward the supplied league baseline.

    Weather is intentionally unused for indoor basketball. Venue effects belong
    in independently estimated score expectations or fitted efficiency ratios.

    Counts above a computed tail quantile are discarded with a bounded error.
    The FFT grid prevents wraparound for every retained possession count.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_half_1_1108_015127",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "half_1",
        family: str = "three_way",
        *,
        baseline_home_expected: float = 40.0,
        baseline_away_expected: float = 37.5,
        possession_prior_support: Sequence[int] = (33, 34, 35, 36),
        dispersion_index: float = 1.0,
        tail_probability_tolerance: Optional[float] = None,
    ):
        if (league, scope, family) != ("NCAAM", "half_1", "three_way"):
            raise ValueError("This model supports only NCAAM|half_1|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.baseline_home_expected = self._positive(
            baseline_home_expected, "baseline_home_expected"
        )
        self.baseline_away_expected = self._positive(
            baseline_away_expected, "baseline_away_expected"
        )
        self.dispersion_index = self._nonnegative(
            dispersion_index, "dispersion_index"
        )

        support = tuple(self._count(x) for x in possession_prior_support)
        if not support or len(set(support)) != len(support):
            raise ValueError("Possession prior support must be nonempty and unique.")

        prior = np.zeros(max(support) + 1, dtype=float)
        prior[list(support)] = 1.0 / len(support)
        self._prior_count_pmf = prior
        self._prior_count_mean, _ = self._moments(prior)
        if self._prior_count_mean <= 0:
            raise ValueError("The possession prior must have a positive mean.")

        # Numerical precision policy, not a sports probability clamp.
        self.tail_probability_tolerance = (
            float(np.finfo(float).eps ** (2.0 / 3.0))
            if tail_probability_tolerance is None
            else self._positive(
                tail_probability_tolerance, "tail_probability_tolerance"
            )
        )
        if self.tail_probability_tolerance >= 1:
            raise ValueError("tail_probability_tolerance must be below one.")

    @staticmethod
    def _finite(value: Any, name: str) -> float:
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
        result = cls._finite(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _count(cls, value: Any) -> int:
        result = cls._nonnegative(value, "possession count")
        if result != math.floor(result):
            raise ValueError("Possession counts must be integers.")
        return int(result)

    @staticmethod
    def _moments(pmf: np.ndarray) -> Tuple[float, float]:
        counts = np.arange(pmf.size, dtype=float)
        mean = float(counts @ pmf)
        variance = float(((counts - mean) ** 2) @ pmf)
        return mean, variance

    def _count_distribution(
        self, context: Mapping[str, Any]
    ) -> Tuple[np.ndarray, float, float]:
        supplied = context.get("possession_count_probabilities")
        has_mean = "possession_mean" in context or "pace" in context

        if supplied is not None:
            if has_mean or "possession_variance" in context:
                raise ValueError("Supply count probabilities OR count moments.")
            if not isinstance(supplied, Mapping) or not supplied:
                raise ValueError("Count probabilities must be a nonempty mapping.")

            entries = {}
            for count, probability in supplied.items():
                count = self._count(count)
                if count in entries:
                    raise ValueError("Duplicate possession counts.")
                entries[count] = self._nonnegative(probability, "count probability")

            pmf = np.zeros(max(entries) + 1, dtype=float)
            for count, probability in entries.items():
                pmf[count] = probability

            mass = float(pmf.sum())
            if not math.isfinite(mass) or mass <= 0:
                raise ValueError("Count probabilities must have positive finite mass.")
            pmf /= mass
            mean, variance = self._moments(pmf)
            if mean <= 0:
                raise ValueError("Expected possessions must be positive.")
            return pmf, mean, variance

        if not has_mean:
            if "possession_variance" in context:
                raise ValueError("possession_variance requires a possession mean.")
            pmf = self._prior_count_pmf.copy()
            mean, variance = self._moments(pmf)
            return pmf, mean, variance

        mean = self._positive(
            context.get("possession_mean", context.get("pace")), "possession_mean"
        )
        if "possession_mean" in context and "pace" in context:
            if mean != self._positive(context["pace"], "pace"):
                raise ValueError("pace and possession_mean disagree.")

        lower = math.floor(mean)
        fraction = mean - lower
        minimum_variance = fraction * (1.0 - fraction)

        # Without estimated tempo uncertainty, use the least-variable integer
        # count distribution compatible with the supplied mean.
        variance = self._nonnegative(
            context.get("possession_variance", minimum_variance),
            "possession_variance",
        )
        if variance < minimum_variance:
            raise ValueError("Possession variance is infeasible for this integer mean.")

        fixed = np.zeros(lower + 2, dtype=float)
        fixed[lower] = 1.0 - fraction
        fixed[lower + 1] = fraction

        if variance == minimum_variance:
            return fixed, mean, variance

        if variance < mean:
            # A mixture of two distributions with the SAME mean interpolates
            # variance exactly, without changing the expected possession count.
            poisson_weight = (variance - minimum_variance) / (
                mean - minimum_variance
            )
            law = poisson(mu=mean)
        elif variance == mean:
            poisson_weight = 1.0
            law = poisson(mu=mean)
        else:
            # Gamma-Poisson mixing: Var(N) = mean + mean^2 / shape.
            shape = mean * mean / (variance - mean)
            success_probability = mean / variance
            if not math.isfinite(shape) or shape <= 0:
                raise ValueError("Invalid negative-binomial possession parameters.")
            law = nbinom(n=shape, p=success_probability)
            poisson_weight = 1.0

        cutoff_value = float(law.isf(self.tail_probability_tolerance))
        if not math.isfinite(cutoff_value):
            raise ArithmeticError("Unable to bound the possession-count tail.")
        cutoff = max(int(math.ceil(cutoff_value)), fixed.size - 1)
        while float(law.sf(cutoff)) > self.tail_probability_tolerance:
            cutoff += 1

        pmf = poisson_weight * law.pmf(np.arange(cutoff + 1))
        if variance < mean:
            pmf[:fixed.size] += (1.0 - poisson_weight) * fixed

        if not np.all(np.isfinite(pmf)) or float(pmf.sum()) <= 0:
            raise ArithmeticError("Possession-count distribution failed numerically.")
        pmf /= pmf.sum()
        return pmf, mean, variance

    def _expected_score(
        self,
        context: Mapping[str, Any],
        side: str,
        opponent: str,
        baseline: float,
        possession_mean: float,
    ) -> float:
        explicit = context.get(f"{side}_expected")
        if explicit is not None:
            return self._nonnegative(explicit, f"{side}_expected")

        team = context.get(side, {})
        other_team = context.get(opponent, {})
        if not isinstance(team, Mapping) or not isinstance(other_team, Mapping):
            raise ValueError("home and away must be mappings.")

        attack = self._positive(team.get("attack_ratio", 1.0), "attack_ratio")
        defense = self._positive(
            other_team.get("defense_ratio", 1.0), "defense_ratio"
        )
        mean = (
            baseline
            * possession_mean
            / self._prior_count_mean
            * attack
            * defense
        )

        rest_coefficient = self._finite(
            context.get("rest_log_ppp_coefficient", 0.0),
            "rest_log_ppp_coefficient",
        )
        if rest_coefficient != 0:
            rest = self._nonnegative(context[f"{side}_rest_days"], "rest_days")
            reference = self._nonnegative(
                context["rest_reference_days"], "rest_reference_days"
            )
            try:
                mean *= math.exp(
                    rest_coefficient * (math.log1p(rest) - math.log1p(reference))
                )
            except OverflowError as exc:
                raise ValueError("Rest adjustment overflowed.") from exc

        return self._nonnegative(mean, f"derived {side}_expected")

    @staticmethod
    def _moment_probabilities(mean: float, variance: float) -> np.ndarray:
        """
        Maximize entropy on {0,1,2,3} subject to E[X] and Var(X).

        Writing t=P(X=3), the constraints give:
          p2=(E[X²]-E[X])/2 - 3t
          p1=2E[X]-E[X²] + 3t
          p0=1-E[X]+(E[X²]-E[X])/2 - t.
        Thus the optimization has one bounded scalar degree of freedom.
        """
        second = variance + mean * mean
        a = (second - mean) / 2.0
        b = 2.0 * mean - second
        c = 1.0 - mean + a
        lower = max(0.0, -b / 3.0)
        upper = min(a / 3.0, c)

        tolerance = np.finfo(float).eps * (
            1.0 + abs(mean) + abs(second)
        )
        if upper < lower - tolerance:
            raise ValueError(
                "Score moments are incompatible with 0–3 points per possession. "
                "Supply fitted possession probabilities, compatible dispersion, "
                "or a richer scoring model."
            )
        if upper < lower:
            upper = lower

        def probabilities(t: float) -> np.ndarray:
            return np.array([c - t, b + 3.0 * t, a - 3.0 * t, t])

        def objective(t: float) -> float:
            p = probabilities(t)
            if np.any(p < -tolerance):
                return math.inf
            p = np.maximum(p, 0.0)
            return float(np.sum(xlogy(p, p)))

        candidates = [lower, upper]
        if upper > lower:
            result = minimize_scalar(
                objective,
                bounds=(lower, upper),
                method="bounded",
                options={"xatol": np.finfo(float).eps},
            )
            if not result.success:
                raise ArithmeticError("Possession entropy optimization failed.")
            candidates.append(float(result.x))

        p = probabilities(min(candidates, key=objective))
        p = np.maximum(p, 0.0)  # Remove floating-point boundary roundoff only.
        return p / p.sum()

    def _tilted_probabilities(self, supplied: Any, mean: float) -> np.ndarray:
        from scipy.optimize import brentq
        from scipy.special import logsumexp

        p = np.asarray(supplied, dtype=float)
        if (
            p.shape != (4,)
            or not np.all(np.isfinite(p))
            or np.any(p < 0)
            or not math.isfinite(float(p.sum()))
            or float(p.sum()) <= 0
        ):
            raise ValueError("Possession probabilities need four nonnegative values.")

        p = p / p.sum()
        support = np.flatnonzero(p > 0)
        minimum, maximum = int(support[0]), int(support[-1])
        if not minimum <= mean <= maximum:
            raise ValueError("Expected PPP lies outside fitted scoring support.")

        if mean == minimum or mean == maximum:
            result = np.zeros(4)
            result[minimum if mean == minimum else maximum] = 1.0
            return result

        log_p = np.log(p[support])

        def tilted(theta: float) -> np.ndarray:
            logits = log_p + theta * support
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(support @ tilted(theta)) - mean

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
            rtol=4.0 * np.finfo(float).eps,
        )
        result = np.zeros(4)
        result[support] = tilted(theta)
        return result

    def _scoring_distribution(
        self,
        context: Mapping[str, Any],
        side: str,
        score_mean: float,
        count_mean: float,
        count_variance: float,
    ) -> np.ndarray:
        mean = score_mean / count_mean
        if not 0 <= mean <= 3:
            raise ValueError(f"{side} expected points per possession exceed support.")

        supplied = context.get(f"{side}_possession_probabilities")
        if supplied is not None:
            return self._tilted_probabilities(supplied, mean)

        dispersion = self._nonnegative(
            context.get(f"{side}_dispersion_index", self.dispersion_index),
            f"{side}_dispersion_index",
        )

        # Var(S) = E[N] Var(X) + Var(N) E[X]^2.
        variance = (
            dispersion * score_mean - count_variance * mean * mean
        ) / count_mean
        return self._moment_probabilities(mean, variance)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        count_pmf, count_mean, count_variance = self._count_distribution(
            game_context
        )
        home_mean = self._expected_score(
            game_context, "home", "away",
            self.baseline_home_expected, count_mean,
        )
        away_mean = self._expected_score(
            game_context, "away", "home",
            self.baseline_away_expected, count_mean,
        )

        reliability = self._finite(
            game_context.get("home_edge_reliability", 1.0),
            "home_edge_reliability",
        )
        if not 0 <= reliability <= 1:
            raise ValueError("home_edge_reliability must lie in [0, 1].")

        if reliability < 1 and home_mean + away_mean > 0:
            from scipy.special import expit

            if home_mean <= 0 or away_mean <= 0:
                raise ValueError("Log-ratio edge shrinkage requires positive means.")
            baseline_log_ratio = (
                math.log(self.baseline_home_expected)
                - math.log(self.baseline_away_expected)
            )
            observed_log_ratio = math.log(home_mean) - math.log(away_mean)
            shrunk_log_ratio = baseline_log_ratio + reliability * (
                observed_log_ratio - baseline_log_ratio
            )
            total = home_mean + away_mean
            home_mean = total * float(expit(shrunk_log_ratio))
            away_mean = total * float(expit(-shrunk_log_ratio))

        home_p = self._scoring_distribution(
            game_context, "home", home_mean, count_mean, count_variance
        )
        away_p = self._scoring_distribution(
            game_context, "away", away_mean, count_mean, count_variance
        )

        # One paired possession has margin support {-3, ..., 3}.
        paired_margin_pmf = np.convolve(home_p, away_p[::-1])
        maximum_count = count_pmf.size - 1
        maximum_margin = 3 * maximum_count
        required_grid = 2 * maximum_margin + 1
        grid_size = 1 << (required_grid - 1).bit_length()

        frequencies = 2.0 * math.pi * np.arange(grid_size) / grid_size
        paired_cf = np.exp(
            1j * np.outer(frequencies, np.arange(-3, 4))
        ) @ paired_margin_pmf

        # E[z^N] evaluated at z = phi_H(t) * phi_A(-t).
        margin_cf = np.polynomial.polynomial.polyval(paired_cf, count_pmf)
        inverse = np.fft.fft(margin_cf) / grid_size

        numerical_tolerance = (
            np.finfo(float).eps
            * grid_size
            * (maximum_count + 1)
        )
        if (
            not np.all(np.isfinite(inverse))
            or float(np.max(np.abs(inverse.imag))) > numerical_tolerance
            or float(np.min(inverse.real)) < -numerical_tolerance
        ):
            raise ArithmeticError("Margin Fourier inversion failed accuracy checks.")

        margin_pmf = np.maximum(inverse.real, 0.0)
        mass = float(margin_pmf.sum())
        if abs(mass - 1.0) > numerical_tolerance:
            raise ArithmeticError("Margin distribution lost probability mass.")
        margin_pmf /= mass

        home = float(margin_pmf[1:maximum_margin + 1].sum())
        draw = float(margin_pmf[0])
        away = (
            float(margin_pmf[-maximum_margin:].sum())
            if maximum_margin
            else 0.0
        )
        probabilities = np.array([home, draw, away], dtype=float)
        probabilities /= probabilities.sum()

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )