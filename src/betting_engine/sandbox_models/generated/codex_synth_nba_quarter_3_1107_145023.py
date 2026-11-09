from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox runners inject DiscreteDistribution into execution scope.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter3WinnerTotalModel(BaseCandidateModel):
    """
    Joint, integer-valued Q3 score model.

    Cold-start mode uses the supplied Poisson prior and empirical means 28, 27.
    Those means alone do not identify possession variability, scoring-lump
    probabilities, rest effects, or halftime effects. No fabricated league
    priors or bookmaker probabilities are introduced.

    Possession mode is enabled by a `possession_prior` mapping, supplied at
    construction or in game_context:

        {
            "mean_possessions": ...,
            "variance_possessions": ...,
            "home_points_probabilities": [...],
            "away_points_probabilities": [...],
        }

    Array index is points scored per possession. Support can include four or
    more points when the possession definition requires it. Alternatively,
    `possession_probabilities` specifies the complete count PMF, indexed by N;
    mean_possessions and variance_possessions must then be omitted.

    Conditional on shared N, the two teams' possession outcomes are independent.
    Empirical scoring PMFs are exponentially tilted to the requested team means,
    preserving their support and minimizing KL divergence from the supplied PMFs.

    Context:
      home_expected, away_expected:
          Expected Q3 points before any explicitly configured feature adjustment.
      pace:
          Combined expected possessions per team per regulation game.
      pace_multiplier:
          Positive multiplicative adjustment.
      home / away:
          Optional positive attack_exposure and defense_exposure multipliers.
          defense_exposure denotes points conceded, so larger means weaker defense.
      home_rest_days, away_rest_days, halftime_margin:
          Used only with explicitly supplied, previously fitted coefficients.
      feature_effects:
          {
              "centers": {"home_rest_days": ..., ...},
              "pace": {"home_rest_days": coefficient, ...},
              "home_efficiency": {"halftime_abs_margin": coefficient, ...},
              "away_efficiency": {...}
          }

    NBA regulation and quarter durations are structural constants, not fitted
    parameters. Context expectations should already include any effects that
    are not separately configured here.

    The score lattice preserves ties and winner/total dependence. Contract
    settlement, including tie and integer-total push rules, belongs downstream.
    Reported probabilities are conditioned on the numerically retained support;
    omitted mass is bounded by `tail_tolerance`.

    The supplied ROI benchmarks are not evidence of this model's performance.
    Validate with chronological holdouts before deployment.
    """

    EMPIRICAL_HOME_MEAN = 28.0
    EMPIRICAL_AWAY_MEAN = 27.0
    QUARTER_MINUTES = 12.0
    REGULATION_MINUTES = 48.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1107_145023",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "winner_total",
        *,
        possession_prior: Optional[Mapping[str, Any]] = None,
        feature_effects: Optional[Mapping[str, Any]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NBA", "quarter_3", "winner_total"):
            raise ValueError("This model supports only NBA|quarter_3|winner_total.")

        self.possession_prior = (
            None if possession_prior is None else dict(possession_prior)
        )
        self.feature_effects = dict(feature_effects or {})
        self.tail_tolerance = self._number(
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else tail_tolerance,
            "tail_tolerance",
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str, *, positive: bool = False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _pmf(cls, values: Sequence[float], name: str) -> np.ndarray:
        try:
            p = np.asarray(values, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a one-dimensional PMF.") from exc
        if (
            p.ndim != 1
            or p.size == 0
            or not np.all(np.isfinite(p))
            or np.any(p < 0.0)
        ):
            raise ValueError(f"{name} must contain finite nonnegative weights.")
        scale = float(p.max())
        if scale <= 0.0:
            raise ValueError(f"{name} must have positive mass.")
        p = p / scale
        p = p / p.sum()
        return p[: int(np.flatnonzero(p)[-1]) + 1]

    def _check_cells(self, rows: int, columns: int) -> None:
        if self.max_score_cells is not None:
            if rows * columns > self.max_score_cells:
                raise MemoryError(
                    "Requested lattice exceeds max_score_cells; "
                    "no probability or score support was silently clipped."
                )

    def _effect(
        self,
        context: Mapping[str, Any],
        effects: Mapping[str, Any],
        channel: str,
    ) -> float:
        coefficients = effects.get(channel, {})
        centers = effects.get("centers", {})
        if not isinstance(coefficients, Mapping) or not isinstance(centers, Mapping):
            raise ValueError("Feature coefficients and centers must be mappings.")

        terms = []
        for name, coefficient in coefficients.items():
            if name == "halftime_abs_margin":
                if "halftime_margin" not in context:
                    raise ValueError("halftime_margin is required by feature_effects.")
                value = abs(self._number(context["halftime_margin"], name))
            else:
                if name not in context:
                    raise ValueError(f"Required situational feature is missing: {name}")
                value = self._number(context[name], name)

            if name in ("home_rest_days", "away_rest_days") and value < 0.0:
                raise ValueError(f"{name} cannot be negative.")
            center = self._number(centers.get(name, 0.0), f"center[{name}]")
            beta = self._number(coefficient, f"{channel}[{name}]")
            terms.append(beta * (value - center))

        try:
            multiplier = math.exp(math.fsum(terms))
        except OverflowError as exc:
            raise ValueError("Feature effects overflow; inspect fitted coefficients.") from exc
        return self._number(multiplier, f"{channel} multiplier", positive=True)

    def _means_and_tempo(
        self, context: Mapping[str, Any], reference_possessions: Optional[float]
    ) -> Tuple[float, float, float]:
        effects = context.get("feature_effects", self.feature_effects)
        if not isinstance(effects, Mapping):
            raise ValueError("feature_effects must be a mapping.")

        tempo = self._number(
            context.get("pace_multiplier", 1.0), "pace_multiplier", positive=True
        )
        tempo *= self._effect(context, effects, "pace")

        if "pace" in context:
            pace = self._number(context["pace"], "pace", positive=True)
            reference = reference_possessions
            if reference is None:
                if "reference_pace" not in context:
                    raise ValueError(
                        "pace requires possession_prior or an explicit reference_pace."
                    )
                reference = (
                    self._number(
                        context["reference_pace"], "reference_pace", positive=True
                    )
                    * self.QUARTER_MINUTES
                    / self.REGULATION_MINUTES
                )
            tempo *= (
                pace * self.QUARTER_MINUTES / self.REGULATION_MINUTES / reference
            )

        tempo = self._number(tempo, "tempo multiplier", positive=True)
        teams = {}
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, Mapping):
                raise ValueError(f"{side} must be a mapping.")
            teams[side] = team

        means = []
        for side, opponent, baseline in (
            ("home", "away", self.EMPIRICAL_HOME_MEAN),
            ("away", "home", self.EMPIRICAL_AWAY_MEAN),
        ):
            mean = self._number(
                context.get(f"{side}_expected", baseline),
                f"{side}_expected",
                positive=True,
            )
            attack = self._number(
                teams[side].get("attack_exposure", 1.0),
                f"{side}.attack_exposure",
                positive=True,
            )
            defense = self._number(
                teams[opponent].get("defense_exposure", 1.0),
                f"{opponent}.defense_exposure",
                positive=True,
            )
            mean *= tempo * attack * defense
            mean *= self._effect(context, effects, f"{side}_efficiency")
            means.append(self._number(mean, f"adjusted {side} mean", positive=True))
        return means[0], means[1], tempo

    @staticmethod
    def _tilt(p: np.ndarray, target: float) -> np.ndarray:
        support = np.flatnonzero(p)
        points = support.astype(float)
        minimum, maximum = float(points[0]), float(points[-1])
        if not minimum <= target <= maximum:
            raise ValueError(
                f"Required points per possession {target} lies outside "
                f"the supplied scoring support [{minimum}, {maximum}]."
            )

        result = np.zeros_like(p)
        if target == minimum or target == maximum:
            result[int(minimum if target == minimum else maximum)] = 1.0
            return result

        log_p = np.log(p[support])
        centered = points - target

        def probabilities(theta: float) -> np.ndarray:
            logits = log_p + theta * centered
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(centered, probabilities(theta)))

        at_zero = residual(0.0)
        if at_zero == 0.0:
            return p.copy()

        # Bracketing scale is inverse support width, not a model parameter.
        step = 1.0 / (maximum - minimum)
        if at_zero < 0.0:
            lower, upper = 0.0, step
            while residual(upper) < 0.0:
                upper *= 2.0
                if not math.isfinite(upper):
                    raise ArithmeticError("Could not bracket scoring tilt.")
        else:
            lower, upper = -step, 0.0
            while residual(lower) > 0.0:
                lower *= 2.0
                if not math.isfinite(lower):
                    raise ArithmeticError("Could not bracket scoring tilt.")

        theta = brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(float).tiny,
            rtol=4.0 * np.finfo(float).eps,
        )
        result[support] = probabilities(theta)
        return result

    def _finite_count_pmf(self, distribution: Any, budget: float) -> np.ndarray:
        quantile = float(distribution.isf(budget))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Could not determine finite count support.")
        maximum = int(math.ceil(quantile))
        while float(distribution.sf(maximum)) > budget:
            maximum += 1
        self._check_cells(maximum + 1, 1)
        p = np.asarray(distribution.pmf(np.arange(maximum + 1)), dtype=float)
        if not np.all(np.isfinite(p)) or float(p.sum()) <= 0.0:
            raise ArithmeticError("Invalid count probabilities.")
        return p

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        """
        Return matrix P[home_score, away_score].

        This method does not mutate fitted parameters or consume game outcomes.
        Use only information available at the forecast timestamp.
        """
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        prior = game_context.get("possession_prior", self.possession_prior)
        if prior is None:
            home_mean, away_mean, _ = self._means_and_tempo(game_context, None)
            # Each marginal loses at most half the total numerical tail budget.
            home = self._finite_count_pmf(
                poisson(home_mean), self.tail_tolerance / 2.0
            )
            away = self._finite_count_pmf(
                poisson(away_mean), self.tail_tolerance / 2.0
            )
            self._check_cells(home.size, away.size)
            matrix = np.outer(home, away)
        else:
            if not isinstance(prior, Mapping):
                raise ValueError("possession_prior must be a mapping.")

            explicit_counts = "possession_probabilities" in prior
            if explicit_counts:
                if "mean_possessions" in prior or "variance_possessions" in prior:
                    raise ValueError(
                        "An explicit count PMF cannot also specify count moments."
                    )
                count_pmf = self._pmf(
                    prior["possession_probabilities"], "possession_probabilities"
                )
                count_support = np.arange(count_pmf.size, dtype=float)
                reference_mean = float(np.dot(count_support, count_pmf))
                if reference_mean <= 0.0:
                    raise ValueError("Expected possession count must be positive.")
            else:
                reference_mean = self._number(
                    prior["mean_possessions"], "mean_possessions", positive=True
                )
                reference_variance = self._number(
                    prior["variance_possessions"], "variance_possessions"
                )
                if reference_variance < reference_mean:
                    raise ValueError(
                        "Underdispersed possession counts require an explicit "
                        "possession_probabilities PMF."
                    )

            home_mean, away_mean, tempo = self._means_and_tempo(
                game_context, reference_mean
            )

            if explicit_counts:
                if tempo != 1.0:
                    raise ValueError(
                        "For an explicit count PMF, supply a scenario-specific PMF "
                        "and scenario-adjusted expectations; omit tempo adjustments."
                    )
                count_mean = reference_mean
            else:
                count_mean = self._number(
                    reference_mean * tempo, "adjusted possessions", positive=True
                )
                if reference_variance == reference_mean:
                    count_distribution = poisson(count_mean)
                else:
                    # Preserve relative latent-tempo variance across scenarios:
                    # Var(N) = E[N] + E[N]^2 / shape.
                    shape = reference_mean * (
                        reference_mean / (reference_variance - reference_mean)
                    )
                    shape = self._number(shape, "possession shape", positive=True)
                    success_probability = 1.0 / (1.0 + count_mean / shape)
                    if not 0.0 < success_probability < 1.0:
                        raise ArithmeticError("Possession parameters exceed precision.")
                    count_distribution = nbinom(shape, success_probability)
                count_pmf = self._finite_count_pmf(
                    count_distribution, self.tail_tolerance
                )

            home_base = self._pmf(
                prior["home_points_probabilities"], "home_points_probabilities"
            )
            away_base = self._pmf(
                prior["away_points_probabilities"], "away_points_probabilities"
            )
            home_step = self._tilt(home_base, home_mean / count_mean)
            away_step = self._tilt(away_base, away_mean / count_mean)

            maximum_n = count_pmf.size - 1
            home_degree = home_step.size - 1
            away_degree = away_step.size - 1
            rows = maximum_n * home_degree + 1
            columns = maximum_n * away_degree + 1
            self._check_cells(rows, columns)
            matrix = np.zeros((rows, columns), dtype=float)

            home_conditional = np.ones(1, dtype=float)
            away_conditional = np.ones(1, dtype=float)
            for n, weight in enumerate(count_pmf):
                if weight > 0.0:
                    matrix[
                        : home_conditional.size, : away_conditional.size
                    ] += weight * np.outer(home_conditional, away_conditional)
                if n < maximum_n:
                    home_conditional = np.convolve(home_conditional, home_step)
                    away_conditional = np.convolve(away_conditional, away_step)

        retained_mass = float(matrix.sum())
        if (
            not math.isfinite(retained_mass)
            or retained_mass <= 0.0
            or not np.all(np.isfinite(matrix))
            or np.any(matrix < 0.0)
        ):
            raise ArithmeticError("Joint score computation produced invalid mass.")

        return matrix / retained_mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        matrix = self.score_matrix(game_context)
        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in zip(*np.nonzero(matrix))
        }
        return DiscreteDistribution.from_scores(scores)

    @classmethod
    def estimate_possession_prior(
        cls,
        shared_possession_counts: Sequence[int],
        home_scoring_counts: Sequence[float],
        away_scoring_counts: Sequence[float],
    ) -> Dict[str, Any]:
        """
        Estimate an explicit prior from historical possession data.

        shared_possession_counts contains one shared count per historical Q3.
        home_scoring_counts[k] and away_scoring_counts[k] count possessions
        scoring k points, using a consistent possession definition.

        Use league-wide training data preceding the forecast timestamp.
        This estimator preserves empirical underdispersion as well as
        overdispersion; it does not force negative-binomial count variability.
        No smoothing strength is invented for unseen scoring outcomes.
        """
        counts = np.asarray(shared_possession_counts, dtype=float)
        if (
            counts.ndim != 1
            or counts.size == 0
            or not np.all(np.isfinite(counts))
            or np.any(counts < 0.0)
            or np.any(counts != np.floor(counts))
            or np.any(counts >= np.iinfo(np.int64).max)
        ):
            raise ValueError("Shared possession counts must be nonnegative integers.")
        if float(counts.sum()) <= 0.0:
            raise ValueError("Training data must include positive possession counts.")

        observed_counts, frequencies = np.unique(
            counts.astype(np.int64), return_counts=True
        )
        count_pmf = np.zeros(int(observed_counts[-1]) + 1, dtype=float)
        count_pmf[observed_counts] = frequencies / frequencies.sum()

        return {
            "possession_probabilities": count_pmf.tolist(),
            "home_points_probabilities": cls._pmf(
                home_scoring_counts, "home_scoring_counts"
            ).tolist(),
            "away_points_probabilities": cls._pmf(
                away_scoring_counts, "away_scoring_counts"
            ).tolist(),
        }