from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBASecondHalfExactScoreModel(BaseCandidateModel):
    """
    Deterministic NBA second-half exact-score candidate.

    Cold-start identification
    -------------------------
    Means of 56 and 54 and a Poisson dispersion prior do not identify possession
    variability, scoring categories, rest effects, or intentional-foul dynamics.
    Without possession data, this model returns the stated independent-Poisson
    prior. It does not invent an empirical correlation or claim validated alpha.

    With externally estimated possession inputs, each regime uses

        P(H=h, A=a) = sum_n w[n] P(X_h[1]+...+X_h[n]=h)
                                P(X_a[1]+...+X_a[n]=a).

    Consequently,
        E[S_i] = E[N] E[X_i],
        Var(S_i) = E[N] Var(X_i) + Var(N) E[X_i]**2,
        Cov(S_h, S_a) = Var(N) E[X_h] E[X_a].

    Per-possession probabilities undergo an exponential tilt. A separate scalar
    tilt for each team makes the full regime mixture recover its target mean.
    No bookmaker prices enter the distribution.

    Optional context schema
    -----------------------
    possession_model:
        home_ppp, away_ppp:
            mappings from nonnegative integer points to probabilities. The
            supplied support must include any modeled 4+ scoring outcomes.
        possession_pmf:
            optional mapping from possession counts to probabilities.
        possession_mean, possession_variance:
            alternative count moments. Variance >= mean uses Poisson/NB.
            Underdispersed counts require an explicit possession_pmf.
        regimes:
            optional sequence of mappings with weight and any overrides above.
            Regime weights must sum to one. These may represent calibrated
            bench-unit and intentional-foul states.

    Alternatively, possession_mean can be derived from home_pace, away_pace,
    league_pace and second_half_pace_multiplier. Pace is possessions per
    regulation game, so the NBA second-half exposure is 1/2.

    Explicit home_expected/away_expected are authoritative. Otherwise the
    baseline means are multiplied by optional home/away efficiency multipliers,
    second_half_tempo_multiplier, and exp(rest_log_effect * rest_days).
    Rest effects and regime probabilities must be estimated outside this class
    using information available at the forecast timestamp.

    score grids contain second-half points, excluding overtime. Feed settlement
    rules and historical inputs consistently. from_scores is expected to accept
    a {(home_points, away_points): probability} mapping.

    tail_probability is a numerical approximation budget, not a forecast clamp.
    Omitted mass is reported in diagnostics. No truncated possession distribution
    is silently rescaled to conceal its omitted probability.
    """

    BASE_HOME_MEAN = 56.0
    BASE_AWAY_MEAN = 54.0
    HALF_GAME_EXPOSURE = 0.5

    def __init__(
        self,
        model_id="codex_synth_nba_half_2_1108_025539",
        version="1.0.0",
        league="NBA",
        scope="half_2",
        family="exact_score",
        *,
        tail_probability: Optional[float] = None,
        max_grid_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NBA", "half_2", "exact_score"):
            raise ValueError("This model only supports NBA|half_2|exact_score.")

        self.tail_probability = (
            float(np.sqrt(np.finfo(float).eps))
            if tail_probability is None
            else self._positive(tail_probability, "tail_probability")
        )
        if self.tail_probability >= 1.0:
            raise ValueError("tail_probability must be less than one.")
        if max_grid_cells is not None:
            max_grid_cells = self._integer(max_grid_cells, "max_grid_cells")
            if max_grid_cells == 0:
                raise ValueError("max_grid_cells must be positive.")
        self.max_grid_cells = max_grid_cells
        self.last_diagnostics: Dict[str, Any] = {}

    @staticmethod
    def _positive(value: Any, name: str) -> float:
        result = float(value)
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
        return result

    @staticmethod
    def _nonnegative(value: Any, name: str) -> float:
        result = float(value)
        if not math.isfinite(result) or result < 0.0:
            raise ValueError(f"{name} must be finite and nonnegative.")
        return result

    @staticmethod
    def _integer(value: Any, name: str) -> int:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or numeric != int(numeric):
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(numeric)

    @classmethod
    def _pmf(cls, values: Mapping, name: str) -> np.ndarray:
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"{name} must be a nonempty probability mapping.")
        entries = {}
        for key, value in values.items():
            point = cls._integer(key, f"{name} support")
            if point in entries:
                raise ValueError(f"{name} contains duplicate support values.")
            entries[point] = cls._nonnegative(value, f"{name} probability")
        total = math.fsum(entries.values())
        tolerance = np.finfo(float).eps * max(len(entries), 1) * 16
        if not math.isclose(total, 1.0, rel_tol=tolerance, abs_tol=tolerance):
            raise ValueError(f"{name} probabilities must sum to one.")
        positive = {key: value for key, value in entries.items() if value > 0}
        if not positive:
            raise ValueError(f"{name} has no positive probability.")
        result = np.zeros(max(positive) + 1, dtype=float)
        for key, value in positive.items():
            result[key] = value / total
        return result

    def _targets(self, context: Dict[str, Any]) -> Tuple[float, float]:
        tempo = self._positive(
            context.get("second_half_tempo_multiplier", 1.0),
            "second_half_tempo_multiplier",
        )
        means = []
        for side, baseline in (
            ("home", self.BASE_HOME_MEAN),
            ("away", self.BASE_AWAY_MEAN),
        ):
            key = f"{side}_expected"
            if key in context:
                means.append(self._nonnegative(context[key], key))
                continue

            efficiency = self._positive(
                context.get(f"{side}_efficiency_multiplier", 1.0),
                f"{side}_efficiency_multiplier",
            )
            coefficient = float(context.get(f"{side}_rest_log_effect", 0.0))
            if not math.isfinite(coefficient):
                raise ValueError("Rest log effects must be finite.")
            if coefficient != 0.0 and f"{side}_rest_days" not in context:
                raise ValueError("A nonzero rest effect requires rest_days.")
            rest = self._nonnegative(
                context.get(f"{side}_rest_days", 0.0), f"{side}_rest_days"
            )
            try:
                mean = baseline * tempo * efficiency * math.exp(coefficient * rest)
            except OverflowError as exc:
                raise ValueError("Situational inputs overflow the scoring mean.") from exc
            means.append(self._positive(mean, f"{side} adjusted mean"))
        return means[0], means[1]

    def _count_distribution(
        self, specification: Dict[str, Any], context: Dict[str, Any], budget: float
    ) -> Tuple[np.ndarray, float, float]:
        if "possession_pmf" in specification:
            pmf = self._pmf(specification["possession_pmf"], "possession_pmf")
            support = np.arange(pmf.size, dtype=float)
            mean = float(pmf @ support)
            variance = float(pmf @ ((support - mean) ** 2))
            return pmf, mean, variance

        if "possession_mean" in specification:
            mean = self._positive(specification["possession_mean"], "possession_mean")
        else:
            required = ("home_pace", "away_pace", "league_pace")
            if not all(key in context for key in required):
                raise ValueError(
                    "Possession modeling requires possession_pmf, possession_mean, "
                    "or home_pace/away_pace/league_pace."
                )
            home = self._positive(context["home_pace"], "home_pace")
            away = self._positive(context["away_pace"], "away_pace")
            league = self._positive(context["league_pace"], "league_pace")
            multiplier = self._positive(
                context.get("second_half_pace_multiplier", 1.0),
                "second_half_pace_multiplier",
            )
            mean = self._positive(
                home * away / league * self.HALF_GAME_EXPOSURE * multiplier,
                "derived possession mean",
            )

        if "possession_variance" not in specification:
            raise ValueError(
                "Possession variance is unidentified; provide an estimated variance "
                "or an explicit possession_pmf."
            )
        variance = self._nonnegative(
            specification["possession_variance"], "possession_variance"
        )
        if variance < mean:
            raise ValueError(
                "Underdispersed possession counts require an explicit possession_pmf."
            )
        if variance == mean:
            distribution = poisson(mean)
        else:
            shape = mean * mean / (variance - mean)
            distribution = nbinom(shape, mean / variance)
        return self._truncate(distribution, budget), mean, variance

    @staticmethod
    def _truncate(distribution: Any, budget: float) -> np.ndarray:
        quantile = float(distribution.isf(budget))
        if not math.isfinite(quantile):
            raise ValueError("Unable to resolve count support at this tail budget.")
        upper = max(0, int(math.ceil(quantile)))
        while float(distribution.sf(upper)) > budget:
            upper += 1
        pmf = np.asarray(distribution.pmf(np.arange(upper + 1)), dtype=float)
        if not np.all(np.isfinite(pmf)) or float(pmf.sum()) <= 0:
            raise ArithmeticError("Invalid numerical count distribution.")
        return pmf

    @staticmethod
    def _tilt(pmf: np.ndarray, theta: float) -> np.ndarray:
        indices = np.flatnonzero(pmf)
        # Centering prevents overflow from multiplying the largest score by theta.
        anchor = indices[-1] if theta >= 0 else indices[0]
        log_weights = np.log(pmf[indices]) + theta * (indices - anchor)
        result = np.zeros_like(pmf)
        result[indices] = np.exp(log_weights - logsumexp(log_weights))
        return result

    def _calibrate_team(
        self, regimes: Sequence[Dict[str, Any]], side: str, target: float
    ) -> Sequence[np.ndarray]:
        distributions = [regime[side] for regime in regimes]
        exposures = np.array(
            [regime["weight"] * regime["mean_n"] for regime in regimes]
        )
        lower = math.fsum(
            exposure * np.flatnonzero(pmf)[0]
            for exposure, pmf in zip(exposures, distributions)
        )
        upper = math.fsum(
            exposure * np.flatnonzero(pmf)[-1]
            for exposure, pmf in zip(exposures, distributions)
        )
        if target < lower or target > upper:
            raise ValueError(
                f"{side} target {target} is outside the attainable mean "
                f"interval [{lower}, {upper}]."
            )

        if target == lower or target == upper:
            result = []
            for pmf in distributions:
                tilted = np.zeros_like(pmf)
                support = np.flatnonzero(pmf)
                tilted[support[0] if target == lower else support[-1]] = 1.0
                result.append(tilted)
            return result

        def residual(theta: float) -> float:
            return math.fsum(
                exposure * float(self._tilt(pmf, theta) @ np.arange(pmf.size))
                for exposure, pmf in zip(exposures, distributions)
            ) - target

        left, right = -1.0, 1.0
        while residual(left) > 0:
            left *= 2.0
            if not math.isfinite(left):
                raise ArithmeticError("Unable to bracket the lower scoring tilt.")
        while residual(right) < 0:
            right *= 2.0
            if not math.isfinite(right):
                raise ArithmeticError("Unable to bracket the upper scoring tilt.")
        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
        )
        return [self._tilt(pmf, theta) for pmf in distributions]

    def _check_size(self, rows: int, columns: int) -> None:
        if (
            self.max_grid_cells is not None
            and rows * columns > self.max_grid_cells
        ):
            raise MemoryError(
                f"Requested grid has {rows * columns} cells, exceeding the "
                "configured resource limit. No forecast has been clipped."
            )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        self.last_diagnostics = {}
        home_mean, away_mean = self._targets(game_context)
        specification = game_context.get("possession_model")

        if specification is None:
            home_pmf = self._truncate(poisson(home_mean), self.tail_probability / 2)
            away_pmf = self._truncate(poisson(away_mean), self.tail_probability / 2)
            self._check_size(home_pmf.size, away_pmf.size)
            grid = np.outer(home_pmf, away_pmf)
            architecture = "cold_start_independent_poisson"
            covariance = 0.0
            home_variance, away_variance = home_mean, away_mean
        else:
            if not isinstance(specification, Mapping):
                raise ValueError("possession_model must be a mapping.")
            base = dict(specification)
            raw_regimes = base.pop("regimes", None)
            if raw_regimes is None:
                raw_regimes = [{"weight": 1.0}]
            if not isinstance(raw_regimes, Sequence) or not raw_regimes:
                raise ValueError("regimes must be a nonempty sequence.")

            weights = [
                self._nonnegative(regime["weight"], "regime weight")
                for regime in raw_regimes
            ]
            total_weight = math.fsum(weights)
            tolerance = np.finfo(float).eps * len(weights) * 16
            if not math.isclose(
                total_weight, 1.0, rel_tol=tolerance, abs_tol=tolerance
            ):
                raise ValueError("Regime weights must sum to one.")

            regimes = []
            for raw, weight in zip(raw_regimes, weights):
                if weight == 0:
                    continue
                current = dict(base)
                current.update(raw)
                count_pmf, mean_n, variance_n = self._count_distribution(
                    current, game_context, self.tail_probability
                )
                regimes.append(
                    {
                        "weight": weight / total_weight,
                        "counts": count_pmf,
                        "mean_n": mean_n,
                        "variance_n": variance_n,
                        "home": self._pmf(current["home_ppp"], "home_ppp"),
                        "away": self._pmf(current["away_ppp"], "away_ppp"),
                    }
                )

            home_ppps = self._calibrate_team(regimes, "home", home_mean)
            away_ppps = self._calibrate_team(regimes, "away", away_mean)
            rows = 1 + max(
                (len(regime["counts"]) - 1) * (len(pmf) - 1)
                for regime, pmf in zip(regimes, home_ppps)
            )
            columns = 1 + max(
                (len(regime["counts"]) - 1) * (len(pmf) - 1)
                for regime, pmf in zip(regimes, away_ppps)
            )
            self._check_size(rows, columns)
            grid = np.zeros((rows, columns), dtype=float)
            home_second = away_second = cross_moment = 0.0

            for regime, home_ppp, away_ppp in zip(regimes, home_ppps, away_ppps):
                weight = regime["weight"]
                mean_n, variance_n = regime["mean_n"], regime["variance_n"]
                mh = float(home_ppp @ np.arange(home_ppp.size))
                ma = float(away_ppp @ np.arange(away_ppp.size))
                vh = float(home_ppp @ ((np.arange(home_ppp.size) - mh) ** 2))
                va = float(away_ppp @ ((np.arange(away_ppp.size) - ma) ** 2))
                home_second += weight * (
                    mean_n * vh + variance_n * mh * mh + (mean_n * mh) ** 2
                )
                away_second += weight * (
                    mean_n * va + variance_n * ma * ma + (mean_n * ma) ** 2
                )
                cross_moment += weight * (
                    variance_n * mh * ma + mean_n * mean_n * mh * ma
                )

                home_sum = np.array([1.0])
                away_sum = np.array([1.0])
                for count, probability in enumerate(regime["counts"]):
                    if count:
                        home_sum = np.convolve(home_sum, home_ppp)
                        away_sum = np.convolve(away_sum, away_ppp)
                    if probability > 0:
                        grid[: home_sum.size, : away_sum.size] += (
                            weight * probability * np.outer(home_sum, away_sum)
                        )

            architecture = "shared_possessions_with_calibrated_regimes"
            home_variance = home_second - home_mean**2
            away_variance = away_second - away_mean**2
            covariance = cross_moment - home_mean * away_mean

        retained_mass = float(grid.sum())
        if (
            not np.all(np.isfinite(grid))
            or np.any(grid < 0)
            or not math.isfinite(retained_mass)
            or retained_mass <= 0
        ):
            raise ArithmeticError("Invalid exact-score probability grid.")

        grid /= retained_mass
        scores = {
            (int(home), int(away)): float(grid[home, away])
            for home, away in zip(*np.nonzero(grid))
        }
        result = DiscreteDistribution.from_scores(scores)
        self.last_diagnostics = {
            "architecture": architecture,
            "target_home_mean": home_mean,
            "target_away_mean": away_mean,
            "untruncated_home_variance": home_variance,
            "untruncated_away_variance": away_variance,
            "untruncated_covariance": covariance,
            "retained_mass_before_normalization": retained_mass,
            "mass_deficit_including_roundoff": 1.0 - retained_mass,
            "tail_probability_budget": self.tail_probability,
            "returned_home_mean": float(
                np.arange(grid.shape[0]) @ grid.sum(axis=1)
            ),
            "returned_away_mean": float(
                np.arange(grid.shape[1]) @ grid.sum(axis=0)
            ),
            "grid_shape": grid.shape,
            "historically_validated": False,
        }
        return result

    @classmethod
    def value_contract(
        cls, probability: float, decimal_odds: float, cost_per_unit: float = 0.0
    ) -> Dict[str, Any]:
        """
        Evaluate a supplied cell probability without modifying the forecast.

        cost_per_unit is the expected additional cost per unit stake. Standard
        decimal odds already contain bookmaker margin; subtracting overround
        again would double-count it. Eligibility is not a staking recommendation.
        """
        probability = cls._nonnegative(probability, "probability")
        if probability > 1:
            raise ValueError("probability must not exceed one.")
        odds = cls._positive(decimal_odds, "decimal_odds")
        if odds < 1:
            raise ValueError("decimal_odds must be at least one.")
        cost = cls._nonnegative(cost_per_unit, "cost_per_unit")
        expected_profit = probability * odds - 1.0 - cost
        return {
            "probability": probability,
            "break_even_probability": (1.0 + cost) / odds,
            "expected_profit_per_unit": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }