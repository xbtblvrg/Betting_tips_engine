from typing import Any, Dict, Optional, Sequence, Tuple
import math

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAMQuarter1MoneylineModel(BaseCandidateModel):
    """
    Independent, deterministic regulation-window moneyline model.

    Cold-start identification
    -------------------------
    The supplied means identify neither possession counts nor a scoring
    composition. Consequently, without tempo this model uses the supplied
    Poisson prior. It does not pretend that an unobserved dispersion estimate
    or an assumed possession count has been learned.

    With tempo, scoring is modeled on the integer lattice. A shared count N
    represents paired possessions. Conditional on N, the teams' possession
    outcomes are independent. Their distributions are exponentially tilted
    to reproduce the requested scoring means exactly.

        E[D] = E[N] * (E[X_home] - E[X_away])

        Var(D) = E[N] * (Var(X_home) + Var(X_away))
                 + Var(N) * (E[X_home] - E[X_away])**2

    The shared-count covariance matters: summing two independent random-count
    variances would count common pace uncertainty twice.

    Count overdispersion must be supplied. A missing count variance uses the
    Poisson limit of the negative binomial, rather than an invented estimate.
    This count assumption is distinct from a Poisson assumption for points.

    Context contract
    ----------------
    home_expected, away_expected:
        Expected points for this exact market window. Defaults are the supplied
        cold-start anchors, 20.00 and 18.75.

    window_minutes, regulation_minutes:
        Defaults 10 and 40 implement the requested synthetic-window hypothesis.
        The caller must verify the actual provider's market definition.

    tempo or pace:
        Expected possessions per team per regulation game. Providing neither
        selects the aggregate-point model.

    possession_count_variance:
        Variance of the shared count in the target window, at least its mean.
        Alternatively, possession_count_dispersion specifies Var(N)/E[N].

    home / away:
        Optional dictionaries containing dimensionless attack and defense
        multipliers, normalized to league-average 1. A larger defense multiplier
        means more points allowed.

    expected_includes_features:
        Defaults True. Supplied expectations are then used unchanged.
        Missing expectations are adjusted using available team multipliers.
        Set False to apply these multipliers to supplied expectations too.

    rest_scoring_coefficient, rest_pace_coefficient:
        Optional externally estimated log-link coefficients per rest day.
        Both rest-day inputs are required when either coefficient is supplied.
        Rest effects on scoring are centered across the two teams.
        Rest effects on pace also require rest_reference_days.

    home_possession_probabilities, away_possession_probabilities:
        Optional reference probability vectors on possession_point_values.
        Missing vectors use maximum entropy on the declared support.
        Reference structural zeros remain zero during mean matching.

    possession_point_values:
        Defaults to (0, 1, 2, 3), an explicit simplified possession model.
        Supply a richer support and measured reference probabilities when
        multi-free-throw sequences or other higher-point outcomes matter.

    score_dispersion_index:
        Aggregate-point fallback only. Defaults to the supplied prior 1.
        Values above 1 use independent negative-binomial scores with exactly
        matching means and variances. Values below 1 require a different
        identifiable model and are rejected.

    margin_observation_variance, margin_prior_variance,
    full_game_home_edge:
        Optional uncertainty-based margin shrinkage. All three are required
        together. No additional home advantage or shrinkage is invented.

    Output
    ------
    Unconditional probabilities of home win, tie, and away win at the end
    of the specified window. Ties are never silently redistributed.

    No bookmaker odds enter prediction. settlement_value() prices the returned
    distribution separately for either push-on-tie or loss-on-tie contracts.
    The model does not claim demonstrated out-of-sample skill from zero wagers.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_1_1107_233016",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_1",
        family: str = "moneyline",
        *,
        numerical_tolerance: Optional[float] = None,
        max_lattice_points: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else self._number(numerical_tolerance, "numerical_tolerance")
        )
        if not np.finfo(float).eps <= tolerance < 1:
            raise ValueError(
                "numerical_tolerance must be at least machine epsilon and below 1."
            )
        self.numerical_tolerance = tolerance

        if max_lattice_points is not None:
            if (
                isinstance(max_lattice_points, bool)
                or not isinstance(max_lattice_points, (int, np.integer))
                or max_lattice_points < 1
            ):
                raise ValueError("max_lattice_points must be a positive integer.")
            max_lattice_points = int(max_lattice_points)
        self.max_lattice_points = max_lattice_points

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be a finite number, not a boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _exponential(value: float, name: str) -> float:
        try:
            result = math.exp(value)
        except OverflowError as exc:
            raise ValueError(f"{name} produces an overflowing multiplier.") from exc
        if not math.isfinite(result) or result == 0:
            raise ValueError(f"{name} produces an unrepresentable multiplier.")
        return result

    def _check_size(self, points: int) -> None:
        if points < 1 or points > np.iinfo(np.intp).max:
            raise ValueError("Requested lattice cannot be represented.")
        if (
            self.max_lattice_points is not None
            and points > self.max_lattice_points
        ):
            raise ValueError(
                f"Required lattice has {points} points; configured resource "
                f"limit is {self.max_lattice_points}. No probability clamp applied."
            )

    def _means_and_duration(
        self, context: Dict[str, Any]
    ) -> Tuple[float, float, float, float]:
        for key, expected in (
            ("league", self.league),
            ("scope", self.scope),
            ("family", self.family),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} does not match this model's segment.")

        duration = self._positive(
            context.get("window_minutes", 10.0), "window_minutes"
        )
        regulation = self._positive(
            context.get("regulation_minutes", 40.0), "regulation_minutes"
        )
        if duration > regulation:
            raise ValueError("window_minutes exceeds regulation_minutes.")

        includes_features = context.get("expected_includes_features", True)
        if not isinstance(includes_features, (bool, np.bool_)):
            raise ValueError("expected_includes_features must be boolean.")

        teams = {}
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, dict):
                raise ValueError(f"{side} must be a feature dictionary.")
            teams[side] = team

        rest_difference = None
        if "rest_scoring_coefficient" in context:
            coefficient = self._number(
                context["rest_scoring_coefficient"], "rest_scoring_coefficient"
            )
            home_rest = self._nonnegative(
                context["home_rest_days"], "home_rest_days"
            )
            away_rest = self._nonnegative(
                context["away_rest_days"], "away_rest_days"
            )
            rest_difference = coefficient * (home_rest - away_rest) / 2.0

        means = []
        for side, other, anchor, rest_sign in (
            ("home", "away", 20.00, 1.0),
            ("away", "home", 18.75, -1.0),
        ):
            key = f"{side}_expected"
            mean = self._nonnegative(context.get(key, anchor), key)
            if key not in context or not includes_features:
                attack = self._positive(
                    teams[side].get("attack", 1.0), f"{side}.attack"
                )
                defense = self._positive(
                    teams[other].get("defense", 1.0), f"{other}.defense"
                )
                mean *= attack * defense
                if rest_difference is not None:
                    mean *= self._exponential(
                        rest_sign * rest_difference, "rest scoring adjustment"
                    )
            means.append(self._nonnegative(mean, f"adjusted {key}"))

        shrink_keys = (
            "margin_observation_variance",
            "margin_prior_variance",
            "full_game_home_edge",
        )
        supplied = [key in context for key in shrink_keys]
        if any(supplied):
            if not all(supplied):
                raise ValueError(
                    "Margin shrinkage requires observation variance, prior "
                    "variance, and full_game_home_edge together."
                )
            observation_variance = self._nonnegative(
                context[shrink_keys[0]], shrink_keys[0]
            )
            prior_variance = self._nonnegative(
                context[shrink_keys[1]], shrink_keys[1]
            )
            variance_sum = observation_variance + prior_variance
            if not math.isfinite(variance_sum) or variance_sum == 0:
                raise ValueError("Shrinkage variances must have a finite positive sum.")

            prior_margin = (
                self._number(context[shrink_keys[2]], shrink_keys[2])
                * duration
                / regulation
            )
            weight = prior_variance / variance_sum
            margin = weight * (means[0] - means[1]) + (1.0 - weight) * prior_margin
            total = means[0] + means[1]
            if not math.isfinite(total) or abs(margin) > total:
                raise ValueError("Shrunken margin is incompatible with total points.")
            means = [(total + margin) / 2.0, (total - margin) / 2.0]

        return means[0], means[1], duration, regulation

    def _normalize(self, probabilities: Sequence[float]) -> np.ndarray:
        values = np.asarray(probabilities, dtype=float)
        if values.ndim != 1 or not np.all(np.isfinite(values)):
            raise ArithmeticError("Computed probabilities are nonfinite or malformed.")
        if np.any(values < -self.numerical_tolerance):
            raise ArithmeticError("Computed probability has material negative mass.")

        # Remove only numerical roundoff, never truncate a modeled tail.
        values = np.where(values < 0, 0.0, values)
        total = float(values.sum())
        if total <= 0 or not math.isfinite(total):
            raise ArithmeticError("Computed probability mass is invalid.")
        if abs(total - 1.0) > self.numerical_tolerance:
            raise ArithmeticError("Probability mass error exceeds numerical tolerance.")
        return values / total

    def _truncated_pmf(self, distribution: Any, tail_budget: float) -> np.ndarray:
        quantile = float(distribution.isf(tail_budget))
        if not math.isfinite(quantile) or quantile < 0:
            raise ArithmeticError("Unable to determine finite count support.")
        endpoint = int(math.ceil(quantile))

        # Verify the tail directly; quantile calculations can round at boundaries.
        while float(distribution.sf(endpoint)) > tail_budget:
            endpoint += 1
            self._check_size(endpoint + 1)

        self._check_size(endpoint + 1)
        values = np.asarray(
            distribution.pmf(np.arange(endpoint + 1, dtype=float)),
            dtype=float,
        )
        if np.any(~np.isfinite(values)) or np.any(values < 0):
            raise ArithmeticError("Invalid discrete count probabilities.")
        retained = float(values.sum())
        if retained <= 0 or abs(1.0 - retained) > self.numerical_tolerance:
            raise ArithmeticError("Count support failed its probability-mass check.")

        # Conditioning on the retained support changes event probabilities by
        # at most the omitted tail probability, checked above.
        return values / retained

    def _count_distribution(
        self, mean: float, variance: float
    ) -> Any:
        if variance < mean:
            raise ValueError("Negative-binomial count variance must be >= its mean.")
        if variance == mean:
            return stats.poisson(mu=mean)

        excess_ratio = (variance - mean) / mean
        shape = mean / excess_ratio
        probability = 1.0 / (1.0 + excess_ratio)

        if not math.isfinite(shape) or probability == 1.0:
            return stats.poisson(mu=mean)
        if shape <= 0 or probability <= 0:
            raise ValueError("Count moments exceed numerical representation.")
        return stats.nbinom(n=shape, p=probability)

    def _aggregate_probabilities(
        self, home_mean: float, away_mean: float, context: Dict[str, Any]
    ) -> np.ndarray:
        index = self._positive(
            context.get("score_dispersion_index", 1.0),
            "score_dispersion_index",
        )
        if index < 1.0:
            raise ValueError(
                "Underdispersed aggregate scores require an explicit possession "
                "model or another identified score distribution."
            )
        if home_mean == 0 and away_mean == 0:
            return np.array([0.0, 1.0, 0.0])

        if index == 1.0:
            if away_mean == 0:
                draw = math.exp(-home_mean)
                return np.array([-math.expm1(-home_mean), draw, 0.0])
            if home_mean == 0:
                draw = math.exp(-away_mean)
                return np.array([0.0, draw, -math.expm1(-away_mean)])

            distribution = stats.skellam(mu1=home_mean, mu2=away_mean)
            return self._normalize(
                [
                    distribution.sf(0),
                    distribution.pmf(0),
                    distribution.cdf(-1),
                ]
            )

        def score_pmf(mean: float) -> np.ndarray:
            if mean == 0:
                return np.array([1.0])
            variance = mean * index
            if not math.isfinite(variance):
                raise ValueError("Score variance exceeds numerical representation.")
            return self._truncated_pmf(
                self._count_distribution(mean, variance),
                self.numerical_tolerance / 2.0,
            )

        home = score_pmf(home_mean)
        away = score_pmf(away_mean)
        away_cdf = np.cumsum(away)
        home_cdf = np.cumsum(home)

        home_indices = np.arange(1, len(home))
        away_indices = np.arange(1, len(away))
        home_win = float(
            np.dot(home[1:], away_cdf[np.minimum(home_indices - 1, len(away) - 1)])
        )
        away_win = float(
            np.dot(away[1:], home_cdf[np.minimum(away_indices - 1, len(home) - 1)])
        )
        overlap = min(len(home), len(away))
        draw = float(np.dot(home[:overlap], away[:overlap]))
        return self._normalize([home_win, draw, away_win])

    def _tilted_possession_pmf(
        self,
        outcomes: np.ndarray,
        reference: Any,
        target: float,
        name: str,
    ) -> np.ndarray:
        if reference is None:
            weights = np.ones(outcomes.size, dtype=float)
        else:
            weights = np.asarray(reference, dtype=float)
            if weights.shape != outcomes.shape:
                raise ValueError(f"{name} must match possession_point_values.")
            if np.any(~np.isfinite(weights)) or np.any(weights < 0):
                raise ValueError(f"{name} must contain finite nonnegative weights.")

        active = weights > 0
        if not np.any(active):
            raise ValueError(f"{name} must contain positive probability mass.")
        support = outcomes[active].astype(float)
        low, high = float(support.min()), float(support.max())
        if not low <= target <= high:
            raise ValueError(
                f"Required points per possession {target} are outside "
                f"{name}'s supported range [{low}, {high}]."
            )

        result = np.zeros(outcomes.size, dtype=float)
        if target == low or target == high:
            result[outcomes == target] = 1.0
            return result

        log_weights = np.log(weights[active])
        log_weights -= special.logsumexp(log_weights)
        centered = support - target

        def tilted(parameter: float) -> np.ndarray:
            logs = log_weights + parameter * centered
            return np.exp(logs - special.logsumexp(logs))

        def residual(parameter: float) -> float:
            return float(np.dot(tilted(parameter), centered))

        lower, upper = -1.0, 1.0
        while residual(lower) > 0:
            lower *= 2.0
            if not math.isfinite(lower):
                raise ArithmeticError("Cannot bracket possession mean.")
        while residual(upper) < 0:
            upper *= 2.0
            if not math.isfinite(upper):
                raise ArithmeticError("Cannot bracket possession mean.")

        parameter = optimize.brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
            maxiter=np.finfo(float).maxexp,
        )
        result[active] = tilted(parameter)
        error = abs(float(np.dot(result, outcomes)) - target)
        if error > self.numerical_tolerance * max(1.0, abs(target)):
            raise ArithmeticError("Possession mean matching failed.")
        return result

    def _possession_probabilities(
        self,
        home_mean: float,
        away_mean: float,
        duration: float,
        regulation: float,
        context: Dict[str, Any],
    ) -> np.ndarray:
        tempo = self._positive(
            context.get("tempo", context.get("pace")), "tempo"
        )
        count_mean = tempo * duration / regulation

        if "rest_pace_coefficient" in context:
            coefficient = self._number(
                context["rest_pace_coefficient"], "rest_pace_coefficient"
            )
            home_rest = self._nonnegative(
                context["home_rest_days"], "home_rest_days"
            )
            away_rest = self._nonnegative(
                context["away_rest_days"], "away_rest_days"
            )
            reference_rest = self._nonnegative(
                context["rest_reference_days"], "rest_reference_days"
            )
            count_mean *= self._exponential(
                coefficient * ((home_rest + away_rest) / 2.0 - reference_rest),
                "rest pace adjustment",
            )
        count_mean = self._positive(count_mean, "window possession mean")

        if (
            "possession_count_variance" in context
            and "possession_count_dispersion" in context
        ):
            raise ValueError("Specify count variance or count dispersion, not both.")
        if "possession_count_variance" in context:
            count_variance = self._positive(
                context["possession_count_variance"], "possession_count_variance"
            )
        else:
            count_variance = count_mean * self._positive(
                context.get("possession_count_dispersion", 1.0),
                "possession_count_dispersion",
            )
        count_variance = self._positive(count_variance, "window possession variance")

        raw_outcomes = np.asarray(
            context.get("possession_point_values", (0, 1, 2, 3)), dtype=float
        )
        if (
            raw_outcomes.ndim != 1
            or raw_outcomes.size == 0
            or np.any(~np.isfinite(raw_outcomes))
            or np.any(raw_outcomes < 0)
            or np.any(raw_outcomes != np.floor(raw_outcomes))
            or np.any(np.diff(raw_outcomes) <= 0)
            or raw_outcomes[-1] >= np.iinfo(np.intp).max
        ):
            raise ValueError(
                "possession_point_values must be strictly increasing, "
                "nonnegative, representable integers."
            )
        outcomes = raw_outcomes.astype(np.intp)

        home = self._tilted_possession_pmf(
            outcomes,
            context.get("home_possession_probabilities"),
            home_mean / count_mean,
            "home_possession_probabilities",
        )
        away = self._tilted_possession_pmf(
            outcomes,
            context.get("away_possession_probabilities"),
            away_mean / count_mean,
            "away_possession_probabilities",
        )
        counts = self._truncated_pmf(
            self._count_distribution(count_mean, count_variance),
            self.numerical_tolerance,
        )

        # One paired possession has integer difference X_home - X_away.
        width = int(outcomes[-1] - outcomes[0])
        radius = width * (len(counts) - 1)
        length = 2 * radius + 1
        self._check_size(length)

        if radius == 0:
            return np.array([0.0, 1.0, 0.0])

        kernel = np.zeros(length, dtype=float)
        for home_points, home_probability in zip(outcomes, home):
            for away_points, away_probability in zip(outcomes, away):
                difference = int(home_points) - int(away_points)
                kernel[difference % length] += home_probability * away_probability

        characteristic = np.fft.rfft(kernel)
        # Evaluate the finite count PGF by Horner's rule. The lattice is large
        # enough to represent every retained margin without circular aliasing.
        compound = np.zeros_like(characteristic)
        for probability in counts[::-1]:
            compound = compound * characteristic + probability

        mass = self._normalize(np.fft.irfft(compound, n=length))
        return self._normalize(
            [
                float(mass[1 : radius + 1].sum()),
                float(mass[0]),
                float(mass[radius + 1 :].sum()),
            ]
        )

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, dict):
            raise TypeError("game_context must be a dictionary.")

        home_mean, away_mean, duration, regulation = self._means_and_duration(
            game_context
        )
        if "tempo" in game_context or "pace" in game_context:
            if "score_dispersion_index" in game_context:
                raise ValueError(
                    "score_dispersion_index belongs to the aggregate-score "
                    "model. For possessions, supply count moments and scoring "
                    "probabilities; their combination determines score dispersion."
                )
            probabilities = self._possession_probabilities(
                home_mean, away_mean, duration, regulation, game_context
            )
        else:
            possession_keys = (
                "possession_count_variance",
                "possession_count_dispersion",
                "home_possession_probabilities",
                "away_possession_probabilities",
                "possession_point_values",
                "rest_pace_coefficient",
            )
            if any(key in game_context for key in possession_keys):
                raise ValueError("Possession-model inputs require tempo or pace.")
            probabilities = self._aggregate_probabilities(
                home_mean, away_mean, game_context
            )

        home, draw, away = self._normalize(probabilities)
        return DiscreteDistribution(
            probabilities={
                "home": float(home),
                "draw": float(draw),
                "away": float(away),
            }
        )

    @classmethod
    def settlement_value(
        cls,
        probabilities: Dict[str, float],
        side: str,
        decimal_odds: float,
        tie_settlement: str,
    ) -> Dict[str, Any]:
        """
        Expected net profit per unit staked; odds never affect prediction.

        tie_settlement must be 'push' or 'loss'. Overtime-inclusive and other
        tie-resolution contracts require a separate continuation model.

        The 1.40 eligibility floor is the requested execution rule, not a
        forecasting constraint. There is no upper odds cap or edge threshold.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")
        if tie_settlement not in ("push", "loss"):
            raise ValueError("tie_settlement must be explicitly 'push' or 'loss'.")
        odds = cls._positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds must be at least 1.")

        values = {
            key: cls._nonnegative(probabilities[key], f"probabilities[{key}]")
            for key in ("home", "draw", "away")
        }
        total = math.fsum(values.values())
        if abs(total - 1.0) > math.sqrt(np.finfo(float).eps):
            raise ValueError("Outcome probabilities must sum to one.")
        values = {key: value / total for key, value in values.items()}

        win = values[side]
        push = values["draw"] if tie_settlement == "push" else 0.0
        other_side = "away" if side == "home" else "home"
        loss = values[other_side]
        if tie_settlement == "loss":
            loss += values["draw"]

        expected_profit = win * (odds - 1.0) - loss
        resolved = win + loss
        fair_odds = (
            resolved / win
            if win > 0
            else (math.inf if resolved > 0 else None)
        )
        return {
            "win_probability": win,
            "push_probability": push,
            "loss_probability": loss,
            "fair_decimal_odds": fair_odds,
            "expected_profit_per_unit": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }