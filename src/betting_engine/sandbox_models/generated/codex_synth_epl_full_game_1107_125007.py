from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import expm_multiply
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class EPLWinnerTotalModel(BaseCandidateModel):
    """
    Deterministic full-time score model for EPL result-by-total contracts.

    Cold start:
        Independent goal counts with expected goals 1.60 and 1.20.
        Winner and total are priced from their JOINT score distribution;
        independent team goal counts do not imply independent betting legs.

    Learned alternative:
        A continuous-time pure-birth process on score states, with one match
        as the time unit. Leading and trailing teams can have different goal
        intensities. All behavioral coefficients start at zero.

    Context conventions:
        home_expected / away_expected:
            Optional pre-match expected goals, overriding the corresponding
            attack/defense construction.
        home / away:
            Mappings with positive, league-relative attack and defense values.
            Higher defense means more goals conceded, not stronger defense.
        home_exposure_mean / away_exposure_mean:
            Positive training-reference means of the corresponding products
            of attack and opposing defense; default one assumes inputs have
            already been normalized.
        pace:
            Positive goal-opportunity exposure relative to an ordinary match.
            Omit when already incorporated into supplied expected goals.
        home_rest_days / away_rest_days:
            Nonnegative rest days. Their log-rest difference enters through
            a learned coefficient; omitted rest has no effect.
        weather:
            Optional mapping containing wind_speed, temp, is_dome.
        weather_reference:
            Training-reference wind_speed and temp. A weather feature is used
            only when its observation and reference are both available.

    No bookmaker information enters the model. Fitting requires externally
    selected positive prior precisions and chronological training data.
    Neither profitability nor out-of-sample calibration is guaranteed.

    Score support is chosen from a dominating counting process. Overflow is
    explicitly retained during propagation and checked before the returned
    finite grid is normalized. The approximation error is bounded by the
    configured tail tolerance, apart from floating-point propagation error.
    """

    PARAMETER_NAMES = (
        "home_allocation",
        "rest_response",
        "wind_response",
        "temperature_response",
        "leading_response",
        "trailing_response",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_full_game_1107_125007",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "full_game",
        family: str = "winner_total",
        *,
        coefficients: Optional[Mapping[str, float]] = None,
        tail_tolerance: Optional[float] = None,
        max_grid_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("EPL", "full_game", "winner_total"):
            raise ValueError("This model supports only EPL|full_game|winner_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.tail_tolerance = self._number(
            np.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else tail_tolerance,
            "tail_tolerance",
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")

        if max_grid_cells is not None:
            if (
                isinstance(max_grid_cells, bool)
                or not isinstance(max_grid_cells, (int, np.integer))
                or max_grid_cells < 1
            ):
                raise ValueError("max_grid_cells must be a positive integer.")
        self.max_grid_cells = max_grid_cells

        supplied = {} if coefficients is None else dict(coefficients)
        unknown = set(supplied).difference(self.PARAMETER_NAMES)
        if unknown:
            raise ValueError(f"Unknown coefficients: {sorted(unknown)}")

        self._theta = np.asarray(
            [
                self._number(supplied.get(name, 0.0), name)
                for name in self.PARAMETER_NAMES
            ],
            dtype=float,
        )
        self.fit_summary: Optional[Dict[str, Any]] = None

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be a finite number.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _mapping(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = context.get(key)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping.")
        return value

    @property
    def coefficients(self) -> Dict[str, float]:
        return dict(zip(self.PARAMETER_NAMES, map(float, self._theta)))

    def _features(self, context: Mapping[str, Any]) -> np.ndarray:
        if not isinstance(context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home = self._mapping(context, "home")
        away = self._mapping(context, "away")

        def base_log_rate(
            side: str,
            prior: float,
            own: Mapping[str, Any],
            opponent: Mapping[str, Any],
        ) -> float:
            expected = context.get(f"{side}_expected")
            if expected is not None:
                rate = self._nonnegative(expected, f"{side}_expected")
                return -math.inf if rate == 0.0 else math.log(rate)

            attack = self._positive(own.get("attack", 1.0), f"{side}.attack")
            defense = self._positive(
                opponent.get("defense", 1.0), f"{side}.opponent_defense"
            )
            normalizer = self._positive(
                context.get(f"{side}_exposure_mean", 1.0),
                f"{side}_exposure_mean",
            )
            return (
                math.log(prior)
                + math.log(attack)
                + math.log(defense)
                - math.log(normalizer)
            )

        log_home = base_log_rate("home", 1.60, home, away)
        log_away = base_log_rate("away", 1.20, away, home)
        log_pace = math.log(self._positive(context.get("pace", 1.0), "pace"))

        rest_values = []
        for side in ("home", "away"):
            value = context.get(f"{side}_rest_days")
            rest_values.append(
                None
                if value is None
                else self._nonnegative(value, f"{side}_rest_days")
            )
        rest_difference = (
            0.0
            if any(value is None for value in rest_values)
            else math.log1p(rest_values[0]) - math.log1p(rest_values[1])
        )

        weather = self._mapping(context, "weather")
        reference = self._mapping(context, "weather_reference")
        dome = weather.get("is_dome", False)
        if not isinstance(dome, (bool, np.bool_)):
            raise ValueError("weather.is_dome must be Boolean.")

        wind_difference = 0.0
        temperature_difference = 0.0
        if not dome:
            if weather.get("wind_speed") is not None and reference.get("wind_speed") is not None:
                wind_difference = self._nonnegative(
                    weather["wind_speed"], "weather.wind_speed"
                ) - self._nonnegative(
                    reference["wind_speed"], "weather_reference.wind_speed"
                )
            if weather.get("temp") is not None and reference.get("temp") is not None:
                temperature_difference = self._number(
                    weather["temp"], "weather.temp"
                ) - self._number(reference["temp"], "weather_reference.temp")

        return np.asarray(
            [
                log_home,
                log_away,
                log_pace,
                rest_difference,
                wind_difference,
                temperature_difference,
            ],
            dtype=float,
        )

    @staticmethod
    def _rates(features: np.ndarray, theta: np.ndarray) -> Tuple[float, float]:
        log_home, log_away, log_pace, rest, wind, temperature = features
        log_total = float(logsumexp([log_home, log_away]))
        if log_total == -math.inf:
            return 0.0, 0.0

        allocation = float(theta[0] + theta[1] * rest)
        common = float(log_pace + theta[2] * wind + theta[3] * temperature)
        if not math.isfinite(allocation) or not math.isfinite(common):
            raise FloatingPointError("Nonfinite feature response.")

        # Preserve the baseline total while learning the allocation between teams.
        logits = np.asarray(
            [log_home + allocation / 2.0, log_away - allocation / 2.0]
        )
        denominator = float(logsumexp(logits))
        log_rates = log_total + common + logits - denominator
        rates = tuple(math.exp(float(value)) for value in log_rates)
        if not all(math.isfinite(rate) for rate in rates):
            raise FloatingPointError("Nonfinite scoring intensity.")
        return rates

    def _support(self, upper_rate: float, minimum: int) -> int:
        if not math.isfinite(upper_rate):
            raise FloatingPointError("Nonfinite dominating intensity.")
        if upper_rate == 0.0:
            return minimum

        # Union bound allocates half the permitted tail error to each team.
        budget = self.tail_tolerance / 2.0
        quantile = float(poisson.isf(budget, upper_rate))
        if not math.isfinite(quantile):
            raise FloatingPointError("Could not resolve the Poisson tail support.")

        bound = max(minimum, int(math.ceil(quantile)))
        while poisson.sf(bound, upper_rate) > budget:
            bound += 1
        return bound

    def _score_grid(
        self,
        features: np.ndarray,
        theta: np.ndarray,
        minimum_home: int = 0,
        minimum_away: int = 0,
    ) -> Tuple[np.ndarray, float]:
        home_rate, away_rate = self._rates(features, theta)
        leading, trailing = map(float, theta[4:6])

        lead_multiplier = math.exp(leading)
        trail_multiplier = math.exp(trailing)
        largest_multiplier = max(1.0, lead_multiplier, trail_multiplier)

        home_max = self._support(home_rate * largest_multiplier, minimum_home)
        away_max = self._support(away_rate * largest_multiplier, minimum_away)

        rows_count = home_max + 1
        columns_count = away_max + 1
        cells = rows_count * columns_count
        if self.max_grid_cells is not None and cells > self.max_grid_cells:
            raise MemoryError(
                f"Required score grid has {cells} cells, exceeding the configured "
                f"resource budget of {self.max_grid_cells}; support was not clipped."
            )

        if leading == 0.0 and trailing == 0.0:
            home_mass = poisson.pmf(np.arange(rows_count), home_rate)
            away_mass = poisson.pmf(np.arange(columns_count), away_rate)
            grid = np.outer(home_mass, away_mass)
            home_tail = float(poisson.sf(home_max, home_rate))
            away_tail = float(poisson.sf(away_max, away_rate))
            overflow = home_tail + away_tail - home_tail * away_tail
        else:
            h, a = np.indices((rows_count, columns_count))
            h = h.ravel()
            a = a.ravel()
            states = np.arange(cells, dtype=np.int64)
            overflow_state = cells

            home_intensity = home_rate * np.where(
                h > a, lead_multiplier, np.where(h < a, trail_multiplier, 1.0)
            )
            away_intensity = away_rate * np.where(
                a > h, lead_multiplier, np.where(a < h, trail_multiplier, 1.0)
            )

            home_destination = np.where(
                h < home_max, states + columns_count, overflow_state
            )
            away_destination = np.where(
                a < away_max, states + 1, overflow_state
            )

            # Column-generator convention: dp/dt = Q p.
            row_indices = np.concatenate(
                [states, home_destination, away_destination]
            )
            column_indices = np.concatenate([states, states, states])
            entries = np.concatenate(
                [
                    -(home_intensity + away_intensity),
                    home_intensity,
                    away_intensity,
                ]
            )
            generator = coo_matrix(
                (entries, (row_indices, column_indices)),
                shape=(cells + 1, cells + 1),
            ).tocsc()

            initial = np.zeros(cells + 1, dtype=float)
            initial[0] = 1.0
            propagated = expm_multiply(generator, initial)
            if not np.all(np.isfinite(propagated)):
                raise FloatingPointError("Score propagation produced nonfinite mass.")
            if np.any(propagated < 0.0):
                raise FloatingPointError(
                    "Score propagation produced negative mass; no clipping applied."
                )

            overflow = float(propagated[-1])
            grid = propagated[:-1].reshape(rows_count, columns_count)

        retained = float(math.fsum(grid.ravel()))
        roundoff = np.finfo(float).eps * (cells + 1)
        if not math.isfinite(retained) or retained <= 0.0:
            raise FloatingPointError("The score grid has no representable mass.")
        if overflow > self.tail_tolerance + roundoff:
            raise FloatingPointError("Score support failed its tail-error guarantee.")
        if abs(retained + overflow - 1.0) > self.tail_tolerance + roundoff:
            raise FloatingPointError("Score propagation failed mass conservation.")

        return grid, overflow

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        features = self._features(game_context)
        grid, _ = self._score_grid(features, self._theta)
        retained = math.fsum(grid.ravel())
        return {
            (int(home), int(away)): float(grid[home, away] / retained)
            for home, away in np.ndindex(grid.shape)
            if grid[home, away] > 0.0
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )

    def joint_probability(
        self,
        game_context: Dict[str, Any],
        winner: str,
        total_side: str,
        total_line: float,
    ) -> float:
        """
        Probability of the specified result AND strict total inequality.

        Integer-line equality is excluded. Push, void-leg, and Asian settlement
        rules belong to the contract settlement layer, not this probability.
        """
        if winner not in {"home", "draw", "away"}:
            raise ValueError("winner must be home, draw, or away.")
        if total_side not in {"over", "under"}:
            raise ValueError("total_side must be over or under.")
        line = self._nonnegative(total_line, "total_line")

        selected = []
        for (home, away), probability in self.score_probabilities(game_context).items():
            result = "home" if home > away else "away" if away > home else "draw"
            total_matches = (
                home + away > line
                if total_side == "over"
                else home + away < line
            )
            if result == winner and total_matches:
                selected.append(probability)
        return float(math.fsum(selected))

    @staticmethod
    def binary_contract_value(
        probability: float, decimal_odds: float
    ) -> Dict[str, Any]:
        """
        Value a unit-stake win-or-lose contract, without pushes or commissions.

        The 1.40 eligibility threshold is the explicitly requested policy.
        There is no upper odds cap and no probability/market blending.
        """
        p = EPLWinnerTotalModel._number(probability, "probability")
        odds = EPLWinnerTotalModel._positive(decimal_odds, "decimal_odds")
        if not 0.0 <= p <= 1.0:
            raise ValueError("probability must lie between zero and one.")
        if odds < 1.0:
            raise ValueError("Decimal odds cannot be below one.")

        expected_profit = p * odds - 1.0
        return {
            "probability": p,
            "fair_decimal_odds": math.inf if p == 0.0 else 1.0 / p,
            "expected_profit_per_unit": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }

    def fit(
        self,
        matches: Iterable[Mapping[str, Any]],
        *,
        prior_precision: Mapping[str, float],
        optimizer_options: Optional[Mapping[str, Any]] = None,
    ) -> "EPLWinnerTotalModel":
        """
        Fit penalized full-score likelihood, atomically updating on convergence.

        Each match must contain:
            game_context: pre-match features available at prediction time
            home_goals: nonnegative integer final score
            away_goals: nonnegative integer final score

        Use one observation per match, not one per wager. Training data, feature
        normalizers, and prior precision must be selected without future data.
        Assess log score, joint-cell calibration, and betting performance on
        later held-out matches. Sparse ROI benchmarks are not fitting targets.

        Prior precision is mandatory: shrinkage strength cannot be inferred
        from the supplied zero-wager history. Priors are centered at zero,
        including the log home-allocation adjustment.
        """
        if set(prior_precision) != set(self.PARAMETER_NAMES):
            raise ValueError(
                "prior_precision must specify every parameter and no unknown keys."
            )
        precision = np.asarray(
            [
                self._positive(prior_precision[name], f"prior_precision.{name}")
                for name in self.PARAMETER_NAMES
            ],
            dtype=float,
        )

        prepared = []
        for match in matches:
            if not isinstance(match, Mapping):
                raise ValueError("Each match must be a mapping.")
            features = self._features(match["game_context"])
            scores = []
            for name in ("home_goals", "away_goals"):
                value = match[name]
                if (
                    isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer))
                    or value < 0
                ):
                    raise ValueError(f"{name} must be a nonnegative integer.")
                scores.append(int(value))
            prepared.append((features, scores[0], scores[1]))

        if not prepared:
            raise ValueError("Fitting requires observed match scores.")

        def objective(theta: Sequence[float]) -> float:
            parameters = np.asarray(theta, dtype=float)
            if not np.all(np.isfinite(parameters)):
                return math.inf
            try:
                penalty = float(np.dot(precision, parameters * parameters) / 2.0)
                terms = [penalty]
                for features, home, away in prepared:
                    if parameters[4] == 0.0 and parameters[5] == 0.0:
                        home_rate, away_rate = self._rates(features, parameters)
                        log_probability = float(
                            poisson.logpmf(home, home_rate)
                            + poisson.logpmf(away, away_rate)
                        )
                    else:
                        grid, _ = self._score_grid(
                            features, parameters, home, away
                        )
                        probability = float(grid[home, away])
                        if probability <= 0.0:
                            return math.inf
                        # Use unnormalized mass: overflow is not conditioned away.
                        log_probability = math.log(probability)
                    if not math.isfinite(log_probability):
                        return math.inf
                    terms.append(-log_probability)
                value = math.fsum(terms)
                return value if math.isfinite(value) else math.inf
            except (OverflowError, FloatingPointError, MemoryError):
                return math.inf

        initial = self._theta.copy()
        initial_objective = objective(initial)
        if not math.isfinite(initial_objective):
            raise ValueError(
                "Training scores have zero or numerically unresolved probability "
                "under the starting model. Check expected goals and resource limits."
            )

        result = minimize(
            objective,
            initial,
            method="BFGS",
            options=dict(optimizer_options or {}),
        )
        if (
            not result.success
            or not np.all(np.isfinite(result.x))
            or not math.isfinite(float(result.fun))
            or float(result.fun) > initial_objective
        ):
            raise RuntimeError(
                f"Calibration did not converge to an improved finite solution: "
                f"{result.message}. Existing parameters were preserved."
            )

        fitted = np.asarray(result.x, dtype=float).copy()
        self._theta = fitted
        self.fit_summary = {
            "matches": len(prepared),
            "initial_penalized_negative_log_likelihood": initial_objective,
            "penalized_negative_log_likelihood": float(result.fun),
            "optimizer_iterations": int(result.nit),
            "prior_precision": dict(zip(self.PARAMETER_NAMES, map(float, precision))),
            "coefficients": self.coefficients,
            "validated_out_of_sample": False,
        }
        return self