from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import linprog, minimize
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class ATPFirstSetCandidateModel(BaseCandidateModel):
    """
    Legal-score, path-weighted exponential-family model for a completed ATP set.

    Assumptions
    -----------
    `period_1` denotes the first set, with a tiebreak at 6-6.
    Scores count games, not sets or points. Retirement settlement is external.

    Construction
    ------------
    First compute a reference distribution by enumerating legal game paths.
    Then find the smallest relative-entropy adjustment satisfying the supplied
    expected game counts:

        q(s) = r(s) exp(theta @ T(s)) / Z(theta).

    This preserves tennis's score lattice and incorporates service dynamics
    without consulting bookmaker prices. An independently estimated, feasible
    total-games variance can supply an additional sufficient statistic.

    Important identification limitation
    -----------------------------------
    The supplied dispersion index, 3.006, cannot be the variance/mean ratio
    of completed first-set total games at mean 11.07. Those totals lie in
    [6, 13], so variance cannot exceed:

        (13 - 11.07) * (11.07 - 6) = 9.7851.

    The implied supplied variance is 33.27642. It is therefore recorded as
    incompatible metadata, not silently fitted or replaced with a clamp.
    Without valid variance data, tails follow the legal-path reference and
    the expected-score constraints.

    Situational effects require externally fitted coefficients. Missing
    coefficients imply no estimated effect; no weather/rest effect is invented.
    Fit coefficients and variance forecasts using strictly preceding matches.
    The supplied summary statistics cannot establish out-of-sample profitability.

    Context
    -------
    home_expected, away_expected:
        Expected first-set games. Defaults are the supplied empirical means.
    home_game_win_on_serve, home_game_win_on_return:
        Optional game-level probabilities; supply both or neither.
    home_tiebreak_win_probability:
        Optional independent tiebreak forecast.
    home_serves_first_probability:
        Optional probability; defaults to symmetry.
    total_games_variance:
        Optional independently estimated variance, checked for feasibility.
    home, away:
        Optional mappings with positive attack / defense indices.
        Larger defense values mean stronger defense.
    home_rest_days, away_rest_days, tempo, pace, weather, venue:
        Optional situational features, used only through fitted coefficients.

    Constructor coefficients
    ------------------------
    feature_coefficients is a mapping:
        {"home": {feature: coefficient}, "away": {feature: coefficient}}

    Each coefficient changes log expected games. Available features:
        log_home_attack, log_home_defense,
        log_away_attack, log_away_defense,
        log1p_home_rest_days, log1p_away_rest_days,
        log_tempo, log_pace, wind_speed, temp, is_dome,
        venue:<exact venue string>.

    An active coefficient requires its corresponding context input.
    """

    EMPIRICAL_HOME_MEAN = 5.76
    EMPIRICAL_AWAY_MEAN = 5.31
    REPORTED_DISPERSION_INDEX = 3.006

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1107_000005",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "tennis_sets",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
    ) -> None:
        if (league, scope, family) != ("ATP", "period_1", "tennis_sets"):
            raise ValueError("This candidate supports ATP|period_1|tennis_sets only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.feature_coefficients: Dict[str, Dict[str, float]] = {
            "home": {},
            "away": {},
        }
        for side, coefficients in (feature_coefficients or {}).items():
            if side not in self.feature_coefficients:
                raise ValueError(f"Unknown coefficient side: {side!r}")
            self.feature_coefficients[side] = {
                str(name): self._finite(value, f"{side}.{name}")
                for name, value in coefficients.items()
            }

        self._scores = tuple(
            sorted(
                [(6, loser) for loser in range(5)]
                + [(loser, 6) for loser in range(5)]
                + [(7, 5), (5, 7), (7, 6), (6, 7)]
            )
        )
        self._score_array = np.asarray(self._scores, dtype=float)
        self._totals = self._score_array.sum(axis=1)
        self._tolerance = float(np.sqrt(np.finfo(float).eps))

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be a finite number, not a boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    def _feature(self, name: str, context: Mapping[str, Any]) -> float:
        player_features = {
            "log_home_attack": ("home", "attack"),
            "log_home_defense": ("home", "defense"),
            "log_away_attack": ("away", "attack"),
            "log_away_defense": ("away", "defense"),
        }
        if name in player_features:
            side, attribute = player_features[name]
            player = context.get(side)
            if not isinstance(player, Mapping) or attribute not in player:
                raise ValueError(f"Active feature requires {side}.{attribute}.")
            value = self._finite(player[attribute], name)
            if value <= 0.0:
                raise ValueError(f"{name} requires a positive index.")
            return math.log(value)

        rest_features = {
            "log1p_home_rest_days": "home_rest_days",
            "log1p_away_rest_days": "away_rest_days",
        }
        if name in rest_features:
            key = rest_features[name]
            if key not in context:
                raise ValueError(f"Active feature requires {key}.")
            value = self._finite(context[key], key)
            if value < 0.0:
                raise ValueError(f"{key} cannot be negative.")
            return math.log1p(value)

        if name in ("log_tempo", "log_pace"):
            key = name.removeprefix("log_")
            if key not in context:
                raise ValueError(f"Active feature requires {key}.")
            value = self._finite(context[key], key)
            if value <= 0.0:
                raise ValueError(f"{key} must be positive.")
            return math.log(value)

        if name in ("wind_speed", "temp", "is_dome"):
            weather = context.get("weather")
            if not isinstance(weather, Mapping) or name not in weather:
                raise ValueError(f"Active feature requires weather.{name}.")
            if name == "is_dome":
                value = weather[name]
                if not isinstance(value, (bool, np.bool_)):
                    raise ValueError("weather.is_dome must be boolean.")
                return float(value)
            value = self._finite(weather[name], name)
            if name == "wind_speed" and value < 0.0:
                raise ValueError("wind_speed cannot be negative.")
            return value

        if name.startswith("venue:"):
            if not isinstance(context.get("venue"), str):
                raise ValueError("An active venue feature requires a venue string.")
            return float(context["venue"] == name.partition(":")[2])

        raise ValueError(f"Unsupported fitted feature: {name!r}")

    def _expected_games(self, context: Mapping[str, Any]) -> np.ndarray:
        values = []
        for side, baseline in (
            ("home", self.EMPIRICAL_HOME_MEAN),
            ("away", self.EMPIRICAL_AWAY_MEAN),
        ):
            mean = self._finite(context.get(f"{side}_expected", baseline), side)
            if mean < 0.0:
                raise ValueError(f"{side}_expected cannot be negative.")

            adjustment = math.fsum(
                coefficient * self._feature(name, context)
                for name, coefficient in self.feature_coefficients[side].items()
                if coefficient != 0.0
            )
            try:
                adjusted = mean * math.exp(adjustment)
            except OverflowError as exc:
                raise ValueError("Situational expected-games forecast overflowed.") from exc
            if not math.isfinite(adjusted):
                raise ValueError("Situational expected-games forecast is not finite.")
            values.append(adjusted)
        return np.asarray(values)

    def _reference(self, context: Mapping[str, Any]) -> np.ndarray:
        serve_key = "home_game_win_on_serve"
        return_key = "home_game_win_on_return"
        if (serve_key in context) != (return_key in context):
            raise ValueError("Supply both serve and return game probabilities.")

        # One half follows exchange symmetry, rather than a home-court advantage.
        symmetric_probability = 1.0 / 2.0
        serve = self._probability(
            context.get(serve_key, symmetric_probability), serve_key
        )
        returning = self._probability(
            context.get(return_key, symmetric_probability), return_key
        )
        tiebreak = self._probability(
            context.get("home_tiebreak_win_probability", symmetric_probability),
            "home_tiebreak_win_probability",
        )
        first = self._probability(
            context.get("home_serves_first_probability", symmetric_probability),
            "home_serves_first_probability",
        )

        terminal = {score: 0.0 for score in self._scores}
        for home_first, first_weight in ((True, first), (False, 1.0 - first)):
            states: Dict[Tuple[int, int], float] = {(0, 0): first_weight}
            while states:
                following: Dict[Tuple[int, int], float] = {}
                for (home, away), mass in states.items():
                    if mass == 0.0:
                        continue
                    if (home, away) in terminal:
                        terminal[(home, away)] += mass
                        continue
                    if home == away == 6:
                        terminal[(7, 6)] += mass * tiebreak
                        terminal[(6, 7)] += mass * (1.0 - tiebreak)
                        continue

                    home_serving = ((home + away) % 2 == 0) == home_first
                    win_probability = serve if home_serving else returning
                    for score, probability in (
                        ((home + 1, away), win_probability),
                        ((home, away + 1), 1.0 - win_probability),
                    ):
                        following[score] = (
                            following.get(score, 0.0) + mass * probability
                        )
                states = following

        reference = np.asarray([terminal[score] for score in self._scores])
        return reference / reference.sum()

    def _project(
        self,
        reference: np.ndarray,
        statistics: np.ndarray,
        target: np.ndarray,
    ) -> np.ndarray:
        """Relative-entropy projection, including targets on support boundaries."""
        original_indices = np.flatnonzero(reference > 0.0)
        supported_statistics = statistics[original_indices]
        constraints = np.vstack(
            (np.ones(len(original_indices)), supported_statistics.T)
        )
        rhs = np.concatenate(([1.0], target))

        feasible = linprog(
            np.zeros(len(original_indices)),
            A_eq=constraints,
            b_eq=rhs,
            bounds=(0.0, None),
            method="highs",
        )
        if not feasible.success:
            raise ValueError(
                "Expected games and/or variance are infeasible on the legal "
                "first-set support permitted by the supplied service probabilities."
            )

        # Determine the minimal feasible face. This avoids infinite tilt
        # parameters when the requested moments lie on a support boundary.
        active = []
        for index in range(len(original_indices)):
            objective = np.zeros(len(original_indices))
            objective[index] = -1.0
            solution = linprog(
                objective,
                A_eq=constraints,
                b_eq=rhs,
                bounds=(0.0, None),
                method="highs",
            )
            if not solution.success:
                raise RuntimeError("Unable to identify the feasible score face.")
            if solution.x[index] > self._tolerance:
                active.append(index)

        if not active:
            raise RuntimeError("Moment constraints have no numerically resolved support.")

        indices = original_indices[np.asarray(active)]
        selected = statistics[indices]
        centered = selected - selected.mean(axis=0)
        _, singular_values, directions = np.linalg.svd(centered, full_matrices=False)
        rank_threshold = (
            np.finfo(float).eps
            * max(centered.shape)
            * (singular_values[0] if singular_values.size else 0.0)
        )
        rank = int(np.count_nonzero(singular_values > rank_threshold))

        if rank == 0:
            probabilities = reference[indices] / reference[indices].sum()
        else:
            basis = directions[:rank].T / singular_values[:rank]
            features = (selected - target) @ basis
            log_reference = np.log(reference[indices])

            def objective(theta: np.ndarray) -> Tuple[float, np.ndarray]:
                logits = log_reference + features @ theta
                normalizer = logsumexp(logits)
                weights = np.exp(logits - normalizer)
                return float(normalizer), features.T @ weights

            solution = minimize(
                objective,
                np.zeros(rank),
                jac=True,
                method="BFGS",
                options={"gtol": self._tolerance},
            )
            logits = log_reference + features @ solution.x
            probabilities = np.exp(logits - logsumexp(logits))

        result = np.zeros(len(reference))
        result[indices] = probabilities
        residual = np.abs(result @ statistics - target)
        scale = np.maximum(1.0, np.abs(target))
        if (
            not np.all(np.isfinite(result))
            or np.any(result < 0.0)
            or np.any(residual > self._tolerance * scale)
        ):
            raise RuntimeError("Score calibration failed its moment-residual check.")
        return result / result.sum()

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "ATP"),
            ("scope", "period_1"),
            ("family", "tennis_sets"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Context {key} must be {expected!r}.")

        means = self._expected_games(game_context)
        reference = self._reference(game_context)
        statistics = self._score_array
        target = means

        if "total_games_variance" in game_context:
            variance = self._finite(
                game_context["total_games_variance"], "total_games_variance"
            )
            if variance < 0.0:
                raise ValueError("total_games_variance cannot be negative.")
            statistics = np.column_stack((statistics, self._totals**2))
            target = np.concatenate((means, [variance + means.sum() ** 2]))

        probabilities = self._project(reference, statistics, target)
        return {
            score: float(probability)
            for score, probability in zip(self._scores, probabilities)
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )

    def diagnostics(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        scores = self.score_probabilities(game_context)
        probabilities = np.asarray([scores[score] for score in self._scores])
        means = probabilities @ self._score_array
        total_mean = float(means.sum())
        variance = float(
            probabilities @ ((self._totals - total_mean) ** 2)
        )
        support_variance_bound = float(
            (self._totals.max() - total_mean)
            * (total_mean - self._totals.min())
        )
        reported_implied_variance = self.REPORTED_DISPERSION_INDEX * total_mean

        return {
            "home_expected_games": float(means[0]),
            "away_expected_games": float(means[1]),
            "total_expected_games": total_mean,
            "total_games_variance": variance,
            "total_games_dispersion_index": variance / total_mean,
            "reported_dispersion_index": self.REPORTED_DISPERSION_INDEX,
            "reported_implied_total_variance": reported_implied_variance,
            "support_variance_upper_bound": support_variance_bound,
            "reported_index_exceeds_support_bound": (
                reported_implied_variance > support_variance_bound
            ),
            "tail_calibration": (
                "independent_variance_constraint"
                if "total_games_variance" in game_context
                else "legal_path_reference_with_expected_score_constraints"
            ),
            "bookmaker_inputs_used": False,
            "out_of_sample_performance_established": False,
        }