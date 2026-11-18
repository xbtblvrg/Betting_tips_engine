from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.signal import fftconvolve
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthMLSFullGameSpreads(BaseCandidateModel):
    """
    Independent integer-score model for MLS full-game spreads.

    Model derivation
    ----------------
    Let theta = D - 1 and r_i = mu_i / theta. Define independent
    Gamma(shape, scale=theta) variables U_h, U_a and Z with shapes
    r_h-k, r_a-k and k, respectively. Conditional scoring intensities are

        Lambda_h = U_h + Z
        Lambda_a = U_a + Z.

    Conditional goals are independent Poisson draws. Consequently,

        E[G_i]       = mu_i
        Var[G_i]     = D * mu_i
        Cov[G_h,G_a] = k * theta**2
        Var[G_h-G_a] = D*(mu_h+mu_a) - 2*k*theta**2.

    Sharing therefore changes margin concentration without changing either
    marginal mean or dispersion. Unlike a generic correlation adjustment,
    the construction preserves nonnegative integer scores.

    The supplied marginal dispersion does not identify covariance, dynamic
    game-state feedback, or asymmetric tail parameters. Without an externally
    estimated covariance or margin variance, k=0. No such parameters are
    inferred from betting PnL, side profitability, odds, or handicap lines.

    Input contract
    --------------
    home_expected / away_expected:
        Positive baseline expected goals; defaults are the supplied empirical
        means. If these already include situational adjustments, leave the
        corresponding exposure factors and fitted coefficients unset.

    home / away:
        Optional mappings with attack_exposure and defense_exposure. These
        are positive ratios relative to the expected-goal baseline; larger
        defense_exposure means more goals conceded. Raw attack/defense ratings
        are intentionally not interpreted as dimensionless ratios.

    tempo_ratio:
        Positive common scoring-exposure ratio, defaulting to the identity.

    model_features:
        Optional finite numeric features, in the units used during training.

    rate_coefficients:
        Constructor-only, previously fitted coefficients keyed by "home" and
        "away", then feature name. No coefficients are fitted during prediction.

    advantage_coefficients:
        Previously fitted coefficients for a differential log-rate adjustment:
        add its value to home log intensity and subtract it from away intensity.

    goal_covariance / margin_variance:
        Optional predictive moments for the final adjusted means. They must
        come from training data or an independent upstream predictive model.
        Unsupported moments raise an error instead of being silently clamped.

    Numerical guarantees
    --------------------
    Each marginal support is selected from its survival function. The omitted
    joint mass is bounded by tail_probability through the union bound.
    The retained score matrix is normalized and validated before export.

    The default tail budget is sqrt(machine epsilon), a numerical precision
    choice, not a sports-model parameter. Resource limits can be supplied by
    deployment configuration. Latency and out-of-sample ROI require measurement
    in the actual engine; neither is guaranteed by this class.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mls_full_game_1117_014116",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "spreads",
        *,
        dispersion_index: float = 1.710,
        rate_coefficients: Optional[Mapping[str, Mapping[str, float]]] = None,
        advantage_coefficients: Optional[Mapping[str, float]] = None,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("MLS", "full_game", "spreads"):
            raise ValueError("This model supports MLS|full_game|spreads only.")

        self.dispersion_index = self._finite(
            dispersion_index, "dispersion_index"
        )
        if self.dispersion_index < 1:
            raise ValueError("dispersion_index must be at least one.")

        eps = np.finfo(float).eps
        self.tail_probability = self._finite(
            math.sqrt(eps) if tail_probability is None else tail_probability,
            "tail_probability",
        )
        if not eps <= self.tail_probability < 1:
            raise ValueError("tail_probability must lie in [machine epsilon, 1).")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

        rates = {} if rate_coefficients is None else dict(rate_coefficients)
        if set(rates) - {"home", "away"}:
            raise ValueError("rate_coefficients accepts only home and away.")
        self.rate_coefficients = {
            side: self._coefficients(rates.get(side, {}))
            for side in ("home", "away")
        }
        self.advantage_coefficients = self._coefficients(
            {} if advantage_coefficients is None else advantage_coefficients
        )

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be finite numeric data.") from exc
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
    def _coefficients(cls, values: Mapping[str, float]) -> Dict[str, float]:
        result = {}
        for key, value in values.items():
            if not isinstance(key, str):
                raise ValueError("Coefficient names must be strings.")
            result[key] = cls._finite(value, f"coefficient[{key}]")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _features(self, context: Mapping[str, Any]) -> Dict[str, float]:
        features: Dict[str, float] = {}
        weather = self._mapping(context.get("weather", {}), "weather")

        for name in ("wind_speed", "temp"):
            if name in weather:
                features[name] = self._finite(weather[name], name)

        for name in (
            "home_rest_days",
            "away_rest_days",
            "home_travel_distance",
            "away_travel_distance",
            "venue_altitude",
        ):
            if name in context:
                features[name] = self._finite(context[name], name)

        if "home_rest_days" in features and "away_rest_days" in features:
            if min(features["home_rest_days"], features["away_rest_days"]) < 0:
                raise ValueError("Rest days cannot be negative.")
            features["rest_difference"] = (
                features["home_rest_days"] - features["away_rest_days"]
            )

        if (
            "home_travel_distance" in features
            and "away_travel_distance" in features
        ):
            if min(
                features["home_travel_distance"],
                features["away_travel_distance"],
            ) < 0:
                raise ValueError("Travel distances cannot be negative.")
            features["travel_difference"] = (
                features["home_travel_distance"]
                - features["away_travel_distance"]
            )

        custom = self._mapping(
            context.get("model_features", {}), "model_features"
        )
        for name, value in custom.items():
            if not isinstance(name, str) or name in features:
                raise ValueError("Custom feature names must be unique strings.")
            features[name] = self._finite(value, name)
        return features

    @staticmethod
    def _linear(
        coefficients: Mapping[str, float], features: Mapping[str, float]
    ) -> float:
        missing = coefficients.keys() - features.keys()
        if missing:
            raise ValueError(f"Missing fitted-model features: {sorted(missing)}")
        value = math.fsum(
            coefficient * features[name]
            for name, coefficient in coefficients.items()
        )
        if not math.isfinite(value):
            raise ValueError("Nonfinite fitted log-rate adjustment.")
        return value

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._mapping(context.get("home", {}), "home")
        away = self._mapping(context.get("away", {}), "away")
        features = self._features(context)
        advantage = self._linear(self.advantage_coefficients, features)
        tempo = self._positive(context.get("tempo_ratio", 1), "tempo_ratio")

        means = []
        for side, opponent, empirical_mean, sign in (
            ("home", away, 1.60, 1),
            ("away", home, 1.20, -1),
        ):
            team = home if side == "home" else away
            baseline = self._positive(
                context.get(f"{side}_expected", empirical_mean),
                f"{side}_expected",
            )
            attack = self._positive(
                team.get("attack_exposure", 1), f"{side}.attack_exposure"
            )
            defense = self._positive(
                opponent.get("defense_exposure", 1),
                f"{side}.opponent_defense_exposure",
            )
            log_mean = (
                math.log(baseline)
                + math.log(attack)
                + math.log(defense)
                + math.log(tempo)
                + self._linear(self.rate_coefficients[side], features)
                + sign * advantage
            )
            try:
                mean = math.exp(log_mean)
            except OverflowError as exc:
                raise ValueError("Expected-goal computation overflowed.") from exc
            means.append(self._positive(mean, f"adjusted_{side}_expected"))
        return means[0], means[1]

    def _shared_shape(
        self, context: Mapping[str, Any], home_mean: float, away_mean: float
    ) -> float:
        theta = self.dispersion_index - 1
        total_variance = self.dispersion_index * (home_mean + away_mean)

        covariance = None
        if "goal_covariance" in context:
            covariance = self._finite(
                context["goal_covariance"], "goal_covariance"
            )
        if "margin_variance" in context:
            margin_variance = self._finite(
                context["margin_variance"], "margin_variance"
            )
            if margin_variance < 0:
                raise ValueError("margin_variance cannot be negative.")
            implied = (total_variance - margin_variance) / 2
            if covariance is not None and not math.isclose(
                covariance,
                implied,
                rel_tol=self.tail_probability,
                abs_tol=np.finfo(float).eps * total_variance,
            ):
                raise ValueError("Supplied covariance and margin variance disagree.")
            covariance = implied

        covariance = 0.0 if covariance is None else covariance
        maximum_covariance = theta * min(home_mean, away_mean)
        if not 0 <= covariance <= maximum_covariance:
            raise ValueError(
                "Predictive covariance is outside this shared-gamma model's "
                f"admissible interval [0, {maximum_covariance}]."
            )
        if theta == 0:
            return 0.0
        return covariance / theta**2

    def _marginal(self, mean: float):
        theta = self.dispersion_index - 1
        if theta == 0:
            return poisson(mu=mean)
        shape = mean / theta
        probability = 1 / self.dispersion_index
        if not math.isfinite(shape) or not 0 < probability < 1:
            raise ValueError("Dispersion is numerically unrepresentable.")
        return nbinom(n=shape, p=probability)

    def _support_end(self, marginal) -> int:
        budget = self.tail_probability / 2
        quantile = float(marginal.isf(budget))
        if not math.isfinite(quantile) or quantile < 0:
            raise ValueError("Could not construct a finite score support.")
        end = int(math.ceil(quantile))
        while float(marginal.sf(end)) > budget:
            end += 1
        return end

    @staticmethod
    def _component_pmf(
        shape: float, probability: float, end: int
    ) -> np.ndarray:
        if shape == 0:
            result = np.zeros(end + 1, dtype=float)
            result[0] = 1.0
            return result
        if shape < 0:
            raise ValueError("Negative gamma component shape.")
        return nbinom.pmf(np.arange(end + 1), shape, probability)

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return P(home goals=h, away goals=a), indexed [h, a]."""
        context = self._mapping(game_context, "game_context")
        home_mean, away_mean = self._means(context)
        shared_shape = self._shared_shape(context, home_mean, away_mean)
        home_marginal = self._marginal(home_mean)
        away_marginal = self._marginal(away_mean)
        home_end = self._support_end(home_marginal)
        away_end = self._support_end(away_marginal)

        cells = (home_end + 1) * (away_end + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise ValueError(
                f"Required score support has {cells} cells; deployment budget "
                f"is {self.max_score_cells}."
            )

        if shared_shape == 0:
            scores = np.outer(
                home_marginal.pmf(np.arange(home_end + 1)),
                away_marginal.pmf(np.arange(away_end + 1)),
            )
        else:
            theta = self.dispersion_index - 1
            probability = 1 / self.dispersion_index
            home_private = self._component_pmf(
                home_mean / theta - shared_shape, probability, home_end
            )
            away_private = self._component_pmf(
                away_mean / theta - shared_shape, probability, away_end
            )
            h = np.arange(home_end + 1, dtype=float)[:, None]
            a = np.arange(away_end + 1, dtype=float)[None, :]
            # Integrating Z gives a bivariate negative-multinomial component.
            log_shared = (
                gammaln(shared_shape + h + a)
                - gammaln(shared_shape)
                - gammaln(h + 1)
                - gammaln(a + 1)
                + (h + a) * (math.log(theta) - math.log1p(2 * theta))
                - shared_shape * math.log1p(2 * theta)
            )
            scores = fftconvolve(
                np.exp(log_shared),
                np.outer(home_private, away_private),
                mode="full",
            )[: home_end + 1, : away_end + 1]

        if not np.all(np.isfinite(scores)):
            raise ArithmeticError("Nonfinite score probabilities.")

        eps = np.finfo(float).eps
        roundoff_budget = eps * scores.size
        if float(scores.min()) < -roundoff_budget:
            raise ArithmeticError("Materially negative score probabilities.")
        # Remove FFT roundoff only; this does not constrain model parameters.
        scores = np.maximum(scores, 0.0)
        retained_mass = math.fsum(scores.ravel())
        if (
            retained_mass <= 0
            or retained_mass > 1 + roundoff_budget
            or retained_mass < 1 - self.tail_probability - roundoff_budget
        ):
            raise ArithmeticError("Score mass violates the truncation budget.")

        scores /= retained_mass
        largest = np.unravel_index(int(np.argmax(scores)), scores.shape)
        scores[largest] += 1.0 - math.fsum(scores.ravel())
        if np.any(scores < 0) or not math.isclose(
            math.fsum(scores.ravel()), 1.0, rel_tol=0, abs_tol=eps
        ):
            raise ArithmeticError("Final score distribution failed validation.")
        return scores

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        # Preserve the score lattice: 3-way probabilities cannot price spreads.
        return DiscreteDistribution.from_scores(self.score_matrix(game_context))

    def margin_distribution(
        self, game_context: Dict[str, Any], side: str = "home"
    ) -> Tuple[np.ndarray, np.ndarray]:
        if side not in ("home", "away"):
            raise ValueError("side must be home or away.")
        scores = self.score_matrix(game_context)
        home = np.arange(scores.shape[0])[:, None]
        away = np.arange(scores.shape[1])[None, :]
        signed_margin = home - away if side == "home" else away - home
        minimum = int(signed_margin.min())
        maximum = int(signed_margin.max())
        probabilities = np.bincount(
            (signed_margin - minimum).ravel(),
            weights=scores.ravel(),
            minlength=maximum - minimum + 1,
        )
        return np.arange(minimum, maximum + 1), probabilities

    def binary_contract_probability(
        self,
        game_context: Dict[str, Any],
        line: float,
        side: str = "home",
        inclusive: bool = False,
    ) -> float:
        """
        Price the explicitly defined binary event selected-side margin > line.

        Set inclusive=True only when the contract resolves equality as Yes.
        Binary equality is otherwise a loss, not an Asian-handicap refund.
        Continuous lines are accepted without rounding to half-goal increments.
        """
        threshold = self._finite(line, "line")
        margins, probabilities = self.margin_distribution(game_context, side)
        selected = margins >= threshold if inclusive else margins > threshold
        return float(math.fsum(probabilities[selected]))

    def asian_handicap_price(
        self,
        game_context: Dict[str, Any],
        handicap: float,
        decimal_odds: Optional[float] = None,
        side: str = "home",
    ) -> Dict[str, Any]:
        """
        Settle selected-side margin + handicap using whole/half/quarter lines.

        A quarter line splits the stake equally between its adjacent half lines.
        win_fraction and loss_fraction are expected fractions of initial stake.
        Expected net return = (decimal_odds-1)*win_fraction-loss_fraction.
        """
        value = self._finite(handicap, "handicap")
        quarter_units = Fraction(str(value)) * 4
        if quarter_units.denominator != 1:
            raise ValueError(
                "Asian handicaps must be multiples of one quarter; use "
                "binary_contract_probability for arbitrary continuous lines."
            )
        units = quarter_units.numerator
        if units % 2:
            legs = (Fraction(units - 1, 4), Fraction(units + 1, 4))
        else:
            legs = (Fraction(units, 4),)

        margins, probabilities = self.margin_distribution(game_context, side)
        win_fraction = push_fraction = loss_fraction = 0.0
        for leg in legs:
            # Exact settlement comparisons, including equality at whole lines.
            threshold = -leg
            wins = np.fromiter(
                (int(margin) > threshold for margin in margins),
                dtype=bool,
                count=margins.size,
            )
            pushes = np.fromiter(
                (int(margin) == threshold for margin in margins),
                dtype=bool,
                count=margins.size,
            )
            losses = ~(wins | pushes)
            win_fraction += math.fsum(probabilities[wins]) / len(legs)
            push_fraction += math.fsum(probabilities[pushes]) / len(legs)
            loss_fraction += math.fsum(probabilities[losses]) / len(legs)

        result: Dict[str, Any] = {
            "win_fraction": win_fraction,
            "push_fraction": push_fraction,
            "loss_fraction": loss_fraction,
            "fair_decimal_odds": (
                1 + loss_fraction / win_fraction
                if win_fraction > 0
                else None
            ),
        }
        if decimal_odds is not None:
            odds = self._positive(decimal_odds, "decimal_odds")
            if odds < 1:
                raise ValueError("Decimal odds must be at least one.")
            expected_return = (odds - 1) * win_fraction - loss_fraction
            result["expected_net_return_per_stake"] = expected_return
            # The minimum is the user's eligibility rule, not a forecast input.
            result["eligible"] = odds >= 1.40 and expected_return > 0
        return result