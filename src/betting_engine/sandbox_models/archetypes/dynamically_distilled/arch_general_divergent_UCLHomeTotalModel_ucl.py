from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
import math

import numpy as np
from scipy.optimize import brentq, least_squares, minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLHomeTotalModel(BaseCandidateModel):
    """
    Independent, moment-anchored home-goal distribution.

    The supplied aggregate moments initialize a finite integer score law by
    minimum relative entropy against a Poisson reference. Situational inputs
    change its mean through exponential tilting, preserving a coherent CDF
    across every total line. Bookmaker odds and contract sides are not inputs.

    Optional fit() learns situational coefficients from unique, chronologically
    ordered completed games, using forward validation to choose shrinkage.

    Expected context:
        home_expected, away_expected: nonnegative expected goal counts
        home/away: optional mappings containing attack and defense multipliers
        tempo or pace: optional positive multiplier, with neutral value one
        home_rest_days, away_rest_days: optional nonnegative values

    Attack/defense values must be multiplicative strengths, with neutral value
    one; defense greater than one means greater defensive strength. Do not
    supply these multipliers if home_expected already incorporates them.

    Weather and venue are intentionally unused without fitted, documented
    feature semantics. Unvalidated signs or magnitudes would be invented.

    The dispersion/skewness supplied in the request are assumed to describe
    home goals, not wager outcomes or pooled home/away scores. That assumption
    must be checked against the underlying historical games.

    Aggregate wager summaries cannot establish out-of-sample profitability,
    remove calibration residuals, or guarantee a Crucible pass. Validation
    must use unseen games, actual settlement rules, and closing market prices.
    Numerical support truncation uses machine precision, not a betting filter.
    """

    HOME_MEAN = 1.60
    AWAY_MEAN = 1.20
    DISPERSION = 0.461
    SKEWNESS = 0.281

    FEATURE_NAMES = (
        "intercept",
        "home_expected",
        "away_expected",
        "home_attack",
        "away_defense",
        "tempo",
        "rest_balance",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1125_014439",
        version="1.0.0",
        league="UCL",
        scope="full_game",
        family="home_total",
    ):
        if (league, scope, family) != ("UCL", "full_game", "home_total"):
            pass

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self._eps = np.finfo(float).eps
        self._tol = math.sqrt(self._eps)
        self._scores = self._support(self.HOME_MEAN)
        self._log_reference = self._moment_reference(self._scores)

        # Unit elasticities follow the documented multiplicative exposure
        # semantics. Cross-team and rest effects require game-level fitting.
        self._prior_beta = np.array(
            [0.0, 1.0, 0.0, 1.0, -1.0, 1.0, 0.0],
            dtype=float,
        )
        self._beta = self._prior_beta.copy()
        self.fit_diagnostics_: Optional[Dict[str, Any]] = None

    @staticmethod
    def _number(value: Any, name: str, strictly_positive: bool = False) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if result < 0.0 or (strictly_positive and result == 0.0):
            raise ValueError(f"{name} has an invalid sign.")
        return result

    def _get(
        self,
        mapping: Mapping[str, Any],
        name: str,
        default: float,
        strictly_positive: bool = False,
    ) -> float:
        value = mapping.get(name)
        return self._number(
            default if value is None else value, name, strictly_positive
        )

    def _support(self, mean: float) -> np.ndarray:
        if mean == 0.0:
            return np.array([0.0])
        upper = float(poisson.isf(self._eps, mean))
        if not math.isfinite(upper):
            raise ValueError("Unable to construct a finite numerical score support.")
        return np.arange(int(math.ceil(upper)) + 1, dtype=float)

    def _moment_reference(self, scores: np.ndarray) -> np.ndarray:
        variance = self.HOME_MEAN * self.DISPERSION
        sd = math.sqrt(variance)
        z = (scores - self.HOME_MEAN) / sd
        statistics = np.column_stack((z, z * z, z * z * z))
        target = np.array([0.0, 1.0, self.SKEWNESS])

        log_prior = (
            scores * math.log(self.HOME_MEAN)
            - self.HOME_MEAN
            - gammaln(scores + 1.0)
        )

        def moments(theta):
            log_weights = log_prior + statistics @ theta
            probabilities = np.exp(log_weights - logsumexp(log_weights))
            expected = probabilities @ statistics
            centered = statistics - expected
            covariance = centered.T @ (probabilities[:, None] * centered)
            return expected - target, covariance

        solution = least_squares(
            fun=lambda theta: moments(theta)[0],
            jac=lambda theta: moments(theta)[1],
            x0=np.zeros(target.size),
            ftol=self._tol,
            xtol=self._tol,
            gtol=self._eps,
        )
        residual, _ = moments(solution.x)
        if not solution.success or np.linalg.norm(residual, ord=np.inf) > self._tol:
            raise RuntimeError(
                "The supplied home-goal moments could not be matched reliably."
            )

        log_weights = log_prior + statistics @ solution.x
        return log_weights - logsumexp(log_weights)

    def _features(self, context: Mapping[str, Any]) -> np.ndarray:
        home = context.get("home") or {}
        away = context.get("away") or {}
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise ValueError("home and away must be mappings of team features.")

        home_expected = self._get(
            context, "home_expected", self.HOME_MEAN, strictly_positive=True
        )
        away_expected = self._get(context, "away_expected", self.AWAY_MEAN)
        home_attack = self._get(home, "attack", 1.0, strictly_positive=True)
        away_defense = self._get(away, "defense", 1.0, strictly_positive=True)

        tempo_value = context.get("tempo")
        if tempo_value is None:
            tempo_value = context.get("pace")
        tempo = self._number(
            1.0 if tempo_value is None else tempo_value,
            "tempo",
            strictly_positive=True,
        )

        home_rest = context.get("home_rest_days")
        away_rest = context.get("away_rest_days")
        rest_balance = 0.0
        if home_rest is not None and away_rest is not None:
            home_rest = self._number(home_rest, "home_rest_days")
            away_rest = self._number(away_rest, "away_rest_days")
            total_rest = home_rest + away_rest
            if total_rest > 0.0:
                rest_balance = (home_rest - away_rest) / total_rest

        # The relative away-mean difference permits zero away expectation.
        return np.array(
            [
                1.0,
                math.log(home_expected / self.HOME_MEAN),
                (away_expected - self.AWAY_MEAN) / self.AWAY_MEAN,
                math.log(home_attack),
                math.log(away_defense),
                math.log(tempo),
                rest_balance,
            ],
            dtype=float,
        )

    def _home_law(self, mean: float):
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError("The predicted home-goal mean must be finite and positive.")
        if mean >= self._scores[-1]:
            raise ValueError(
                "Home expectation exceeds the calibrated numerical support; "
                "refit the score model with appropriate domain data."
            )

        def evaluate(theta):
            weights = self._log_reference + theta * self._scores
            log_probabilities = weights - logsumexp(weights)
            probabilities = np.exp(log_probabilities)
            expected = float(probabilities @ self._scores)
            return expected, log_probabilities, probabilities

        def residual(theta):
            return evaluate(theta)[0] - mean

        at_zero = residual(0.0)
        if abs(at_zero) <= self._eps * max(1.0, mean):
            theta = 0.0
        else:
            # One natural-parameter unit initializes numerical bracketing;
            # doubling expands the search and imposes no forecast clamp.
            direction = 1.0 if at_zero < 0.0 else -1.0
            endpoint = direction
            while residual(endpoint) * at_zero > 0.0:
                endpoint *= 2.0
                if not math.isfinite(endpoint):
                    raise RuntimeError("Unable to bracket the mean-matching tilt.")
            theta = brentq(
                residual,
                min(0.0, endpoint),
                max(0.0, endpoint),
                xtol=self._eps,
                rtol=4.0 * self._eps,
            )

        expected, log_probabilities, probabilities = evaluate(theta)
        variance = float(
            probabilities @ ((self._scores - expected) ** 2)
        )
        if variance <= 0.0 or not math.isfinite(variance):
            raise RuntimeError("Numerically degenerate home-goal distribution.")
        return log_probabilities, probabilities, variance

    def _mean(self, features: np.ndarray, beta: np.ndarray) -> float:
        log_mean = math.log(self.HOME_MEAN) + float(features @ beta)
        try:
            return math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Situational features imply an invalid goal mean.") from exc

    def fit(
        self,
        games: Iterable[Mapping[str, Any]],
        regularization_candidates: Optional[Sequence[float]] = None,
    ):
        """
        Fit on unique games in strictly increasing `kickoff_order`.

        Required fields:
            game_id: unique, hashable game identifier
            kickoff_order: strictly increasing numeric chronological index
            home_goals: observed nonnegative integer goal count

        Each row otherwise uses the predict_distribution context schema.
        Use kickoff_order to define chronological game groups externally if
        games share a kickoff. Never pass repeated wager rows as games.

        The last sqrt(n) games select regularization by predictive log loss.
        They become training data in the final refit and are therefore NOT an
        out-of-sample performance report. Crucible needs a later untouched set.
        """
        rows = list(games)
        dimension = len(self.FEATURE_NAMES)
        if len(rows) <= dimension + 1:
            raise ValueError("Insufficient unique games for forward validation.")

        seen = set()
        previous_order = -math.inf
        features = []
        outcomes = []

        for row in rows:
            game_id = row["game_id"]
            if game_id in seen:
                raise ValueError("Duplicate game_id: wager rows are not independent games.")
            seen.add(game_id)

            order = float(row["kickoff_order"])
            if not math.isfinite(order) or order <= previous_order:
                raise ValueError("Games must have strictly increasing kickoff_order.")
            previous_order = order

            goals = self._number(row["home_goals"], "home_goals")
            if goals != math.floor(goals) or goals > self._scores[-1]:
                raise ValueError("home_goals must be an integer within model support.")
            outcomes.append(int(goals))
            features.append(self._features(row))

        x = np.asarray(features)
        y = np.asarray(outcomes, dtype=int)
        holdout_size = int(math.ceil(math.sqrt(len(rows))))
        split = len(rows) - holdout_size
        if split <= dimension:
            raise ValueError("Insufficient training games after forward splitting.")

        scales = np.sqrt(np.mean(x[:split] ** 2, axis=0))
        scales[scales == 0.0] = 1.0
        standardized = x / scales
        prior = self._prior_beta * scales

        if regularization_candidates is None:
            count = int(math.ceil(math.sqrt(len(rows))))
            candidates = np.concatenate(
                ([0.0], np.geomspace(1.0 / len(rows), float(len(rows)), count))
            )
        else:
            candidates = np.asarray(
                [
                    self._number(value, "regularization")
                    for value in regularization_candidates
                ],
                dtype=float,
            )
            if candidates.size == 0:
                raise ValueError("Provide at least one regularization candidate.")

        def objective(beta, design, targets, penalty):
            loss = 0.0
            gradient = np.zeros_like(beta)
            try:
                for vector, target in zip(design, targets):
                    mean = self._mean(vector, beta)
                    log_p, _, variance = self._home_law(mean)
                    loss -= float(log_p[target])
                    # d log p(y)/d log(mean) = (y - mean) * mean / Var(Y).
                    gradient += vector * ((mean - target) * mean / variance)
            except (ValueError, RuntimeError):
                return math.inf, np.zeros_like(beta)

            delta = beta - prior
            loss += penalty * float(delta @ delta) / 2.0
            gradient += penalty * delta
            return loss, gradient

        best = None
        for penalty in candidates:
            result = minimize(
                objective,
                prior.copy(),
                args=(standardized[:split], y[:split], float(penalty)),
                method="L-BFGS-B",
                jac=True,
            )
            if not result.success or not np.isfinite(result.fun):
                continue
            validation_loss, _ = objective(
                result.x, standardized[split:], y[split:], 0.0
            )
            if not math.isfinite(validation_loss):
                continue
            if best is None or validation_loss < best[0]:
                best = (validation_loss, float(penalty), result.x.copy())

        if best is None:
            raise RuntimeError("No regularization candidate fitted successfully.")

        final = minimize(
            objective,
            best[2],
            args=(standardized, y, best[1]),
            method="L-BFGS-B",
            jac=True,
        )
        if not final.success or not np.isfinite(final.fun):
            raise RuntimeError("Final refit failed; existing model was not modified.")

        fitted_beta = final.x / scales
        if not np.all(np.isfinite(fitted_beta)):
            raise RuntimeError("Fitting produced nonfinite coefficients.")

        self._beta = fitted_beta
        self.fit_diagnostics_ = {
            "unique_games": len(rows),
            "forward_validation_games": holdout_size,
            "selected_regularization": best[1],
            "selection_log_loss_per_game": best[0] / holdout_size,
            "coefficients": dict(zip(self.FEATURE_NAMES, self._beta.tolist())),
            "requires_untouched_crucible_evaluation": True,
        }
        return self

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """
        Return a normalized matrix indexed [home_goals, away_goals].

        Away goals use an independent Poisson nuisance marginal. Its shape
        does not affect home-total prices; no joint residual correlation was
        supplied from which to identify a dependence model.

        The downstream settlement engine must distinguish wins, pushes, and
        split settlements for integer and quarter-goal lines. It must not
        interpret an unconditional win probability as push-adjusted value.

        No inference-time optimizer fits model parameters. The only scalar
        solve matches the situational mean. Measure the 250 ms requirement in
        the actual runtime; an unconditional wall-clock guarantee is impossible.
        """
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home_expected = self._get(
            game_context, "home_expected", self.HOME_MEAN
        )
        if home_expected == 0.0:
            home_p = np.array([1.0])
        else:
            features = self._features(game_context)
            mean = self._mean(features, self._beta)
            _, home_p, _ = self._home_law(mean)

        away_mean = self._get(game_context, "away_expected", self.AWAY_MEAN)
        away_scores = self._support(away_mean)
        if away_mean == 0.0:
            away_p = np.array([1.0])
        else:
            away_log_p = (
                away_scores * math.log(away_mean)
                - away_mean
                - gammaln(away_scores + 1.0)
            )
            away_p = np.exp(away_log_p - logsumexp(away_log_p))

        # Square padding accommodates score-matrix consumers that require
        # equal home and away support sizes without moving probability mass.
        size = max(home_p.size, away_p.size)
        scores = np.zeros((size, size), dtype=float)
        scores[:home_p.size, :away_p.size] = np.outer(home_p, away_p)

        total = float(scores.sum())
        if (
            not np.all(np.isfinite(scores))
            or np.any(scores < 0.0)
            or not math.isfinite(total)
            or total <= 0.0
        ):
            raise RuntimeError("Invalid score probability matrix.")

        scores /= total
        return DiscreteDistribution.from_scores(scores)