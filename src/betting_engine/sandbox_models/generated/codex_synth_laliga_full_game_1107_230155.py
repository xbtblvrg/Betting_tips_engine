from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the execution environment.


class LaLigaBothScoreCandidate(BaseCandidateModel):
    """
    Independent, bookmaker-free BTTS model with estimable goal dispersion and
    marginal-preserving low-score dependence.

    Cold-start assumptions:
        E[H] = 1.60, E[A] = 1.20.
        Var[X] = E[X], hence Poisson marginals.
        Dependence is zero until supported by fitted parameters.

    No observed wagers means there is no demonstrated calibration defect.
    Unverified league-rate and tactical hypotheses do not justify subtracting
    probability from BTTS-Yes.

    Marginals:
        Var[X] = mu + alpha * mu**2.
        alpha == 0 gives Poisson; alpha > 0 gives Gamma-mixed Poisson.

    Dependence adds delta to cells (0,0), (1,1) and subtracts it from
    (0,1), (1,0). This preserves both complete marginal distributions.
    Its admissible magnitude is derived from the four cell probabilities.

    Feature contract:
        home_expected / away_expected are positive pre-match base intensities.
        home / away may contain positive attack_multiplier and
        defense_concession_multiplier, both normalized to neutral == 1.
        pace_multiplier is positive and normalized to neutral == 1.
        Rest is represented by log1p(home_rest_days)-log1p(away_rest_days).

    Expected intensities and multipliers must not encode the same adjustment
    twice. Raw, unnormalized attack/defense ratings are deliberately not used.
    Weather and venue need fitted, explicitly defined transformations before
    they can defensibly change a goal intensity.

    Training uses scored fixtures, not selected historical wagers. Fit on
    chronologically earlier data and assess calibration on held-out fixtures.
    Regularization precision must come from the caller's prior or validation
    procedure; there is no hidden regularization constant.

    Score output uses tuple-keyed score probabilities. Numerically negligible
    positive-score tails are collected in the last positive bin, preserving
    the exact BTTS probability. This distribution is intended only for BTTS,
    not totals, handicaps, or exact-score contracts.
    """

    FEATURE_COUNT = 5
    PARAMETER_COUNT = 14

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_full_game_1107_230155",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "full_game",
        family: str = "both_score",
        *,
        parameters: Optional[Sequence[float]] = None,
        score_tail_tolerance: float = np.finfo(float).eps,
        max_score_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("LALIGA", "full_game", "both_score"):
            raise ValueError("This candidate supports LALIGA|full_game|both_score only.")

        tolerance = self._number(score_tail_tolerance, "score_tail_tolerance")
        if not 0.0 < tolerance < 1.0:
            raise ValueError("score_tail_tolerance must lie strictly between zero and one.")
        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells < 4
            ):
                raise ValueError("max_score_cells must be an integer of at least four.")

        # Per-side coefficients: intercept, log attack, log opponent
        # concessions, log pace, signed log-rest difference.
        # Unit multiplicative exposures have coefficients one by definition.
        neutral = np.array(
            [
                0.0, 1.0, 1.0, 1.0, 0.0,
                0.0, 1.0, 1.0, 1.0, 0.0,
                0.0, 0.0,  # Home and away alpha.
                0.0, 0.0,  # Dependence intercept and intensity mismatch.
            ],
            dtype=float,
        )
        self._parameters = self._validated_parameters(
            neutral if parameters is None else parameters
        )
        self.score_tail_tolerance = tolerance
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be finite and numeric.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _validated_parameters(cls, values: Sequence[float]) -> np.ndarray:
        result = np.asarray(values, dtype=float).copy()
        if result.shape != (cls.PARAMETER_COUNT,) or not np.isfinite(result).all():
            raise ValueError("parameters must contain fourteen finite numbers.")
        if np.any(result[10:12] < 0.0):
            raise ValueError("Goal overdispersion parameters must be nonnegative.")
        result.setflags(write=False)
        return result

    @property
    def parameters(self) -> Tuple[float, ...]:
        return tuple(float(x) for x in self._parameters)

    @classmethod
    def _design(cls, context: Mapping[str, Any]):
        if not isinstance(context, Mapping):
            raise ValueError("game_context must be a mapping.")
        home = context.get("home", {})
        away = context.get("away", {})
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise ValueError("home and away must be feature mappings when supplied.")

        home_mean = cls._positive(context.get("home_expected", 1.60), "home_expected")
        away_mean = cls._positive(context.get("away_expected", 1.20), "away_expected")
        pace = cls._positive(context.get("pace_multiplier", 1.0), "pace_multiplier")

        has_home_rest = context.get("home_rest_days") is not None
        has_away_rest = context.get("away_rest_days") is not None
        if has_home_rest != has_away_rest:
            raise ValueError("Supply both rest-day values or neither.")
        rest = 0.0
        if has_home_rest:
            home_rest = cls._number(context["home_rest_days"], "home_rest_days")
            away_rest = cls._number(context["away_rest_days"], "away_rest_days")
            if min(home_rest, away_rest) < 0.0:
                raise ValueError("Rest days cannot be negative.")
            rest = math.log1p(home_rest) - math.log1p(away_rest)

        def features(team, opponent, signed_rest):
            attack = cls._positive(
                team.get("attack_multiplier", 1.0), "attack_multiplier"
            )
            concession = cls._positive(
                opponent.get("defense_concession_multiplier", 1.0),
                "defense_concession_multiplier",
            )
            return np.array(
                [1.0, math.log(attack), math.log(concession), math.log(pace), signed_rest],
                dtype=float,
            )

        return (
            math.log(home_mean),
            math.log(away_mean),
            features(home, away, rest),
            features(away, home, -rest),
        )

    @staticmethod
    def _logpmf(score: Any, mean: float, alpha: float):
        if alpha == 0.0:
            return poisson.logpmf(score, mean)

        size = 1.0 / alpha
        if not math.isfinite(size):
            return poisson.logpmf(score, mean)

        count = np.asarray(score, dtype=float)
        log_mean = math.log(mean)
        log_size = math.log(size)
        log_sum = np.logaddexp(log_size, log_mean)
        return (
            gammaln(count + size)
            - gammaln(size)
            - gammaln(count + 1.0)
            - size * np.logaddexp(0.0, log_mean - log_size)
            + count * (log_mean - log_sum)
        )

    @classmethod
    def _state(cls, design, parameters):
        base_h, base_a, features_h, features_a = design
        log_h = base_h + float(features_h @ parameters[:5])
        log_a = base_a + float(features_a @ parameters[5:10])
        try:
            mean_h, mean_a = math.exp(log_h), math.exp(log_a)
        except OverflowError as exc:
            raise FloatingPointError("Goal intensity exceeds numerical range.") from exc
        if (
            not math.isfinite(mean_h)
            or not math.isfinite(mean_a)
            or min(mean_h, mean_a) <= 0.0
        ):
            raise FloatingPointError("Goal intensity is outside numerical range.")

        alpha_h, alpha_a = map(float, parameters[10:12])
        low_h = np.exp(cls._logpmf(np.array([0, 1]), mean_h, alpha_h))
        low_a = np.exp(cls._logpmf(np.array([0, 1]), mean_a, alpha_a))
        independent = np.outer(low_h, low_a)

        eta = float(parameters[12] + parameters[13] * abs(log_h - log_a))
        if not math.isfinite(eta):
            raise FloatingPointError("Dependence predictor exceeds numerical range.")

        # The sign-specific bounds guarantee nonnegative corrected cells.
        fraction = math.tanh(eta)
        if fraction >= 0.0:
            delta = fraction * min(independent[0, 1], independent[1, 0])
        else:
            delta = fraction * min(independent[0, 0], independent[1, 1])

        return mean_h, mean_a, alpha_h, alpha_a, independent, float(delta)

    def btts_probability(self, game_context: Dict[str, Any]) -> float:
        mean_h, mean_a, alpha_h, alpha_a, _, delta = self._state(
            self._design(game_context), self._parameters
        )
        log_zero_h = float(self._logpmf(0, mean_h, alpha_h))
        log_zero_a = float(self._logpmf(0, mean_a, alpha_a))

        # This form avoids cancellation when the scoring intensities are small.
        probability = (
            -math.expm1(log_zero_h) * -math.expm1(log_zero_a) + delta
        )
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise FloatingPointError("Invalid BTTS probability.")
        return probability

    def _marginal(self, mean: float, alpha: float) -> np.ndarray:
        if alpha == 0.0 or not math.isfinite(1.0 / alpha):
            distribution = poisson(mean)
        else:
            size = 1.0 / alpha
            success = math.exp(
                -float(np.logaddexp(0.0, math.log(mean) - math.log(size)))
            )
            if not 0.0 < success < 1.0:
                raise FloatingPointError("Negative-binomial parameters lost precision.")
            distribution = nbinom(size, success)

        quantile = float(distribution.isf(self.score_tail_tolerance))
        if not math.isfinite(quantile):
            raise FloatingPointError("Unable to determine a finite score support.")

        # Keep 0 and 1 exact; only scores of at least 2 may share a tail bin.
        last = max(2, int(math.ceil(quantile)) + 1)
        if self.max_score_cells is not None and last + 1 > self.max_score_cells:
            raise MemoryError("Requested marginal exceeds the score-cell budget.")

        probabilities = np.empty(last + 1, dtype=float)
        probabilities[:last] = distribution.pmf(np.arange(last))
        probabilities[last] = distribution.sf(last - 1)
        if (
            not np.isfinite(probabilities).all()
            or np.any(probabilities < 0.0)
            or probabilities.sum() <= 0.0
        ):
            raise FloatingPointError("Invalid score marginal.")
        return probabilities

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        mean_h, mean_a, alpha_h, alpha_a, _, delta = self._state(
            self._design(game_context), self._parameters
        )
        home = self._marginal(mean_h, alpha_h)
        away = self._marginal(mean_a, alpha_a)
        cells = len(home) * len(away)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError("Requested score matrix exceeds the score-cell budget.")

        scores = np.outer(home, away)
        scores[0, 0] += delta
        scores[1, 1] += delta
        scores[0, 1] -= delta
        scores[1, 0] -= delta

        if not np.isfinite(scores).all() or np.any(scores < 0.0):
            raise FloatingPointError("Invalid corrected score distribution.")
        total = math.fsum(float(p) for p in scores.flat)
        if not math.isfinite(total) or total <= 0.0:
            raise FloatingPointError("Score distribution has invalid total mass.")

        return DiscreteDistribution.from_scores(
            {
                (home_goals, away_goals): float(scores[home_goals, away_goals] / total)
                for home_goals in range(len(home))
                for away_goals in range(len(away))
            }
        )

    def fit(
        self,
        fixtures: Sequence[Mapping[str, Any]],
        *,
        prior_precision: Sequence[float],
        prior_parameters: Optional[Sequence[float]] = None,
        optimizer_options: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fit penalized joint-score likelihood atomically.

        Each fixture must contain:
            game_context: pre-match feature mapping
            home_goals: nonnegative integer
            away_goals: nonnegative integer

        prior_precision contains fourteen strictly positive diagonal Gaussian
        prior precisions. These are supplied explicitly because cold-start
        observations cannot identify a defensible shrinkage strength.

        A failed optimizer leaves the deployed parameters unchanged. A
        successful fit is not evidence of out-of-sample predictive advantage.
        """
        precision = np.asarray(prior_precision, dtype=float)
        if (
            precision.shape != (self.PARAMETER_COUNT,)
            or not np.isfinite(precision).all()
            or np.any(precision <= 0.0)
        ):
            raise ValueError("Supply fourteen finite, strictly positive prior precisions.")

        prior = self._validated_parameters(
            self._parameters if prior_parameters is None else prior_parameters
        )
        observations = []
        for fixture in fixtures:
            if not isinstance(fixture, Mapping):
                raise ValueError("Every fixture must be a mapping.")
            goals = []
            for name in ("home_goals", "away_goals"):
                score = self._number(fixture[name], name)
                if score < 0.0 or not score.is_integer():
                    raise ValueError(f"{name} must be a nonnegative integer.")
                goals.append(int(score))
            observations.append((self._design(fixture["game_context"]), *goals))
        if not observations:
            raise ValueError("Fitting requires observed scored fixtures.")

        def objective(parameters):
            difference = parameters - prior
            loss = 0.5 * float(np.dot(precision, difference * difference))
            try:
                for design, home_goals, away_goals in observations:
                    mean_h, mean_a, alpha_h, alpha_a, low, delta = self._state(
                        design, parameters
                    )
                    if home_goals <= 1 and away_goals <= 1:
                        sign = 1.0 if home_goals == away_goals else -1.0
                        probability = float(low[home_goals, away_goals] + sign * delta)
                        if probability <= 0.0:
                            return math.inf
                        log_probability = math.log(probability)
                    else:
                        log_probability = float(
                            self._logpmf(home_goals, mean_h, alpha_h)
                            + self._logpmf(away_goals, mean_a, alpha_a)
                        )
                    if not math.isfinite(log_probability):
                        return math.inf
                    loss -= log_probability
            except (FloatingPointError, OverflowError, ValueError):
                return math.inf
            return loss if math.isfinite(loss) else math.inf

        # Alpha=0 is a genuine model boundary, not an arbitrary forecast clamp.
        bounds = [(None, None)] * self.PARAMETER_COUNT
        bounds[10] = (0.0, None)
        bounds[11] = (0.0, None)
        initial = self._parameters.copy()
        initial_objective = objective(initial)
        if not math.isfinite(initial_objective):
            raise ValueError("Initial model cannot assign finite likelihood to the data.")

        result = minimize(
            objective,
            initial,
            method="L-BFGS-B",
            bounds=bounds,
            options=dict(optimizer_options or {}),
        )
        if not result.success or not math.isfinite(float(result.fun)):
            raise RuntimeError(f"Calibration failed; parameters unchanged: {result.message}")
        if float(result.fun) > initial_objective:
            raise RuntimeError("Calibration worsened its objective; parameters unchanged.")

        fitted = self._validated_parameters(result.x)
        self._parameters = fitted
        return {
            "fixtures": len(observations),
            "penalized_negative_log_likelihood": float(result.fun),
            "initial_penalized_negative_log_likelihood": float(initial_objective),
            "parameters": self.parameters,
            "optimizer_message": str(result.message),
            "requires_out_of_sample_validation": True,
        }

    def expected_return(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """Odds are used for valuation only; they never enter the forecast."""
        if side not in {"yes", "no"}:
            raise ValueError("side must be 'yes' or 'no'.")
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds <= 1.0:
            raise ValueError("Decimal odds must exceed one.")
        yes = self.btts_probability(game_context)
        probability = yes if side == "yes" else 1.0 - yes
        expected_profit = probability * odds - 1.0
        return {
            "side": side,
            "probability": probability,
            "expected_profit_per_unit_staked": expected_profit,
            # This threshold is the explicit contract eligibility requirement.
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }