from math import exp, expm1, fsum, isfinite, log, sqrt
from numbers import Real
from typing import Any, Dict, Mapping, Optional

from scipy.special import ive
from scipy.stats import skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UELFirstHalfThreeWayModel(BaseCandidateModel):
    """
    Independent first-half goal-arrival model for UEL three-way contracts.

    With no settled observations, calibration and profitability are unverified.
    The supplied first-half mean goals, 0.80 and 0.60, define the cold-start
    prior. Conditional independent Poisson counts imply variance/mean = 1
    and preserve integer scoring and the full draw mass.

    Input contract
    --------------
    home_expected, away_expected:
        Optional nonnegative FIRST-HALF expected goals. Missing values use
        the supplied prior. These are pre-adjustment means: do not supply
        means already incorporating the multipliers below.
    expected_scope:
        Optional explicit scope declaration; must equal "half_1".
    tempo_multiplier:
        Optional positive common goal-rate multiplier; neutral value is 1.
    home, away:
        Optional mappings with positive, neutral-at-1 multipliers:
        attack_multiplier, defense_concession_multiplier, exposure_multiplier.
        A larger defense_concession_multiplier means more goals conceded.
    home_rest_days, away_rest_days:
        Required only when an externally estimated rest coefficient is active.
    rate_covariates:
        Mapping of finite, already-centered numeric covariates, including any
        weather or venue encodings. Every configured coefficient requires its
        corresponding covariate.

    Coefficients must be estimated using information available before kickoff,
    independently of the evaluated outcome. Cold start supplies no evidence
    for nonzero rest, weather, venue, or additional home-advantage effects.
    Consequently, those effects default to zero rather than invented weights.

    For side s:
        log(lambda_s) = log(prior_or_expected_s)
                        + log(tempo)
                        + log(attack_s)
                        + log(opponent_concession)
                        + log(exposure_s)
                        + beta_s' x
                        +/- beta_rest * (home_rest - away_rest) / 2.

    The rest parameter therefore measures the change in the log home/away
    rate ratio per day of rest advantage. The supplied baseline already
    contains its home/away asymmetry.

    Contract prices never enter the forecast. No finite score lattice,
    arbitrary probability floor, odds ceiling, or market blending is used.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1108_152214",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "three_way",
        *,
        home_log_rate_coefficients: Optional[Mapping[str, float]] = None,
        away_log_rate_coefficients: Optional[Mapping[str, float]] = None,
        rest_log_rate_ratio_coefficient: float = 0.0,
    ) -> None:
        if (league, scope, family) != ("UEL", "half_1", "three_way"):
            raise ValueError("This model supports only UEL|half_1|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._home_coefficients = self._coefficients(
            home_log_rate_coefficients, "home_log_rate_coefficients"
        )
        self._away_coefficients = self._coefficients(
            away_log_rate_coefficients, "away_log_rate_coefficients"
        )
        self._rest_coefficient = self._number(
            rest_log_rate_ratio_coefficient,
            "rest_log_rate_ratio_coefficient",
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"{name} must be a real number.")
        result = float(value)
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
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
        for feature, coefficient in values.items():
            if not isinstance(feature, str) or not feature:
                raise ValueError(f"{name} keys must be nonempty strings.")
            result[feature] = cls._number(coefficient, f"{name}.{feature}")
        return result

    @staticmethod
    def _mapping(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = context.get(key, {})
        if not isinstance(value, Mapping):
            raise TypeError(f"{key} must be a mapping when supplied.")
        return value

    def _feature_effect(
        self,
        coefficients: Mapping[str, float],
        covariates: Mapping[str, Any],
    ) -> float:
        terms = []
        for feature, coefficient in coefficients.items():
            if coefficient == 0.0:
                continue
            if feature not in covariates:
                raise ValueError(f"Missing active rate_covariates.{feature}.")
            value = self._number(covariates[feature], f"rate_covariates.{feature}")
            term = coefficient * value
            if not isfinite(term):
                raise ValueError(f"Nonfinite rate effect for {feature}.")
            terms.append(term)
        try:
            effect = fsum(terms)
        except OverflowError as exc:
            raise ValueError("Combined covariate effect overflowed.") from exc
        if not isfinite(effect):
            raise ValueError("Combined covariate effect must be finite.")
        return effect

    @staticmethod
    def _adjust_rate(mean: float, log_adjustment: float) -> float:
        if not isfinite(log_adjustment):
            raise ValueError("Combined log-rate adjustment must be finite.")
        if mean == 0.0:
            return 0.0
        try:
            rate = exp(log(mean) + log_adjustment)
        except OverflowError as exc:
            raise ValueError("Adjusted goal rate exceeds numerical range.") from exc
        if not isfinite(rate) or rate == 0.0:
            raise ValueError("Adjusted positive goal rate is outside numerical range.")
        return rate

    def expected_goals(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Return conditional first-half goal means without consulting prices."""
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "UEL"),
            ("scope", "half_1"),
            ("family", "three_way"),
            ("expected_scope", "half_1"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        home_mean = self._nonnegative(
            game_context.get("home_expected", 0.80), "home_expected"
        )
        away_mean = self._nonnegative(
            game_context.get("away_expected", 0.60), "away_expected"
        )
        tempo = self._positive(
            game_context.get("tempo_multiplier", 1.0), "tempo_multiplier"
        )

        home = self._mapping(game_context, "home")
        away = self._mapping(game_context, "away")
        covariates = self._mapping(game_context, "rate_covariates")

        def multiplier(team: Mapping[str, Any], side: str, field: str) -> float:
            return self._positive(team.get(field, 1.0), f"{side}.{field}")

        home_terms = [
            log(tempo),
            log(multiplier(home, "home", "attack_multiplier")),
            log(multiplier(away, "away", "defense_concession_multiplier")),
            log(multiplier(home, "home", "exposure_multiplier")),
            self._feature_effect(self._home_coefficients, covariates),
        ]
        away_terms = [
            log(tempo),
            log(multiplier(away, "away", "attack_multiplier")),
            log(multiplier(home, "home", "defense_concession_multiplier")),
            log(multiplier(away, "away", "exposure_multiplier")),
            self._feature_effect(self._away_coefficients, covariates),
        ]

        if self._rest_coefficient != 0.0:
            for field in ("home_rest_days", "away_rest_days"):
                if field not in game_context:
                    raise ValueError(f"Missing {field} for active rest adjustment.")
            home_rest = self._nonnegative(
                game_context["home_rest_days"], "home_rest_days"
            )
            away_rest = self._nonnegative(
                game_context["away_rest_days"], "away_rest_days"
            )
            rest_effect = (
                self._rest_coefficient * (home_rest - away_rest) / 2.0
            )
            if not isfinite(rest_effect):
                raise ValueError("Rest effect must be finite.")
            home_terms.append(rest_effect)
            away_terms.append(-rest_effect)

        try:
            home_adjustment = fsum(home_terms)
            away_adjustment = fsum(away_terms)
        except OverflowError as exc:
            raise ValueError("Combined rate adjustment overflowed.") from exc

        return {
            "home": self._adjust_rate(home_mean, home_adjustment),
            "away": self._adjust_rate(away_mean, away_adjustment),
        }

    def predict_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Integrate the complete integer goal-margin distribution."""
        rates = self.expected_goals(game_context)
        home_rate, away_rate = rates["home"], rates["away"]

        if home_rate == 0.0:
            return {
                "home": 0.0,
                "draw": exp(-away_rate),
                "away": -expm1(-away_rate),
            }
        if away_rate == 0.0:
            return {
                "home": -expm1(-home_rate),
                "draw": exp(-home_rate),
                "away": 0.0,
            }

        # D = home_goals - away_goals. Compute each tail directly to avoid
        # recovering a small away probability by subtracting large masses.
        p_home = float(skellam.sf(0, home_rate, away_rate))
        p_away = float(skellam.cdf(-1, home_rate, away_rate))

        # exp(-(h+a))*I_0(2*sqrt(h*a)), expressed with scaled I_0.
        root_home, root_away = sqrt(home_rate), sqrt(away_rate)
        bessel_argument = (root_home * root_away) * 2.0
        if not isfinite(bessel_argument):
            raise ArithmeticError("Goal rates exceed the stable solver range.")
        difference = root_home - root_away
        p_draw = float(
            exp(-(difference * difference)) * ive(0, bessel_argument)
        )

        probabilities = {"home": p_home, "draw": p_draw, "away": p_away}
        if any(
            not isfinite(p) or p < 0.0 or p > 1.0
            for p in probabilities.values()
        ):
            raise ArithmeticError("Goal-margin solver returned invalid probabilities.")

        mass = fsum(probabilities.values())
        if not isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Goal-margin solver returned invalid total mass.")

        # Floating-point normalization only; no probability clipping.
        return {side: p / mass for side, p in probabilities.items()}

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution(
            probabilities=self.predict_probabilities(game_context)
        )

    def value_contract(
        self,
        game_context: Dict[str, Any],
        side: str,
        price: float,
        *,
        settlement_payout: float = 1.0,
        upfront_cost: float = 0.0,
        winning_settlement_fee: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Value a purchased outcome contract continuously in currency per share.

        `price` must be the executable purchase price, including anticipated
        slippage. `upfront_cost` is additional per-share execution cost.
        `winning_settlement_fee` is charged only on a winning settlement.

        Eligibility uses the requested gross decimal-odds minimum of 1.40
        and strictly positive expectation after supplied costs. It is not a
        claim of validated alpha, an execution instruction, or a sizing rule.
        """
        if side not in ("home", "draw", "away"):
            raise ValueError("side must be 'home', 'draw', or 'away'.")

        price = self._positive(price, "price")
        payout = self._positive(settlement_payout, "settlement_payout")
        cost = self._nonnegative(upfront_cost, "upfront_cost")
        fee = self._nonnegative(winning_settlement_fee, "winning_settlement_fee")
        if price > payout:
            raise ValueError("price cannot exceed settlement_payout.")
        if fee >= payout:
            raise ValueError("winning_settlement_fee must be less than payout.")

        probability = self.predict_probabilities(game_context)[side]
        invested = price + cost
        gross_decimal_odds = payout / price
        if not isfinite(invested) or not isfinite(gross_decimal_odds):
            raise ValueError("Contract arithmetic exceeds numerical range.")

        expected_receipt = probability * (payout - fee)
        expected_profit = expected_receipt - invested
        return {
            "side": side,
            "probability": probability,
            "gross_decimal_odds": gross_decimal_odds,
            "fair_purchase_price_after_costs": expected_receipt - cost,
            "expected_profit_per_share": expected_profit,
            "expected_roi": expected_profit / invested,
            "eligible": gross_decimal_odds >= 1.40 and expected_profit > 0.0,
        }