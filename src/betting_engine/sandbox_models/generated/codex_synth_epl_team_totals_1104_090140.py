"""Independent full-match EPL team-goal candidate; deterministic and unfunded.

Expected-goal inputs must be pre-match, independently estimated full-match means.
They must not be inferred from bookmaker or Polymarket prices. Market prices
belong in downstream valuation, never in this forecast.

The shared-gamma Poisson model permits calibrated overdispersion and positive
score dependence. An optional Dixon–Coles four-cell adjustment preserves both
team-goal marginals. Consequently, Dixon–Coles dependence alone cannot improve
team-total forecasts: independently estimated marginal means and dispersion
must supply the predictive skill.

This class neither submits orders nor certifies admission, profitability, daily
volume, or portfolio risk. Those require chronological out-of-sample evaluation,
an immutable settlement ledger, and external promotion controls.
"""

from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
)


class EPLTeamTotalsCandidateModel(BaseCandidateModel):
    """Joint home/away score distribution supporting either team's totals.

    Context contract
    ----------------
    Required:
        home_expected, away_expected:
            Independent, positive full-match expected goals.

    Optional:
        expected_goals_source:
            Provenance label; explicitly market-derived labels are rejected.
        expected_goals_period:
            "full_match" or "full_time".
        league:
            "EPL".
        home_rest_days, away_rest_days:
            Nonnegative rest durations.
        weather:
            Mapping containing wind_speed, temp, and optionally is_dome.

    Calibration contract
    --------------------
    shared_pace_variance:
        Nonnegative variance of a unit-mean gamma match-pace factor. Zero
        recovers independent Poisson marginals. Each team's marginal variance
        is mean + shared_pace_variance * mean**2.
    dc_rho:
        Four-cell low-score parameter. At zero pace variance this is the
        standard Dixon–Coles parameter. Invalid probabilities raise errors;
        the parameter is never silently clipped.
    home_log_rate_coefficients, away_log_rate_coefficients:
        Optional independently fitted coefficients for the documented feature
        names. Defaults apply no speculative contextual adjustments.

    Fit all parameters using information available before each fixture.
    Calibrate and evaluate on chronologically subsequent, match-disjoint data.
    Multiple lines from one match do not constitute independent observations.
    """

    market_anchor_mode = "shadow"
    model_weight = 1.0
    external_data_cost = 0.0
    automated_order_submission = False

    _FEATURE_NAMES = frozenset(
        {
            "intercept",
            "home_log_rest",
            "away_log_rest",
            "outdoor_wind_speed",
            "outdoor_temperature",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_team_totals_1104_090140",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "team_totals",
        family: str = "totals",
        *,
        shared_pace_variance: float = 0.0,
        dc_rho: float = 0.0,
        home_log_rate_coefficients: Optional[Mapping[str, float]] = None,
        away_log_rate_coefficients: Optional[Mapping[str, float]] = None,
        tail_tolerance: float = 1e-12,
        max_lattice_cells: int = 1_000_000,
    ) -> None:
        if (league, scope, family) != ("EPL", "team_totals", "totals"):
            raise ValueError("This candidate exclusively supports EPL team totals.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.shared_pace_variance = self._finite(
            shared_pace_variance, "shared_pace_variance"
        )
        if self.shared_pace_variance < 0.0:
            raise ValueError("shared_pace_variance must be nonnegative.")

        self.dc_rho = self._finite(dc_rho, "dc_rho")
        self.tail_tolerance = self._finite(tail_tolerance, "tail_tolerance")
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if (
            isinstance(max_lattice_cells, bool)
            or not isinstance(max_lattice_cells, int)
            or max_lattice_cells < 4
        ):
            raise ValueError("max_lattice_cells must be an integer of at least four.")
        self.max_lattice_cells = max_lattice_cells

        self._home_coefficients = self._validate_coefficients(
            home_log_rate_coefficients
        )
        self._away_coefficients = self._validate_coefficients(
            away_log_rate_coefficients
        )

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be a finite number.")
        return result

    @classmethod
    def _validate_coefficients(
        cls, coefficients: Optional[Mapping[str, float]]
    ) -> Tuple[Tuple[str, float], ...]:
        if coefficients is None:
            return ()
        if not isinstance(coefficients, Mapping):
            raise ValueError("Rate coefficients must be a mapping.")
        unknown = set(coefficients).difference(cls._FEATURE_NAMES)
        if unknown:
            raise ValueError(f"Unsupported rate features: {sorted(map(str, unknown))}")
        return tuple(
            (name, cls._finite(value, f"coefficient[{name}]"))
            for name, value in sorted(coefficients.items())
            if cls._finite(value, f"coefficient[{name}]") != 0.0
        )

    def _feature(self, name: str, context: Mapping[str, Any]) -> float:
        if name == "intercept":
            return 1.0

        if name in ("home_log_rest", "away_log_rest"):
            field = "home_rest_days" if name == "home_log_rest" else "away_rest_days"
            if field not in context:
                raise ValueError(f"Fitted feature requires {field}.")
            rest = self._finite(context[field], field)
            if rest < 0.0:
                raise ValueError(f"{field} must be nonnegative.")
            return math.log1p(rest)

        weather = context.get("weather")
        if not isinstance(weather, Mapping):
            raise ValueError(f"Fitted feature {name} requires a weather mapping.")

        is_dome = weather.get("is_dome", False)
        if not isinstance(is_dome, (bool, np.bool_)):
            raise ValueError("weather.is_dome must be boolean.")
        if is_dome:
            return 0.0

        field = "wind_speed" if name == "outdoor_wind_speed" else "temp"
        if field not in weather:
            raise ValueError(f"Fitted feature requires weather.{field}.")
        value = self._finite(weather[field], f"weather.{field}")
        if field == "wind_speed" and value < 0.0:
            raise ValueError("weather.wind_speed must be nonnegative.")
        return value

    def _rate(
        self,
        side: str,
        context: Mapping[str, Any],
        coefficients: Tuple[Tuple[str, float], ...],
    ) -> float:
        field = f"{side}_expected"
        if field not in context:
            raise ValueError(f"Missing independent full-match estimate: {field}.")
        mean = self._finite(context[field], field)
        if mean <= 0.0:
            raise ValueError(f"{field} must be strictly positive.")

        if not coefficients:
            return mean

        adjustment = math.fsum(
            coefficient * self._feature(name, context)
            for name, coefficient in coefficients
        )
        try:
            adjusted = math.exp(math.log(mean) + adjustment)
        except OverflowError as exc:
            raise ValueError(f"{side} adjusted scoring rate overflowed.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError(f"{side} adjusted scoring rate is not representable.")
        return adjusted

    def _support_endpoint(self, mean: float) -> int:
        """Choose support by tail probability, never by a fixed goal cutoff."""
        tail_budget = self.tail_tolerance / 2.0

        if self.shared_pace_variance == 0.0:
            marginal = poisson(mu=mean)
        else:
            shape = 1.0 / self.shared_pace_variance
            probability = 1.0 / (1.0 + self.shared_pace_variance * mean)
            if (
                not math.isfinite(shape)
                or not 0.0 < probability < 1.0
            ):
                raise ValueError("Dispersion is outside representable numeric range.")
            marginal = nbinom(n=shape, p=probability)

        endpoint = float(marginal.isf(tail_budget))
        if not math.isfinite(endpoint):
            raise ValueError("Cannot resolve score support at requested tolerance.")

        endpoint_int = max(1, int(math.ceil(endpoint)))
        while True:
            remaining = float(marginal.sf(endpoint_int))
            if not math.isfinite(remaining):
                raise ArithmeticError("Nonfinite marginal tail probability.")
            if remaining <= tail_budget:
                return endpoint_int
            endpoint_int += 1
            if endpoint_int + 1 > self.max_lattice_cells:
                raise ValueError("Requested tail accuracy exceeds lattice budget.")

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")
        if game_context.get("league", "EPL") != "EPL":
            raise ValueError("Context league must be EPL.")
        if game_context.get("expected_goals_period", "full_match") not in (
            "full_match",
            "full_time",
        ):
            raise ValueError("Team totals require full-match expected goals.")

        source = str(game_context.get("expected_goals_source", "")).lower()
        if any(
            token in source
            for token in ("bookmaker", "polymarket", "market_implied", "market-implied")
        ):
            raise ValueError("Market-derived expected goals violate independence.")

        home_mean = self._rate(
            "home", game_context, self._home_coefficients
        )
        away_mean = self._rate(
            "away", game_context, self._away_coefficients
        )
        home_max = self._support_endpoint(home_mean)
        away_max = self._support_endpoint(away_mean)

        cells = (home_max + 1) * (away_max + 1)
        if cells > self.max_lattice_cells:
            raise ValueError(
                f"Required lattice has {cells} cells; resource budget is "
                f"{self.max_lattice_cells}. No score truncation was substituted."
            )

        home = np.arange(home_max + 1, dtype=np.float64)[:, None]
        away = np.arange(away_max + 1, dtype=np.float64)[None, :]

        if self.shared_pace_variance == 0.0:
            log_mass = (
                poisson.logpmf(home, home_mean)
                + poisson.logpmf(away, away_mean)
            )
        else:
            shape = 1.0 / self.shared_pace_variance
            total_mean = home_mean + away_mean
            if not math.isfinite(total_mean):
                raise ValueError("Combined scoring rate is not representable.")
            log_denominator = math.log(shape + total_mean)
            log_mass = (
                gammaln(shape + home + away)
                - gammaln(shape)
                - gammaln(home + 1.0)
                - gammaln(away + 1.0)
                - shape * math.log1p(total_mean / shape)
                + home * (math.log(home_mean) - log_denominator)
                + away * (math.log(away_mean) - log_denominator)
            )

        joint = np.exp(log_mass)
        if not np.all(np.isfinite(joint)):
            raise ArithmeticError("Score lattice contains nonfinite probabilities.")

        # Alternating cell transfers preserve every row and column sum.
        # For independent Poisson, P(1,1) = lambda_h * lambda_a * P(0,0),
        # giving exactly the conventional Dixon–Coles low-score correction.
        transfer = -self.dc_rho * float(joint[1, 1])
        corrected = np.array(
            [
                joint[0, 0] + transfer,
                joint[0, 1] - transfer,
                joint[1, 0] - transfer,
                joint[1, 1] + transfer,
            ],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(corrected)) or np.any(corrected < 0.0):
            raise ValueError("dc_rho produces an invalid low-score probability.")
        joint[0, 0], joint[0, 1], joint[1, 0], joint[1, 1] = corrected

        retained_mass = math.fsum(joint.ravel())
        numerical_slack = (
            128.0 * np.finfo(np.float64).eps * (home_max + away_max + 2)
        )
        if (
            retained_mass <= 0.0
            or not math.isfinite(retained_mass)
            or abs(retained_mass - 1.0)
            > self.tail_tolerance + numerical_slack
        ):
            raise ArithmeticError(
                "Lattice mass failed verification; do not fund this forecast."
            )

        # Conditioning on the retained rectangle incurs at most the verified
        # omitted-tail error. No tail mass is piled into an artificial last bin.
        joint /= retained_mass
        scores = {
            (home_goals, away_goals): float(joint[home_goals, away_goals])
            for home_goals in range(home_max + 1)
            for away_goals in range(away_max + 1)
        }
        return DiscreteDistribution.from_scores(scores)