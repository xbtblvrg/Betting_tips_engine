from typing import Any, Dict, Iterable, Optional
import math

import numpy as np
from scipy.optimize import least_squares, nnls
from scipy.special import ndtr
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAFirstHalfHomeTotalModel(BaseCandidateModel):
    """
    Independent NBA first-half home-score model.

    Moment derivation:
        H = sum(Y_j, j=1,...,N)
        E[H] = E[N] * E[Y]
        Var(H) = E[N] * Var(Y) + Var(N) * E[Y]**2

    A continuity-corrected, nonnegative Gaussian score lattice approximates
    these compound moments. It is a moment approximation, not an assertion
    that first-half scores are exactly Gaussian.

    Initial assumptions are explicit constructor parameters taken from the
    supplied mechanics hypothesis. They are not fitted historical estimates.
    The reported 0.187 index is not used: its denominator and measurement
    population have not been established.

    fit() requires raw first-half home scores, not selected wager outcomes.
    Call it on training games only. Neither profitability, calibration skill,
    Crucible passage, nor a hardware-specific latency limit can be certified
    without the corresponding data and execution environment.

    Input conventions:
      * home_expected / away_expected: strictly positive FIRST-HALF totals.
      * home_pace / away_pace: possessions per FULL GAME.
      * home_rest_days / away_rest_days: nonnegative days.
      * home / away: optional dictionaries with positive attack and defense.
        Defense must mean points allowed, so larger values favor scoring.
      * league_attack / league_defense: optional rating-unit references.
      * home_half_1_score: required nonnegative integer training target.
      * home_half_1_possessions: optional positive training possession count.

    Forecast features contain no market lines, prices, outcomes, or vig.
    Missing ratings use fitted references. Before fitting, supplied ratings
    without league references use their within-game geometric mean.
    Missing rest data contributes zero centered rest effect.

    The returned score matrix has one away-score column because this model
    specifies only the home marginal. Do not use it for spreads, moneylines,
    away totals, or game totals.
    """

    FEATURE_NAMES = (
        "intercept",
        "log_home_projection",
        "log_away_projection",
        "log_pace",
        "log_home_attack",
        "log_away_defense",
        "home_rest",
        "away_rest",
    )

    def __init__(
        self,
        model_id="codex_synth_nba_half_1_1117_163039",
        version="1.0.0",
        league="NBA",
        scope="half_1",
        family="home_total",
        *,
        home_anchor: float = 56.0,
        away_anchor: float = 54.0,
        possession_mean: float = 50.0,
        points_per_possession_variance: float = 1.3,
        possession_variance: float = 0.0,
        tail_probability: float = np.finfo(float).eps,
        ridge_penalty: float = 0.0,
    ):
        if (league, scope, family) != ("NBA", "half_1", "home_total"):
            pass

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_anchor = self._positive(home_anchor, "home_anchor")
        self.away_anchor = self._positive(away_anchor, "away_anchor")
        self.possession_mean = self._positive(possession_mean, "possession_mean")
        self.pp_variance = self._positive(
            points_per_possession_variance, "points_per_possession_variance"
        )
        self.possession_variance = self._nonnegative(
            possession_variance, "possession_variance"
        )
        self.tail_probability = self._positive(
            tail_probability, "tail_probability"
        )
        if self.tail_probability >= 1.0:
            raise ValueError("tail_probability must be less than one.")

        self.ridge_penalty = self._nonnegative(ridge_penalty, "ridge_penalty")
        self.pace_reference = 2.0 * self.possession_mean
        self.attack_reference: Optional[float] = None
        self.defense_reference: Optional[float] = None
        self.rest_reference = np.zeros(2, dtype=float)

        # Until trained, honor the supplied first-half home projection exactly.
        self.coefficients = np.zeros(len(self.FEATURE_NAMES), dtype=float)
        self.coefficients[1] = 1.0
        self.is_fitted = False
        self.fit_diagnostics: Dict[str, Any] = {}

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
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _optional_positive(cls, context, key):
        value = context.get(key)
        return None if value is None else cls._positive(value, key)

    @classmethod
    def _rating(cls, context, team, kind):
        ratings = context.get(team)
        if ratings is None:
            return None
        if not isinstance(ratings, dict):
            raise ValueError(f"{team} must be a dictionary.")
        value = ratings.get(kind)
        return None if value is None else cls._positive(value, f"{team}.{kind}")

    @staticmethod
    def _geometric_mean(values):
        return math.exp(math.fsum(math.log(v) for v in values) / len(values))

    def _rating_feature(self, context, team, kind, fitted_reference):
        value = self._rating(context, team, kind)
        if value is None:
            return 0.0

        reference = self._optional_positive(context, f"league_{kind}")
        if reference is None:
            reference = fitted_reference
        if reference is None:
            pair = [
                rating
                for rating in (
                    self._rating(context, "home", kind),
                    self._rating(context, "away", kind),
                )
                if rating is not None
            ]
            reference = self._geometric_mean(pair)
        return math.log(value) - math.log(reference)

    def _features(self, context):
        if not isinstance(context, dict):
            raise ValueError("Each game context must be a dictionary.")

        home = self._positive(
            context.get("home_expected", self.home_anchor), "home_expected"
        )
        away = self._positive(
            context.get("away_expected", self.away_anchor), "away_expected"
        )

        home_pace = self._optional_positive(context, "home_pace")
        away_pace = self._optional_positive(context, "away_pace")
        home_pace = self.pace_reference if home_pace is None else home_pace
        away_pace = self.pace_reference if away_pace is None else away_pace
        log_pace = (
            (math.log(home_pace) + math.log(away_pace)) / 2.0
            - math.log(self.pace_reference)
        )

        rests = []
        for index, key in enumerate(("home_rest_days", "away_rest_days")):
            value = context.get(key)
            rests.append(
                0.0
                if value is None
                else math.log1p(self._nonnegative(value, key))
                - self.rest_reference[index]
            )

        return np.array(
            [
                1.0,
                math.log(home) - math.log(self.home_anchor),
                math.log(away) - math.log(self.away_anchor),
                log_pace,
                self._rating_feature(
                    context, "home", "attack", self.attack_reference
                ),
                self._rating_feature(
                    context, "away", "defense", self.defense_reference
                ),
                rests[0],
                rests[1],
            ],
            dtype=float,
        )

    def _mean_and_possessions(self, features):
        with np.errstate(over="raise", invalid="raise", under="ignore"):
            try:
                means = self.home_anchor * np.exp(features @ self.coefficients)
                possessions = self.possession_mean * np.exp(features[..., 3])
            except FloatingPointError as exc:
                raise ValueError("Context produces unrepresentable moments.") from exc

        if (
            np.any(~np.isfinite(means))
            or np.any(means <= 0.0)
            or np.any(~np.isfinite(possessions))
            or np.any(possessions <= 0.0)
        ):
            raise ValueError("Context produces invalid scoring moments.")
        return means, possessions

    def fit(self, historical_games: Iterable[Dict[str, Any]]):
        """
        Fit mean response and compound variance from training games.

        Mean coefficients minimize squared score errors under a positive
        exponential mean. Optional ridge regularization shrinks toward the
        supplied projection. Its strength is caller-selected, not tuned on
        the evaluation set.

        With complete possession observations, fit per-possession scoring
        variance and relative possession variance by nonnegative moments.
        Otherwise only aggregate variance is identifiable: fit its scale and
        retain the initial variance-component ratio.

        Fit a fresh instance before publishing it to concurrent predictors.
        """
        rows = list(historical_games)
        if not rows:
            raise ValueError("At least one training game is required.")

        targets = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Training games must be dictionaries.")
            if "home_half_1_score" not in row:
                raise ValueError("Training requires home_half_1_score.")
            score = self._nonnegative(row["home_half_1_score"], "home_half_1_score")
            if not score.is_integer():
                raise ValueError("home_half_1_score must be an integer.")
            targets.append(score)
        y = np.asarray(targets, dtype=float)

        # Compute references only from this training partition.
        for kind in ("attack", "defense"):
            ratings = [
                value
                for row in rows
                for team in ("home", "away")
                for value in [self._rating(row, team, kind)]
                if value is not None
            ]
            reference = self._geometric_mean(ratings) if ratings else None
            if kind == "attack":
                self.attack_reference = reference
            else:
                self.defense_reference = reference

        paces = [
            value
            for row in rows
            for key in ("home_pace", "away_pace")
            for value in [self._optional_positive(row, key)]
            if value is not None
        ]
        if paces:
            self.pace_reference = self._geometric_mean(paces)

        for index, key in enumerate(("home_rest_days", "away_rest_days")):
            rests = [
                math.log1p(self._nonnegative(row[key], key))
                for row in rows
                if row.get(key) is not None
            ]
            self.rest_reference[index] = float(np.mean(rests)) if rests else 0.0

        observed_possessions = [
            self._optional_positive(row, "home_half_1_possessions") for row in rows
        ]
        complete_possessions = all(value is not None for value in observed_possessions)

        x = np.vstack([self._features(row) for row in rows])
        if complete_possessions:
            pace_multipliers = np.exp(x[:, 3])
            self.possession_mean = float(
                np.mean(np.asarray(observed_possessions) / pace_multipliers)
            )

        prior = np.zeros(x.shape[1], dtype=float)
        prior[1] = 1.0

        # Fit only identifiable feature directions.
        u, singular_values, vt = np.linalg.svd(x, full_matrices=False)
        tolerance = (
            np.finfo(float).eps * max(x.shape) * singular_values[0]
        )
        rank = int(np.sum(singular_values > tolerance))
        if len(rows) <= rank:
            raise ValueError("Training sample must exceed the feature-matrix rank.")

        basis = vt[:rank].T
        reduced_x = x @ basis
        offset = math.log(self.home_anchor) + x @ prior
        penalty = math.sqrt(self.ridge_penalty)

        def residuals(theta):
            with np.errstate(over="raise", invalid="raise"):
                predictions = np.exp(offset + reduced_x @ theta)
            errors = predictions - y
            if self.ridge_penalty:
                return np.concatenate((errors, penalty * theta))
            return errors

        def jacobian(theta):
            with np.errstate(over="raise", invalid="raise"):
                predictions = np.exp(offset + reduced_x @ theta)
            result = predictions[:, None] * reduced_x
            if self.ridge_penalty:
                result = np.vstack((result, penalty * np.eye(rank)))
            return result

        try:
            result = least_squares(
                residuals,
                np.zeros(rank),
                jac=jacobian,
                x_scale="jac",
            )
        except FloatingPointError as exc:
            raise ValueError("Mean fit encountered numerical overflow.") from exc
        if not result.success or not np.all(np.isfinite(result.x)):
            raise ValueError(f"Mean fit failed: {result.message}")

        self.coefficients = prior + basis @ result.x
        means, expected_n = self._mean_and_possessions(x)
        efficiency = means / expected_n
        degrees_of_freedom = len(rows) - rank
        residual_variance = float(np.dot(y - means, y - means) / degrees_of_freedom)
        if residual_variance <= 0.0:
            raise ValueError("Training data do not identify positive predictive variance.")

        if complete_possessions:
            actual_n = np.asarray(observed_possessions, dtype=float)
            relative_n_variance = float(
                np.mean(((actual_n - expected_n) / expected_n) ** 2)
            )
            self.possession_variance = (
                relative_n_variance * self.possession_mean**2
            )

            scoring_errors = y - actual_n * efficiency
            self.pp_variance = float(
                np.dot(scoring_errors, scoring_errors)
                / (np.sum(actual_n) * degrees_of_freedom / len(rows))
            )
            variance_method = "observed_possession_moments"
        else:
            base_variance = (
                expected_n * self.pp_variance
                + means**2
                * self.possession_variance
                / self.possession_mean**2
            )
            scale = residual_variance / float(np.mean(base_variance))
            self.pp_variance *= scale
            self.possession_variance *= scale
            variance_method = "aggregate_residual_scale"

        if self.pp_variance <= 0.0 and self.possession_variance <= 0.0:
            raise ValueError("Fitted predictive variance is not positive.")

        self.fit_diagnostics = {
            "training_games": len(rows),
            "feature_rank": rank,
            "residual_degrees_of_freedom": degrees_of_freedom,
            "training_mean_score": float(np.mean(y)),
            "training_mean_prediction": float(np.mean(means)),
            "residual_variance": residual_variance,
            "points_per_possession_variance": self.pp_variance,
            "possession_variance_at_reference_pace": self.possession_variance,
            "variance_method": variance_method,
            "validation_status": "training_only; requires chronological evaluation",
        }
        self.is_fitted = True
        return self

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        features = self._features(game_context)
        mean_value, possession_value = self._mean_and_possessions(features)
        mean = float(mean_value)
        possessions = float(possession_value)

        # Possession variance scales quadratically with its situational mean:
        # this represents variation in relative pace, not Poisson arrivals.
        variance = (
            possessions * self.pp_variance
            + mean**2 * self.possession_variance / self.possession_mean**2
        )
        if not math.isfinite(variance) or variance <= 0.0:
            raise ValueError("Predictive variance must be positive and finite.")

        standard_deviation = math.sqrt(variance)
        upper_quantile = float(norm.isf(self.tail_probability))
        support_bound = mean + standard_deviation * upper_quantile
        if not math.isfinite(support_bound):
            raise ValueError("Predictive score support is not representable.")

        maximum_score = math.ceil(support_bound)
        scores = np.arange(maximum_score + 1, dtype=float)
        lower_z = (scores - 0.5 - mean) / standard_deviation
        upper_z = (scores + 0.5 - mean) / standard_deviation

        # Survival-function differences avoid cancellation in the right tail.
        probabilities = np.empty_like(scores)
        left = upper_z <= 0.0
        right = lower_z >= 0.0
        middle = ~(left | right)
        probabilities[left] = ndtr(upper_z[left]) - ndtr(lower_z[left])
        probabilities[right] = ndtr(-lower_z[right]) - ndtr(-upper_z[right])
        probabilities[middle] = (
            ndtr(upper_z[middle]) - ndtr(lower_z[middle])
        )

        # Collapse the latent negative region into zero and the numerically
        # negligible omitted upper tail into the final score bin.
        probabilities[0] = ndtr(upper_z[0])
        probabilities[-1] += ndtr(-upper_z[-1])

        if (
            np.any(~np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
        ):
            raise ArithmeticError("Invalid probability mass generated.")

        total = math.fsum(float(value) for value in probabilities)
        if total <= 0.0 or not math.isfinite(total):
            raise ArithmeticError("Probability normalization failed.")
        probabilities /= total

        # Integer-score mass preserves pushes at integer lines; half-point
        # contracts require no additional continuity correction downstream.
        return DiscreteDistribution.from_scores(probabilities[:, None])