from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some execution environments preload DiscreteDistribution.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class MLBFirstFiveExactScoreModel(BaseCandidateModel):
    """
    Deterministic first-five exact-score candidate.

    Construction
    ------------
    F ~ Gamma(shape=1/v, scale=v), with F=1 when v=0.
    N_t | F ~ Poisson(F * mu_t / b_t), independently across teams.
    Y_tj ~ Geometric(success_probability=1/b_t), supported on {1, 2, ...}.
    R_t = sum(Y_tj for j in range(N_t)).

    Therefore:
        E[R_t] = mu_t
        Var(R_t) / mu_t = 2*b_t - 1 + v*mu_t
        Cov(R_home, R_away) = v*mu_home*mu_away

    Positive geometric cluster sizes represent scoring sequences. Shared frailty
    represents common game conditions. This is a compound distribution, not a
    bivariate negative binomial unless both cluster means equal one.

    Cold-start defaults preserve the supplied unit-dispersion prior exactly:
    b_home=b_away=1, v=0. The asserted 1.6--2.0 dispersion range is a hypothesis,
    not an observed estimate, and is not silently substituted for that prior.

    Calibrated baseline dispersion indices and frailty variance must be supplied
    from training data preceding the prediction date. Cluster means are inferred
    at the baseline means, then held fixed as situational means change.

    Feature contract
    ----------------
    home_expected / away_expected:
        Optional already-adjusted FIRST-FIVE expected runs. When supplied, these
        override the corresponding baseline plus feature regression.

    Otherwise:
        mu_t = baseline_t * exp(sum(beta_tj * (x_j - reference_j)))

    Coefficient keys are dotted context paths, for example:
        "home.attack", "away.defense", "home_rest_days",
        "weather.wind_speed", "weather.is_dome", "weather.temp".

    Coefficients and reference values must be supplied together. No unestimated
    weather, rest, starter, or times-through-order effects are invented.

    Output
    ------
    Tuple-keyed score probabilities, with home runs first. Both teams receive
    five complete offensive innings. Probabilities are conditional on valid
    completion; cancellation and sportsbook-specific grading are external.

    The finite lattice has omitted probability at most tail_probability, up to
    floating-point error. Its retained mass is explicitly normalized because
    DiscreteDistribution requires a finite probability distribution. Tail
    tolerance and resource limits are numerical controls, not scoring clamps.

    No bookmaker prices enter prediction. There is no claim of demonstrated
    calibration, positive ROI, or benchmark attainment without held-out testing.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mlb_innings_1_5_1108_050250",
        version: str = "1.0.0",
        league: str = "MLB",
        scope: str = "innings_1_5",
        family: str = "exact_score",
        *,
        baseline_home: float = 2.53,
        baseline_away: float = 2.31,
        home_dispersion_index: float = 1.0,
        away_dispersion_index: float = 1.0,
        shared_frailty_variance: float = 0.0,
        home_coefficients: Optional[Mapping[str, float]] = None,
        away_coefficients: Optional[Mapping[str, float]] = None,
        feature_reference: Optional[Mapping[str, float]] = None,
        tail_probability: float = 1e-12,
        max_matrix_cells: int = 4_000_000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("MLB", "innings_1_5", "exact_score"):
            raise ValueError("This candidate supports only MLB first-five exact scores.")

        self.baselines = (
            self._number(baseline_home, "baseline_home", positive=True),
            self._number(baseline_away, "baseline_away", positive=True),
        )
        self.frailty_variance = self._number(
            shared_frailty_variance, "shared_frailty_variance", nonnegative=True
        )
        indices = (
            self._number(home_dispersion_index, "home_dispersion_index", positive=True),
            self._number(away_dispersion_index, "away_dispersion_index", positive=True),
        )
        clusters = tuple(
            (index + 1.0 - self.frailty_variance * mean) / 2.0
            for mean, index in zip(self.baselines, indices)
        )
        if any(not math.isfinite(b) or b < 1.0 for b in clusters):
            raise ValueError(
                "Incompatible moments: each dispersion index must be at least "
                "1 + shared_frailty_variance * its baseline mean. "
                "Geometric cluster means cannot be below one."
            )
        self.cluster_means = clusters

        self.tail_probability = self._number(
            tail_probability, "tail_probability", positive=True
        )
        if self.tail_probability >= 1.0:
            raise ValueError("tail_probability must be strictly between zero and one.")
        if (
            isinstance(max_matrix_cells, bool)
            or not isinstance(max_matrix_cells, int)
            or max_matrix_cells <= 0
        ):
            raise ValueError("max_matrix_cells must be a positive integer.")
        self.max_matrix_cells = max_matrix_cells

        self.coefficients = (
            self._validate_mapping(home_coefficients or {}, "home_coefficients"),
            self._validate_mapping(away_coefficients or {}, "away_coefficients"),
        )
        self.feature_reference = self._validate_mapping(
            feature_reference or {}, "feature_reference"
        )
        for coefficients in self.coefficients:
            missing = set(coefficients) - set(self.feature_reference)
            if missing:
                raise ValueError(
                    "Missing training reference values for: " + ", ".join(sorted(missing))
                )

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        positive: bool = False,
        nonnegative: bool = False,
    ) -> float:
        if isinstance(value, (str, bytes, bool)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0:
            raise ValueError(f"{name} must be positive.")
        if nonnegative and result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _validate_mapping(
        cls, values: Mapping[str, float], name: str
    ) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for path, value in values.items():
            if not isinstance(path, str) or not path or any(
                not part for part in path.split(".")
            ):
                raise ValueError(f"{name} contains an invalid feature path.")
            result[path] = cls._number(value, f"{name}[{path!r}]")
        return result

    @classmethod
    def _feature(cls, context: Mapping[str, Any], path: str) -> float:
        value: Any = context
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                raise ValueError(f"Missing required calibrated feature: {path}")
            value = value[part]
        if isinstance(value, (bool, np.bool_)):
            return float(value)
        return cls._number(value, path)

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        means = []
        for side, baseline, coefficients in zip(
            ("home", "away"), self.baselines, self.coefficients
        ):
            key = f"{side}_expected"
            if key in context:
                mean = self._number(context[key], key, nonnegative=True)
            else:
                terms = [
                    coefficient
                    * (self._feature(context, path) - self.feature_reference[path])
                    for path, coefficient in coefficients.items()
                    if coefficient != 0.0
                ]
                try:
                    mean = math.exp(math.log(baseline) + math.fsum(terms))
                except (OverflowError, ValueError) as exc:
                    raise ValueError(f"{side} feature effects are not representable.") from exc
                if not math.isfinite(mean) or mean == 0.0:
                    raise ValueError(f"{side} feature effects overflowed or underflowed.")
            means.append(mean)
        return means[0], means[1]

    def _check_shape(self, rows: int, columns: int) -> None:
        if rows * columns > self.max_matrix_cells:
            raise ValueError(
                f"Required matrix {rows} x {columns} exceeds max_matrix_cells. "
                "Increase the resource budget or explicitly revise tail_probability."
            )

    def _upper_quantile(self, distribution: Any, tolerance: float) -> int:
        quantile = float(distribution.isf(tolerance))
        if not math.isfinite(quantile) or quantile < 0:
            raise ArithmeticError("Unable to determine a finite numerical support.")
        upper = int(math.ceil(quantile))
        if upper + 1 > self.max_matrix_cells:
            raise ValueError("Required support exceeds the configured resource budget.")
        while float(distribution.sf(upper)) > tolerance:
            upper += 1
            if upper + 1 > self.max_matrix_cells:
                raise ValueError("Required support exceeds the configured resource budget.")
        return upper

    def _cluster_count_distribution(self, rate: float) -> Any:
        if self.frailty_variance == 0.0:
            return poisson(mu=rate)
        shape = 1.0 / self.frailty_variance
        probability = 1.0 / (1.0 + self.frailty_variance * rate)
        if (
            not math.isfinite(shape)
            or not 0.0 < probability <= 1.0
            or (rate > 0.0 and probability == 1.0)
        ):
            raise ArithmeticError("Frailty parameters exceed numerical resolution.")
        return nbinom(n=shape, p=probability)

    def _severity_matrix(
        self, count_max: int, cluster_mean: float, tolerance: float
    ) -> np.ndarray:
        if count_max == 0:
            return np.ones((1, 1), dtype=float)
        if cluster_mean == 1.0:
            self._check_shape(count_max + 1, count_max + 1)
            return np.eye(count_max + 1, dtype=float)

        success = 1.0 / cluster_mean
        # The sum of count_max positive geometric variables stochastically
        # dominates every smaller count represented in this matrix.
        extra_max = self._upper_quantile(
            nbinom(n=count_max, p=success), tolerance
        )
        score_max = count_max + extra_max
        self._check_shape(count_max + 1, score_max + 1)
        matrix = np.zeros((count_max + 1, score_max + 1), dtype=float)
        matrix[0, 0] = 1.0
        for count in range(1, count_max + 1):
            extras = np.arange(score_max - count + 1)
            matrix[count, count:] = nbinom.pmf(extras, n=count, p=success)
        return matrix

    def _joint_counts(
        self, rates: Tuple[float, float], maxima: Tuple[int, int]
    ) -> np.ndarray:
        home_rate, away_rate = rates
        nh, na = maxima
        self._check_shape(nh + 1, na + 1)

        if self.frailty_variance == 0.0:
            return np.outer(
                poisson.pmf(np.arange(nh + 1), home_rate),
                poisson.pmf(np.arange(na + 1), away_rate),
            )
        if home_rate == 0.0:
            return self._cluster_count_distribution(away_rate).pmf(
                np.arange(na + 1)
            )[None, :]
        if away_rate == 0.0:
            return self._cluster_count_distribution(home_rate).pmf(
                np.arange(nh + 1)
            )[:, None]

        # Gamma mixing gives negative-multinomial cluster counts. Recurrence
        # avoids subtracting nearly equal large log-gamma values.
        variance = self.frailty_variance
        shape = 1.0 / variance
        total_rate = home_rate + away_rate
        denominator = 1.0 + variance * total_rate
        if not math.isfinite(denominator):
            raise ArithmeticError("Joint intensity exceeds numerical range.")
        qh = variance * home_rate / denominator
        qa = variance * away_rate / denominator
        if qh <= 0.0 or qa <= 0.0:
            raise ArithmeticError("Joint cluster probabilities underflowed.")

        log_counts = np.empty((nh + 1, na + 1), dtype=float)
        log_counts[0, 0] = -math.log1p(variance * total_rate) / variance
        for home_count in range(1, nh + 1):
            log_counts[home_count, 0] = (
                log_counts[home_count - 1, 0]
                + math.log(shape + home_count - 1)
                + math.log(qh)
                - math.log(home_count)
            )
        home_axis = np.arange(nh + 1, dtype=float)
        for away_count in range(1, na + 1):
            log_counts[:, away_count] = (
                log_counts[:, away_count - 1]
                + np.log(shape + home_axis + away_count - 1)
                + math.log(qa)
                - math.log(away_count)
            )
        return np.exp(log_counts)

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        means = self._means(game_context)
        rates = tuple(
            mean / cluster_mean
            for mean, cluster_mean in zip(means, self.cluster_means)
        )
        if any(
            not math.isfinite(rate) or (mean > 0.0 and rate == 0.0)
            for mean, rate in zip(means, rates)
        ):
            raise ArithmeticError("Cluster intensities exceed numerical resolution.")

        # Four truncation events: each team's count tail and severity tail.
        event_tolerance = self.tail_probability / 4.0
        if event_tolerance == 0.0:
            raise ValueError("tail_probability is below floating-point resolution.")

        maxima = tuple(
            0 if rate == 0.0 else self._upper_quantile(
                self._cluster_count_distribution(rate), event_tolerance
            )
            for rate in rates
        )
        severity_home, severity_away = (
            self._severity_matrix(maximum, cluster_mean, event_tolerance)
            for maximum, cluster_mean in zip(maxima, self.cluster_means)
        )
        self._check_shape(severity_home.shape[1], maxima[1] + 1)
        self._check_shape(severity_home.shape[1], severity_away.shape[1])

        counts = self._joint_counts(rates, maxima)
        scores = severity_home.T @ counts @ severity_away
        if not np.all(np.isfinite(scores)) or np.any(scores < 0.0):
            raise ArithmeticError("Invalid numerical probability matrix.")

        retained_mass = math.fsum(float(p) for p in scores.ravel())
        if retained_mass <= 0.0:
            raise ArithmeticError("No representable probability mass.")

        operations_scale = sum(maxima) + sum(scores.shape) + 1
        rounding_allowance = np.finfo(float).eps * operations_scale
        if (
            retained_mass < 1.0 - self.tail_probability - rounding_allowance
            or retained_mass > 1.0 + rounding_allowance
        ):
            raise ArithmeticError(
                "Numerical distribution failed its probability-mass check."
            )

        probabilities = {
            (int(home_score), int(away_score)): float(
                scores[home_score, away_score] / retained_mass
            )
            for home_score, away_score in zip(*np.nonzero(scores))
        }
        return DiscreteDistribution.from_scores(probabilities)