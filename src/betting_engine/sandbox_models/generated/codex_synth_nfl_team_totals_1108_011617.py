from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLTeamTotalsModel(BaseCandidateModel):
    """
    Independent NFL team-total model with an explicitly identified cold-start prior.

    Means alone do not identify possession counts, drive outcome probabilities,
    dispersion, or score dependence. With no drive data, this model uses the
    supplied Poisson prior (variance / mean = 1). It does not manufacture a
    possession count or claim that a drive model has been empirically calibrated.

    Supply `drive_model` to activate compound-drive scoring:

        game_context = {
            "home_expected": 24.5,
            "away_expected": 21.5,
            "drive_model": {
                "home": {
                    "count_mean": ...,
                    "count_dispersion": ...,
                    "outcome_weights": {
                        0: ..., 2: ..., 3: ..., 6: ..., 7: ..., 8: ...
                    },
                },
                "away": {...},
            },
        }

    count_dispersion is Var(N) / E[N], and must be >= 1. Exactly 1 selects
    Poisson possessions; larger values select negative-binomial possessions.
    Outcome weights must come from external, pregame data or declared priors.
    Exponential tilting preserves their relative structure while matching each
    requested team mean exactly before numerical tail truncation.

    Optional context:
      pace_multiplier: positive multiplier for both points and possessions.
      home/away:
        pace_multiplier: positive team multiplier.
        attack, defense, exposure: positive ratios relative to training baselines.
      home_rest_days, away_rest_days
      weather: wind_speed, temp, is_dome

    Optional constructor coefficients, fitted outside this class using only
    information available before prediction:
      attack_log, opponent_defense_log, exposure_log,
      rest_difference, wind_speed, temp.

    Features without fitted coefficients have no effect. Explicit expected
    values are pre-feature anchors; omit coefficients if those values already
    incorporate the same effects.

    from_scores receives a mapping {(home_score, away_score): probability}.
    Its marginals price team totals. Independence is an unidentified joint
    completion, not an assertion about actual NFL score dependence.

    Numerical truncation is controlled by a probability-error budget derived
    from machine precision unless supplied explicitly. No odds blending,
    probability clipping, learned coefficients, or performance claims are hidden
    in the implementation.
    """

    DEFAULT_HOME_EXPECTED = 24.50
    DEFAULT_AWAY_EXPECTED = 21.50
    SEGMENT = ("NFL", "team_totals", "totals")

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_team_totals_1108_011617",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "team_totals",
        family: str = "totals",
        *,
        coefficients: Optional[Mapping[str, float]] = None,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        if (league, scope, family) != self.SEGMENT:
            raise ValueError("This model supports only NFL|team_totals|totals.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        allowed = {
            "attack_log",
            "opponent_defense_log",
            "exposure_log",
            "rest_difference",
            "wind_speed",
            "temp",
        }
        supplied = dict(coefficients or {})
        unknown = set(supplied) - allowed
        if unknown:
            raise ValueError(f"Unknown feature coefficients: {sorted(unknown)}")
        self.coefficients = {
            key: self._number(value, key) for key, value in supplied.items()
        }

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_probability must be strictly between 0 and 1.")
        self.tail_probability = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
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
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _pace(self, context: Mapping, side: str) -> float:
        team = self._mapping(context.get(side, {}), side)
        pace = self._positive(
            context.get("pace_multiplier", 1.0), "pace_multiplier"
        ) * self._positive(
            team.get("pace_multiplier", 1.0), f"{side}.pace_multiplier"
        )
        if not math.isfinite(pace):
            raise ValueError("Combined pace multiplier overflowed.")
        return pace

    def _expected(self, context: Mapping, side: str) -> float:
        opponent = "away" if side == "home" else "home"
        default = (
            self.DEFAULT_HOME_EXPECTED
            if side == "home"
            else self.DEFAULT_AWAY_EXPECTED
        )
        anchor = self._number(
            context.get(f"{side}_expected", default), f"{side}_expected"
        )
        if anchor < 0.0:
            raise ValueError("Expected points cannot be negative.")

        team = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)
        features: Dict[str, float] = {}

        ratio_features = {
            "attack_log": (team, "attack", side),
            "opponent_defense_log": (other, "defense", opponent),
            "exposure_log": (team, "exposure", side),
        }
        for name, (source, key, owner) in ratio_features.items():
            if name in self.coefficients:
                if key not in source:
                    raise ValueError(f"Missing fitted feature {owner}.{key}.")
                features[name] = math.log(
                    self._positive(source[key], f"{owner}.{key}")
                )

        if "rest_difference" in self.coefficients:
            rest = []
            for owner in (side, opponent):
                key = f"{owner}_rest_days"
                if key not in context:
                    raise ValueError(f"Missing fitted feature {key}.")
                days = self._number(context[key], key)
                if days < 0.0:
                    raise ValueError("Rest days cannot be negative.")
                rest.append(days)
            features["rest_difference"] = rest[0] - rest[1]

        if {"wind_speed", "temp"} & self.coefficients.keys():
            weather = self._mapping(context.get("weather", {}), "weather")
            for name in ("wind_speed", "temp"):
                if name not in self.coefficients:
                    continue
                if name == "wind_speed":
                    dome = weather.get("is_dome", False)
                    if not isinstance(dome, (bool, np.bool_)):
                        raise ValueError("weather.is_dome must be boolean.")
                    if dome:
                        features[name] = 0.0
                        continue
                if name not in weather:
                    raise ValueError(f"Missing fitted feature weather.{name}.")
                value = self._number(weather[name], f"weather.{name}")
                if name == "wind_speed" and value < 0.0:
                    raise ValueError("Wind speed cannot be negative.")
                features[name] = value

        log_effect = math.fsum(
            self.coefficients[name] * value for name, value in features.items()
        )
        pace = self._pace(context, side)
        if anchor == 0.0:
            return 0.0
        try:
            mean = math.exp(math.log(anchor) + math.log(pace) + log_effect)
        except OverflowError as exc:
            raise ValueError("Feature-adjusted expected points overflowed.") from exc
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError("Feature-adjusted expected points are unrepresentable.")
        return mean

    def _finite_pmf(self, distribution: Any) -> Tuple[np.ndarray, float]:
        # Divide the total joint error budget equally between the two teams.
        budget = self.tail_probability / 2.0
        quantile = float(distribution.isf(budget))
        if not math.isfinite(quantile):
            raise ArithmeticError("Unable to resolve the requested tail budget.")
        upper = max(0, int(math.ceil(quantile)))

        tail = float(distribution.sf(upper))
        while math.isfinite(tail) and tail > budget:
            upper += 1
            tail = float(distribution.sf(upper))
        if not math.isfinite(tail):
            raise ArithmeticError("Non-finite survival probability.")

        probabilities = np.asarray(
            distribution.pmf(np.arange(upper + 1)), dtype=float
        )
        return self._normalize(probabilities), tail

    @staticmethod
    def _normalize(probabilities: np.ndarray) -> np.ndarray:
        if (
            probabilities.ndim != 1
            or not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
        ):
            raise ArithmeticError("Invalid probability vector.")
        mass = float(np.sum(probabilities))
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Probability mass vanished numerically.")
        return probabilities / mass

    def _drive_outcomes(
        self, weights: Any, target: float
    ) -> Tuple[np.ndarray, float, float]:
        weights = self._mapping(weights, "outcome_weights")
        allowed_scores = {0, 2, 3, 6, 7, 8}
        parsed: Dict[int, float] = {}

        for score, weight in weights.items():
            numeric_score = self._number(score, "drive outcome")
            if not numeric_score.is_integer() or int(numeric_score) not in allowed_scores:
                raise ValueError("Drive outcomes must be in {0, 2, 3, 6, 7, 8}.")
            score = int(numeric_score)
            if score in parsed:
                raise ValueError("Duplicate drive outcome after numeric conversion.")
            weight = self._number(weight, f"outcome_weights[{score}]")
            if weight < 0.0:
                raise ValueError("Outcome weights cannot be negative.")
            parsed[score] = weight

        active = sorted(score for score, weight in parsed.items() if weight > 0.0)
        if not active:
            raise ValueError("At least one outcome weight must be positive.")

        scores = np.asarray(active, dtype=float)
        low, high = float(scores[0]), float(scores[-1])
        if not low <= target <= high:
            raise ValueError(
                "Expected points per drive lie outside the supplied outcome support."
            )

        if target == low or target == high:
            probabilities = (scores == target).astype(float)
        else:
            log_weights = np.log([parsed[int(score)] for score in scores])
            # Rescaling improves conditioning without changing the tilted family.
            coordinates = (scores - low) / (high - low)
            target_coordinate = (target - low) / (high - low)

            def tilted(parameter: float) -> np.ndarray:
                logits = log_weights + parameter * coordinates
                return np.exp(logits - logsumexp(logits))

            def residual(parameter: float) -> float:
                return float(np.dot(tilted(parameter), coordinates)) - target_coordinate

            left, right = -1.0, 1.0
            while residual(left) > 0.0:
                left *= 2.0
                if not math.isfinite(left):
                    raise ArithmeticError("Could not bracket outcome calibration.")
            while residual(right) < 0.0:
                right *= 2.0
                if not math.isfinite(right):
                    raise ArithmeticError("Could not bracket outcome calibration.")

            root = brentq(
                residual,
                left,
                right,
                xtol=np.finfo(float).eps,
                rtol=4.0 * np.finfo(float).eps,
            )
            probabilities = self._normalize(tilted(root))

        outcome_pmf = np.zeros(int(high) + 1, dtype=float)
        outcome_pmf[scores.astype(int)] = probabilities
        mean = float(np.dot(scores, probabilities))
        variance = float(np.dot((scores - mean) ** 2, probabilities))
        return outcome_pmf, mean, variance

    def _team_pmf(
        self, context: Mapping, side: str
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        mean = self._expected(context, side)
        drive_models = self._mapping(context.get("drive_model", {}), "drive_model")

        if side not in drive_models:
            if mean == 0.0:
                pmf, omitted = np.ones(1), 0.0
            else:
                pmf, omitted = self._finite_pmf(poisson(mu=mean))
            return pmf, {
                "mode": "cold_start_poisson_prior",
                "mean": mean,
                "variance": mean,
                "dispersion_index": 1.0 if mean > 0.0 else None,
                "omitted_mass_bound": omitted,
            }

        specification = self._mapping(drive_models[side], f"drive_model.{side}")
        for required in ("count_mean", "count_dispersion", "outcome_weights"):
            if required not in specification:
                raise ValueError(f"drive_model.{side}.{required} is required.")

        count_mean = self._positive(
            specification["count_mean"], f"{side}.count_mean"
        ) * self._pace(context, side)
        if not math.isfinite(count_mean):
            raise ValueError("Adjusted drive count mean overflowed.")

        count_dispersion = self._number(
            specification["count_dispersion"], f"{side}.count_dispersion"
        )
        if count_dispersion < 1.0:
            raise ValueError(
                "This count family requires count_dispersion >= 1; "
                "underdispersion requires a separately specified count model."
            )

        outcome, outcome_mean, outcome_variance = self._drive_outcomes(
            specification["outcome_weights"], mean / count_mean
        )

        if count_dispersion == 1.0:
            count_distribution = poisson(mu=count_mean)
        else:
            shape = count_mean / (count_dispersion - 1.0)
            success_probability = 1.0 / count_dispersion
            if not math.isfinite(shape) or shape <= 0.0:
                raise ValueError("Negative-binomial shape is unrepresentable.")
            count_distribution = nbinom(n=shape, p=success_probability)

        counts, omitted = self._finite_pmf(count_distribution)
        maximum_score = (len(counts) - 1) * (len(outcome) - 1)
        total = np.zeros(maximum_score + 1, dtype=float)
        convolution = np.ones(1, dtype=float)

        for count, probability in enumerate(counts):
            if probability > 0.0:
                total[: len(convolution)] += probability * convolution
            if count + 1 < len(counts):
                convolution = np.convolve(convolution, outcome)

        total = self._normalize(total)
        variance = count_mean * (
            outcome_variance + count_dispersion * outcome_mean ** 2
        )
        if not math.isfinite(variance):
            raise ArithmeticError("Compound-score variance overflowed.")
        return total, {
            "mode": "compound_drive",
            "mean": mean,
            "variance": variance,
            "dispersion_index": variance / mean if mean > 0.0 else None,
            "drive_count_mean": count_mean,
            "drive_count_variance": count_mean * count_dispersion,
            "points_per_drive": outcome_mean,
            "omitted_mass_bound": omitted,
        }

    def team_distributions(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        """Return marginal PMFs and auditable, untruncated theoretical moments."""
        context = self._mapping(game_context, "game_context")
        result: Dict[str, Any] = {}
        for side in ("home", "away"):
            pmf, diagnostics = self._team_pmf(context, side)
            diagnostics["represented_mean"] = float(
                np.dot(np.arange(len(pmf), dtype=float), pmf)
            )
            result[side] = {"pmf": pmf, "diagnostics": diagnostics}
        return result

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        marginals = self.team_distributions(game_context)
        home = marginals["home"]["pmf"]
        away = marginals["away"]["pmf"]
        home_support = np.flatnonzero(home)
        away_support = np.flatnonzero(away)
        cells = len(home_support) * len(away_support)

        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Score matrix requires {cells} cells, exceeding the configured "
                f"resource budget of {self.max_score_cells}."
            )

        scores = {
            (int(h), int(a)): float(home[h] * away[a])
            for h in home_support
            for a in away_support
            if home[h] * away[a] > 0.0
        }
        mass = math.fsum(scores.values())
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Joint score mass vanished numerically.")
        scores = {score: probability / mass for score, probability in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    def price_contract(
        self,
        game_context: Dict[str, Any],
        *,
        team: str,
        line: float,
        side: str,
        decimal_odds: float,
        tie_settlement: str,
        cost_per_unit_stake: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Price arbitrary real-valued lines without interpolating scoring mass.

        tie_settlement:
          "refund": equality returns stake;
          "lose": equality loses;
          "win": equality wins.

        For a binary YES contract on T > L, use side="over", ties="lose".
        Its complementary NO contract uses side="under", ties="win".
        Settlement must match the actual contract rules.

        Decimal odds are gross payout per unit stake. Costs include any
        execution fees in the same units. Eligibility implements the requested
        odds floor and positive expected profit without any additional filters.
        """
        if team not in ("home", "away"):
            raise ValueError("team must be 'home' or 'away'.")
        if side not in ("over", "under"):
            raise ValueError("side must be 'over' or 'under'.")
        if tie_settlement not in ("refund", "lose", "win"):
            raise ValueError("tie_settlement must be 'refund', 'lose', or 'win'.")

        line = self._number(line, "line")
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Gross decimal odds cannot be below 1.")
        cost = self._number(cost_per_unit_stake, "cost_per_unit_stake")
        if cost < 0.0:
            raise ValueError("Execution cost cannot be negative.")

        context = self._mapping(game_context, "game_context")
        pmf, diagnostics = self._team_pmf(context, team)
        support = np.arange(len(pmf))
        p_under = float(np.sum(pmf[support < line]))
        p_equal = float(np.sum(pmf[support == line]))
        p_over = float(np.sum(pmf[support > line]))

        p_win = p_over if side == "over" else p_under
        p_loss = p_under if side == "over" else p_over
        p_refund = 0.0
        if tie_settlement == "refund":
            p_refund = p_equal
        elif tie_settlement == "win":
            p_win += p_equal
        else:
            p_loss += p_equal

        expected_profit = p_win * (odds - 1.0) - p_loss - cost
        fair_odds = (
            (1.0 - p_refund + cost) / p_win if p_win > 0.0 else math.inf
        )
        error_bound = odds * diagnostics["omitted_mass_bound"]

        return {
            "team": team,
            "side": side,
            "line": line,
            "p_under": p_under,
            "p_equal": p_equal,
            "p_over": p_over,
            "p_win": p_win,
            "p_loss": p_loss,
            "p_refund": p_refund,
            "fair_decimal_odds_including_cost": fair_odds,
            "expected_profit_per_unit_stake": expected_profit,
            "numerical_expected_profit_error_bound": error_bound,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
            "diagnostics": diagnostics,
        }