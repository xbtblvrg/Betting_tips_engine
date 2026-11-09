from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NHLPeriodOneWinnerTotalModel(BaseCandidateModel):
    """
    Joint first-period score model for NHL winner-and-total contracts.

    With no settled observations, the supplied period means and unit
    variance-to-mean ratio identify a Poisson starting model. Conditional
    independence is an explicit assumption: the available evidence does
    not identify score dependence or additional dispersion.

    Conditional scoring intensities:
        log(lambda_home) = log(home_expected)
                           + log(home_attack_ratio)
                           + log(away_defense_ratio)
                           + log(home_exposure)
                           + log(tempo_ratio)
                           + calibrated feature effects

    The away intensity is constructed symmetrically. Defense ratios describe
    goals conceded: larger values increase the opponent's scoring intensity.
    All ratios must be relative to the population underlying the baseline
    means; raw ratings and raw pace values are deliberately not interpreted
    as multipliers.

    Context contract:
        home_expected, away_expected:
            Nonnegative FIRST-PERIOD expected goals, before any modifiers
            supplied to this class. Defaults are the provided empirical means.
            If these expectations already incorporate a feature, omit that
            feature's modifier to prevent double counting.

        home, away:
            Optional mappings containing attack_ratio, defense_ratio and
            scoring_exposure. Omitted modifiers are neutral.

        tempo_ratio:
            Optional nonnegative shared scoring-opportunity multiplier.

        home_rest_days, away_rest_days:
            Optional nonnegative days. Rest affects forecasts only when
            externally estimated rest coefficients are supplied.

        log_rate_features:
            Optional mapping of finite, preprocessed covariates. Any feature
            with a configured coefficient must be present.

    Coefficients must be estimated on data available before prediction.
    This class neither fits coefficients nor reads prices or bookmaker lines.
    Missing fitted effects are neutral rather than invented.

    The returned score lattice preserves winner/total dependence: callers
    must settle each joint contract directly against its score cells,
    including first-period ties and any total-line push rules.

    Assumes DiscreteDistribution.from_scores accepts a mapping from
    (home_goals, away_goals) to probability. Tail truncation is controlled by
    numerical tolerance, not a fixed maximum hockey score. Normalization
    conditions on the retained rectangle; its omitted probability is bounded
    by tail_tolerance.

    Cold-start construction is not evidence of calibration or profitability.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_1_1107_131559",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_1",
        family: str = "winner_total",
        *,
        baseline_home: float = 1.02,
        baseline_away: float = 0.92,
        home_feature_coefficients: Optional[Mapping[str, float]] = None,
        away_feature_coefficients: Optional[Mapping[str, float]] = None,
        own_rest_coefficient: Optional[float] = None,
        opponent_rest_coefficient: Optional[float] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("NHL", "period_1", "winner_total"):
            raise ValueError("This model supports only NHL|period_1|winner_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.baseline_home = self._nonnegative(baseline_home, "baseline_home")
        self.baseline_away = self._nonnegative(baseline_away, "baseline_away")
        self.home_feature_coefficients = self._coefficients(
            home_feature_coefficients, "home_feature_coefficients"
        )
        self.away_feature_coefficients = self._coefficients(
            away_feature_coefficients, "away_feature_coefficients"
        )
        self.own_rest_coefficient = (
            None
            if own_rest_coefficient is None
            else self._finite(own_rest_coefficient, "own_rest_coefficient")
        )
        self.opponent_rest_coefficient = (
            None
            if opponent_rest_coefficient is None
            else self._finite(
                opponent_rest_coefficient, "opponent_rest_coefficient"
            )
        )

        tolerance = (
            float(np.finfo(np.float64).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must be strictly between zero and one.")
        if tolerance / 2.0 == 0.0:
            raise ValueError("tail_tolerance is below representable precision.")
        self.tail_tolerance = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite real number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite real number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _coefficients(
        cls, values: Optional[Mapping[str, float]], name: str
    ) -> Dict[str, float]:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        result = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{name} requires nonempty string feature names.")
            result[key] = cls._finite(value, f"{name}[{key!r}]")
        return result

    @staticmethod
    def _mapping(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = context.get(key, {})
        if not isinstance(value, Mapping):
            raise TypeError(f"{key} must be a mapping when provided.")
        return value

    def _feature_effect(
        self,
        features: Mapping[str, Any],
        coefficients: Mapping[str, float],
    ) -> float:
        terms = []
        for name, coefficient in coefficients.items():
            if name not in features:
                raise ValueError(f"Missing configured log-rate feature: {name!r}.")
            value = self._finite(features[name], f"log_rate_features[{name!r}]")
            terms.append(self._finite(coefficient * value, f"effect[{name!r}]"))
        try:
            return self._finite(math.fsum(terms), "combined feature effect")
        except OverflowError as exc:
            raise ValueError("Combined feature effect overflowed.") from exc

    def _intensities(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._mapping(context, "home")
        away = self._mapping(context, "away")
        features = self._mapping(context, "log_rate_features")

        home_effect = self._feature_effect(
            features, self.home_feature_coefficients
        )
        away_effect = self._feature_effect(
            features, self.away_feature_coefficients
        )

        if (
            self.own_rest_coefficient is not None
            or self.opponent_rest_coefficient is not None
        ):
            for key in ("home_rest_days", "away_rest_days"):
                if key not in context:
                    raise ValueError(f"{key} is required by the rest model.")
            home_rest = math.log1p(
                self._nonnegative(context["home_rest_days"], "home_rest_days")
            )
            away_rest = math.log1p(
                self._nonnegative(context["away_rest_days"], "away_rest_days")
            )
            own = self.own_rest_coefficient
            opponent = self.opponent_rest_coefficient
            if own is not None:
                home_effect += own * home_rest
                away_effect += own * away_rest
            if opponent is not None:
                home_effect += opponent * away_rest
                away_effect += opponent * home_rest

        tempo = self._nonnegative(context.get("tempo_ratio", 1.0), "tempo_ratio")

        def intensity(
            side: str,
            baseline: float,
            attack: Mapping[str, Any],
            defense: Mapping[str, Any],
            effect: float,
        ) -> float:
            effect = self._finite(effect, f"{side} log-rate effect")
            factors = (
                self._nonnegative(
                    context.get(f"{side}_expected", baseline), f"{side}_expected"
                ),
                self._nonnegative(
                    attack.get("attack_ratio", 1.0), f"{side}.attack_ratio"
                ),
                self._nonnegative(
                    defense.get("defense_ratio", 1.0),
                    f"{side} opponent defense_ratio",
                ),
                self._nonnegative(
                    attack.get("scoring_exposure", 1.0),
                    f"{side}.scoring_exposure",
                ),
                tempo,
            )
            if any(value == 0.0 for value in factors):
                return 0.0
            try:
                log_rate = math.fsum([effect, *(math.log(x) for x in factors)])
                rate = math.exp(log_rate)
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"{side} scoring intensity is unrepresentable.") from exc
            if not math.isfinite(rate) or rate == 0.0:
                raise ValueError(f"{side} scoring intensity is unrepresentable.")
            return rate

        return (
            intensity("home", self.baseline_home, home, away, home_effect),
            intensity("away", self.baseline_away, away, home, away_effect),
        )

    def _upper_support(self, rate: float) -> int:
        if rate == 0.0:
            return 0

        marginal_tolerance = self.tail_tolerance / 2.0
        quantile = float(poisson.isf(marginal_tolerance, rate))
        if not math.isfinite(quantile):
            raise ArithmeticError("Poisson support quantile is not representable.")

        upper = max(0, int(math.ceil(quantile)))
        log_tolerance = math.log(marginal_tolerance)
        while float(poisson.logsf(upper, rate)) > log_tolerance:
            upper += 1
        return upper

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_rate, away_rate = self._intensities(game_context)
        home_upper = self._upper_support(home_rate)
        away_upper = self._upper_support(away_rate)
        cells = (home_upper + 1) * (away_upper + 1)

        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Score lattice requires {cells} cells, exceeding the configured "
                f"resource budget of {self.max_score_cells}. No scores were clipped."
            )

        home_scores = np.arange(home_upper + 1, dtype=np.int64)
        away_scores = np.arange(away_upper + 1, dtype=np.int64)
        home_log_mass = poisson.logpmf(home_scores, home_rate)
        away_log_mass = poisson.logpmf(away_scores, away_rate)

        home_mass = np.exp(home_log_mass - logsumexp(home_log_mass))
        away_mass = np.exp(away_log_mass - logsumexp(away_log_mass))
        joint_mass = np.multiply.outer(home_mass, away_mass)

        scores = {
            (int(h), int(a)): float(joint_mass[h, a])
            for h in range(home_upper + 1)
            for a in range(away_upper + 1)
            if joint_mass[h, a] > 0.0
        }
        total_mass = math.fsum(scores.values())
        if not math.isfinite(total_mass) or total_mass <= 0.0:
            raise ArithmeticError("Failed to construct a finite score distribution.")

        scores = {score: mass / total_mass for score, mass in scores.items()}
        return DiscreteDistribution.from_scores(scores)