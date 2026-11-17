from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLQuarter4SpreadsModel(BaseCandidateModel):
    """
    Independent, discrete Q4 score model.

    The default marginal means are 6.12 and 5.38. A compound-Poisson
    scoring reference respects scoring increments {2, 3, 6, 7, 8}.
    Its information projection matches the requested score mean and
    variance without importing market prices into the forecast.

    Important identification limits:
      * A compound-Poisson score process with positive increments >= 2
        has variance/mean >= 2. Consequently, the supplied score
        dispersion of 1.040 cannot describe that unmodified process.
        Moment projection resolves this mathematical incompatibility.
      * Aggregate means and dispersion do not identify key-margin masses.
        Uniform scoring-event weights are an explicit maximum-entropy
        initialization, not an empirical estimate of NFL scoring rates.
        Replace them with scoring-event frequencies from training data.
      * Side PnL alone does not identify a causal home-field correction.
        home_edge_multiplier must be estimated on training data; its
        default preserves the supplied empirical means.
      * No ROI, acceptance-count, or hardware latency guarantee follows
        from these aggregate statistics.

    Context contract:
      home_expected / away_expected:
          Nonnegative, pre-Q4 expected Q4 points. When supplied, these
          replace the baseline expectation for that team.
      pace or tempo:
          Positive multiplier relative to the baseline possession rate.
      home / away:
          Optional mappings with attack_exposure and defense_exposure.
          Exposures are positive ratios relative to the training baseline;
          defense_exposure means points allowed, so larger means weaker.
          Unspecified-unit attack/defense ratings are deliberately ignored.
      home_rest_days / away_rest_days:
          Nonnegative days, used through an explicitly trained coefficient.
      q4_start_home_margin:
          Home full-game score minus away full-game score entering Q4.
      wind_speed, temp, is_dome:
          Weather features, used only with explicitly supplied coefficients.

    Scores returned are Q4-only scores, excluding overtime. The downstream
    engine must apply the actual contract's quarter and push rules.
    """

    SCORING_INCREMENTS = np.array([2, 3, 6, 7, 8], dtype=np.int64)
    FEATURE_NAMES = (
        "rest_advantage",
        "trailing_margin",
        "leading_margin",
        "outdoor_wind",
        "outdoor_temperature",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_4_1117_040316",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_4",
        family: str = "spreads",
        *,
        home_mean: float = 6.12,
        away_mean: float = 5.38,
        dispersion_index: float = 1.040,
        home_edge_multiplier: float = 1.0,
        feature_coefficients: Optional[Mapping[str, float]] = None,
        scoring_event_weights: Optional[Sequence[float]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: int = 1_000_000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NFL", "quarter_4", "spreads"):
            raise ValueError("This model supports NFL|quarter_4|spreads only.")

        self.home_mean = self._positive(home_mean, "home_mean")
        self.away_mean = self._positive(away_mean, "away_mean")
        self.dispersion_index = self._positive(
            dispersion_index, "dispersion_index"
        )
        self.home_edge_multiplier = self._finite(
            home_edge_multiplier, "home_edge_multiplier"
        )

        self.feature_coefficients = dict(feature_coefficients or {})
        for name, value in self.feature_coefficients.items():
            if name not in self.FEATURE_NAMES:
                raise ValueError(f"Unsupported feature coefficient: {name}")
            self.feature_coefficients[name] = self._finite(value, name)

        if scoring_event_weights is None:
            weights = np.ones(len(self.SCORING_INCREMENTS), dtype=float)
        else:
            weights = np.asarray(scoring_event_weights, dtype=float)

        if (
            weights.shape != self.SCORING_INCREMENTS.shape
            or not np.all(np.isfinite(weights))
            or np.any(weights <= 0)
        ):
            raise ValueError(
                "scoring_event_weights must contain five positive finite values."
            )
        weights = weights / weights.max()
        self.scoring_event_weights = weights / weights.sum()

        numerical_tolerance = np.finfo(float).eps ** 0.75
        self.tail_tolerance = self._positive(
            numerical_tolerance if tail_tolerance is None else tail_tolerance,
            "tail_tolerance",
        )
        if self.tail_tolerance >= 1:
            raise ValueError("tail_tolerance must be less than one.")
        if isinstance(max_score_cells, bool) or not isinstance(max_score_cells, int):
            raise TypeError("max_score_cells must be an integer.")
        if max_score_cells <= 0:
            raise ValueError("max_score_cells must be positive.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be numeric, not Boolean.")
        result = float(value)
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

    def _feature_values(
        self, context: Mapping[str, Any]
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        home: Dict[str, float] = {}
        away: Dict[str, float] = {}

        if "rest_advantage" in self.feature_coefficients:
            hr = self._nonnegative(context["home_rest_days"], "home_rest_days")
            ar = self._nonnegative(context["away_rest_days"], "away_rest_days")
            rest = math.log1p(hr) - math.log1p(ar)
            home["rest_advantage"] = rest
            away["rest_advantage"] = -rest

        if (
            "trailing_margin" in self.feature_coefficients
            or "leading_margin" in self.feature_coefficients
        ):
            margin = self._finite(
                context["q4_start_home_margin"], "q4_start_home_margin"
            )
            # These positive parts define game states; they are not
            # probability or price clamps.
            home["trailing_margin"] = max(-margin, 0.0)
            home["leading_margin"] = max(margin, 0.0)
            away["trailing_margin"] = max(margin, 0.0)
            away["leading_margin"] = max(-margin, 0.0)

        weather_names = {"outdoor_wind", "outdoor_temperature"}
        if weather_names.intersection(self.feature_coefficients):
            dome = context["is_dome"]
            if not isinstance(dome, (bool, np.bool_)):
                raise TypeError("is_dome must be Boolean.")

            if "outdoor_wind" in self.feature_coefficients:
                wind = (
                    0.0
                    if dome
                    else self._nonnegative(context["wind_speed"], "wind_speed")
                )
                home["outdoor_wind"] = away["outdoor_wind"] = wind

            if "outdoor_temperature" in self.feature_coefficients:
                temperature = (
                    0.0 if dome else self._finite(context["temp"], "temp")
                )
                home["outdoor_temperature"] = temperature
                away["outdoor_temperature"] = temperature

        return home, away

    def _expected_scores(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float]:
        baseline_total = self.home_mean + self.away_mean
        log_ratio = (
            math.log(self.home_mean) - math.log(self.away_mean)
        ) * self.home_edge_multiplier

        # Stable logistic allocation preserves the empirical total while
        # allowing a trained home-contrast coefficient to shrink the edge.
        baseline_home = baseline_total * math.exp(
            -float(np.logaddexp(0.0, -log_ratio))
        )
        baseline_away = baseline_total * math.exp(
            -float(np.logaddexp(0.0, log_ratio))
        )

        means = [
            self._nonnegative(
                context.get("home_expected", baseline_home), "home_expected"
            ),
            self._nonnegative(
                context.get("away_expected", baseline_away), "away_expected"
            ),
        ]
        pace = self._positive(
            context.get("pace", context.get("tempo", 1.0)), "pace"
        )
        teams = [context.get("home", {}), context.get("away", {})]
        if not all(isinstance(team, Mapping) for team in teams):
            raise TypeError("home and away must be mappings.")

        features = self._feature_values(context)
        result = []
        for i in range(2):
            attack = self._positive(
                teams[i].get("attack_exposure", 1.0), "attack_exposure"
            )
            defense = self._positive(
                teams[1 - i].get("defense_exposure", 1.0),
                "defense_exposure",
            )
            if means[i] == 0:
                result.append(0.0)
                continue
            log_mean = (
                math.log(means[i])
                + math.log(pace)
                + math.log(attack)
                + math.log(defense)
            )
            log_mean += sum(
                coefficient * features[i][name]
                for name, coefficient in self.feature_coefficients.items()
            )
            try:
                mean = math.exp(log_mean)
            except OverflowError as exc:
                raise ValueError("Context implies an unrepresentable mean.") from exc
            result.append(self._positive(mean, "adjusted expected score"))
        return result[0], result[1]

    def _support_limit(self, mean: float) -> int:
        if mean == 0:
            return 0
        event_mean = float(
            np.dot(self.SCORING_INCREMENTS, self.scoring_event_weights)
        )
        rate = mean / event_mean
        count_limit = float(poisson.isf(self.tail_tolerance, rate))
        if not math.isfinite(count_limit):
            raise ValueError("Unable to construct finite numerical support.")
        # Include two events even when the mean is tiny so that moment
        # constraints have an interior feasible set.
        return int(self.SCORING_INCREMENTS[-1]) * max(2, int(count_limit))

    def _marginal(self, mean: float, limit: int) -> np.ndarray:
        if mean == 0:
            return np.ones(1, dtype=float)

        variance = mean * self.dispersion_index
        if not math.isfinite(variance):
            raise ValueError("Requested variance is not finite.")

        increments = self.SCORING_INCREMENTS
        weights = self.scoring_event_weights
        rate = mean / float(np.dot(increments, weights))

        # Log-space compound-Poisson recursion:
        # q[n] = rate/n * sum_k k*w[k]*q[n-k].
        log_reference = np.full(limit + 1, -np.inf, dtype=float)
        log_reference[0] = -rate
        log_event_terms = np.log(rate * increments * weights)
        for score in range(1, limit + 1):
            valid = increments <= score
            if np.any(valid):
                log_reference[score] = (
                    logsumexp(
                        log_event_terms[valid]
                        + log_reference[score - increments[valid]]
                    )
                    - math.log(score)
                )

        reachable = np.isfinite(log_reference)
        scores = np.flatnonzero(reachable).astype(float)
        reference = log_reference[reachable]

        lower = scores[scores <= mean]
        upper = scores[scores >= mean]
        if lower.size == 0 or upper.size == 0:
            raise ValueError("Requested mean lies outside numerical support.")
        minimum_variance = (mean - lower[-1]) * (upper[0] - mean)
        maximum_variance = (mean - scores[0]) * (scores[-1] - mean)
        if not minimum_variance < variance < maximum_variance:
            raise ValueError(
                "Requested moments are incompatible with the scoring lattice. "
                "The supplied dispersion may refer to possessions rather than points."
            )

        scale = math.sqrt(variance)
        standardized = (scores - mean) / scale
        sufficient = np.column_stack((standardized, standardized**2))
        target = np.array([0.0, 1.0])

        def evaluate(theta: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
            log_weights = reference + sufficient @ theta
            probability = np.exp(log_weights - logsumexp(log_weights))
            return probability, probability @ sufficient

        def residual(theta: np.ndarray) -> np.ndarray:
            _, moments = evaluate(theta)
            return moments - target

        def jacobian(theta: np.ndarray) -> np.ndarray:
            probability, moments = evaluate(theta)
            centered = sufficient - moments
            return (centered.T * probability) @ centered

        tolerance = math.sqrt(np.finfo(float).eps)
        solution = least_squares(
            residual,
            np.zeros(2),
            jac=jacobian,
            ftol=tolerance,
            xtol=tolerance,
            gtol=tolerance,
        )
        probability, moments = evaluate(solution.x)
        if (
            not solution.success
            or np.linalg.norm(moments - target, ord=np.inf) > tolerance
        ):
            raise ArithmeticError("Discrete moment projection failed to converge.")

        # A positive quadratic tilt can invalidate the reference's tail
        # bound. Reject it instead of silently delivering truncated tails.
        if solution.x[1] > tolerance:
            raise ArithmeticError(
                "Requested moments require a heavier tail than this reference "
                "supports; recalibrate scoring-event weights or dispersion."
            )

        pmf = np.zeros(limit + 1, dtype=float)
        pmf[reachable] = probability
        pmf /= pmf.sum()
        return pmf

    def score_probabilities(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return P(home Q4 points=h, away Q4 points=a), indexed [h, a]."""
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_mean, away_mean = self._expected_scores(game_context)
        home_limit = self._support_limit(home_mean)
        away_limit = self._support_limit(away_mean)
        if (home_limit + 1) * (away_limit + 1) > self.max_score_cells:
            raise ValueError(
                "Context exceeds the configured computation budget; "
                "check that expectations are Q4 points and exposures are ratios."
            )

        home = self._marginal(home_mean, home_limit)
        away = self._marginal(away_mean, away_limit)
        scores = np.multiply.outer(home, away)
        total = float(scores.sum())
        if (
            not np.all(np.isfinite(scores))
            or np.any(scores < 0)
            or not math.isfinite(total)
            or total <= 0
        ):
            raise ArithmeticError("Invalid joint score distribution.")
        scores /= total
        return scores

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )

    def spread_probabilities(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        handicap: float,
    ) -> Dict[str, float]:
        """
        Price a Q4 spread using selected-team Q4 margin + handicap.

        Example: home -3.5 means side='home', handicap=-3.5.
        Exact integer handicaps can push; noninteger handicaps cannot.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")
        handicap = self._finite(handicap, "handicap")
        scores = self.score_probabilities(game_context)
        margin = (
            np.arange(scores.shape[0], dtype=np.int64)[:, None]
            - np.arange(scores.shape[1], dtype=np.int64)[None, :]
        )
        selected_margin = margin if side == "home" else -margin
        adjusted = selected_margin.astype(float) + handicap
        return {
            "win": float(scores[adjusted > 0].sum()),
            "push": float(scores[adjusted == 0].sum()),
            "loss": float(scores[adjusted < 0].sum()),
        }

    def value_spread(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        handicap: float,
        decimal_odds: float,
        push_return: float,
    ) -> Dict[str, Any]:
        """
        Expected net return per unit stake.

        push_return is the contract's gross payout on a push:
        1 for a refunded stake, 0 for a losing binary contract.
        Supply the actual resolution rule explicitly.
        """
        odds = self._positive(decimal_odds, "decimal_odds")
        refund = self._nonnegative(push_return, "push_return")
        probabilities = self.spread_probabilities(
            game_context, side=side, handicap=handicap
        )
        ev = (
            probabilities["win"] * (odds - 1.0)
            + probabilities["push"] * (refund - 1.0)
            - probabilities["loss"]
        )
        return {
            **probabilities,
            "expected_net_return": ev,
            "eligible": odds >= 1.40 and ev > 0.0,
        }