from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLHalf2AwayTotalModel(BaseCandidateModel):
    """
    Independent second-half score model for UCL away-total contracts.

    Expectations refer to regulation-time second-half goals, including stoppage
    time but excluding extra-time periods. Missing expectations use the supplied
    cold-start priors: home=0.80, away=0.60.

    Conditional away goals are Poisson with an estimated log-linear intensity.
    Uncertain halftime scores produce a mixture of conditional distributions.
    Between-state intensity variation creates overdispersion without assuming
    that the unobserved dispersion prior is an empirical measurement.

    Cold-start coefficients are zero. Thus unestimated tactical effects cannot
    manufacture an edge. Zero supplied skewness is not treated as observed
    evidence: a positive-mean Poisson distribution is inherently right-skewed.

    Context conventions:
      * home/away attack and defense are positive dimensionless multipliers.
        Larger defense means stronger defense.
      * Set expectations_include_strength=True if supplied expectations already
        incorporate attack and defense.
      * pace is a positive multiplier relative to the expectation's baseline.
      * halftime_home_goals and halftime_away_goals describe known halftime.
      * Alternatively halftime_score_probabilities maps (home, away) to mass.
      * Alternatively first_half_home_expected and first_half_away_expected
        specify an independent first-half Poisson approximation.
      * aggregate_deficit is positive when the away team trails on aggregate,
        measured at halftime.
      * home_rest_days and away_rest_days must be supplied together.

    No halftime information means a neutral tactical feature, not a claim that
    the actual halftime score was 0-0. Aggregate state must be supplied explicitly.
    Weather effects are omitted until separately estimable coefficients exist.

    fit() estimates coefficients using historical second-half away goal counts,
    including mixture likelihoods when halftime state is uncertain. Historical
    contexts must contain only information available at the intended forecast
    time. Chronological external validation is required; training cannot
    establish closing-line skill, ROI, or a universal latency guarantee.

    Numerical tolerances and support limits control approximation and resource
    use, never forecast clipping. Excessive requested support raises an error.
    """

    FEATURE_NAMES = (
        "intercept",
        "halftime_away_margin",
        "aggregate_deficit",
        "log_rest_ratio",
        "log_pace",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_1_1125_205130",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_2",
        family: str = "away_total",
        *,
        numerical_tolerance: float = 1e-12,
        max_score_cells: int = 65536,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("UCL", "half_2", "away_total"):
            pass
        if not 0.0 < numerical_tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie strictly in (0, 1).")
        if isinstance(max_score_cells, bool) or not isinstance(max_score_cells, int):
            raise ValueError("max_score_cells must be an integer.")
        if max_score_cells < 1:
            raise ValueError("max_score_cells must be positive.")
        self.numerical_tolerance = float(numerical_tolerance)
        self.max_score_cells = max_score_cells
        self.coefficients_ = np.zeros(len(self.FEATURE_NAMES), dtype=float)
        self.fit_summary_: Optional[Dict[str, Any]] = None

    @staticmethod
    def _number(value: Any, name: str, minimum: Optional[float] = None) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be numeric.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be numeric.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if minimum is not None and result < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
        return result

    @classmethod
    def _count(cls, value: Any, name: str) -> int:
        result = cls._number(value, name, 0.0)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer goal count.")
        return int(result)

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name, 0.0)
        if result == 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    def _support_end(self, mean: float) -> int:
        if not math.isfinite(mean) or mean < 0.0:
            raise ValueError("Goal intensity must be finite and nonnegative.")
        if mean == 0.0:
            return 0
        endpoint = poisson.isf(self.numerical_tolerance / 2.0, mean)
        if not np.isfinite(endpoint) or endpoint >= self.max_score_cells:
            raise ValueError("Requested goal support exceeds the resource budget.")
        endpoint = int(endpoint)
        while poisson.sf(endpoint, mean) > self.numerical_tolerance / 2.0:
            endpoint += 1
            if endpoint >= self.max_score_cells:
                raise ValueError("Requested goal support exceeds the resource budget.")
        return endpoint

    def _baseline(self, context: Mapping[str, Any], side: str) -> float:
        prior = 0.80 if side == "home" else 0.60
        mean = self._number(context.get(f"{side}_expected", prior),
                            f"{side}_expected", 0.0)
        included = context.get("expectations_include_strength", False)
        if not isinstance(included, bool):
            raise ValueError("expectations_include_strength must be boolean.")
        if not included:
            opponent = "away" if side == "home" else "home"
            team = context.get(side, {})
            defense_team = context.get(opponent, {})
            if not isinstance(team, Mapping) or not isinstance(defense_team, Mapping):
                raise ValueError("home and away must be mappings.")
            attack = self._positive(team.get("attack", 1.0), f"{side}.attack")
            defense = self._positive(
                defense_team.get("defense", 1.0), f"{opponent}.defense"
            )
            mean *= attack / defense
        pace = self._positive(context.get("pace", 1.0), "pace")
        mean *= pace
        if not math.isfinite(mean):
            raise ValueError("Baseline intensity overflow.")
        return mean

    def _halftime_states(
        self, context: Mapping[str, Any]
    ) -> List[Tuple[int, int, float]]:
        known_home = "halftime_home_goals" in context
        known_away = "halftime_away_goals" in context
        if known_home != known_away:
            raise ValueError("Both known halftime goal counts are required.")
        if known_home:
            return [(
                self._count(context["halftime_home_goals"], "halftime_home_goals"),
                self._count(context["halftime_away_goals"], "halftime_away_goals"),
                1.0,
            )]

        supplied = context.get("halftime_score_probabilities")
        if supplied is not None:
            if not isinstance(supplied, Mapping) or not supplied:
                raise ValueError("halftime_score_probabilities must be a nonempty mapping.")
            if len(supplied) > self.max_score_cells:
                raise ValueError("Too many halftime states.")
            states = []
            for score, probability in supplied.items():
                if not isinstance(score, tuple) or len(score) != 2:
                    raise ValueError("Halftime score keys must be (home, away) tuples.")
                home = self._count(score[0], "halftime home goals")
                away = self._count(score[1], "halftime away goals")
                weight = self._number(probability, "halftime probability", 0.0)
                if weight > 0.0:
                    states.append((home, away, weight))
            total = math.fsum(weight for _, _, weight in states)
            if not math.isfinite(total) or total <= 0.0:
                raise ValueError("Halftime probabilities have invalid total mass.")
            if abs(total - 1.0) > self.numerical_tolerance:
                raise ValueError("Halftime probabilities must sum to one.")
            return [(h, a, w / total) for h, a, w in states]

        has_home = "first_half_home_expected" in context
        has_away = "first_half_away_expected" in context
        if has_home != has_away:
            raise ValueError("Both first-half expectations are required.")
        if not has_home:
            return [(0, 0, 1.0)]

        home_mean = self._number(
            context["first_half_home_expected"], "first_half_home_expected", 0.0
        )
        away_mean = self._number(
            context["first_half_away_expected"], "first_half_away_expected", 0.0
        )
        nh = self._support_end(home_mean) + 1
        na = self._support_end(away_mean) + 1
        if nh * na > self.max_score_cells:
            raise ValueError("First-half support exceeds the resource budget.")
        weights = np.outer(
            poisson.pmf(np.arange(nh), home_mean),
            poisson.pmf(np.arange(na), away_mean),
        )
        weights /= weights.sum()
        return [
            (h, a, float(weights[h, a]))
            for h in range(nh)
            for a in range(na)
            if weights[h, a] > 0.0
        ]

    def _design(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, np.ndarray, np.ndarray]:
        baseline = self._baseline(context, "away")
        pace = self._positive(context.get("pace", 1.0), "pace")
        aggregate = self._number(
            context.get("aggregate_deficit", 0.0), "aggregate_deficit"
        )
        has_home_rest = "home_rest_days" in context
        has_away_rest = "away_rest_days" in context
        if has_home_rest != has_away_rest:
            raise ValueError("Both rest-day inputs are required.")
        rest = 0.0
        if has_home_rest:
            home_rest = self._number(context["home_rest_days"], "home_rest_days", 0.0)
            away_rest = self._number(context["away_rest_days"], "away_rest_days", 0.0)
            rest = math.log1p(away_rest) - math.log1p(home_rest)

        states = self._halftime_states(context)
        features = np.asarray([
            [1.0, float(a - h), aggregate, rest, math.log(pace)]
            for h, a, _ in states
        ], dtype=float)
        weights = np.asarray([w for _, _, w in states], dtype=float)
        if not np.isfinite(features).all():
            raise ValueError("Nonfinite feature values.")
        return baseline, features, weights

    @staticmethod
    def _rates(baseline: float, features: np.ndarray,
               coefficients: np.ndarray) -> np.ndarray:
        if baseline == 0.0:
            return np.zeros(features.shape[0], dtype=float)
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            rates = np.exp(math.log(baseline) + features @ coefficients)
        if not np.isfinite(rates).all() or np.any(rates <= 0.0):
            raise ValueError("Intensity is outside representable numerical range.")
        return rates

    def fit(
        self,
        historical_games: Iterable[Mapping[str, Any]],
        *,
        target_key: str = "away_goals_half_2",
        ridge_precision: float = 0.0,
    ) -> "UCLHalf2AwayTotalModel":
        """
        Fit on one row per historical game, never one row per offered contract.

        ridge_precision is an explicit caller-selected statistical prior.
        Select it using training-only chronological validation. The default adds
        no penalty. Constant/unobserved features retain zero coefficients.
        """
        precision = self._number(ridge_precision, "ridge_precision", 0.0)
        rows = []
        for context in historical_games:
            if target_key not in context:
                raise ValueError(f"Missing target {target_key!r}.")
            target = self._count(context[target_key], target_key)
            baseline, features, weights = self._design(context)
            if baseline == 0.0:
                if target != 0:
                    raise ValueError("Positive goals contradict a zero expected intensity.")
                continue
            rows.append((baseline, features, weights, target))
        if not rows:
            raise ValueError("No informative historical games supplied.")

        design = np.vstack([features for _, features, _, _ in rows])
        active = [0]
        for column in range(1, design.shape[1]):
            if np.ptp(design[:, column]) > 0.0:
                active.append(column)
        active_array = np.asarray(active, dtype=int)
        if precision == 0.0:
            if len(rows) <= len(active):
                raise ValueError("Insufficient games for unpenalized estimation.")
            if np.linalg.matrix_rank(design[:, active_array]) < len(active):
                raise ValueError("Features are not identifiable; provide a statistical prior.")
            if sum(target for _, _, _, target in rows) == 0:
                raise ValueError("All-zero outcomes require a positive statistical prior.")

        def objective(theta: np.ndarray) -> Tuple[float, np.ndarray]:
            coefficients = np.zeros(len(self.FEATURE_NAMES), dtype=float)
            coefficients[active_array] = theta
            loss = 0.5 * precision * float(theta @ theta)
            gradient = precision * theta
            for baseline, features, weights, target in rows:
                try:
                    rates = self._rates(baseline, features, coefficients)
                except ValueError:
                    return float("inf"), np.zeros_like(theta)
                log_terms = (
                    np.log(weights)
                    + target * np.log(rates)
                    - rates
                    - gammaln(target + 1.0)
                )
                log_probability = logsumexp(log_terms)
                posterior = np.exp(log_terms - log_probability)
                loss -= float(log_probability)
                gradient = gradient + (
                    posterior * (rates - target)
                ) @ features[:, active_array]
            return loss / len(rows), gradient / len(rows)

        result = minimize(
            objective,
            np.zeros(len(active), dtype=float),
            jac=True,
            method="BFGS",
        )
        if not result.success or not np.isfinite(result.fun):
            raise RuntimeError(f"Coefficient estimation failed: {result.message}")
        fitted = np.zeros(len(self.FEATURE_NAMES), dtype=float)
        fitted[active_array] = result.x
        if not np.isfinite(fitted).all():
            raise RuntimeError("Estimation produced nonfinite coefficients.")

        self.coefficients_ = fitted
        self.fit_summary_ = {
            "informative_games": len(rows),
            "objective_per_game": float(result.fun),
            "ridge_precision": precision,
            "coefficients": dict(zip(self.FEATURE_NAMES, fitted.tolist())),
            "out_of_sample_validated": False,
        }
        return self

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """
        Return a normalized second-half score matrix: rows=home, columns=away.

        Bookmaker lines and prices are deliberately absent. Integer and
        quarter-line settlement must be performed by the betting engine using
        this goal distribution, including pushes and split settlements.
        """
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home_mean = self._baseline(game_context, "home")
        baseline, features, weights = self._design(game_context)
        coefficients = np.asarray(self.coefficients_, dtype=float)
        if coefficients.shape != (len(self.FEATURE_NAMES),):
            raise ValueError("Invalid coefficient vector shape.")
        if not np.isfinite(coefficients).all():
            raise ValueError("Coefficients must be finite.")
        rates = self._rates(baseline, features, coefficients)

        home_count = self._support_end(home_mean) + 1
        away_count = self._support_end(float(rates.max())) + 1
        if home_count * away_count > self.max_score_cells:
            raise ValueError("Second-half score matrix exceeds the resource budget.")

        # Equal intensities collapse exactly to one component at cold start.
        unique_rates, inverse = np.unique(rates, return_inverse=True)
        unique_weights = np.bincount(
            inverse, weights=weights, minlength=unique_rates.size
        )
        if unique_rates.size * away_count > self.max_score_cells:
            raise ValueError("Mixture evaluation exceeds the resource budget.")

        away_goals = np.arange(away_count)
        away_mass = np.sum(
            unique_weights[:, None]
            * poisson.pmf(away_goals[None, :], unique_rates[:, None]),
            axis=0,
        )
        home_mass = poisson.pmf(np.arange(home_count), home_mean)
        scores = np.outer(home_mass, away_mass)

        total = float(scores.sum())
        if (
            not np.isfinite(scores).all()
            or np.any(scores < 0.0)
            or not math.isfinite(total)
            or total <= 0.0
        ):
            raise ArithmeticError("Invalid predictive probability mass.")
        if abs(total - 1.0) > self.numerical_tolerance:
            raise ArithmeticError("Score truncation exceeded the numerical tolerance.")

        scores /= total
        return DiscreteDistribution.from_scores(scores)