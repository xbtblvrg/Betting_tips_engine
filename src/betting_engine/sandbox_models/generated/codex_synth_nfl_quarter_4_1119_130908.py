from math import exp, floor, isfinite, log, sqrt
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import root
from scipy.special import logsumexp
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLQuarter4MoneylineModel(BaseCandidateModel):
    """
    Regulation fourth-quarter scoring model, including a genuine draw outcome.

    This model prices points scored DURING quarter four, not the final game
    winner or an overtime-inclusive contract.

    Derivation
    ----------
    Given a mean and variance, maximize entropy on the attainable NFL score
    lattice. A quarter score can be zero or any integer >= 2; one point alone
    is unattainable. The resulting marginal has probability proportional to

        exp(a * score + b * score**2).

    Joint outcomes are calculated from the full product of the two marginals.
    Independence is an explicit default assumption, not a conclusion obtainable
    from marginal moments. Paired score observations are needed to estimate
    dependence.

    Home-advantage treatment
    ------------------------
    The supplied 6.12 and 5.38 estimates establish the scoring scale and the
    reference points for situational changes. Their venue contrast is not
    automatically treated as predictive: the default is exchangeable at these
    reference inputs. A home contrast must be supplied from independently
    calibrated score data through ``home_log_advantage``.

    This is a structural symmetry assumption, not a correction estimated from
    wager PnL. Aggregate wins and PnL cannot identify calibrated probabilities,
    a home coefficient, dependence, or future betting profitability.

    Feature contract
    ----------------
    * home_expected / away_expected: nonnegative remaining Q4 point forecasts.
      If omitted, the supplied historical reference means are used.
    * home_scoring_exposure / away_scoring_exposure: nonnegative multiplicative
      exposures, defaulting to the multiplicative identity.
    * tempo_ratio: positive pace relative to the forecast's reference pace.
      Do not supply it if pace is already incorporated in expected points.
    * team dictionaries may contain attack and defense; their units must match
      those used when fitting feature coefficients.
    * ``feature_coefficients`` maps feature names to
      (home_log_mean_coefficient, away_log_mean_coefficient).
      Uncalibrated rest, weather, and team-strength coefficients are omitted,
      rather than invented.
    * home_dispersion_index / away_dispersion_index override the historical
      variance-to-mean index when supported by conditional score data.

    Numerical tolerances below are floating-point accuracy controls, not betting
    thresholds or probability clamps. Infeasible variances are projected onto
    the exact lattice-implied minimum variance.

    No bookmaker prices enter the predictive distribution. Runtime must be
    benchmarked in the deployment environment; no universal latency guarantee
    is possible from this implementation alone.
    """

    HOME_REFERENCE = 6.12
    AWAY_REFERENCE = 5.38
    HISTORICAL_DISPERSION = 1.0

    _EPS = np.finfo(np.float64).eps
    _TOL = sqrt(_EPS)

    _FEATURE_NAMES = frozenset(
        {
            "home_attack",
            "home_defense",
            "away_attack",
            "away_defense",
            "rest_difference",
            "rest_sum",
            "wind_speed",
            "temp",
            "is_dome",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_4_1119_130908",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_4",
        family: str = "moneyline",
        *,
        home_log_advantage: float = 0.0,
        feature_coefficients: Optional[
            Mapping[str, Tuple[float, float]]
        ] = None,
        feature_centers: Optional[Mapping[str, float]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NFL", "quarter_4", "moneyline"):
            raise ValueError("This model supports NFL|quarter_4|moneyline only.")

        self.home_log_advantage = self._finite(
            home_log_advantage, "home_log_advantage"
        )
        self.feature_coefficients: Dict[str, Tuple[float, float]] = {}
        self.feature_centers: Dict[str, float] = {}

        centers = dict(feature_centers or {})
        for name, pair in dict(feature_coefficients or {}).items():
            if name not in self._FEATURE_NAMES:
                raise ValueError(f"Unsupported calibrated feature: {name}")
            if len(pair) != 2:
                raise ValueError(f"{name} requires two coefficients.")
            if name not in centers:
                raise ValueError(f"A training-set center is required for {name}.")
            self.feature_coefficients[name] = (
                self._finite(pair[0], f"{name}.home_coefficient"),
                self._finite(pair[1], f"{name}.away_coefficient"),
            )
            self.feature_centers[name] = self._finite(
                centers[name], f"{name}.center"
            )

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be finite and numeric.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    def _feature(self, context: Mapping[str, Any], name: str) -> float:
        if name in ("rest_difference", "rest_sum"):
            home = self._nonnegative(
                context["home_rest_days"], "home_rest_days"
            )
            away = self._nonnegative(
                context["away_rest_days"], "away_rest_days"
            )
            return home - away if name == "rest_difference" else home + away

        if name in (
            "home_attack",
            "home_defense",
            "away_attack",
            "away_defense",
        ):
            team, metric = name.split("_")
            team_data = context.get(team, {})
            if not isinstance(team_data, Mapping):
                raise ValueError(f"{team} must be a mapping.")
            return self._finite(team_data[metric], name)

        weather = context.get("weather", {})
        if not isinstance(weather, Mapping):
            raise ValueError("weather must be a mapping.")
        value = context[name] if name in context else weather[name]

        if name == "is_dome":
            if not isinstance(value, (bool, np.bool_)):
                raise ValueError("is_dome must be boolean.")
            return float(value)

        return self._finite(value, name)

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home_expected = self._nonnegative(
            context.get("home_expected", self.HOME_REFERENCE),
            "home_expected",
        )
        away_expected = self._nonnegative(
            context.get("away_expected", self.AWAY_REFERENCE),
            "away_expected",
        )

        # Preserve the empirical total scale while allowing only a calibrated
        # venue contrast to shift its allocation between otherwise equal teams.
        pooled_mean = (self.HOME_REFERENCE + self.AWAY_REFERENCE) / 2
        contrast = self.home_log_advantage
        log_home_share = -float(np.logaddexp(0.0, -contrast))
        log_away_share = -float(np.logaddexp(0.0, contrast))

        home_shift = log(2 * pooled_mean) + log_home_share
        away_shift = log(2 * pooled_mean) + log_away_share

        for name, (home_coef, away_coef) in self.feature_coefficients.items():
            try:
                feature = self._feature(context, name)
            except KeyError as exc:
                raise ValueError(
                    f"Missing input for calibrated feature {name}."
                ) from exc
            centered = feature - self.feature_centers[name]
            home_shift += home_coef * centered
            away_shift += away_coef * centered

        tempo = self._finite(context.get("tempo_ratio", 1.0), "tempo_ratio")
        if tempo <= 0:
            raise ValueError("tempo_ratio must be positive.")

        values = []
        for side, expected, reference, shift in (
            ("home", home_expected, self.HOME_REFERENCE, home_shift),
            ("away", away_expected, self.AWAY_REFERENCE, away_shift),
        ):
            exposure = self._nonnegative(
                context.get(f"{side}_scoring_exposure", 1.0),
                f"{side}_scoring_exposure",
            )
            if expected == 0 or exposure == 0:
                values.append(0.0)
                continue

            log_mean = (
                shift
                + log(expected)
                - log(reference)
                + log(exposure)
                + log(tempo)
            )
            try:
                mean = exp(log_mean)
            except OverflowError as exc:
                raise ValueError(f"{side} conditional mean overflowed.") from exc
            if not isfinite(mean) or mean <= 0:
                raise ValueError(f"{side} conditional mean is unrepresentable.")
            values.append(mean)

        return values[0], values[1]

    @classmethod
    def _marginal(cls, mean: float, dispersion: float) -> np.ndarray:
        if mean == 0:
            return np.ones(1, dtype=np.float64)

        variance = mean * dispersion
        if not isfinite(variance):
            raise ValueError("Conditional variance overflowed.")

        # Consecutive attainable scores surrounding the requested mean.
        lower = 0 if mean < 2 else floor(mean)
        upper = 2 if mean < 2 else lower + 1
        minimum_variance = (mean - lower) * (upper - mean)

        # This boundary is imposed by attainable scores, not a static clamp.
        if variance <= minimum_variance:
            probabilities = np.zeros(upper + 1, dtype=np.float64)
            probabilities[lower] = (upper - mean) / (upper - lower)
            probabilities[upper] = (mean - lower) / (upper - lower)
            return probabilities

        scale = sqrt(variance)
        tail_z = float(norm.isf(cls._EPS))
        endpoint = int(np.ceil(mean + tail_z * scale))
        endpoint = max(endpoint, upper + 1)

        # A finite lattice has variance <= mean * (endpoint - mean).
        endpoint = max(endpoint, int(np.floor(mean + variance / mean)) + 1)

        theta = np.array([0.0, -0.5], dtype=np.float64)

        while True:
            scores = np.concatenate(
                (np.zeros(1), np.arange(2, endpoint + 1, dtype=np.float64))
            )
            standardized = (scores - mean) / scale
            features = np.column_stack((standardized, standardized**2))
            target = np.array([0.0, 1.0])

            def moments(parameters: np.ndarray):
                logits = features @ parameters
                probabilities = np.exp(logits - logsumexp(logits))
                expectations = probabilities @ features
                centered = features - expectations
                covariance = centered.T @ (probabilities[:, None] * centered)
                return probabilities, expectations, covariance

            def equations(parameters: np.ndarray) -> np.ndarray:
                return moments(parameters)[1] - target

            def jacobian(parameters: np.ndarray) -> np.ndarray:
                return moments(parameters)[2]

            solution = root(
                equations,
                theta,
                jac=jacobian,
                method="hybr",
                options={"xtol": cls._TOL},
            )
            theta = np.asarray(solution.x, dtype=np.float64)
            if not np.all(np.isfinite(theta)):
                raise ArithmeticError("Nonfinite maximum-entropy parameters.")

            probabilities, expectations, _ = moments(theta)
            residual = np.max(np.abs(expectations - target))
            if residual > cls._TOL:
                raise ArithmeticError(
                    f"Score moment solver failed; residual={residual!r}."
                )

            # Bound the omitted infinite-lattice tail using the decreasing
            # successive probability ratio of a concave quadratic log PMF.
            edge = (endpoint - mean) / scale
            step = 1 / scale
            log_ratio = (
                theta[0] * step
                + theta[1] * (2 * edge * step + step**2)
            )
            if theta[1] <= 0 and log_ratio < 0:
                ratio = exp(log_ratio)
                omitted_bound = (
                    probabilities[-1] * ratio / (-np.expm1(log_ratio))
                )
                if omitted_bound <= cls._EPS:
                    dense = np.zeros(endpoint + 1, dtype=np.float64)
                    dense[scores.astype(np.int64)] = probabilities
                    dense /= dense.sum()
                    return dense

            # A positive quadratic coefficient cannot normalize on an infinite
            # lattice. Expansion distinguishes truncation effects from a
            # dispersion requiring a different distribution family.
            if theta[1] > 0:
                raise ValueError(
                    "Requested dispersion is outside this normalizable "
                    "quadratic maximum-entropy family; supply a separately "
                    "calibrated overdispersion model."
                )

            endpoint += max(1, int(np.ceil(tail_z * scale)))

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home_mean, away_mean = self._means(game_context)
        marginals = []

        for side, mean in (("home", home_mean), ("away", away_mean)):
            dispersion = self._nonnegative(
                game_context.get(
                    f"{side}_dispersion_index", self.HISTORICAL_DISPERSION
                ),
                f"{side}_dispersion_index",
            )
            marginals.append(self._marginal(mean, dispersion))

        home, away = marginals
        size = max(home.size, away.size)
        home = np.pad(home, (0, size - home.size))
        away = np.pad(away, (0, size - away.size))

        # Strict CDFs exclude ties; no subtraction of nearly equal probabilities.
        home_less = np.concatenate((np.zeros(1), np.cumsum(home[:-1])))
        away_less = np.concatenate((np.zeros(1), np.cumsum(away[:-1])))

        outcomes = np.array(
            [
                np.dot(home, away_less),
                np.dot(home, away),
                np.dot(away, home_less),
            ],
            dtype=np.float64,
        )
        total = float(outcomes.sum())

        if (
            not np.all(np.isfinite(outcomes))
            or np.any(outcomes < 0)
            or not isfinite(total)
            or total <= 0
        ):
            raise ArithmeticError("Invalid moneyline outcome probabilities.")

        outcomes /= total
        return DiscreteDistribution(
            probabilities={
                "home": float(outcomes[0]),
                "draw": float(outcomes[1]),
                "away": float(outcomes[2]),
            }
        )