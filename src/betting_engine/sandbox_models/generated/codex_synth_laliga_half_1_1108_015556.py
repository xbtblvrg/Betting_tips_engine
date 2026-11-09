from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class LaLigaHalf1WinnerTotalModel(BaseCandidateModel):
    """
    Joint first-half score model for LALIGA winner/total contracts.

    Cold start:
        E[H] = 0.80, E[A] = 0.60.
        With no observed dependence or dispersion evidence, independent
        Poisson counts are the identifiable baseline. Winner and total
        probabilities MUST still be calculated jointly from scorelines.

    Optional, externally estimated dependence:
        H = X + Z, A = Y + Z,
        E[Z] = common_fraction * min(E[H], E[A]).

    An optional low-score transfer adds d to (0,0) and (1,1), and subtracts
    d from (1,0) and (0,1). Its feasible magnitude is derived from the
    affected probabilities. This preserves both complete marginal
    distributions, hence their means and Poisson dispersion indices.

    Neither a common shock nor the low-score interaction is inferred from
    league stereotypes. Both default to zero. In particular, common shocks
    preserve Poisson marginal variance; they do not imply overdispersed
    individual goal counts.

    Context conventions:
      * home_expected/away_expected are FIRST-HALF goal expectations.
      * home/away attack_exposure are positive relative scoring multipliers.
      * home/away defense_exposure are positive relative concession multipliers.
      * attack/defense ratings have no assumed units and require fitted
        coefficients to affect predictions.
      * feature coefficients operate on centered features. Their intercept
        effects should already be represented in the supplied expectations.
      * pace, tempo, weather, and rest have no effect without fitted coefficients.

    DiscreteDistribution is supplied by the execution environment, as specified
    by the candidate-model interface. from_scores is expected to accept a
    mapping from (home_goals, away_goals) to probability.

    Fit only on first-half match scores available before prediction time.
    Settled bets alone are not an unbiased scoring-data sample.
    """

    BASE_HOME_EXPECTED = 0.80
    BASE_AWAY_EXPECTED = 0.60

    FEATURE_NAMES = frozenset(
        {
            "attack",
            "opponent_defense",
            "pace",
            "tempo",
            "rest_difference",
            "wind_speed",
            "temperature",
            "is_dome",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_half_1_1108_015556",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "half_1",
        family: str = "winner_total",
        *,
        coefficients: Optional[Mapping[str, float]] = None,
        feature_centers: Optional[Mapping[str, float]] = None,
        common_fraction: float = 0.0,
        low_score_interaction: float = 0.0,
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
        if (league, scope, family) != ("LALIGA", "half_1", "winner_total"):
            raise ValueError("This candidate supports only LALIGA|half_1|winner_total.")

        self.coefficients = self._parameter_mapping(coefficients, "coefficients")
        self.feature_centers = self._parameter_mapping(
            feature_centers, "feature_centers"
        )

        self.common_fraction = self._finite(common_fraction, "common_fraction")
        if not 0.0 <= self.common_fraction <= 1.0:
            raise ValueError("common_fraction must lie in its feasible interval [0, 1].")

        self.low_score_interaction = self._finite(
            low_score_interaction, "low_score_interaction"
        )

        # This is a numerical error budget, not a statistical probability clamp.
        if tail_tolerance is None:
            tail_tolerance = math.sqrt(np.finfo(float).eps)
        self.tail_tolerance = self._finite(tail_tolerance, "tail_tolerance")
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if self.tail_tolerance / 2.0 < np.finfo(float).eps:
            raise ValueError("tail_tolerance is below reliable float64 quantile resolution.")

        if (
            isinstance(max_score_cells, bool)
            or not isinstance(max_score_cells, int)
            or max_score_cells <= 0
        ):
            raise ValueError("max_score_cells must be a positive integer.")
        # Resource limit only: excessive grids raise rather than cap scoring rates.
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _parameter_mapping(
        cls, values: Optional[Mapping[str, float]], name: str
    ) -> Dict[str, float]:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        unknown = set(values) - cls.FEATURE_NAMES
        if unknown:
            raise ValueError(f"Unsupported {name}: {sorted(unknown)}")
        return {key: cls._finite(value, f"{name}.{key}") for key, value in values.items()}

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        return value

    def _validate_context(self, context: Mapping[str, Any]) -> None:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        expected = {
            "league": "LALIGA",
            "scope": "half_1",
            "family": "winner_total",
        }
        for key, value in expected.items():
            if key in context and context[key] != value:
                raise ValueError(f"game_context.{key} must equal {value!r}.")

    def _feature(
        self,
        name: str,
        context: Mapping[str, Any],
        team: Mapping[str, Any],
        opponent: Mapping[str, Any],
        home_side: bool,
    ) -> float:
        center = self.feature_centers.get(name, 0.0)

        if name == "attack":
            raw = team.get("attack")
        elif name == "opponent_defense":
            raw = opponent.get("defense")
        elif name in ("pace", "tempo"):
            raw = context.get(name)
        elif name == "rest_difference":
            home_rest = context.get("home_rest_days")
            away_rest = context.get("away_rest_days")
            if home_rest is None or away_rest is None:
                return 0.0
            home_rest = self._finite(home_rest, "home_rest_days")
            away_rest = self._finite(away_rest, "away_rest_days")
            if home_rest < 0.0 or away_rest < 0.0:
                raise ValueError("Rest days cannot be negative.")
            difference = home_rest - away_rest
            raw = difference if home_side else -difference
        else:
            weather = self._mapping(context.get("weather"), "weather")
            key = "temp" if name == "temperature" else name
            raw = weather.get(key)
            if name == "is_dome" and isinstance(raw, bool):
                raw = float(raw)

        # Missing features are imputed at their training center.
        if raw is None:
            return 0.0
        return self._finite(raw, name) - center

    def _mean(self, context: Mapping[str, Any], home_side: bool) -> float:
        side = "home" if home_side else "away"
        other = "away" if home_side else "home"
        anchor = self.BASE_HOME_EXPECTED if home_side else self.BASE_AWAY_EXPECTED

        supplied = context.get(f"{side}_expected")
        mean = anchor if supplied is None else self._finite(supplied, f"{side}_expected")
        if mean < 0.0:
            raise ValueError(f"{side}_expected cannot be negative.")

        team = self._mapping(context.get(side), side)
        opponent = self._mapping(context.get(other), other)

        log_multiplier = 0.0
        for owner, key, label in (
            (team, "attack_exposure", f"{side}.attack_exposure"),
            (opponent, "defense_exposure", f"{other}.defense_exposure"),
        ):
            exposure = owner.get(key)
            if exposure is not None:
                exposure = self._finite(exposure, label)
                if exposure <= 0.0:
                    raise ValueError(f"{label} must be positive.")
                log_multiplier += math.log(exposure)

        for name, coefficient in self.coefficients.items():
            if coefficient != 0.0:
                log_multiplier += coefficient * self._feature(
                    name, context, team, opponent, home_side
                )

        if not math.isfinite(log_multiplier):
            raise ValueError("Feature effects produced a nonfinite log-rate.")
        if mean == 0.0:
            return 0.0

        try:
            result = math.exp(math.log(mean) + log_multiplier)
        except OverflowError as exc:
            raise ValueError("Conditional goal expectation overflowed.") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError("Conditional goal expectation is outside float64 range.")
        return result

    def _rates(self, context: Mapping[str, Any]) -> Tuple[float, float, float]:
        self._validate_context(context)
        home_mean = self._mean(context, True)
        away_mean = self._mean(context, False)
        common = self.common_fraction * min(home_mean, away_mean)
        return home_mean - common, away_mean - common, common

    @staticmethod
    def _low_cells(
        home_private: float, away_private: float, common: float
    ) -> np.ndarray:
        base = math.exp(-(home_private + away_private + common))
        return np.array(
            [
                [base, base * away_private],
                [
                    base * home_private,
                    base * (home_private * away_private + common),
                ],
            ],
            dtype=float,
        )

    @staticmethod
    def _adjust_low_cells(cells: np.ndarray, interaction: float) -> np.ndarray:
        adjusted = cells.copy()
        if interaction == 0.0:
            return adjusted

        # Exact positivity constraints, not empirical or arbitrary clamps.
        if interaction > 0.0:
            capacity = min(cells[0, 1], cells[1, 0])
        else:
            capacity = min(cells[0, 0], cells[1, 1])

        transfer = capacity * math.tanh(interaction)
        adjusted[0, 0] += transfer
        adjusted[1, 1] += transfer
        adjusted[0, 1] -= transfer
        adjusted[1, 0] -= transfer
        return adjusted

    def _cutoff(self, mean: float) -> int:
        if mean == 0.0:
            return 1
        budget = self.tail_tolerance / 2.0
        quantile = float(poisson.isf(budget, mean))
        if not math.isfinite(quantile):
            raise ArithmeticError("Could not obtain a finite Poisson tail cutoff.")
        cutoff = max(1, math.ceil(quantile))
        while float(poisson.sf(cutoff, mean)) > budget:
            cutoff += 1
        return cutoff

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        """
        Return a normalized joint score lattice.

        Before normalization, omitted mass is bounded by tail_tolerance using
        the union bound on the two exact Poisson marginal tails. Conditioning
        on the retained rectangle changes any event probability by no more
        than that omitted mass, apart from floating-point roundoff.
        """
        home_private, away_private, common = self._rates(game_context)
        home_max = self._cutoff(home_private + common)
        away_max = self._cutoff(away_private + common)

        cells_required = (home_max + 1) * (away_max + 1)
        if cells_required > self.max_score_cells:
            raise ValueError(
                f"Required score grid has {cells_required} cells, exceeding "
                f"the configured resource limit {self.max_score_cells}."
            )

        home_pmf = poisson.pmf(np.arange(home_max + 1), home_private)
        away_pmf = poisson.pmf(np.arange(away_max + 1), away_private)
        grid = np.zeros((home_max + 1, away_max + 1), dtype=float)

        if common == 0.0:
            grid[:] = np.outer(home_pmf, away_pmf)
        else:
            shared_pmf = poisson.pmf(
                np.arange(min(home_max, away_max) + 1), common
            )
            for shared_goals, mass in enumerate(shared_pmf):
                if mass != 0.0:
                    grid[shared_goals:, shared_goals:] += mass * np.outer(
                        home_pmf[: home_max + 1 - shared_goals],
                        away_pmf[: away_max + 1 - shared_goals],
                    )

        low = self._low_cells(home_private, away_private, common)
        grid[:2, :2] = self._adjust_low_cells(low, self.low_score_interaction)

        if not np.all(np.isfinite(grid)) or np.any(grid < 0.0):
            raise ArithmeticError("Invalid joint score probabilities.")

        retained_mass = math.fsum(float(value) for value in grid.flat)
        if not math.isfinite(retained_mass) or retained_mass <= 0.0:
            raise ArithmeticError("Joint score distribution has no representable mass.")
        grid /= retained_mass

        return {
            (home_goals, away_goals): float(grid[home_goals, away_goals])
            for home_goals in range(home_max + 1)
            for away_goals in range(away_max + 1)
            if grid[home_goals, away_goals] > 0.0
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        # Preserve the full score lattice: a 1X2-only distribution cannot price
        # winner_total contracts.
        return DiscreteDistribution.from_scores(self.score_probabilities(game_context))

    def joint_probability(
        self,
        game_context: Dict[str, Any],
        *,
        winner: str,
        total_side: str,
        total_line: float,
    ) -> float:
        """
        Probability of the strict binary event winner AND over/under total.

        Equality with the total line is excluded. Asian pushes and quarter-line
        settlements require the engine's explicit settlement rules and must not
        be valued as binary contracts using this method.
        """
        if winner not in {"home", "draw", "away"}:
            raise ValueError("winner must be 'home', 'draw', or 'away'.")
        if total_side not in {"over", "under"}:
            raise ValueError("total_side must be 'over' or 'under'.")
        line = self._finite(total_line, "total_line")
        if line < 0.0:
            raise ValueError("total_line cannot be negative.")

        selected = []
        for (home_goals, away_goals), probability in self.score_probabilities(
            game_context
        ).items():
            outcome = (
                "home"
                if home_goals > away_goals
                else "away"
                if away_goals > home_goals
                else "draw"
            )
            total = home_goals + away_goals
            total_matches = total > line if total_side == "over" else total < line
            if outcome == winner and total_matches:
                selected.append(probability)
        return math.fsum(selected)

    @staticmethod
    def _score(value: Any, name: str) -> int:
        number = LaLigaHalf1WinnerTotalModel._finite(value, name)
        if number < 0.0 or not number.is_integer():
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(number)

    @staticmethod
    def _log_poisson_count(count: int, rate: float) -> float:
        if rate == 0.0:
            return 0.0 if count == 0 else -math.inf
        return count * math.log(rate) - rate - float(gammaln(count + 1))

    @classmethod
    def _log_score_probability(
        cls,
        home_goals: int,
        away_goals: int,
        home_private: float,
        away_private: float,
        common: float,
    ) -> float:
        if common == 0.0:
            return cls._log_poisson_count(
                home_goals, home_private
            ) + cls._log_poisson_count(away_goals, away_private)

        terms = [
            cls._log_poisson_count(home_goals - shared, home_private)
            + cls._log_poisson_count(away_goals - shared, away_private)
            + cls._log_poisson_count(shared, common)
            for shared in range(min(home_goals, away_goals) + 1)
        ]
        return float(logsumexp(terms))

    def fit_low_score_interaction(
        self,
        matches: Sequence[Mapping[str, Any]],
        *,
        prior_precision: float,
        prior_mean: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Fit one dependence parameter by penalized match-score likelihood.

        Each record contains:
            game_context: pre-match features and first-half expectations
            home_goals: observed first-half home goals
            away_goals: observed first-half away goals

        prior_precision is explicitly supplied from a training-only hierarchical
        prior or temporal validation. No arbitrary shrinkage strength is built in.
        Common-shock and feature parameters remain fixed during this fit.

        The caller must enforce chronological train/validation separation and
        use match-level observations rather than duplicating scores per wager.
        State is updated only after successful optimization.
        """
        precision = self._finite(prior_precision, "prior_precision")
        center = self._finite(prior_mean, "prior_mean")
        if precision <= 0.0:
            raise ValueError("prior_precision must be positive.")

        low_observations = []
        fixed_log_likelihood = 0.0
        count = 0

        for record in matches:
            record = self._mapping(record, "match")
            context = record["game_context"]
            home_goals = self._score(record["home_goals"], "home_goals")
            away_goals = self._score(record["away_goals"], "away_goals")
            home_private, away_private, common = self._rates(context)
            count += 1

            if home_goals <= 1 and away_goals <= 1:
                cells = self._low_cells(home_private, away_private, common)
                if cells[home_goals, away_goals] <= 0.0:
                    raise ValueError(
                        "A training score has zero or unrepresentable baseline "
                        "probability; repair its pre-match expectation inputs."
                    )
                low_observations.append((cells, home_goals, away_goals))
            else:
                log_probability = self._log_score_probability(
                    home_goals,
                    away_goals,
                    home_private,
                    away_private,
                    common,
                )
                if not math.isfinite(log_probability):
                    raise ValueError("A training score is impossible under its inputs.")
                fixed_log_likelihood += log_probability

        if count == 0:
            raise ValueError("At least one completed first-half match is required.")

        def objective(parameters: np.ndarray) -> float:
            interaction = float(parameters[0])
            if not math.isfinite(interaction):
                return math.inf
            difference = interaction - center
            penalty = precision * difference * difference / 2.0
            if not math.isfinite(penalty):
                return math.inf

            result = penalty
            for cells, home_goals, away_goals in low_observations:
                probability = self._adjust_low_cells(cells, interaction)[
                    home_goals, away_goals
                ]
                if probability <= 0.0 or not math.isfinite(float(probability)):
                    return math.inf
                result -= math.log(float(probability))
            return result

        # The positive and negative transfer capacities can differ. Starting
        # from the prior and from zero avoids relying on a single side of that
        # piecewise-smooth parameterization.
        results = [
            minimize(objective, np.array([start], dtype=float), method="Powell")
            for start in dict.fromkeys((center, 0.0))
        ]
        successful = [
            result
            for result in results
            if result.success
            and math.isfinite(float(result.fun))
            and math.isfinite(float(result.x[0]))
        ]
        if not successful:
            raise RuntimeError(
                "Dependence fitting failed; existing model parameters were preserved."
            )

        best = min(successful, key=lambda result: float(result.fun))
        fitted = float(best.x[0])
        self.low_score_interaction = fitted

        return {
            "matches": count,
            "low_score_matches": len(low_observations),
            "low_score_interaction": fitted,
            "common_fraction": self.common_fraction,
            "prior_mean": center,
            "prior_precision": precision,
            "penalized_negative_log_likelihood": (
                float(best.fun) - fixed_log_likelihood
            ),
        }