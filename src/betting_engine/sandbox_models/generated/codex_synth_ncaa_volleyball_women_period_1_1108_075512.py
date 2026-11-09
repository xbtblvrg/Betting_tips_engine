from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import expit, logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWomenFirstSetDoubleChanceModel(BaseCandidateModel):
    """
    Serve-dependent, absorbing rally model for first-set double chance.

    Scoring:
        First to 25, win by two; the rally winner serves next.
        https://goduke.com/news/2008/8/21/1566064

    Completed-set outcomes:
        1X = home set win; X2 = away set win; draw probability = 0.
        The tautological 12 contract is deliberately not quoted.

    Cold-start identification:
        0.53 and 0.40 are relative set-win strengths, not point intensities.
        Their normalization is a modeling assumption; their missing mass
        is not evidence of an observed draw frequency.

        These two anchors do not identify serving effects, rest effects,
        attack coefficients, or predictive uncertainty. Without an externally
        estimated league_sideout_rate, the model uses maximum-entropy 1/2.
        No unsupported 0.60 side-out estimate or situational coefficient is
        silently introduced.

    Optional context:
        home_expected, away_expected:
            Strictly positive, independently generated relative strengths.
            Defaults are the supplied empirical anchors.

        league_sideout_rate:
            Independently estimated receiving-team rally-win probability.

        first_server:
            "home" or "away"; otherwise first_server_home_probability,
            defaulting to the symmetric unknown-server prior.

        feature_coefficients:
            Mapping of feature names below to externally fitted coefficients,
            on the HOME RALLY log-odds scale. Supply only coefficients fitted
            on information available before prediction. If expected strengths
            already include these effects, omit coefficients to avoid duplication.

        home / away:
            attack: {"kills": ..., "errors": ..., "attempts": ...}
            defense: {"opponent_kills": ..., "opponent_errors": ...,
                      "opponent_attempts": ...}
            pace: positive rallies per unit time

        home_rest_days, away_rest_days

    Available situational features:
        attack_efficiency_difference
        defensive_efficiency_difference
        rest_days_difference
        log_pace_ratio

    Pace changes elapsed time, not the set's scoring threshold. It has no
    direct probability effect unless an independently fitted coefficient
    establishes a fatigue or composition effect. Indoor weather is unused.

    Predictions are conditional on the first set being completed and settled
    under ordinary set-winner rules. No calibration or profitability claim
    can be established from zero settled wagers.
    """

    HOME_ANCHOR = 0.53
    AWAY_ANCHOR = 0.40
    SET_TARGET = 25
    MIN_DECIMAL_ODDS = 1.40

    FEATURE_NAMES = frozenset(
        {
            "attack_efficiency_difference",
            "defensive_efficiency_difference",
            "rest_days_difference",
            "log_pace_ratio",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_volleyball_women_period_1_1108_075512",
        version: str = "1.0.0",
        league: str = "NCAA_VOLLEYBALL_WOMEN",
        scope: str = "period_1",
        family: str = "double_chance",
    ) -> None:
        if (league, scope, family) != (
            "NCAA_VOLLEYBALL_WOMEN",
            "period_1",
            "double_chance",
        ):
            raise ValueError("This candidate supports only its declared segment.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _probability(
        cls, value: Any, name: str, *, interior: bool = False
    ) -> float:
        result = cls._number(value, name)
        valid = 0.0 < result < 1.0 if interior else 0.0 <= result <= 1.0
        if not valid:
            raise ValueError(f"{name} is outside its probability domain.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _efficiency(
        cls, team: Mapping, side: str, *, defense: bool
    ) -> float:
        group = "defense" if defense else "attack"
        prefix = "opponent_" if defense else ""
        stats = cls._mapping(team.get(group), f"{side}.{group}")
        names = [prefix + field for field in ("kills", "errors", "attempts")]
        values = []
        for name in names:
            if name not in stats:
                raise ValueError(f"Missing {side}.{group}.{name}.")
            values.append(cls._number(stats[name], f"{side}.{group}.{name}"))

        kills, errors, attempts = values
        if attempts <= 0.0 or kills < 0.0 or errors < 0.0:
            raise ValueError(f"Invalid {side}.{group} exposure.")
        if kills + errors > attempts:
            raise ValueError(f"{side}.{group}: kills + errors exceed attempts.")

        efficiency = (kills - errors) / attempts
        return -efficiency if defense else efficiency

    @classmethod
    def _feature(cls, name: str, context: Mapping) -> float:
        if name == "rest_days_difference":
            values = []
            for side in ("home", "away"):
                key = f"{side}_rest_days"
                if key not in context:
                    raise ValueError(f"Missing {key} for configured rest effect.")
                value = cls._number(context[key], key)
                if value < 0.0:
                    raise ValueError(f"{key} cannot be negative.")
                values.append(value)
            return values[0] - values[1]

        home = cls._mapping(context.get("home"), "home")
        away = cls._mapping(context.get("away"), "away")

        if name == "log_pace_ratio":
            h = cls._positive(home.get("pace"), "home.pace")
            a = cls._positive(away.get("pace"), "away.pace")
            return math.log(h) - math.log(a)

        defense = name == "defensive_efficiency_difference"
        return cls._efficiency(home, "home", defense=defense) - cls._efficiency(
            away, "away", defense=defense
        )

    @classmethod
    def _situational_shift(cls, context: Mapping) -> float:
        coefficients = cls._mapping(
            context.get("feature_coefficients", {}), "feature_coefficients"
        )
        unknown = set(coefficients) - cls.FEATURE_NAMES
        if unknown:
            raise ValueError(f"Unsupported feature coefficients: {unknown!r}")

        terms = []
        for name, raw_coefficient in coefficients.items():
            coefficient = cls._number(raw_coefficient, f"coefficient[{name}]")
            if coefficient != 0.0:
                term = coefficient * cls._feature(name, context)
                if not math.isfinite(term):
                    raise ValueError(f"Nonfinite contribution from {name}.")
                terms.append(term)
        try:
            shift = math.fsum(terms)
        except OverflowError as exc:
            raise ValueError("Situational log-odds shift overflowed.") from exc
        if not math.isfinite(shift):
            raise ValueError("Situational log-odds shift must be finite.")
        return shift

    @staticmethod
    def _deuce_probabilities(
        home_serve_logit: float, away_serve_logit: float
    ) -> Tuple[float, float]:
        """
        Exact infinite-deuce solution, retaining the server state.

        a = P(home rally win | home serves)
        b = P(home rally win | away serves)

        With x,y the home set-win probabilities at tied deuce:
            x = a*a + a*(1-a)*y + (1-a)*b*x
            y = b*a + b*(1-a)*y + (1-b)*b*x

        Unlike a single A/(A+B) expression, these equations account for a
        split pair of rallies changing the server. Positive-term determinant
        expansions avoid subtractive cancellation; logs avoid underflow.
        """
        log_a = -float(np.logaddexp(0.0, -home_serve_logit))
        log_not_a = -float(np.logaddexp(0.0, home_serve_logit))
        log_b = -float(np.logaddexp(0.0, -away_serve_logit))
        log_not_b = -float(np.logaddexp(0.0, away_serve_logit))

        # Two-rally absorption and tied-state transition probabilities.
        A = log_a + log_a
        B = log_not_a + log_not_b
        C = log_a + log_not_a
        F = log_b + log_a
        G = log_not_b + log_not_b
        E = log_not_b + log_b

        denominator = float(
            logsumexp(
                [A + F, A + G, A + E, B + F, B + G, B + E, C + F, C + G]
            )
        )
        numerator_h = float(logsumexp([A + F, A + G, A + E, C + F]))
        numerator_a = float(logsumexp([F + A, F + B, F + C, E + A]))

        return (
            math.exp(numerator_h - denominator),
            math.exp(numerator_a - denominator),
        )

    @classmethod
    def _set_probability(
        cls,
        strength: float,
        sideout_logit: float,
        first_server_home_probability: float,
    ) -> float:
        home_serve_logit = strength - sideout_logit
        away_serve_logit = strength + sideout_logit
        if not all(
            math.isfinite(x) for x in (home_serve_logit, away_serve_logit)
        ):
            raise ValueError("Rally log-odds overflowed.")

        a = float(expit(home_serve_logit))
        b = float(expit(away_serve_logit))
        deuce_h, deuce_a = cls._deuce_probabilities(
            home_serve_logit, away_serve_logit
        )

        target = cls.SET_TARGET
        table = np.empty((target + 1, target + 1, 2), dtype=float)
        table[target, :, :] = 1.0
        table[:, target, :] = 0.0

        for home_score in range(target - 1, -1, -1):
            for away_score in range(target - 1, -1, -1):
                if home_score == away_score == target - 1:
                    table[home_score, away_score] = (deuce_h, deuce_a)
                    continue

                after_home = table[home_score + 1, away_score, 0]
                after_away = table[home_score, away_score + 1, 1]
                table[home_score, away_score, 0] = (
                    a * after_home + (1.0 - a) * after_away
                )
                table[home_score, away_score, 1] = (
                    b * after_home + (1.0 - b) * after_away
                )

        result = (
            first_server_home_probability * table[0, 0, 0]
            + (1.0 - first_server_home_probability) * table[0, 0, 1]
        )
        if not math.isfinite(result) or not 0.0 <= result <= 1.0:
            raise ArithmeticError("Invalid probability from rally recursion.")
        return float(result)

    @staticmethod
    @lru_cache(maxsize=None)
    def _calibrated_strength(target: float, sideout_logit: float) -> float:
        """Invert the monotone set-win map without a fixed strength clamp."""
        symmetric_server_prior = 1.0 / 2.0

        def residual(strength: float) -> float:
            return (
                NCAAWomenFirstSetDoubleChanceModel._set_probability(
                    strength, sideout_logit, symmetric_server_prior
                )
                - target
            )

        lower, upper = -1.0, 1.0
        while residual(lower) > 0.0:
            lower *= 2.0
            if not math.isfinite(lower):
                raise ArithmeticError("Unable to bracket lower rally strength.")
        while residual(upper) < 0.0:
            upper *= 2.0
            if not math.isfinite(upper):
                raise ArithmeticError("Unable to bracket upper rally strength.")

        return float(brentq(residual, lower, upper))

    def _home_probability(self, game_context: Dict[str, Any]) -> float:
        context = self._mapping(game_context, "game_context")
        for key, expected in (
            ("league", "NCAA_VOLLEYBALL_WOMEN"),
            ("scope", "period_1"),
            ("family", "double_chance"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Context {key} does not match this candidate.")

        home = self._positive(
            context.get("home_expected", self.HOME_ANCHOR), "home_expected"
        )
        away = self._positive(
            context.get("away_expected", self.AWAY_ANCHOR), "away_expected"
        )

        # Scale first to avoid overflowing the normalization denominator.
        scale = max(home, away)
        home_scaled, away_scaled = home / scale, away / scale
        target = home_scaled / (home_scaled + away_scaled)
        if not 0.0 < target < 1.0:
            raise ValueError("Relative strengths exceed numerical resolution.")

        sideout = self._probability(
            context.get("league_sideout_rate", 1.0 / 2.0),
            "league_sideout_rate",
            interior=True,
        )
        sideout_logit = math.log(sideout) - math.log1p(-sideout)

        first_server = context.get("first_server")
        if first_server is None:
            server_probability = self._probability(
                context.get("first_server_home_probability", 1.0 / 2.0),
                "first_server_home_probability",
            )
        elif first_server in ("home", "away"):
            server_probability = float(first_server == "home")
            if "first_server_home_probability" in context:
                supplied = self._probability(
                    context["first_server_home_probability"],
                    "first_server_home_probability",
                )
                if supplied != server_probability:
                    raise ValueError("Conflicting first-server inputs.")
        else:
            raise ValueError("first_server must be 'home', 'away', or None.")

        strength = self._calibrated_strength(target, sideout_logit)
        strength += self._situational_shift(context)
        if not math.isfinite(strength):
            raise ValueError("Combined rally strength must be finite.")

        return self._set_probability(strength, sideout_logit, server_probability)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        home_probability = self._home_probability(game_context)
        return DiscreteDistribution(
            probabilities={
                "home": home_probability,
                "draw": 0.0,
                "away": 1.0 - home_probability,
            }
        )

    def double_chance_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[str, float]:
        """Supported contracts only; intentionally excludes the certain 12 leg."""
        home_probability = self._home_probability(game_context)
        return {"1X": home_probability, "X2": 1.0 - home_probability}

    def evaluate_contract(
        self,
        game_context: Dict[str, Any],
        selection: str,
        decimal_odds: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Continuous unit-stake valuation, separate from the forecast.

        There is no upper odds cap, bookmaker-derived forecast adjustment,
        confidence threshold, or arbitrary minimum positive edge.
        """
        if selection == "12":
            return None
        if selection not in ("1X", "X2"):
            raise ValueError("selection must be '1X', 'X2', or '12'.")

        odds = self._number(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds cannot be below one.")

        probability = self.double_chance_probabilities(game_context)[selection]
        expected_return = probability * odds - 1.0
        return {
            "selection": selection,
            "probability": probability,
            "decimal_odds": odds,
            "fair_decimal_odds": (
                1.0 / probability if probability > 0.0 else math.inf
            ),
            "expected_return_per_unit_stake": expected_return,
            "eligible": (
                odds >= self.MIN_DECIMAL_ODDS and expected_return > 0.0
            ),
        }