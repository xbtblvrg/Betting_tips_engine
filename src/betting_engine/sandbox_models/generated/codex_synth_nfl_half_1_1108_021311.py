from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import softmax
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLFirstHalfDoubleChanceModel(BaseCandidateModel):
    """
    Deterministic first-half scoring-lattice candidate.

    Identification
    --------------
    The supplied point means do not identify possession counts, touchdown/field
    goal composition, score correlation, or draw probability. This candidate
    therefore makes its structural priors explicit:

    * Baseline first-half means are 12.25 home and 10.75 away.
    * The possession-count prior is the midpoint of the supplied 5--6 range.
      It is a hypothesis, not an estimate from settled wagers.
    * Both teams receive a common random number of scoring opportunities.
    * Conditional on that number, team drive outcomes are independent.
    * Maximum-entropy drive probabilities on {nothing, field goal, touchdown}
      match each team's expected points per opportunity.
    * Touchdowns initially score seven points. An externally estimated
      touchdown-point PMF can represent missed conversions and two-point tries.

    The supplied point dispersion index of one is an unobserved placeholder.
    It is NOT treated as an estimated drive-count dispersion. Compound scoring
    generally has point variance greater than its mean, even with Poisson
    possession counts.

    Context contract
    ----------------
    home_expected, away_expected:
        FIRST-HALF expected points. Defaults use the supplied empirical means.
    pace_multiplier:
        Relative exposure; multiplies both means and expected opportunities.
    expected_drives_per_team:
        Expected opportunities before pace adjustment.
    drive_count_dispersion:
        Var(N)/E(N), at least one. One gives Poisson; larger values give a
        gamma-Poisson mixture. Supply drive_count_probabilities for a different
        count law, including an underdispersed law.
    drive_count_probabilities:
        Mapping of nonnegative integer opportunity counts to probabilities.
        Already reflects pace. Cannot accompany count-parameter overrides.
    home / away:
        Optional mappings containing attack_multiplier and
        defense_concession_multiplier, both dimensionless and relative to the
        baseline. An opponent's defense multiplier adjusts a team's mean.
    home_attack_exposure, away_attack_exposure:
        Additional dimensionless multiplicative exposures.
    home_defense_exposure, away_defense_exposure:
        Additional concession multipliers, applied to the opposing team.
    weather_scoring_multiplier:
        Externally estimated weather effect. Raw weather has no invented
        coefficient.
    rest_log_effect_per_day:
        Externally estimated coefficient. When nonzero, both rest-day inputs
        are required. Opposite half-log adjustments preserve the geometric
        mean of the two teams' expected points.
    home_log_mean_adjustment, away_log_mean_adjustment:
        Other externally estimated log adjustments.
    touchdown_point_probabilities:
        Conditional touchdown-point PMF, e.g. probabilities on 6, 7, and 8.

    Feature multipliers are residual adjustments. Do not supply an adjustment
    already incorporated into home_expected or away_expected.

    No additional home advantage is added: the baseline means already differ.
    Raw attack/defense ratings, full-game means, coin-toss results, and rest or
    weather effects without estimated coefficients are not silently converted.

    Numerical accuracy
    ------------------
    Infinite count distributions are truncated using an explicit probability
    error budget. The returned distribution conditions on retained counts;
    its total-variation error is no greater than the reported omitted mass,
    apart from floating-point error. Resource limits raise instead of silently
    clipping probabilities or changing the model.

    The returned home/draw/away probabilities are mutually exclusive.
    Double-chance prices are their unions, not a normalized three-way market.
    No bookmaker prices enter the forecast. Cold-start calibration and the
    cited ROI/Brier targets cannot be established without held-out outcomes.
    """

    BASELINE_HOME = 12.25
    BASELINE_AWAY = 10.75
    POSSESSION_PRIOR_RANGE = (5.0, 6.0)
    FIELD_GOAL_POINTS = 3
    DEFAULT_TOUCHDOWN_POINTS = 7
    MINIMUM_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_half_1_1108_021311",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "half_1",
        family: str = "double_chance",
        *,
        expected_drives_per_team: Optional[float] = None,
        drive_count_dispersion: float = 1.0,
        probability_error_budget: Optional[float] = None,
        max_score_cells: int = 100_000,
    ) -> None:
        if (league, scope, family) != ("NFL", "half_1", "double_chance"):
            raise ValueError("This candidate supports NFL|half_1|double_chance only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        prior = math.fsum(self.POSSESSION_PRIOR_RANGE) / len(
            self.POSSESSION_PRIOR_RANGE
        )
        self.expected_drives_per_team = self._positive(
            prior if expected_drives_per_team is None else expected_drives_per_team,
            "expected_drives_per_team",
        )
        self.drive_count_dispersion = self._finite(
            drive_count_dispersion, "drive_count_dispersion"
        )
        if self.drive_count_dispersion < 1:
            raise ValueError(
                "For underdispersed counts, provide drive_count_probabilities."
            )

        # This is a numerical tolerance, not a probability or valuation clamp.
        default_tolerance = math.sqrt(np.finfo(float).eps)
        self.probability_error_budget = self._positive(
            default_tolerance
            if probability_error_budget is None
            else probability_error_budget,
            "probability_error_budget",
        )
        if self.probability_error_budget >= 1:
            raise ValueError("probability_error_budget must be less than one.")
        if isinstance(max_score_cells, bool) or not isinstance(
            max_score_cells, int
        ):
            raise TypeError("max_score_cells must be an integer.")
        if max_score_cells < 1:
            raise ValueError("max_score_cells must be positive.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise TypeError(f"{name} must be numeric.")
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

    @classmethod
    def _pmf(cls, value: Any, name: str) -> Dict[int, float]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} must be a nonempty integer-keyed mapping.")
        result: Dict[int, float] = {}
        for key, mass in value.items():
            if (
                isinstance(key, bool)
                or not isinstance(key, (int, np.integer))
                or key < 0
            ):
                raise ValueError(f"{name} keys must be nonnegative integers.")
            probability = cls._nonnegative(mass, f"{name}[{key}]")
            if probability:
                result[int(key)] = probability

        total = math.fsum(result.values())
        tolerance = np.finfo(float).eps * max(1, len(value))
        if not math.isclose(total, 1.0, rel_tol=tolerance, abs_tol=tolerance):
            raise ValueError(f"{name} must sum to one; got {total!r}.")
        return {key: mass / total for key, mass in result.items()}

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float, float]:
        home = context.get("home", {})
        away = context.get("away", {})
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise TypeError("home and away must be mappings when provided.")

        pace = self._positive(context.get("pace_multiplier", 1), "pace_multiplier")
        weather = self._positive(
            context.get("weather_scoring_multiplier", 1),
            "weather_scoring_multiplier",
        )
        rest_beta = self._finite(
            context.get("rest_log_effect_per_day", 0), "rest_log_effect_per_day"
        )
        rest_effect = 0.0
        if rest_beta:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("Both rest-day inputs are required.")
            home_rest = self._nonnegative(context["home_rest_days"], "home_rest_days")
            away_rest = self._nonnegative(context["away_rest_days"], "away_rest_days")
            rest_effect = rest_beta * (home_rest - away_rest) / 2

        means = []
        for side, team, opponent, rest_sign, baseline in (
            ("home", home, away, 1, self.BASELINE_HOME),
            ("away", away, home, -1, self.BASELINE_AWAY),
        ):
            other = "away" if side == "home" else "home"
            mean = self._nonnegative(
                context.get(f"{side}_expected", baseline), f"{side}_expected"
            )
            factors = (
                pace,
                weather,
                self._positive(
                    team.get("attack_multiplier", 1), f"{side}.attack_multiplier"
                ),
                self._positive(
                    opponent.get("defense_concession_multiplier", 1),
                    f"{other}.defense_concession_multiplier",
                ),
                self._positive(
                    context.get(f"{side}_attack_exposure", 1),
                    f"{side}_attack_exposure",
                ),
                self._positive(
                    context.get(f"{other}_defense_exposure", 1),
                    f"{other}_defense_exposure",
                ),
            )
            adjustment = self._finite(
                context.get(f"{side}_log_mean_adjustment", 0),
                f"{side}_log_mean_adjustment",
            ) + rest_sign * rest_effect
            if mean:
                log_mean = math.log(mean) + math.fsum(
                    math.log(factor) for factor in factors
                ) + adjustment
                try:
                    mean = math.exp(log_mean)
                except OverflowError as exc:
                    raise ValueError("Adjusted expected points overflow.") from exc
                if mean == 0:
                    raise ValueError("Adjusted expected points underflow.")
            means.append(self._nonnegative(mean, f"{side} adjusted mean"))
        return means[0], means[1], pace

    def _count_law(
        self, context: Mapping[str, Any], pace: float
    ) -> Tuple[Dict[int, float], float, float]:
        if "drive_count_probabilities" in context:
            if any(
                key in context
                for key in ("expected_drives_per_team", "drive_count_dispersion")
            ):
                raise ValueError("Specify a count PMF or count parameters, not both.")
            masses = self._pmf(
                context["drive_count_probabilities"], "drive_count_probabilities"
            )
            mean = math.fsum(n * p for n, p in masses.items())
            return masses, mean, 0.0

        mean = self._positive(
            context.get("expected_drives_per_team", self.expected_drives_per_team),
            "expected_drives_per_team",
        ) * pace
        mean = self._positive(mean, "pace-adjusted drive mean")
        dispersion = self._finite(
            context.get("drive_count_dispersion", self.drive_count_dispersion),
            "drive_count_dispersion",
        )
        if dispersion < 1:
            raise ValueError("Underdispersion requires drive_count_probabilities.")

        if dispersion == 1:
            law = poisson(mu=mean)
        else:
            # Var(N) = mean + mean**2 / shape.
            shape = mean / (dispersion - 1)
            law = nbinom(n=shape, p=1 / dispersion)

        quantile = float(law.isf(self.probability_error_budget))
        if not math.isfinite(quantile):
            raise ArithmeticError("Unable to resolve possession-count tail.")
        upper = int(math.ceil(quantile))
        if upper < 0 or upper >= self.max_score_cells:
            raise ValueError("Possession distribution exceeds the resource budget.")

        tail = float(law.sf(upper))
        while tail > self.probability_error_budget:
            upper += 1
            if upper >= self.max_score_cells:
                raise ValueError("Possession distribution exceeds the resource budget.")
            tail = float(law.sf(upper))

        probabilities = law.pmf(np.arange(upper + 1))
        if not np.all(np.isfinite(probabilities)):
            raise ArithmeticError("Nonfinite count probabilities.")
        masses = {n: float(p) for n, p in enumerate(probabilities) if p > 0}
        return masses, mean, tail

    def _drive_pmf(
        self, target: float, touchdown: Mapping[int, float]
    ) -> np.ndarray:
        touchdown_mean = math.fsum(score * mass for score, mass in touchdown.items())
        rewards = np.array([0.0, float(self.FIELD_GOAL_POINTS), touchdown_mean])
        maximum = float(rewards.max())
        if not 0 <= target <= maximum:
            raise ValueError(
                f"Expected points per drive {target!r} exceed the supported "
                f"range [0, {maximum!r}]. Supply coherent opportunity exposure."
            )

        if target == 0:
            return np.array([1.0])
        if target == maximum:
            categories = (rewards == maximum).astype(float)
            categories /= categories.sum()
        else:
            # Maximize -sum(q log q) subject to sum(q)=1 and q·rewards=target.
            # Its unique interior solution is q_j ∝ exp(theta * reward_j).
            scaled_rewards = rewards / maximum
            scaled_target = target / maximum

            def residual(theta: float) -> float:
                return float(softmax(theta * scaled_rewards) @ scaled_rewards) - scaled_target

            lower, upper = -1.0, 1.0
            while residual(lower) > 0:
                lower *= 2
            while residual(upper) < 0:
                upper *= 2
            theta = brentq(
                residual,
                lower,
                upper,
                xtol=np.nextafter(0.0, 1.0),
                rtol=4 * np.finfo(float).eps,
            )
            categories = softmax(theta * scaled_rewards)

        largest_score = max(self.FIELD_GOAL_POINTS, max(touchdown))
        if largest_score + 1 > self.max_score_cells:
            raise ValueError("Drive-score support exceeds the resource budget.")
        pmf = np.zeros(largest_score + 1)
        pmf[0] = categories[0]
        pmf[self.FIELD_GOAL_POINTS] += categories[1]
        for score, mass in touchdown.items():
            pmf[score] += categories[2] * mass
        return np.trim_zeros(pmf, trim="b")

    def _forecast(
        self, game_context: Mapping[str, Any]
    ) -> Tuple[Dict[str, float], float]:
        home_mean, away_mean, pace = self._means(game_context)
        touchdown = self._pmf(
            game_context.get(
                "touchdown_point_probabilities",
                {self.DEFAULT_TOUCHDOWN_POINTS: 1.0},
            ),
            "touchdown_point_probabilities",
        )
        if any(score not in (6, 7, 8) for score in touchdown):
            raise ValueError("Touchdown points must be 6, 7, or 8.")

        counts, count_mean, tail = self._count_law(game_context, pace)
        if count_mean == 0:
            if home_mean or away_mean:
                raise ValueError("Positive expected points require positive exposure.")
            return {"home": 0.0, "draw": 1.0, "away": 0.0}, tail

        home_drive = self._drive_pmf(home_mean / count_mean, touchdown)
        away_drive = self._drive_pmf(away_mean / count_mean, touchdown)
        upper = max(counts)
        cells = 1 + upper * max(len(home_drive) - 1, len(away_drive) - 1)
        if cells > self.max_score_cells:
            raise ValueError("Score support exceeds the resource budget.")

        home_scores = np.array([1.0])
        away_scores = np.array([1.0])
        contributions = ([], [], [])

        for n in range(upper + 1):
            weight = counts.get(n, 0.0)
            if weight:
                common = min(len(home_scores), len(away_scores))
                draw = float(home_scores[:common] @ away_scores[:common])

                away_cdf = np.cumsum(away_scores)
                home_cdf = np.cumsum(home_scores)
                home_indices = np.arange(1, len(home_scores))
                away_indices = np.arange(1, len(away_scores))
                home_win = float(
                    home_scores[1:]
                    @ away_cdf[np.minimum(home_indices - 1, len(away_cdf) - 1)]
                )
                away_win = float(
                    away_scores[1:]
                    @ home_cdf[np.minimum(away_indices - 1, len(home_cdf) - 1)]
                )
                for terms, probability in zip(
                    contributions, (home_win, draw, away_win)
                ):
                    terms.append(weight * probability)

            if n < upper:
                home_scores = np.convolve(home_scores, home_drive)
                away_scores = np.convolve(away_scores, away_drive)

        values = np.array([math.fsum(terms) for terms in contributions])
        if not np.all(np.isfinite(values)) or np.any(values < 0):
            raise ArithmeticError("Invalid outcome probabilities.")
        total = math.fsum(values)
        if total <= 0:
            raise ArithmeticError("No retained probability mass.")
        values /= total
        return dict(zip(("home", "draw", "away"), map(float, values))), tail

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        probabilities, _ = self._forecast(game_context)
        return DiscreteDistribution(probabilities=probabilities)

    def price_double_chance(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        probabilities, tail = self._forecast(game_context)
        home, draw, away = (
            probabilities["home"],
            probabilities["draw"],
            probabilities["away"],
        )
        unions = {
            "1X": math.fsum((home, draw)),
            "X2": math.fsum((away, draw)),
            "12": math.fsum((home, away)),
        }
        return {
            "probabilities": unions,
            "fair_decimal_odds": {
                side: 1 / probability if probability else math.inf
                for side, probability in unions.items()
            },
            "numerical_total_variation_bound": tail,
        }

    def evaluate_contract(
        self,
        game_context: Dict[str, Any],
        side: str,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        if side not in ("1X", "X2", "12"):
            raise ValueError("side must be '1X', 'X2', or '12'.")
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds < 1:
            raise ValueError("Decimal odds must be at least one.")
        pricing = self.price_double_chance(game_context)
        probability = pricing["probabilities"][side]
        expected_return = odds * probability - 1
        return {
            "side": side,
            "probability": probability,
            "decimal_odds": odds,
            "expected_net_return_per_unit": expected_return,
            "eligible": odds >= self.MINIMUM_ELIGIBLE_ODDS and expected_return > 0,
            "numerical_expected_return_error_bound": (
                odds * pricing["numerical_total_variation_bound"]
            ),
        }