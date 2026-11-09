from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NHLPeriod1ExactScoreModel(BaseCandidateModel):
    """
    Deterministic NHL first-period joint score model.

    Cold start
    ----------
    Home and away goals are independent Poisson with means 1.02 and 0.92
    at neutral inputs. The supplied marginal means and dispersion index
    do not identify dependence or a low-score correction. Consequently,
    both corrections remain zero until fitted from period-one outcomes.

    Conditional means
    -----------------
    mu_h = home_expected * exp(intercept_h + features_h @ beta)
    mu_a = away_expected * exp(intercept_a + features_a @ beta)

    Features are:
      log(pace),
      log(attack scoring-rate multiplier),
      -log(opponent defensive-strength multiplier),
      log1p(own rest days) - log1p(opponent rest days).

    Pace and normalized attack/defense multipliers have unit elasticities
    initially. Rest has zero effect until estimated. Missing features are
    neutral; missing expected goals use the supplied empirical anchors.

    Expected-goal inputs default to first-period units. Set
    expected_scope="game" only when first_period_share is also supplied,
    either as a scalar or {"home": ..., "away": ...}. An unidentified
    game-to-period share is never silently inferred.

    Nested team inputs use explicit, positive normalized multipliers:
      home/away.attack_multiplier
      home/away.defense_strength_multiplier

    Generic home/away.attack and defense fields are accepted only with
    ratings_are_multipliers=True. Larger defense strength suppresses the
    opponent's scoring. Raw ratings require an upstream fitted conversion.
    Expected goals should precede any supplied situational adjustments to
    avoid counting the same information twice.

    Dependence
    ----------
    H = U + C, A = V + C, with independent Poisson components and
    E[C] = shared_fraction * min(mu_h, mu_a).
    Marginal distributions remain exactly Poisson. Shared scoring affects
    covariance, not marginal dispersion; Var/Mean cannot identify it.

    A fitted four-cell transport adds delta to (0,0) and (1,1), and subtracts
    delta from (1,0) and (0,1). This preserves both complete marginal
    distributions. Its feasible magnitude follows from the actual cell
    probabilities rather than an arbitrary probability clamp.

    Fitting
    -------
    fit() uses penalized joint maximum likelihood. The caller must supply
    a strictly positive penalty_strength, selected without consulting the
    evaluation outcomes. Historical rows must contain pregame features and
    integer home_goals/away_goals for period one. Use chronological held-out
    log loss and calibration to assess improvement; ROI is not guaranteed.

    No bookmaker price participates in the predictive distribution.
    """

    HOME_PRIOR = 1.02
    AWAY_PRIOR = 0.92

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_1_1108_041840",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_1",
        family: str = "exact_score",
        *,
        tail_tolerance: Optional[float] = None,
        max_matrix_cells: Optional[int] = None,
    ):
        if (league, scope, family) != ("NHL", "period_1", "exact_score"):
            raise ValueError("This model supports only NHL|period_1|exact_score.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        if tail_tolerance is None:
            tail_tolerance = float(np.finfo(float).eps ** 0.75)
        self.tail_tolerance = self._number(tail_tolerance, "tail_tolerance")
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if 1.0 - self.tail_tolerance / 2.0 == 1.0:
            raise ValueError("tail_tolerance is below floating-point resolution.")

        if max_matrix_cells is not None:
            max_matrix_cells = self._integer(
                max_matrix_cells, "max_matrix_cells"
            )
            if max_matrix_cells < 4:
                raise ValueError("max_matrix_cells must be at least four.")
        self.max_matrix_cells = max_matrix_cells

        # Two intercept offsets, four elasticities, common fraction, transport.
        self._prior = np.asarray(
            [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0], dtype=float
        )
        self._parameters = self._prior.copy()
        self.fit_diagnostics_: Optional[Dict[str, Any]] = None

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
    def _integer(cls, value: Any, name: str) -> int:
        result = cls._number(value, name)
        if result < 0.0 or not result.is_integer():
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(result)

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _team_features(
        cls, context: Mapping[str, Any], side: str
    ) -> Tuple[float, float]:
        team = context.get(side, {})
        if team is None:
            team = {}
        if not isinstance(team, Mapping):
            # A team name or identifier contains no numerical rating information.
            team = {}

        generic = context.get("ratings_are_multipliers", False)
        if not isinstance(generic, (bool, np.bool_)):
            raise ValueError("ratings_are_multipliers must be boolean.")

        attack = team.get(
            "attack_multiplier", team.get("attack", 1.0) if generic else 1.0
        )
        defense = team.get(
            "defense_strength_multiplier",
            team.get("defense", 1.0) if generic else 1.0,
        )
        return (
            cls._positive(attack, f"{side}.attack_multiplier"),
            cls._positive(defense, f"{side}.defense_strength_multiplier"),
        )

    def _prepare(
        self, context: Mapping[str, Any]
    ) -> Tuple[np.ndarray, np.ndarray]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        for name, expected in (
            ("league", "NHL"),
            ("scope", "period_1"),
            ("family", "exact_score"),
        ):
            if name in context and context[name] != expected:
                raise ValueError(f"{name} must be {expected!r}.")

        # This architecture intentionally does not claim to fit overdispersion.
        if "dispersion_index" in context:
            dispersion = self._positive(
                context["dispersion_index"], "dispersion_index"
            )
            if dispersion != 1.0:
                raise ValueError(
                    "This model requires Poisson marginal dispersion. "
                    "A different observed dispersion needs a separately "
                    "estimated marginal-count model."
                )

        expected_scope = context.get("expected_scope", "period_1")
        if expected_scope not in ("period_1", "game"):
            raise ValueError("expected_scope must be 'period_1' or 'game'.")

        means = np.empty(2, dtype=float)
        for index, (side, anchor) in enumerate(
            (("home", self.HOME_PRIOR), ("away", self.AWAY_PRIOR))
        ):
            key = f"{side}_expected"
            if expected_scope == "game" and key not in context:
                raise ValueError(f"{key} is required for game-level expectations.")
            means[index] = self._number(context.get(key, anchor), key)
            if means[index] < 0.0:
                raise ValueError(f"{key} cannot be negative.")

            if expected_scope == "game":
                if "first_period_share" not in context:
                    raise ValueError(
                        "Game-level expectations require first_period_share."
                    )
                shares = context["first_period_share"]
                if isinstance(shares, Mapping):
                    if side not in shares:
                        raise ValueError(f"first_period_share lacks {side!r}.")
                    shares = shares[side]
                share = self._positive(shares, f"first_period_share.{side}")
                if share > 1.0:
                    raise ValueError("A first-period share cannot exceed one.")
                means[index] *= share

        pace = self._positive(context.get("pace_multiplier", 1.0), "pace_multiplier")
        home_attack, home_defense = self._team_features(context, "home")
        away_attack, away_defense = self._team_features(context, "away")

        # Missing rest on either side means the contrast is unavailable.
        rest = []
        for side in ("home", "away"):
            key = f"{side}_rest_days"
            value = context.get(key)
            if value is None:
                rest.append(None)
            else:
                value = self._number(value, key)
                if value < 0.0:
                    raise ValueError(f"{key} cannot be negative.")
                rest.append(value)
        rest_difference = (
            math.log1p(rest[0]) - math.log1p(rest[1])
            if all(value is not None for value in rest)
            else 0.0
        )

        features = np.asarray(
            [
                [
                    math.log(pace),
                    math.log(home_attack),
                    -math.log(away_defense),
                    rest_difference,
                ],
                [
                    math.log(pace),
                    math.log(away_attack),
                    -math.log(home_defense),
                    -rest_difference,
                ],
            ],
            dtype=float,
        )
        return means, features

    @staticmethod
    def _means(
        prepared: Tuple[np.ndarray, np.ndarray], parameters: np.ndarray
    ) -> Tuple[float, float]:
        baseline, features = prepared
        shifts = parameters[:2] + features @ parameters[2:6]
        means = []
        for base, shift in zip(baseline, shifts):
            if base == 0.0:
                means.append(0.0)
                continue
            try:
                mean = math.exp(math.log(float(base)) + float(shift))
            except OverflowError as exc:
                raise ValueError("Conditional scoring rate overflowed.") from exc
            if not math.isfinite(mean) or mean == 0.0:
                raise ValueError("Conditional scoring rate exceeds numerical range.")
            means.append(mean)
        return means[0], means[1]

    @staticmethod
    def _log_poisson(counts: np.ndarray, rate: float) -> np.ndarray:
        if rate == 0.0:
            return np.where(counts == 0, 0.0, -np.inf)
        return counts * math.log(rate) - rate - gammaln(counts + 1.0)

    @classmethod
    def _base_log_probability(
        cls, home_goals: int, away_goals: int, home: float, away: float, common: float
    ) -> float:
        if common == 0.0:
            return float(
                cls._log_poisson(np.asarray(home_goals), home)
                + cls._log_poisson(np.asarray(away_goals), away)
            )
        shared = np.arange(min(home_goals, away_goals) + 1, dtype=float)
        terms = (
            cls._log_poisson(home_goals - shared, home - common)
            + cls._log_poisson(away_goals - shared, away - common)
            + cls._log_poisson(shared, common)
        )
        return float(logsumexp(terms))

    @classmethod
    def _corrected_low_cells(
        cls, home: float, away: float, common: float, transport: float
    ) -> Dict[Tuple[int, int], float]:
        cells = {
            (h, a): math.exp(cls._base_log_probability(h, a, home, away, common))
            for h in range(2)
            for a in range(2)
        }
        capacity = (
            min(cells[(1, 0)], cells[(0, 1)])
            if transport >= 0.0
            else min(cells[(0, 0)], cells[(1, 1)])
        )
        delta = transport * capacity
        cells[(0, 0)] += delta
        cells[(1, 1)] += delta
        cells[(1, 0)] -= delta
        cells[(0, 1)] -= delta
        return cells

    def score_probability(
        self, game_context: Dict[str, Any], home_goals: int, away_goals: int
    ) -> float:
        """Evaluate a score directly, without finite-grid renormalization."""
        home_goals = self._integer(home_goals, "home_goals")
        away_goals = self._integer(away_goals, "away_goals")
        parameters = self._parameters.copy()
        home, away = self._means(self._prepare(game_context), parameters)
        common = float(parameters[6]) * min(home, away)
        if home_goals <= 1 and away_goals <= 1:
            return self._corrected_low_cells(
                home, away, common, float(parameters[7])
            )[(home_goals, away_goals)]
        return math.exp(
            self._base_log_probability(home_goals, away_goals, home, away, common)
        )

    def _support_limit(self, mean: float) -> int:
        if mean == 0.0:
            return 1
        quantile = poisson.isf(self.tail_tolerance / 2.0, mean)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Could not determine a finite score support.")
        limit = max(1, int(quantile))
        while poisson.sf(limit, mean) > self.tail_tolerance / 2.0:
            limit += 1
        return limit

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        parameters = self._parameters.copy()
        home, away = self._means(self._prepare(game_context), parameters)
        common = float(parameters[6]) * min(home, away)
        home_limit = self._support_limit(home)
        away_limit = self._support_limit(away)

        cell_count = (home_limit + 1) * (away_limit + 1)
        if self.max_matrix_cells is not None and cell_count > self.max_matrix_cells:
            raise MemoryError(
                f"Requested numerical accuracy requires {cell_count} score cells, "
                f"exceeding max_matrix_cells={self.max_matrix_cells}."
            )

        home_unique = poisson.pmf(np.arange(home_limit + 1), home - common)
        away_unique = poisson.pmf(np.arange(away_limit + 1), away - common)
        shared_limit = min(home_limit, away_limit)
        shared_probabilities = poisson.pmf(np.arange(shared_limit + 1), common)

        matrix = np.zeros((home_limit + 1, away_limit + 1), dtype=float)
        for shared, probability in enumerate(shared_probabilities):
            if probability != 0.0:
                matrix[shared:, shared:] += probability * np.outer(
                    home_unique[: home_limit + 1 - shared],
                    away_unique[: away_limit + 1 - shared],
                )

        for cell, probability in self._corrected_low_cells(
            home, away, common, float(parameters[7])
        ).items():
            matrix[cell] = probability

        if not np.all(np.isfinite(matrix)) or np.any(matrix < 0.0):
            raise ArithmeticError("Invalid numerical score probabilities.")

        retained_mass = math.fsum(matrix.ravel())
        if not math.isfinite(retained_mass) or retained_mass <= 0.0:
            raise ArithmeticError("The score lattice has no representable mass.")

        # Marginals remain Poisson after both dependence operations. Thus the
        # union bound certifies omitted mass <= tail_tolerance. Normalization
        # conditions on this adaptive rectangle with the same TV error bound.
        matrix /= retained_mass
        scores = {
            (h, a): float(matrix[h, a])
            for h in range(home_limit + 1)
            for a in range(away_limit + 1)
            if matrix[h, a] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    def fit(
        self,
        observations: Iterable[Mapping[str, Any]],
        *,
        penalty_strength: float,
        optimizer_options: Optional[Dict[str, Any]] = None,
    ) -> "NHLPeriod1ExactScoreModel":
        """
        Fit using training data only; update parameters only after convergence.

        Each row is either a game-context mapping containing home_goals and
        away_goals, or {"game_context": {...}, "home_goals": int, "away_goals": int}.
        penalty_strength is the precision of a quadratic prior in the documented
        parameter coordinates, applied to the summed negative log likelihood.
        """
        penalty = self._positive(penalty_strength, "penalty_strength")
        rows = []
        for row in observations:
            if not isinstance(row, Mapping):
                raise TypeError("Each training observation must be a mapping.")
            if "home_goals" not in row or "away_goals" not in row:
                raise ValueError("Training rows require home_goals and away_goals.")
            context = row.get("game_context", row)
            rows.append(
                (
                    self._prepare(context),
                    self._integer(row["home_goals"], "home_goals"),
                    self._integer(row["away_goals"], "away_goals"),
                )
            )
        if not rows:
            raise ValueError("Cannot fit without observed first-period scores.")

        for prepared, h, a in rows:
            if (prepared[0][0] == 0.0 and h > 0) or (
                prepared[0][1] == 0.0 and a > 0
            ):
                raise ValueError(
                    "A positive observed score contradicts a structural zero rate."
                )

        def objective(parameters: np.ndarray) -> float:
            if not np.all(np.isfinite(parameters)):
                return math.inf
            losses = []
            try:
                for prepared, h, a in rows:
                    home, away = self._means(prepared, parameters)
                    common = float(parameters[6]) * min(home, away)
                    if h <= 1 and a <= 1:
                        probability = self._corrected_low_cells(
                            home, away, common, float(parameters[7])
                        )[(h, a)]
                        if probability <= 0.0:
                            return math.inf
                        log_probability = math.log(probability)
                    else:
                        log_probability = self._base_log_probability(
                            h, a, home, away, common
                        )
                    if not math.isfinite(log_probability):
                        return math.inf
                    losses.append(-log_probability)
            except (ValueError, OverflowError, ArithmeticError):
                return math.inf

            displacement = parameters - self._prior
            return math.fsum(losses) + penalty * float(
                displacement @ displacement
            ) / 2.0

        bounds = [(None, None)] * 6 + [(0.0, 1.0), (-1.0, 1.0)]
        result = minimize(
            objective,
            self._prior.copy(),
            method="L-BFGS-B",
            bounds=bounds,
            options=dict(optimizer_options or {}),
        )

        if not result.success or not math.isfinite(float(result.fun)):
            raise RuntimeError(f"Model fitting did not converge: {result.message}")
        fitted = np.asarray(result.x, dtype=float)
        if (
            not np.all(np.isfinite(fitted))
            or not 0.0 <= fitted[6] <= 1.0
            or not -1.0 <= fitted[7] <= 1.0
        ):
            raise ArithmeticError("Optimizer returned invalid parameters.")

        prior_objective = objective(self._prior)
        if float(result.fun) > prior_objective:
            raise RuntimeError("Fitted objective is worse than the cold-start prior.")

        self._parameters = fitted.copy()
        self.fit_diagnostics_ = {
            "observations": len(rows),
            "penalty_strength": penalty,
            "penalized_negative_log_likelihood": float(result.fun),
            "prior_penalized_negative_log_likelihood": prior_objective,
            "iterations": int(result.nit),
            "optimizer_message": str(result.message),
            "parameters": fitted.tolist(),
            "out_of_sample_validation_performed": False,
        }
        return self