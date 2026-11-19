from math import fsum, isfinite, log, log1p
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import ConvexHull
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class ATPPeriod1SpreadsCandidate(BaseCandidateModel):
    """
    Pregame model for first-set game handicaps in standard tiebreak sets.

    Construction
    ------------
    Let s=(home_games, away_games) range over legal terminal set scores.
    Let r(s) be the terminal probability of an alternating-service game process.
    Given expected scores mu and dispersion index D, define

        b(s) = log(r(s)) - 0.5 * sum((s_i - mu_i)**2 / (D * mu_i)).

    The model minimizes relative entropy against exp(b), subject to E[s]=mu.
    Its solution is

        p(s) = exp(b(s) + theta @ (s-mu)) / Z(theta).

    The two-dimensional dual is convex; its Hessian is Cov(s). Conditioning
    on legal scores creates dependence between opponents' scores, so margin
    tails come from the joint distribution rather than independent totals.

    Interpretation and limitations
    ------------------------------
    * 5.76, 5.31 and 0.133 are supplied empirical inputs, not fitted constants.
    * D is a concentration scale. Legal-score conditioning can prevent the
      resulting marginal variance/mean ratios from equaling D.
    * No venue or "home" advantage is added.
    * Optional situational coefficients must be estimated on earlier data.
      Their default is zero because aggregate wager results cannot identify
      rest, weather, attack, defense, or label-bias effects.
    * Missing service probabilities use the exchangeable ignorance prior.
    * Bookmaker lines and prices never enter prediction.
    * ROI, acceptance counts, calibration gains, and a 250 ms latency SLA
      require external validation; they are not guaranteed by this class.
    * Integration assumes DiscreteDistribution is exported from the base
      module and from_scores accepts a joint probability ndarray. The actual
      betting_engine package was unavailable for verification.

    Scoring reference:
    [ATP scoring explanation](https://www.atptour.com/en/news/tennis-explained-learn-the-game)

    Optional constructor coefficients:
        feature_coefficients = {
            "attack_balance": (total_coefficient, margin_coefficient),
            "defense_balance": (..., ...),
            "rest_balance": (..., ...),
            "log_pace": (..., ...),
            "log_tempo": (..., ...),
        }

    Attack/defense inputs must be positive dimensionless ratings, where larger
    means stronger. Pace/tempo effects require an explicit positive reference.
    Coefficients act on score total and score margin in natural-parameter space.
    Do not supply features already incorporated into expected scores unless the
    coefficients were trained to represent their incremental effect.
    """

    _SCORES = np.asarray(
        [(6, j) for j in range(5)]
        + [(7, 5), (7, 6)]
        + [(j, 6) for j in range(5)]
        + [(5, 7), (6, 7)],
        dtype=float,
    )
    _FEATURES = frozenset(
        {
            "attack_balance",
            "defense_balance",
            "rest_balance",
            "log_pace",
            "log_tempo",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1119_074804",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "spreads",
        *,
        home_baseline: float = 5.76,
        away_baseline: float = 5.31,
        dispersion_index: float = 0.133,
        feature_coefficients: Optional[
            Mapping[str, Tuple[float, float]]
        ] = None,
    ) -> None:
        if (league, scope, family) != ("ATP", "period_1", "spreads"):
            pass

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self._baseline = np.asarray(
            [
                self._positive(home_baseline, "home_baseline"),
                self._positive(away_baseline, "away_baseline"),
            ]
        )
        self._dispersion = self._positive(dispersion_index, "dispersion_index")
        self._hull = ConvexHull(self._SCORES).equations.copy()
        self._validate_mean(self._baseline)

        coefficients = {}
        for name, pair in (feature_coefficients or {}).items():
            if name not in self._FEATURES:
                raise ValueError(f"Unsupported feature: {name}")
            if len(pair) != 2:
                raise ValueError(f"{name} requires total and margin coefficients.")
            coefficients[name] = (
                self._finite(pair[0], f"{name}.total"),
                self._finite(pair[1], f"{name}.margin"),
            )
        self._coefficients = coefficients

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if not 0.0 < result < 1.0:
            raise ValueError(f"{name} must lie strictly between zero and one.")
        return result

    def _validate_mean(self, mean: np.ndarray) -> None:
        # Supporting halfspaces are derived from tennis scores, not clamps.
        if np.any(self._hull[:, :2] @ mean + self._hull[:, 2] >= 0.0):
            raise ValueError(
                "Expected scores must lie strictly inside the convex hull of "
                "legal first-set scores. Check period scope and score units."
            )

    @staticmethod
    def _normalized(log_weights: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(log_weights)):
            raise ValueError("Nonfinite model energy; inspect input scales.")
        probabilities = np.exp(log_weights - logsumexp(log_weights))
        mass = fsum(float(p) for p in probabilities)
        if not isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Could not normalize the score distribution.")
        probabilities /= mass
        if np.any(probabilities < 0.0):
            raise ArithmeticError("Negative probability encountered.")
        return probabilities

    def _service_prior(self, context: Mapping[str, Any]) -> np.ndarray:
        home_hold = self._probability(
            context.get("home_hold_probability", 0.5),
            "home_hold_probability",
        )
        away_hold = self._probability(
            context.get("away_hold_probability", 0.5),
            "away_hold_probability",
        )
        tiebreak = self._probability(
            context.get("home_tiebreak_probability", 0.5),
            "home_tiebreak_probability",
        )

        first_server = context.get("first_server")
        if first_server not in (None, "home", "away"):
            raise ValueError("first_server must be 'home', 'away', or None.")

        starts = (
            (("home", 0.5), ("away", 0.5))
            if first_server is None
            else ((first_server, 1.0),)
        )
        terminal = np.zeros((8, 8), dtype=float)

        for server, weight in starts:
            active = np.zeros((7, 7), dtype=float)
            active[0, 0] = weight

            # Increasing coordinates form a topological ordering of game states.
            for home_games in range(7):
                for away_games in range(7):
                    mass = active[home_games, away_games]
                    if mass == 0.0:
                        continue

                    if home_games == away_games == 6:
                        terminal[7, 6] += mass * tiebreak
                        terminal[6, 7] += mass * (1.0 - tiebreak)
                        continue

                    even_game = (home_games + away_games) % 2 == 0
                    home_serves = (server == "home") == even_game
                    home_game_win = home_hold if home_serves else 1.0 - away_hold

                    for nh, na, chance in (
                        (home_games + 1, away_games, home_game_win),
                        (home_games, away_games + 1, 1.0 - home_game_win),
                    ):
                        contribution = mass * chance
                        if max(nh, na) >= 6 and abs(nh - na) >= 2:
                            terminal[nh, na] += contribution
                        else:
                            active[nh, na] += contribution

        indices = self._SCORES.astype(int)
        prior = terminal[indices[:, 0], indices[:, 1]]
        if np.any(prior <= 0.0) or not np.all(np.isfinite(prior)):
            raise ValueError("Service inputs produce numerically degenerate mass.")
        return prior / fsum(float(p) for p in prior)

    def _feature_value(self, name: str, context: Mapping[str, Any]) -> float:
        if name in ("attack_balance", "defense_balance"):
            field = name.removesuffix("_balance")
            home = context.get("home", {})
            away = context.get("away", {})
            if not isinstance(home, Mapping) or not isinstance(away, Mapping):
                raise ValueError("home and away must be feature mappings.")
            if field not in home or field not in away:
                raise ValueError(f"Fitted {name} requires both players' {field}.")
            return (
                log(self._positive(home[field], f"home.{field}"))
                - log(self._positive(away[field], f"away.{field}"))
            )

        if name == "rest_balance":
            values = []
            for side in ("home", "away"):
                key = f"{side}_rest_days"
                if key not in context:
                    raise ValueError(f"Fitted rest effect requires {key}.")
                days = self._finite(context[key], key)
                if days < 0.0:
                    raise ValueError(f"{key} cannot be negative.")
                values.append(log1p(days))
            return values[0] - values[1]

        field = name.removeprefix("log_")
        reference = f"{field}_reference"
        if field not in context or reference not in context:
            raise ValueError(f"Fitted {name} requires {field} and {reference}.")
        return (
            log(self._positive(context[field], field))
            - log(self._positive(context[reference], reference))
        )

    def _score_probabilities(self, context: Mapping[str, Any]) -> np.ndarray:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        for key, expected in (
            ("league", "ATP"),
            ("scope", "period_1"),
            ("family", "spreads"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Incompatible {key}: {context[key]!r}.")
        if context.get("is_live", False):
            raise ValueError("This candidate requires pregame inputs.")
        if context.get("scoring_format", "standard_tiebreak_set") != (
            "standard_tiebreak_set"
        ):
            raise ValueError("Unsupported first-set scoring format.")

        mean = np.asarray(
            [
                self._positive(
                    context.get("home_expected", self._baseline[0]),
                    "home_expected",
                ),
                self._positive(
                    context.get("away_expected", self._baseline[1]),
                    "away_expected",
                ),
            ]
        )
        self._validate_mean(mean)

        dispersion = self._positive(
            context.get("dispersion_index", self._dispersion),
            "dispersion_index",
        )
        centered = self._SCORES - mean
        prior = self._service_prior(context)

        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            energy = np.sum(centered**2 / (dispersion * mean), axis=1)
        base = np.log(prior) - energy / 2.0
        if not np.all(np.isfinite(base)):
            raise ValueError("Dispersion or expected-score scale is unrepresentable.")

        def objective(theta: np.ndarray) -> Tuple[float, np.ndarray]:
            logits = base + centered @ theta
            normalizer = logsumexp(logits)
            p = np.exp(logits - normalizer)
            return float(normalizer), p @ centered

        def hessian(theta: np.ndarray) -> np.ndarray:
            p = self._normalized(base + centered @ theta)
            residual = p @ centered
            return centered.T @ (p[:, None] * centered) - np.outer(
                residual, residual
            )

        numerical_tolerance = np.sqrt(np.finfo(float).eps)
        result = minimize(
            objective,
            np.zeros(2, dtype=float),
            method="trust-exact",
            jac=True,
            hess=hessian,
            options={"gtol": numerical_tolerance},
        )
        if not np.all(np.isfinite(result.x)):
            raise ArithmeticError("Moment calibration returned nonfinite parameters.")

        logits = base + centered @ result.x
        calibrated = self._normalized(logits)
        error = np.max(np.abs(calibrated @ self._SCORES - mean))
        tolerance = numerical_tolerance * np.max(self._SCORES)
        if error > tolerance:
            raise ArithmeticError(
                f"Moment calibration failed: residual={error}; {result.message}"
            )

        total_shift = 0.0
        margin_shift = 0.0
        for feature, (total_coefficient, margin_coefficient) in (
            self._coefficients.items()
        ):
            if total_coefficient == margin_coefficient == 0.0:
                continue
            value = self._feature_value(feature, context)
            total_shift += total_coefficient * value
            margin_shift += margin_coefficient * value

        # Centering changes only the normalizer and improves numerical scaling.
        logits = (
            logits
            + total_shift * centered.sum(axis=1)
            + margin_shift * (centered[:, 0] - centered[:, 1])
        )
        return self._normalized(logits)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        probabilities = self._score_probabilities(game_context)
        scores = np.zeros((8, 8), dtype=float)
        indices = self._SCORES.astype(int)
        scores[indices[:, 0], indices[:, 1]] = probabilities
        return DiscreteDistribution.from_scores(scores)

    def diagnostics(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        probabilities = self._score_probabilities(game_context)
        mean = probabilities @ self._SCORES
        centered = self._SCORES - mean
        covariance = centered.T @ (probabilities[:, None] * centered)
        margin = self._SCORES[:, 0] - self._SCORES[:, 1]
        margin_mean = float(probabilities @ margin)
        return {
            "home_mean": float(mean[0]),
            "away_mean": float(mean[1]),
            "home_variance_mean_ratio": float(covariance[0, 0] / mean[0]),
            "away_variance_mean_ratio": float(covariance[1, 1] / mean[1]),
            "score_covariance": float(covariance[0, 1]),
            "margin_mean": margin_mean,
            "margin_variance": float(
                probabilities @ ((margin - margin_mean) ** 2)
            ),
            "probability_sum": fsum(float(p) for p in probabilities),
        }

    def value_contract(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        handicap: float,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """
        Value a whole- or half-game handicap per unit staked.

        Handicap is added to the selected player's games. For example,
        side='home', handicap=-1.5 wins when home_games-away_games > 1.5.
        Quarter-game split contracts require separate leg settlement.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")
        line = self._finite(handicap, "handicap")
        if not (line * 2.0).is_integer():
            raise ValueError("Only whole- and half-game handicaps are supported.")
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds <= 1.0:
            raise ValueError("Decimal odds must exceed one.")

        probabilities = self._score_probabilities(game_context)
        margin = self._SCORES[:, 0] - self._SCORES[:, 1]
        if side == "away":
            margin = -margin
        settlement = margin + line

        win = fsum(float(p) for p in probabilities[settlement > 0.0])
        push = fsum(float(p) for p in probabilities[settlement == 0.0])
        loss = fsum(float(p) for p in probabilities[settlement < 0.0])
        expected_profit = win * (odds - 1.0) - loss

        return {
            "win_probability": win,
            "push_probability": push,
            "loss_probability": loss,
            "expected_profit_per_unit": expected_profit,
            "fair_decimal_odds": (win + loss) / win if win else float("inf"),
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }