from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the execution environment, as specified.


class BundesligaFullGameSpreadsModel(BaseCandidateModel):
    """
    Cold-start goal-lattice model for Bundesliga Asian handicaps.

    The supplied means, 1.60 and 1.20, identify scoring intensities; they do
    not identify dependence, rest effects, or game-state response. Accordingly,
    unestimated effects are zero. No bookmaker information enters forecasts.

    Model:
        T ~ Gamma(shape=r, rate=r)
        X | T ~ Poisson(T * home_rate)
        Y | T ~ Poisson(T * away_rate)

    With dispersion_index=1, T is identically one. Otherwise r is determined
    by the specified variance/mean ratio of total goals:
        r = (home_rate + away_rate) / (dispersion_index - 1).

    An optional low-score checkerboard adjustment preserves both scoring
    marginals. Its coefficient must be estimated externally using historical
    scores and chronological validation. Zero is the cold-start default.

    Context contract:
      * home_expected / away_expected: final pregame scoring means. Explicit
        values take precedence over all feature adjustments for that team.
      * home / away: optional mappings with positive attack and defense
        multipliers. Defense means goals-conceded exposure; larger is weaker.
        Neutral exposure is one.
      * home_rest_days / away_rest_days: nonnegative days.
      * pace: positive multiplicative scoring exposure, neutral at one.
      * dispersion_index: optional observed total-goal variance/mean, >= 1.
      * low_score_rho: optional externally estimated dependence parameter.

    Constructor coefficients:
      * rest_log_rate_per_day: effect of rest-day difference on log intensity.
      * home_log_rate_contrast: fitted adjustment to the baseline home/away
        intensity ratio, applied symmetrically on the log scale.

    These coefficients should be posterior estimates from a separately fitted,
    regularized model. This class deliberately performs no online fitting.
    Weather is unused because no identified weather response was supplied.

    Finite score support has omitted probability bounded by tail_probability
    before normalization. The default is machine precision. Returned prices
    are conditional on that support, with the numerical error bound reported.

    Requires the execution environment's DiscreteDistribution.from_scores
    to accept a mapping from (home_goals, away_goals) to probability.
    """

    BASE_HOME_RATE = 1.60
    BASE_AWAY_RATE = 1.20
    MINIMUM_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_full_game_1107_180839",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "full_game",
        family: str = "spreads",
        *,
        rest_log_rate_per_day: float = 0.0,
        home_log_rate_contrast: float = 0.0,
        dispersion_index: float = 1.0,
        low_score_rho: float = 0.0,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("BUNDESLIGA", "full_game", "spreads"):
            raise ValueError("This candidate is specific to Bundesliga full-game spreads.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.rest_log_rate_per_day = self._finite(
            rest_log_rate_per_day, "rest_log_rate_per_day"
        )
        self.home_log_rate_contrast = self._finite(
            home_log_rate_contrast, "home_log_rate_contrast"
        )
        self.dispersion_index = self._dispersion(dispersion_index)
        self.low_score_rho = self._finite(low_score_rho, "low_score_rho")
        self.tail_probability = self._finite(
            np.finfo(float).eps if tail_probability is None else tail_probability,
            "tail_probability",
        )
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between zero and one.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _dispersion(cls, value: Any) -> float:
        result = cls._finite(value, "dispersion_index")
        if result < 1.0:
            raise ValueError(
                "This latent-tempo model requires dispersion_index >= 1; "
                "underdispersion requires a different count mechanism."
            )
        return result

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise ValueError(f"{side} must be a mapping of team features.")
        return team

    def _rates(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        needs_home = context.get("home_expected") is None
        needs_away = context.get("away_expected") is None

        if not needs_home and not needs_away:
            return (
                self._positive(context["home_expected"], "home_expected"),
                self._positive(context["away_expected"], "away_expected"),
            )

        home = self._team(context, "home")
        away = self._team(context, "away")
        pace = self._positive(context.get("pace", 1.0), "pace")

        rest_effect = 0.0
        if self.rest_log_rate_per_day != 0.0:
            if (
                context.get("home_rest_days") is None
                or context.get("away_rest_days") is None
            ):
                raise ValueError("Both rest-day inputs are required by the fitted rest effect.")
            home_rest = self._finite(context["home_rest_days"], "home_rest_days")
            away_rest = self._finite(context["away_rest_days"], "away_rest_days")
            if min(home_rest, away_rest) < 0.0:
                raise ValueError("Rest days cannot be negative.")
            rest_effect = self.rest_log_rate_per_day * (home_rest - away_rest)

        contrast = self.home_log_rate_contrast / 2.0 + rest_effect

        def rate(side: str, baseline: float, effect: float) -> float:
            explicit = context.get(f"{side}_expected")
            if explicit is not None:
                return self._positive(explicit, f"{side}_expected")

            own, opponent = (home, away) if side == "home" else (away, home)
            attack = self._positive(own.get("attack", 1.0), f"{side}.attack")
            defense = self._positive(
                opponent.get("defense", 1.0), f"{side}.opponent_defense"
            )
            log_rate = (
                math.log(baseline)
                + math.log(attack)
                + math.log(defense)
                + math.log(pace)
                + effect
            )
            try:
                result = math.exp(log_rate)
            except OverflowError as exc:
                raise ValueError(f"{side} scoring intensity overflowed.") from exc
            return self._positive(result, f"{side}_derived_expected")

        return (
            rate("home", self.BASE_HOME_RATE, contrast),
            rate("away", self.BASE_AWAY_RATE, -contrast),
        )

    def _upper_support(self, marginal: Any) -> int:
        budget = self.tail_probability / 2.0
        if budget == 0.0:
            raise ValueError("tail_probability is below representable numerical precision.")

        quantile = float(marginal.isf(budget))
        if not math.isfinite(quantile):
            raise ArithmeticError("Unable to determine finite scoring support.")
        upper = max(1, int(math.ceil(quantile)))

        # Verify the actual tail rather than relying solely on inverse-CDF rounding.
        while True:
            tail = float(marginal.sf(upper))
            if not math.isfinite(tail):
                raise ArithmeticError("Nonfinite marginal tail probability.")
            if tail <= budget:
                return upper
            upper += 1

    def _score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_rate, away_rate = self._rates(game_context)
        dispersion = self._dispersion(
            game_context.get("dispersion_index", self.dispersion_index)
        )
        rho = self._finite(
            game_context.get("low_score_rho", self.low_score_rho), "low_score_rho"
        )

        shape = None
        if dispersion == 1.0:
            home_marginal = poisson(home_rate)
            away_marginal = poisson(away_rate)
        else:
            total_rate = home_rate + away_rate
            shape = total_rate / (dispersion - 1.0)
            if not math.isfinite(shape) or shape <= 0.0:
                raise ArithmeticError("Latent-tempo shape is not numerically representable.")
            home_p = shape / (shape + home_rate)
            away_p = shape / (shape + away_rate)
            if not (0.0 < home_p < 1.0 and 0.0 < away_p < 1.0):
                raise ArithmeticError("Negative-binomial parameters lost numerical precision.")
            home_marginal = nbinom(shape, home_p)
            away_marginal = nbinom(shape, away_p)

        home_upper = self._upper_support(home_marginal)
        away_upper = self._upper_support(away_marginal)
        cells = (home_upper + 1) * (away_upper + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Score lattice requires {cells} cells; configured budget is "
                f"{self.max_score_cells}. No probability support was silently clipped."
            )

        x = np.arange(home_upper + 1, dtype=float)[:, None]
        y = np.arange(away_upper + 1, dtype=float)[None, :]

        if shape is None:
            log_mass = poisson.logpmf(x, home_rate) + poisson.logpmf(y, away_rate)
        else:
            total_rate = home_rate + away_rate
            log_denominator = math.log(shape) + math.log1p(total_rate / shape)
            log_mass = (
                gammaln(shape + x + y)
                - gammaln(shape)
                - gammaln(x + 1.0)
                - gammaln(y + 1.0)
                - shape * math.log1p(total_rate / shape)
                + x * (math.log(home_rate) - log_denominator)
                + y * (math.log(away_rate) - log_denominator)
            )

        mass = np.exp(log_mass)

        if rho != 0.0:
            p00, p01 = float(mass[0, 0]), float(mass[0, 1])
            p10, p11 = float(mass[1, 0]), float(mass[1, 1])
            if p11 == 0.0:
                raise ArithmeticError("Low-score correction is below numerical resolution.")

            delta = rho * p11
            lower = -min(p01, p10)
            upper = min(p00, p11)
            if not math.isfinite(delta) or not lower <= delta <= upper:
                raise ValueError(
                    "low_score_rho violates probability nonnegativity: "
                    f"required interval is [{lower / p11}, {upper / p11}]."
                )

            # Negative rho adds draw mass at 0-0 and 1-1 while preserving
            # every row and column sum, hence both marginal goal distributions.
            mass[0, 0] = p00 - delta
            mass[0, 1] = p01 + delta
            mass[1, 0] = p10 + delta
            mass[1, 1] = p11 - delta

        if not np.isfinite(mass).all() or np.any(mass < 0.0):
            raise ArithmeticError("Invalid joint score probabilities.")

        retained_mass = math.fsum(map(float, mass.flat))
        if not math.isfinite(retained_mass) or retained_mass <= 0.0:
            raise ArithmeticError("Joint score distribution has no representable mass.")

        return {
            (int(i), int(j)): float(mass[i, j]) / retained_mass
            for i in range(home_upper + 1)
            for j in range(away_upper + 1)
            if mass[i, j] > 0.0
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(
            self._score_probabilities(game_context)
        )

    def price_asian_handicap(
        self,
        game_context: Dict[str, Any],
        handicap: float,
        decimal_odds: float,
        side: str = "home",
    ) -> Dict[str, Any]:
        """
        Price a handicap added to the selected team's goal margin.

        Quarter lines split equally between adjacent half-goal lines.
        EV is net profit per unit stake; pushes return the corresponding stake.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")

        line = self._finite(handicap, "handicap")
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds cannot be below one.")

        quarter_units = line * 4.0
        if not math.isfinite(quarter_units) or not quarter_units.is_integer():
            raise ValueError("Asian handicap must be an exact multiple of 0.25.")

        quarter_units = int(quarter_units)
        if quarter_units % 2:
            legs = (quarter_units - 1, quarter_units + 1)
        else:
            legs = (quarter_units,)

        scores = self._score_probabilities(game_context)
        win_terms, loss_terms, push_terms = [], [], []
        direction = 1 if side == "home" else -1

        for (home_goals, away_goals), probability in scores.items():
            margin = direction * (home_goals - away_goals)
            leg_probability = probability / len(legs)
            for leg in legs:
                adjusted_margin = 4 * margin + leg
                if adjusted_margin > 0:
                    win_terms.append(leg_probability)
                elif adjusted_margin < 0:
                    loss_terms.append(leg_probability)
                else:
                    push_terms.append(leg_probability)

        win = math.fsum(win_terms)
        loss = math.fsum(loss_terms)
        push = math.fsum(push_terms)
        expected_profit = win * (odds - 1.0) - loss

        return {
            "side": side,
            "handicap": line,
            "decimal_odds": odds,
            "winning_stake_probability": win,
            "losing_stake_probability": loss,
            "pushed_stake_probability": push,
            "fair_decimal_odds": 1.0 + loss / win if win > 0.0 else math.inf,
            "expected_profit_per_unit_stake": expected_profit,
            "eligible": odds >= self.MINIMUM_ELIGIBLE_ODDS and expected_profit > 0.0,
            "truncation_ev_error_bound": odds * self.tail_probability,
        }