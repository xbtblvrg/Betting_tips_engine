from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple

from scipy.optimize import brentq
from scipy.special import expit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWomenVolleyballPeriod2DoubleChanceModel(BaseCandidateModel):
    """
    Conditional set-2 winner model for a normally completed volleyball set.

    DiscreteDistribution is supplied by the execution environment.

    A completed second set has no draw. Consequently, 1X and X2 have the
    probabilities of home and away set wins; 12 has probability one.
    Abandonment, forfeiture, and bookmaker settlement rules belong outside
    this conditional sporting-outcome distribution.

    The supplied 0.53/0.40 figures are interpreted as relative set-strength
    weights, not expected volleyball points or independent Poisson means.

    Identifiability:
        One set-win probability cannot identify two sideout probabilities.
        Their common log-odds level must therefore come from an independently
        estimated sideout rate. In its absence, maximum entropy supplies
        probability 1/2, explicitly an unvalidated cold-start assumption.

    Situational coefficients must be estimated externally on chronologically
    separated training data. No predictive coefficients are invented from
    the zero-wager sample. An empty coefficient mapping is the honest
    baseline-only candidate, not evidence of calibrated betting alpha.

    Context contract:
        home_expected, away_expected:
            Positive relative set-strength weights.
        sideout_rate:
            Optional independently estimated receiving-team rally-win rate.
        home_serves_first_probability:
            Optional probability home serves first in set 2. Defaults to 1/2.
        home / away:
            Optional mappings containing attack, defense, exposure, rest_days.
            Attack and defense must be consistently oriented strength metrics.
        home_attack, away_attack, home_defense, away_defense,
        home_exposure, away_exposure, home_rest_days, away_rest_days:
            Flat alternatives to nested team fields.
        pace or tempo:
            Positive exposure/tempo measure, in training-consistent units.
        set_1_winner:
            "home" or "away", observed before the second set.

    Supported externally fitted coefficient names:
        attack_difference, defense_difference, log_exposure_ratio,
        rest_difference, log_pace, set_1_result, home_term.

    Features shift the calibrated sideout log-odds contrast. Positive shifts
    increase home sideout and decrease away sideout. Pace changes forecast
    probabilities only if its fitted coefficient supports that relationship.
    Weather and odds are deliberately absent from this indoor sporting model.
    """

    _FEATURE_NAMES = frozenset(
        {
            "attack_difference",
            "defense_difference",
            "log_exposure_ratio",
            "rest_difference",
            "log_pace",
            "set_1_result",
            "home_term",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_volleyball_women_period_2_1108_111247",
        version: str = "1.0.0",
        league: str = "NCAA_VOLLEYBALL_WOMEN",
        scope: str = "period_2",
        family: str = "double_chance",
        *,
        baseline_home: float = 0.53,
        baseline_away: float = 0.40,
        sideout_rate: Optional[float] = None,
        coefficients: Optional[Mapping[str, float]] = None,
    ) -> None:
        if (league, scope, family) != (
            "NCAA_VOLLEYBALL_WOMEN",
            "period_2",
            "double_chance",
        ):
            raise ValueError("This candidate is specific to its declared segment.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.baseline_home = self._positive(baseline_home, "baseline_home")
        self.baseline_away = self._positive(baseline_away, "baseline_away")
        self.sideout_rate = (
            1.0 / 2.0
            if sideout_rate is None
            else self._probability(sideout_rate, "sideout_rate", interior=True)
        )
        supplied = dict(coefficients or {})
        unknown = supplied.keys() - self._FEATURE_NAMES
        if unknown:
            raise ValueError(f"Unsupported coefficients: {sorted(unknown)}")
        self.coefficients = {
            name: self._finite(value, f"coefficient[{name}]")
            for name, value in supplied.items()
        }

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _probability(
        cls, value: Any, name: str, *, interior: bool = False
    ) -> float:
        result = cls._finite(value, name)
        valid = 0.0 < result < 1.0 if interior else 0.0 <= result <= 1.0
        if not valid:
            interval = "(0, 1)" if interior else "[0, 1]"
            raise ValueError(f"{name} must lie in {interval}.")
        return result

    @staticmethod
    def _logit(probability: float) -> float:
        return math.log(probability) - math.log1p(-probability)

    @classmethod
    def _team_value(
        cls, context: Mapping[str, Any], side: str, field: str
    ) -> float:
        flat_name = f"{side}_{field}"
        if flat_name in context:
            return cls._finite(context[flat_name], flat_name)
        team = context.get(side)
        if isinstance(team, Mapping) and field in team:
            return cls._finite(team[field], f"{side}.{field}")
        raise ValueError(f"Fitted feature requires {flat_name} or {side}.{field}.")

    def _feature_value(self, name: str, context: Mapping[str, Any]) -> float:
        if name == "home_term":
            return 1.0

        if name in {"attack_difference", "defense_difference", "rest_difference"}:
            field = {
                "attack_difference": "attack",
                "defense_difference": "defense",
                "rest_difference": "rest_days",
            }[name]
            home = self._team_value(context, "home", field)
            away = self._team_value(context, "away", field)
            if field == "rest_days" and (home < 0.0 or away < 0.0):
                raise ValueError("Rest days must be nonnegative.")
            return self._finite(home - away, name)

        if name == "log_exposure_ratio":
            home = self._positive(
                self._team_value(context, "home", "exposure"), "home_exposure"
            )
            away = self._positive(
                self._team_value(context, "away", "exposure"), "away_exposure"
            )
            return math.log(home) - math.log(away)

        if name == "log_pace":
            if "pace" in context:
                value = context["pace"]
            elif "tempo" in context:
                value = context["tempo"]
            else:
                raise ValueError("Fitted log_pace coefficient requires pace or tempo.")
            return math.log(self._positive(value, "pace"))

        if name == "set_1_result":
            winner = context.get("set_1_winner")
            if winner not in ("home", "away"):
                raise ValueError(
                    "Fitted set_1_result coefficient requires set_1_winner."
                )
            return 1.0 if winner == "home" else -1.0

        raise ValueError(f"Unsupported feature: {name}")

    @staticmethod
    def _rally_probabilities(
        common_logit: float, contrast: float
    ) -> Tuple[float, float, float, float]:
        """
        Return home/away rally probabilities under home/away service.

        Compute complementary probabilities directly through expit to avoid
        cancellation in 1 - expit(x) for large finite predictors.
        """
        home_sideout_logit = common_logit + contrast
        away_sideout_logit = common_logit - contrast
        return (
            float(expit(-away_sideout_logit)),
            float(expit(away_sideout_logit)),
            float(expit(home_sideout_logit)),
            float(expit(-home_sideout_logit)),
        )

    @classmethod
    def _set_home_probability(
        cls, common_logit: float, contrast: float, first_home: float
    ) -> float:
        """
        Exact finite-state race to 25, with an analytically absorbed deuce tail.

        The rally winner serves next. Deuce retains the server state; replacing
        it with a single independent two-rally probability is generally invalid.
        """
        q, a, r, b = cls._rally_probabilities(common_logit, contrast)

        # From tied scores, inspect the next two rallies. Each path either
        # absorbs or returns to a tie with home/away serving.
        win_h, lose_h, switch_h = q * q, a * b, q * a
        win_a, lose_a, switch_a = r * q, b * b, b * r
        exit_h = win_h + lose_h
        exit_a = win_a + lose_a
        determinant = (
            exit_h * exit_a + exit_h * switch_a + switch_h * exit_a
        )
        if determinant <= 0.0 or not math.isfinite(determinant):
            raise ArithmeticError(
                "Sideout inputs produce a numerically nonabsorbing deuce race."
            )
        deuce_h = (
            win_h * (exit_a + switch_a) + switch_h * win_a
        ) / determinant
        deuce_a = (
            win_a * (exit_h + switch_h) + switch_a * win_h
        ) / determinant

        # Scores before deuce are 0..24. Each entry holds probability mass
        # indexed by current server: 0 = home, 1 = away.
        target = 25
        tie = target - 1
        states = [
            [[0.0, 0.0] for _ in range(target)] for _ in range(target)
        ]
        states[0][0] = [first_home, 1.0 - first_home]
        home_win_terms = []

        for home_score in range(target):
            for away_score in range(target):
                home_serving, away_serving = states[home_score][away_score]
                if home_score == tie and away_score == tie:
                    home_win_terms.extend(
                        (home_serving * deuce_h, away_serving * deuce_a)
                    )
                    continue

                home_next = home_serving * q + away_serving * r
                away_next = home_serving * a + away_serving * b

                if home_score == tie:
                    home_win_terms.append(home_next)
                else:
                    states[home_score + 1][away_score][0] += home_next

                if away_score < tie:
                    states[home_score][away_score + 1][1] += away_next

        probability = math.fsum(home_win_terms)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ArithmeticError("Invalid set-win probability; no clipping applied.")
        return probability

    @staticmethod
    @lru_cache(maxsize=None)
    def _calibrated_contrast(
        common_logit: float, target_probability: float, first_home: float
    ) -> float:
        def residual(contrast: float) -> float:
            return (
                NCAAWomenVolleyballPeriod2DoubleChanceModel._set_home_probability(
                    common_logit, contrast, first_home
                )
                - target_probability
            )

        origin = residual(0.0)
        if origin == 0.0:
            return 0.0

        # Adaptive bracketing supplies numerical search bounds, not forecast
        # clamps or assumptions about the attainable team-strength contrast.
        if origin < 0.0:
            lower, upper = 0.0, 1.0
            while residual(upper) < 0.0:
                upper *= 2.0
                if not math.isfinite(upper):
                    raise ArithmeticError("Unable to bracket baseline calibration.")
        else:
            lower, upper = -1.0, 0.0
            while residual(lower) > 0.0:
                lower *= 2.0
                if not math.isfinite(lower):
                    raise ArithmeticError("Unable to bracket baseline calibration.")

        return float(brentq(residual, lower, upper))

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home = self._positive(
            game_context.get("home_expected", self.baseline_home), "home_expected"
        )
        away = self._positive(
            game_context.get("away_expected", self.baseline_away), "away_expected"
        )
        baseline = float(expit(math.log(home) - math.log(away)))
        if not 0.0 < baseline < 1.0:
            raise ArithmeticError(
                "Relative strengths exceed floating-point calibration resolution."
            )

        sideout = self._probability(
            game_context.get("sideout_rate", self.sideout_rate),
            "sideout_rate",
            interior=True,
        )
        first_home = self._probability(
            game_context.get("home_serves_first_probability", 1.0 / 2.0),
            "home_serves_first_probability",
        )
        common_logit = self._logit(sideout)
        baseline_contrast = self._calibrated_contrast(
            common_logit, baseline, first_home
        )

        feature_terms = [
            self._finite(
                coefficient * self._feature_value(name, game_context),
                f"contribution[{name}]",
            )
            for name, coefficient in self.coefficients.items()
            if coefficient != 0.0
        ]
        try:
            shift = math.fsum(feature_terms)
        except OverflowError as exc:
            raise ValueError("Situational feature contributions overflow.") from exc

        contrast = self._finite(baseline_contrast + shift, "sideout_contrast")
        home_probability = (
            baseline
            if shift == 0.0
            else self._set_home_probability(common_logit, contrast, first_home)
        )

        return DiscreteDistribution(
            probabilities={
                "home": home_probability,
                "draw": 0.0,
                "away": 1.0 - home_probability,
            }
        )