from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

import math
import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAFirstHalfHomeTotalModel(BaseCandidateModel):
    """
    Independent NBA first-half home-total model.

    Historical game rows must contain pregame features and
    `home_half_1_points`. Call fit() on training games only.

    Derivation
    ----------
    For possession count N and possession scoring X:

        E[H] = E[N] E[X]
        Var(H) = E[N] Var(X) + Var(N) E[X]**2.

    Aggregate scores alone cannot separately identify these variance
    components. This model therefore estimates their identifiable aggregate
    effect rather than inventing possession probabilities.

    Given positive aggregate scoring intensity and constraints on its
    arithmetic and logarithmic moments, the maximum-entropy density is
    gamma. Integrating that density over integer-score cells produces an
    asymmetric discrete scoring distribution. Its dispersion is fitted
    directly by score likelihood; bookmaker lines never enter the model.

    The supplied 0.187 index is not used: its denominator and provenance
    are unspecified, and Var/Mean < 1 does not establish a heavy tail.
    Without training data, predictions use an explicitly provisional
    Poisson distribution around the supplied empirical mean.

    The 56 and 54 anchors are supplied empirical inputs. Other numerical
    tolerances derive from floating-point precision, not probability clamps.

    `DiscreteDistribution` must be available in the execution scope, as
    specified by the candidate-model interface.

    No historical data or Crucible runner was supplied with this class.
    Performance, calibration, and latency must be validated externally.
    """

    FEATURE_NAMES = (
        "intercept",
        "log_home_expected",
        "log_away_expected",
        "log_home_attack",
        "log_away_defense",
        "log_home_pace",
        "log_away_pace",
        "log_home_rest",
        "log_away_rest",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_185140",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NBA", "half_1", "home_total"):
            raise ValueError("This model supports NBA|half_1|home_total only.")

        self.home_anchor = 56.0
        self.away_anchor = 54.0
        self._eps = np.finfo(np.float64).eps
        self._beta = np.zeros(len(self.FEATURE_NAMES), dtype=np.float64)
        self._beta[1] = 1.0
        self._log_dispersion: Optional[float] = None
        self.fit_diagnostics_: Dict[str, Any] = {}

    @staticmethod
    def _finite(value: Any, default: float) -> float:
        if value is None or isinstance(value, (bool, np.bool_)):
            return default
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if math.isfinite(result) else default

    @classmethod
    def _positive(cls, value: Any, default: float) -> float:
        result = cls._finite(value, default)
        return result if result > 0.0 else default

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        value = context.get(side)
        return value if isinstance(value, Mapping) else {}

    @classmethod
    def _rating_feature(
        cls,
        context: Mapping[str, Any],
        team: Mapping[str, Any],
        field: str,
    ) -> float:
        """
        Ratios are usable directly. Absolute ratings require a finite
        league_rating, avoiding an invented league-rating constant.
        """
        ratio = cls._positive(team.get(field + "_ratio"), float("nan"))
        if math.isfinite(ratio):
            return math.log(ratio)

        rating = cls._positive(team.get(field), float("nan"))
        league = cls._positive(context.get("league_rating"), float("nan"))
        if math.isfinite(rating) and math.isfinite(league):
            return math.log(rating) - math.log(league)
        return 0.0

    @classmethod
    def _pace_feature(
        cls,
        context: Mapping[str, Any],
        team: Mapping[str, Any],
        side: str,
    ) -> float:
        pace = cls._positive(
            context.get(side + "_pace", team.get("pace")), float("nan")
        )
        league_pace = cls._positive(context.get("league_pace"), float("nan"))
        if math.isfinite(pace) and math.isfinite(league_pace):
            return math.log(pace) - math.log(league_pace)
        return 0.0

    def _features(self, context: Mapping[str, Any]) -> np.ndarray:
        home = self._team(context, "home")
        away = self._team(context, "away")
        home_mean = self._positive(
            context.get("home_expected"), self.home_anchor
        )
        away_mean = self._positive(
            context.get("away_expected"), self.away_anchor
        )
        home_rest = self._finite(context.get("home_rest_days"), 0.0)
        away_rest = self._finite(context.get("away_rest_days"), 0.0)
        if home_rest < 0.0:
            home_rest = 0.0
        if away_rest < 0.0:
            away_rest = 0.0

        return np.array(
            [
                1.0,
                math.log(home_mean) - math.log(self.home_anchor),
                math.log(away_mean) - math.log(self.away_anchor),
                self._rating_feature(context, home, "attack"),
                self._rating_feature(context, away, "defense"),
                self._pace_feature(context, home, "home"),
                self._pace_feature(context, away, "away"),
                math.log1p(home_rest),
                math.log1p(away_rest),
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _integer_scores(values: Iterable[Any]) -> np.ndarray:
        scores = np.asarray(list(values), dtype=np.float64)
        if scores.ndim != 1 or scores.size == 0:
            raise ValueError("Training scores must be a nonempty vector.")
        if not np.all(np.isfinite(scores)):
            raise ValueError("Training scores must be finite.")
        if np.any(scores < 0.0) or np.any(scores != np.floor(scores)):
            raise ValueError("Training scores must be nonnegative integers.")
        return scores

    @staticmethod
    def _gamma_cells(
        scores: np.ndarray,
        means: np.ndarray,
        dispersion: float,
    ) -> np.ndarray:
        """
        Y = floor(Z + 1/2), Z ~ Gamma(mean / dispersion, dispersion).

        CDF differences are used in the lower tail; survival differences
        are used in the upper tail to avoid catastrophic cancellation.
        """
        lower = np.maximum(scores - 0.5, 0.0) / dispersion
        upper = (scores + 0.5) / dispersion
        shape = means / dispersion

        cdf_lower = special.gammainc(shape, lower)
        cdf_upper = special.gammainc(shape, upper)
        sf_lower = special.gammaincc(shape, lower)
        sf_upper = special.gammaincc(shape, upper)

        return np.where(
            cdf_upper <= 0.5,
            cdf_upper - cdf_lower,
            sf_lower - sf_upper,
        )

    def fit(
        self,
        games: Iterable[Mapping[str, Any]],
        target_key: str = "home_half_1_points",
    ) -> "NBAFirstHalfHomeTotalModel":
        """
        Fit score likelihood on one row per historical game.

        Wager-level rows must first be deduplicated by game. Features must
        be available before tipoff; outcomes, lines, odds, and settlement
        information must not be embedded in expected-score inputs.

        Only identifiable feature directions are fitted. Fit is atomic:
        failed optimization leaves the existing model unchanged.
        """
        rows = list(games)
        if not rows:
            raise ValueError("No historical games supplied.")
        if any(not isinstance(row, Mapping) for row in rows):
            raise TypeError("Each historical game must be a mapping.")
        if any(target_key not in row for row in rows):
            raise ValueError("Every training game needs " + target_key + ".")

        y = self._integer_scores(row[target_key] for row in rows)
        x = np.vstack([self._features(row) for row in rows])

        if np.mean(y) <= 0.0 or np.var(y) <= 0.0:
            raise ValueError("Training scores need positive mean and variance.")

        # SVD removes unidentifiable directions without imposing a ridge
        # coefficient or choosing arbitrary feature-selection thresholds.
        _, singular, vt = np.linalg.svd(x, full_matrices=False)
        tolerance = self._eps * max(x.shape) * singular[0]
        rank = int(np.count_nonzero(singular > tolerance))
        if y.size <= rank + 1:
            raise ValueError(
                "More independent games than mean and dispersion parameters "
                "are required."
            )

        basis = vt[:rank].T
        design = x @ basis

        prior_beta = np.zeros(x.shape[1], dtype=np.float64)
        prior_beta[1] = 1.0
        prior_eta = math.log(self.home_anchor) + x @ prior_beta

        def poisson_objective(theta: np.ndarray):
            eta = prior_eta + design @ theta
            with np.errstate(over="ignore", invalid="ignore"):
                mu = np.exp(eta)
            if not np.all(np.isfinite(mu)) or np.any(mu <= 0.0):
                return float("inf"), np.zeros_like(theta)
            value = float(np.mean(mu - y * eta))
            gradient = design.T @ (mu - y) / y.size
            return value, gradient

        mean_fit = optimize.minimize(
            poisson_objective,
            np.zeros(rank, dtype=np.float64),
            method="BFGS",
            jac=True,
        )
        if not np.isfinite(mean_fit.fun):
            raise RuntimeError("Initial mean fit failed.")

        initial_eta = prior_eta + design @ mean_fit.x
        initial_mu = np.exp(initial_eta)
        initial_phi = float(np.mean((y - initial_mu) ** 2 / initial_mu))
        if not math.isfinite(initial_phi) or initial_phi <= 0.0:
            raise ValueError("Residual dispersion is not identifiable.")

        initial = np.concatenate(
            [mean_fit.x, np.array([math.log(initial_phi)])]
        )

        def objective(parameters: np.ndarray) -> float:
            eta = prior_eta + design @ parameters[:-1]
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                mu = np.exp(eta)
                phi = float(np.exp(parameters[-1]))
            if (
                not np.all(np.isfinite(mu))
                or np.any(mu <= 0.0)
                or not math.isfinite(phi)
                or phi <= 0.0
            ):
                return float("inf")
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                probability = self._gamma_cells(y, mu, phi)
            if (
                not np.all(np.isfinite(probability))
                or np.any(probability <= 0.0)
            ):
                return float("inf")
            return -float(np.mean(np.log(probability)))

        fitted = optimize.minimize(objective, initial, method="BFGS")
        if not fitted.success or not np.isfinite(fitted.fun):
            # Derivative-free refinement addresses finite-difference
            # precision loss in very narrow integer scoring cells.
            start = fitted.x if np.isfinite(fitted.fun) else initial
            fitted = optimize.minimize(objective, start, method="Powell")

        if not fitted.success or not np.isfinite(fitted.fun):
            raise RuntimeError("Discrete scoring likelihood did not converge.")

        beta = prior_beta + basis @ fitted.x[:-1]
        log_dispersion = float(fitted.x[-1])
        if not np.all(np.isfinite(beta)) or not math.isfinite(log_dispersion):
            raise RuntimeError("Fit produced nonfinite parameters.")

        self._beta = beta
        self._log_dispersion = log_dispersion
        self.fit_diagnostics_ = {
            "games": int(y.size),
            "identifiable_mean_parameters": rank,
            "negative_log_likelihood_per_game": float(fitted.fun),
            "latent_variance_to_mean": math.exp(log_dispersion),
            "observed_score_mean": float(np.mean(y)),
            "observed_score_variance": float(np.var(y, ddof=1)),
            "coefficients": dict(zip(self.FEATURE_NAMES, beta.tolist())),
            "validation": "Training likelihood only; out-of-sample testing required.",
        }
        return self

    @staticmethod
    def _normalize(probability: np.ndarray) -> np.ndarray:
        if probability.ndim != 1 or probability.size == 0:
            raise ArithmeticError("Invalid score probability vector.")
        if not np.all(np.isfinite(probability)) or np.any(probability < 0.0):
            raise ArithmeticError("Nonfinite or negative score probability.")
        mass = float(np.sum(probability, dtype=np.float64))
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Score distribution has no finite mass.")
        return probability / mass

    def _home_pmf(self, context: Mapping[str, Any]) -> np.ndarray:
        eta = math.log(self.home_anchor) + float(
            self._features(context) @ self._beta
        )
        try:
            mean = math.exp(eta)
        except OverflowError as error:
            raise ValueError("Context implies an unrepresentable mean.") from error
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError("Context implies an invalid mean.")

        # The lattice truncation tolerance is numerical precision, not a
        # learned tail parameter or an economic eligibility threshold.
        if self._log_dispersion is None:
            bound = stats.poisson.isf(self._eps, mean)
            if not math.isfinite(float(bound)):
                raise ArithmeticError("Could not bound the Poisson score tail.")
            support = np.arange(int(math.ceil(float(bound))) + 1, dtype=float)
            return self._normalize(stats.poisson.pmf(support, mean))

        dispersion = math.exp(self._log_dispersion)
        shape = mean / dispersion
        bound = stats.gamma.isf(self._eps, a=shape, scale=dispersion)
        if not math.isfinite(float(bound)):
            raise ArithmeticError("Could not bound the gamma score tail.")

        maximum = int(math.ceil(float(bound) + 0.5))
        support = np.arange(maximum + 1, dtype=np.float64)
        probability = self._gamma_cells(
            support, np.full_like(support, mean), dispersion
        )
        return self._normalize(probability)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """
        Return a score lattice whose home marginal is the target forecast.

        For this home-total-only model the single away column is a nuisance
        coordinate, not an away-score prediction. This distribution must
        not be reused to price away totals, margins, or moneylines.
        """
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        home_probability = self._home_pmf(game_context)
        scores = home_probability.reshape((-1, 1))
        return DiscreteDistribution.from_scores(scores)

    def price_contract(
        self,
        game_context: Dict[str, Any],
        line: float,
        side: str,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """
        Price a standard integer or half-integer total, including pushes.

        Expected profit is per unit stake. Contract prices never influence
        the underlying scoring forecast.
        """
        line = float(line)
        decimal_odds = float(decimal_odds)
        if not math.isfinite(line) or line < 0.0:
            raise ValueError("line must be finite and nonnegative.")
        if not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
            raise ValueError("decimal_odds must be finite and greater than one.")
        if side not in ("over", "under"):
            raise ValueError("side must be 'over' or 'under'.")
        if line * 2.0 != math.floor(line * 2.0):
            raise ValueError("Only integer and half-integer contracts are supported.")

        probability = self._home_pmf(game_context)
        support = np.arange(probability.size, dtype=np.float64)
        push = float(np.sum(probability[support == line]))
        winning = support > line if side == "over" else support < line
        losing = support < line if side == "over" else support > line
        win = float(np.sum(probability[winning]))
        loss = float(np.sum(probability[losing]))
        expectation = win * (decimal_odds - 1.0) - loss

        return {
            "win_probability": win,
            "push_probability": push,
            "loss_probability": loss,
            "expected_profit_per_unit": expectation,
            "fair_decimal_odds": (
                1.0 + loss / win if win > 0.0 else float("inf")
            ),
            "eligible": decimal_odds >= 1.40 and expectation > 0.0,
            "historically_fitted": self._log_dispersion is not None,
        }