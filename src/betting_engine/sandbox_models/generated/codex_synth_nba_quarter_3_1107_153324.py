from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import expit, logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter3WinnerTotalModel(BaseCandidateModel):
    """
    Joint integer-score model for NBA third-quarter winner/total contracts.

    Cold-start identification:
        Expected scores alone do not identify possession counts, possession
        payout probabilities, score covariance, or halftime adjustment effects.
        Until possession evidence is supplied, the model uses the stated
        independent Poisson prior with means 28 and 27 and dispersion index 1.

    Possession model, when identified:
        N has mean nu and variance nu + alpha * nu**2.
        Conditional on N, each score is an N-fold convolution of its team's
        possession payout distribution. Conditional independence of team
        payouts is an explicit modeling assumption.

        E[S_i] = nu * m_i
        Var(S_i) = nu * v_i + Var(N) * m_i**2
        Cov(S_h, S_a) = Var(N) * m_h * m_a

        Cov(S_h - S_a, S_h + S_a) = Var(S_h) - Var(S_a).
        Thus margin/total correlation is not assumed always positive.

    Context conventions:
        home_expected, away_expected:
            Expected Q3 scores at the reference pace, before the optional
            adjustments described below. Defaults are the supplied 28 and 27.
        pace_multiplier:
            Positive relative pace. Applied to expected points and possessions.
        possession_mean:
            Expected possessions per team at reference pace.
        possession_dispersion:
            Nonnegative alpha above; zero gives Poisson possessions.
        home_possession_probabilities, away_possession_probabilities:
            Probability vectors indexed by integer possession points.
            Supports may extend beyond three points.
        home / away:
            Optional mappings with attack_multiplier and defense_multiplier.
            Defense multiplier means relative points conceded. Raw attack and
            defense ratings are used only with their corresponding
            attack_reference and defense_reference values.
        halftime_margin:
            Home minus away halftime score.
        home_rest_days, away_rest_days:
            Nonnegative rest days.
        prediction_time:
            Numeric event-time timestamp; required after fitting.

    State adjustment:
        log(home_share / away_share)
            = log(base_home / base_away)
              - kappa * halftime_margin + beta_rest * rest_difference.

        This adjustment preserves the combined expected score. Coefficients
        default to zero because the cold-start evidence cannot identify them.

    fit() estimates state coefficients from conditional score allocation and
    possession parameters from directly observed possession data. Predictions
    after fitting must have prediction_time strictly after the training cutoff.

    DiscreteDistribution is supplied by the execution environment. Alternatively,
    pass distribution_factory exposing from_scores({(home, away): probability}).

    Numerical tail truncation is bounded by tail_tolerance. It is not a betting
    filter or a fitted probability clamp. Bookmaker odds and lines are never
    inputs to the predictive distribution.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1107_153324",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "winner_total",
        *,
        possession_mean: Optional[float] = None,
        possession_dispersion: Optional[float] = None,
        home_possession_probabilities: Optional[Sequence[float]] = None,
        away_possession_probabilities: Optional[Sequence[float]] = None,
        state_coefficients: Optional[Sequence[float]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
        distribution_factory: Any = None,
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

        self.possession_mean = (
            None
            if possession_mean is None
            else self._positive(possession_mean, "possession_mean")
        )
        self.possession_dispersion = (
            None
            if possession_dispersion is None
            else self._nonnegative(
                possession_dispersion, "possession_dispersion"
            )
        )
        self.home_possession_probabilities = (
            None
            if home_possession_probabilities is None
            else self._pmf(
                home_possession_probabilities, "home_possession_probabilities"
            )
        )
        self.away_possession_probabilities = (
            None
            if away_possession_probabilities is None
            else self._pmf(
                away_possession_probabilities, "away_possession_probabilities"
            )
        )
        coefficients = (
            np.zeros(2, dtype=float)
            if state_coefficients is None
            else np.asarray(state_coefficients, dtype=float)
        )
        if coefficients.shape != (2,) or not np.all(np.isfinite(coefficients)):
            raise ValueError("state_coefficients must contain two finite values.")
        self.state_coefficients = coefficients.copy()

        self.tail_tolerance = (
            float(np.sqrt(np.finfo(float).eps))
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if 1.0 - self.tail_tolerance / 2.0 == 1.0:
            raise ValueError("tail_tolerance is below floating-point resolution.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or int(max_score_cells) != max_score_cells
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        self.max_score_cells = max_score_cells
        self.distribution_factory = distribution_factory
        self.training_cutoff: Optional[float] = None

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
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @staticmethod
    def _pmf(values: Sequence[float], name: str) -> np.ndarray:
        probabilities = np.asarray(values, dtype=float)
        if (
            probabilities.ndim != 1
            or probabilities.size == 0
            or not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
        ):
            raise ValueError(f"{name} must be a finite nonnegative vector.")
        total = float(probabilities.sum())
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError(f"{name} must have positive finite mass.")
        probabilities = probabilities / total
        last_positive = int(np.flatnonzero(probabilities > 0.0)[-1])
        return probabilities[: last_positive + 1].copy()

    @classmethod
    def _team_multiplier(cls, team: Any, field: str) -> float:
        if team is None:
            return 1.0
        if not isinstance(team, Mapping):
            raise ValueError("home and away must be mappings when supplied.")

        multiplier_key = f"{field}_multiplier"
        reference_key = f"{field}_reference"
        if multiplier_key in team:
            return cls._positive(team[multiplier_key], multiplier_key)
        if field in team and reference_key in team:
            return cls._positive(team[field], field) / cls._positive(
                team[reference_key], reference_key
            )
        return 1.0

    @classmethod
    def _base_means(cls, context: Mapping[str, Any]) -> Tuple[float, float]:
        pace = cls._positive(context.get("pace_multiplier", 1.0), "pace_multiplier")
        home = (
            cls._positive(context.get("home_expected", 28.0), "home_expected")
            * pace
            * cls._team_multiplier(context.get("home"), "attack")
            * cls._team_multiplier(context.get("away"), "defense")
        )
        away = (
            cls._positive(context.get("away_expected", 27.0), "away_expected")
            * pace
            * cls._team_multiplier(context.get("away"), "attack")
            * cls._team_multiplier(context.get("home"), "defense")
        )
        return cls._positive(home, "adjusted home mean"), cls._positive(
            away, "adjusted away mean"
        )

    @classmethod
    def _features(cls, context: Mapping[str, Any]) -> np.ndarray:
        margin = cls._finite(context.get("halftime_margin", 0.0), "halftime_margin")
        has_home_rest = "home_rest_days" in context
        has_away_rest = "away_rest_days" in context
        if has_home_rest != has_away_rest:
            raise ValueError("Supply both teams' rest days, or neither.")
        rest_difference = 0.0
        if has_home_rest:
            rest_difference = cls._nonnegative(
                context["home_rest_days"], "home_rest_days"
            ) - cls._nonnegative(context["away_rest_days"], "away_rest_days")
        return np.asarray([-margin, rest_difference], dtype=float)

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home, away = self._base_means(context)
        linear = (
            math.log(home)
            - math.log(away)
            + float(self._features(context) @ self.state_coefficients)
        )
        if not math.isfinite(linear):
            raise ValueError("State-adjusted log odds are not finite.")
        total = self._positive(home + away, "combined expected score")
        adjusted_home = total * float(expit(linear))
        adjusted_away = total * float(expit(-linear))
        if adjusted_home <= 0.0 or adjusted_away <= 0.0:
            raise FloatingPointError(
                "State adjustment exceeds representable positive score means."
            )
        return adjusted_home, adjusted_away

    @staticmethod
    def _tilt_pmf(probabilities: np.ndarray, target_mean: float) -> np.ndarray:
        """
        Minimum-relative-entropy adjustment subject to the required mean:
            q_k = p_k * exp(theta*k) / sum_j p_j*exp(theta*j).

        Unsupported payouts remain unsupported; infeasible means raise.
        """
        support = np.flatnonzero(probabilities > 0.0)
        low, high = float(support[0]), float(support[-1])
        if not low <= target_mean <= high:
            raise ValueError(
                f"Required possession mean {target_mean} is outside "
                f"the supplied payout support [{low}, {high}]."
            )
        if target_mean == low or target_mean == high:
            result = np.zeros_like(probabilities)
            result[int(target_mean)] = 1.0
            return result

        log_probabilities = np.log(probabilities[support])
        points = support.astype(float)

        def tilted(theta: float) -> np.ndarray:
            weights = log_probabilities + theta * points
            return np.exp(weights - logsumexp(weights))

        def residual(theta: float) -> float:
            return float(tilted(theta) @ points) - target_mean

        current_mean = float(probabilities @ np.arange(probabilities.size))
        if current_mean == target_mean:
            return probabilities.copy()

        left = -1.0
        right = 1.0
        while residual(left) > 0.0:
            left *= 2.0
            if not math.isfinite(left):
                raise FloatingPointError("Cannot bracket possession tilt.")
        while residual(right) < 0.0:
            right *= 2.0
            if not math.isfinite(right):
                raise FloatingPointError("Cannot bracket possession tilt.")

        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
        )
        result = np.zeros_like(probabilities)
        result[support] = tilted(theta)
        return result

    def _check_size(self, rows: int, columns: int) -> None:
        if self.max_score_cells is not None:
            if rows * columns > self.max_score_cells:
                raise MemoryError(
                    f"Requested score lattice has {rows * columns} cells; "
                    f"configured resource budget is {self.max_score_cells}."
                )

    @staticmethod
    def _upper_quantile(distribution: Any, tail: float) -> int:
        upper = float(distribution.isf(tail))
        if not math.isfinite(upper) or upper < 0.0:
            raise FloatingPointError("Cannot construct a finite score support.")
        result = int(math.ceil(upper))
        # Correct any discrete-quantile rounding at the requested tail boundary.
        while float(distribution.sf(result)) > tail:
            result += 1
        return result

    def _poisson_scores(
        self, home_mean: float, away_mean: float
    ) -> Tuple[np.ndarray, float]:
        home_distribution = poisson(home_mean)
        away_distribution = poisson(away_mean)
        home_max = self._upper_quantile(
            home_distribution, self.tail_tolerance / 2.0
        )
        away_max = self._upper_quantile(
            away_distribution, self.tail_tolerance / 2.0
        )
        self._check_size(home_max + 1, away_max + 1)
        home_pmf = home_distribution.pmf(np.arange(home_max + 1))
        away_pmf = away_distribution.pmf(np.arange(away_max + 1))
        scores = np.outer(home_pmf, away_pmf)
        home_tail = float(home_distribution.sf(home_max))
        away_tail = float(away_distribution.sf(away_max))
        omitted_mass = home_tail + away_tail - home_tail * away_tail
        return scores, omitted_mass

    def _possession_scores(
        self,
        home_mean: float,
        away_mean: float,
        possession_mean: float,
        dispersion: float,
        home_probabilities: np.ndarray,
        away_probabilities: np.ndarray,
    ) -> Tuple[np.ndarray, float]:
        home_pmf = self._tilt_pmf(
            home_probabilities, home_mean / possession_mean
        )
        away_pmf = self._tilt_pmf(
            away_probabilities, away_mean / possession_mean
        )
        if dispersion == 0.0:
            count_distribution = poisson(possession_mean)
        else:
            shape = 1.0 / dispersion
            success_probability = 1.0 / (1.0 + dispersion * possession_mean)
            if (
                not math.isfinite(shape)
                or not 0.0 < success_probability < 1.0
            ):
                raise FloatingPointError(
                    "Possession dispersion is outside numerical resolution."
                )
            count_distribution = nbinom(shape, success_probability)

        count_max = self._upper_quantile(
            count_distribution, self.tail_tolerance
        )
        rows = count_max * (home_pmf.size - 1) + 1
        columns = count_max * (away_pmf.size - 1) + 1
        self._check_size(rows, columns)
        scores = np.zeros((rows, columns), dtype=float)
        count_probabilities = count_distribution.pmf(np.arange(count_max + 1))

        conditional_home = np.ones(1, dtype=float)
        conditional_away = np.ones(1, dtype=float)
        for count, weight in enumerate(count_probabilities):
            if weight > 0.0:
                scores[
                    : conditional_home.size, : conditional_away.size
                ] += weight * np.outer(conditional_home, conditional_away)
            if count < count_max:
                conditional_home = np.convolve(conditional_home, home_pmf)
                conditional_away = np.convolve(conditional_away, away_pmf)

        return scores, float(count_distribution.sf(count_max))

    def score_probabilities(
        self, game_context: Mapping[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        """Return the normalized joint Q3 score lattice, including exact ties."""
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        if self.training_cutoff is not None:
            if "prediction_time" not in game_context:
                raise ValueError("A fitted model requires prediction_time.")
            prediction_time = self._finite(
                game_context["prediction_time"], "prediction_time"
            )
            if prediction_time <= self.training_cutoff:
                raise ValueError(
                    "Prediction time must be strictly after the training cutoff."
                )

        home_mean, away_mean = self._means(game_context)
        parameters = (
            game_context.get("possession_mean", self.possession_mean),
            game_context.get("possession_dispersion", self.possession_dispersion),
            game_context.get(
                "home_possession_probabilities",
                self.home_possession_probabilities,
            ),
            game_context.get(
                "away_possession_probabilities",
                self.away_possession_probabilities,
            ),
        )
        supplied = tuple(value is not None for value in parameters)
        if any(supplied) and not all(supplied):
            raise ValueError(
                "Possession modeling requires possession_mean, "
                "possession_dispersion, and both possession probability vectors."
            )

        if all(supplied):
            possession_mean = self._positive(parameters[0], "possession_mean")
            possession_mean *= self._positive(
                game_context.get("pace_multiplier", 1.0), "pace_multiplier"
            )
            possession_mean = self._positive(
                possession_mean, "pace-adjusted possession_mean"
            )
            scores, omitted_mass = self._possession_scores(
                home_mean,
                away_mean,
                possession_mean,
                self._nonnegative(parameters[1], "possession_dispersion"),
                self._pmf(parameters[2], "home_possession_probabilities"),
                self._pmf(parameters[3], "away_possession_probabilities"),
            )
        else:
            scores, omitted_mass = self._poisson_scores(home_mean, away_mean)

        if (
            not np.all(np.isfinite(scores))
            or np.any(scores < 0.0)
            or not math.isfinite(omitted_mass)
        ):
            raise FloatingPointError("Invalid joint score probabilities.")
        if omitted_mass > self.tail_tolerance:
            raise FloatingPointError("Numerical truncation exceeded its budget.")

        mass = float(scores.sum())
        if mass <= 0.0 or not math.isfinite(mass):
            raise FloatingPointError("Joint score distribution has invalid mass.")
        scores /= mass
        home_indices, away_indices = np.nonzero(scores)
        return {
            (int(home), int(away)): float(scores[home, away])
            for home, away in zip(home_indices, away_indices)
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        factory = self.distribution_factory
        if factory is None:
            factory = globals().get("DiscreteDistribution")
        if factory is None or not callable(getattr(factory, "from_scores", None)):
            raise RuntimeError(
                "The host must supply DiscreteDistribution, or pass a "
                "distribution_factory exposing from_scores(score_mapping)."
            )
        return factory.from_scores(self.score_probabilities(game_context))

    def fit(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        training_cutoff: float,
    ) -> "NBAQuarter3WinnerTotalModel":
        """
        Fit exclusively from outcomes available by training_cutoff.

        Each record requires:
            prediction_time, settled_at, home_score, away_score,
            plus pre-Q3 context features.

        Optional mechanics observations must supply all three:
            possessions,
            home_possession_counts,
            away_possession_counts.

        Count vectors contain frequencies by possession points, must each sum
        to possessions, and must reproduce their corresponding quarter score.
        They therefore explicitly require the shared-possession approximation.

        State coefficients use identifiable, unpenalized conditional-binomial
        maximum likelihood. Unidentified or separated fits raise rather than
        silently manufacturing coefficient bounds.

        Possession dispersion uses exposure-adjusted factorial moments:
            alpha = sum((N - nu)**2 - N) / sum(nu**2).
        Projection onto alpha >= 0 is the NB family's parameter boundary.
        Finite-sample estimates and downstream betting performance require
        chronological out-of-sample validation.
        """
        cutoff = self._finite(training_cutoff, "training_cutoff")
        rows = list(records)
        if not rows:
            raise ValueError("fit requires observed quarters.")

        features = []
        offsets = []
        home_scores = []
        total_scores = []
        mechanics = []

        for index, record in enumerate(rows):
            if not isinstance(record, Mapping):
                raise TypeError(f"Record {index} must be a mapping.")
            prediction_time = self._finite(
                record["prediction_time"], "prediction_time"
            )
            settled_at = self._finite(record["settled_at"], "settled_at")
            if not prediction_time < settled_at <= cutoff:
                raise ValueError(
                    "Training records must satisfy "
                    "prediction_time < settled_at <= training_cutoff."
                )

            home_score = self._integer(record["home_score"], "home_score")
            away_score = self._integer(record["away_score"], "away_score")
            base_home, base_away = self._base_means(record)
            features.append(self._features(record))
            offsets.append(math.log(base_home) - math.log(base_away))
            home_scores.append(home_score)
            total_scores.append(home_score + away_score)

            keys = (
                "possessions",
                "home_possession_counts",
                "away_possession_counts",
            )
            present = [key in record for key in keys]
            if any(present) and not all(present):
                raise ValueError("Incomplete possession observations.")
            if all(present):
                count = self._integer(record["possessions"], "possessions")
                count_vectors = []
                for key, observed_score in zip(keys[1:], (home_score, away_score)):
                    counts = np.asarray(record[key], dtype=float)
                    if (
                        counts.ndim != 1
                        or counts.size == 0
                        or not np.all(np.isfinite(counts))
                        or np.any(counts < 0.0)
                        or np.any(counts != np.floor(counts))
                        or float(counts.sum()) != count
                        or float(counts @ np.arange(counts.size)) != observed_score
                    ):
                        raise ValueError(
                            f"{key} must reproduce possessions and observed score."
                        )
                    count_vectors.append(counts.copy())
                pace = self._positive(
                    record.get("pace_multiplier", 1.0), "pace_multiplier"
                )
                mechanics.append((count, pace, *count_vectors))

        x = np.asarray(features, dtype=float)
        offset = np.asarray(offsets, dtype=float)
        home = np.asarray(home_scores, dtype=float)
        total = np.asarray(total_scores, dtype=float)
        informative = total > 0.0
        if not np.any(informative):
            raise ValueError("No scoring observations identify state effects.")
        x = x[informative]
        offset = offset[informative]
        home = home[informative]
        total = total[informative]

        active = np.any(x != 0.0, axis=0)
        coefficients = np.zeros(2, dtype=float)
        if np.any(active):
            active_x = x[:, active]
            scale = np.sqrt(np.mean(active_x**2, axis=0))
            design = active_x / scale
            if (
                design.shape[0] <= design.shape[1]
                or np.linalg.matrix_rank(design) != design.shape[1]
            ):
                raise ValueError("State effects are not identifiable.")

            def objective(beta: np.ndarray) -> Tuple[float, np.ndarray]:
                linear = offset + design @ beta
                value = float(
                    np.sum(
                        home * np.logaddexp(0.0, -linear)
                        + (total - home) * np.logaddexp(0.0, linear)
                    )
                )
                gradient = design.T @ (total * expit(linear) - home)
                return value, gradient

            result = minimize(
                objective,
                np.zeros(design.shape[1], dtype=float),
                jac=True,
                method="BFGS",
            )
            if not result.success or not np.all(np.isfinite(result.x)):
                raise ValueError(f"State calibration failed: {result.message}")

            fitted_share = expit(offset + design @ result.x)
            information = design.T @ (
                (total * fitted_share * (1.0 - fitted_share))[:, None] * design
            )
            if np.linalg.matrix_rank(information) != design.shape[1]:
                raise ValueError("State fit has singular observed information.")
            # A finite MLE requires positive score mass on both sides.
            if np.any(fitted_share == 0.0) or np.any(fitted_share == 1.0):
                raise ValueError("State fit is numerically separated.")
            coefficients[active] = result.x / scale

        possession_mean = self.possession_mean
        dispersion = self.possession_dispersion
        home_pmf = self.home_possession_probabilities
        away_pmf = self.away_possession_probabilities

        if mechanics:
            if len(mechanics) <= 1:
                raise ValueError(
                    "Multiple observed quarters are required to estimate "
                    "possession-count dispersion."
                )
            counts = np.asarray([row[0] for row in mechanics], dtype=float)
            exposures = np.asarray([row[1] for row in mechanics], dtype=float)
            possession_mean = self._positive(
                float(counts.sum() / exposures.sum()), "fitted possession_mean"
            )
            expected_counts = possession_mean * exposures
            estimate = float(
                np.sum((counts - expected_counts) ** 2 - counts)
                / np.sum(expected_counts**2)
            )
            dispersion = max(0.0, self._finite(estimate, "fitted dispersion"))

            pooled = []
            for column in (2, 3):
                width = max(row[column].size for row in mechanics)
                frequencies = np.zeros(width, dtype=float)
                for row in mechanics:
                    frequencies[: row[column].size] += row[column]
                pooled.append(self._pmf(frequencies, "pooled possession counts"))
            home_pmf, away_pmf = pooled

        # Commit only after every estimation and validation step succeeds.
        self.state_coefficients = coefficients
        self.possession_mean = possession_mean
        self.possession_dispersion = dispersion
        self.home_possession_probabilities = home_pmf
        self.away_possession_probabilities = away_pmf
        self.training_cutoff = cutoff
        return self