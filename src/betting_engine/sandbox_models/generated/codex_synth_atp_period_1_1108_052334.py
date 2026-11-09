from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import ConvexHull
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The candidate execution environment may inject this primitive.
    if "DiscreteDistribution" not in globals():
        raise ImportError(
            "DiscreteDistribution must be exported by the base module "
            "or injected into the candidate execution namespace."
        )


class ATPFirstSetDoubleChanceModel(BaseCandidateModel):
    """
    Completed, standard ATP first-set outcome model.

    Legal terminal scores are (6, 0..4), (7, 5), (7, 6), and reversals.
    A draw is impossible. Consequently, 1X = home, X2 = away, and 12 = 1.

    Identification:
        Two expected game counts do not uniquely identify two serve strengths.
        In particular, averaging the initial server introduces serve/return
        symmetries. This model does not silently resolve that ambiguity with
        an invented serve-strength prior.

    Construction:
        Build a reference terminal-score law from alternating service games
        and a point-level seven-point, win-by-two tiebreak. Explicit serve-point
        probabilities can inform that law. Otherwise, player-exchange symmetry
        supplies the neutral reference.

        For requested means mu, find the unique minimum-relative-entropy law:

            p(s) = r(s) exp(theta . s) / Z(theta)
            E_p[s] = mu.

        This is a moment-constrained score model, not a claim that the fitted
        terminal law identifies constant serve-hold probabilities. Its variance
        and covariance follow from legal score support; no Poisson assumption,
        bookmaker probability, dispersion clamp, or home advantage is imposed.

    Context:
        home_expected, away_expected:
            Expected completed-first-set game counts. Defaults are the supplied
            empirical means, 5.76 and 5.31.

        home_serve_point_win, away_serve_point_win:
            Optional paired probabilities strictly between zero and one.

        first_server:
            "home", "away", or "unknown". Unknown averages both service orders.

        first_set_tiebreak_points:
            Optional; only the standard seven-point format is supported.

        league, scope, family:
            Optional segment assertions.

    Feature effects:
        feature_coefficients maps "home"/"away" to {dotted_context_path: beta}.
        Applied as mu_i *= exp(sum(beta * feature)). Coefficients must be learned
        externally using training-only data. No effects for rest, attack,
        defense, weather, or pace are fabricated from the two supplied means.
        Inputs must be centered/transformed as expected by those coefficients.
        Do not supply effects already incorporated into *_expected.

    Infeasible moments raise ValueError instead of being silently clipped.
    Exact feasible boundary moments are handled on their supporting face.
    Probabilities are conditional on completion and ordinary set settlement;
    retirement, void, and nonstandard bookmaker settlement require another layer.
    Historical ROI targets cannot be established from a zero-wager sample.
    """

    BASELINE_HOME = 5.76
    BASELINE_AWAY = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1108_052334",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "double_chance",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
    ) -> None:
        if (league, scope, family) != ("ATP", "period_1", "double_chance"):
            raise ValueError("This model supports only ATP|period_1|double_chance.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        supplied = feature_coefficients or {}
        if set(supplied) - {"home", "away"}:
            raise ValueError("Feature coefficient groups must be home or away.")

        self._coefficients: Dict[str, Dict[str, float]] = {}
        for side in ("home", "away"):
            self._coefficients[side] = {}
            for path, coefficient in supplied.get(side, {}).items():
                if not isinstance(path, str) or not path:
                    raise ValueError("Feature paths must be nonempty strings.")
                self._coefficients[side][path] = self._finite(
                    coefficient, f"{side} coefficient {path}"
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

    @staticmethod
    def _lookup(context: Mapping[str, Any], path: str) -> Any:
        if path in context:
            return context[path]
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing configured feature: {path}")
            value = value[component]
        return value

    def _target(self, context: Mapping[str, Any]) -> np.ndarray:
        means = []
        for side, baseline in (
            ("home", self.BASELINE_HOME),
            ("away", self.BASELINE_AWAY),
        ):
            mean = self._finite(
                context.get(f"{side}_expected", baseline),
                f"{side}_expected",
            )
            if mean < 0.0:
                raise ValueError("Expected game counts cannot be negative.")

            terms = [
                coefficient
                * self._finite(self._lookup(context, path), path)
                for path, coefficient in self._coefficients[side].items()
            ]
            try:
                effect = math.fsum(terms)
                adjusted = mean * math.exp(effect)
            except (OverflowError, ValueError) as exc:
                raise ValueError("Feature effects exceed numerical range.") from exc

            if not math.isfinite(adjusted):
                raise ValueError("Adjusted game expectation must be finite.")
            means.append(adjusted)
        return np.asarray(means, dtype=float)

    @staticmethod
    def _hold_probability(point_win: float) -> float:
        """Exact advantage-game probability, including the infinite deuce tail."""
        q = point_win
        r = 1.0 - q
        before_deuce = math.fsum(
            math.comb(3 + losses, losses) * q**4 * r**losses
            for losses in range(3)
        )
        reach_deuce = math.comb(6, 3) * q**3 * r**3
        win_from_deuce = q * q / (q * q + r * r)
        return before_deuce + reach_deuce * win_from_deuce

    @staticmethod
    def _tiebreak_home_probability(
        home_point: float, away_point: float, first_server: int
    ) -> float:
        # Server IDs: home=0, away=1. The order is A, BB, AA, BB, ...
        home_on_home_serve = home_point
        home_on_away_serve = 1.0 - away_point

        # At 6-6 and subsequent tied scores, each two-point block contains
        # one service point by each player. Split blocks renew the tie.
        win_pair = home_on_home_serve * home_on_away_serve
        lose_pair = (1.0 - home_on_home_serve) * (
            1.0 - home_on_away_serve
        )
        tie_win = win_pair / (win_pair + lose_pair)

        @lru_cache(maxsize=None)
        def recurse(home: int, away: int) -> float:
            if max(home, away) >= 7 and abs(home - away) >= 2:
                return float(home > away)
            if home == away == 6:
                return tie_win

            point_index = home + away
            server = (
                first_server
                if point_index % 4 in (0, 3)
                else 1 - first_server
            )
            p = home_on_home_serve if server == 0 else home_on_away_serve
            return (
                p * recurse(home + 1, away)
                + (1.0 - p) * recurse(home, away + 1)
            )

        return recurse(0, 0)

    @staticmethod
    @lru_cache(maxsize=256)
    def _reference_scores(
        home_point: float,
        away_point: float,
        first_server: str,
    ) -> Tuple[Tuple[Tuple[int, int], float], ...]:
        holds = (
            ATPFirstSetDoubleChanceModel._hold_probability(home_point),
            ATPFirstSetDoubleChanceModel._hold_probability(away_point),
        )
        starts = (
            (0, 1)
            if first_server == "unknown"
            else (0 if first_server == "home" else 1,)
        )
        terminal: Dict[Tuple[int, int], float] = {}

        def add(score: Tuple[int, int], mass: float) -> None:
            terminal[score] = terminal.get(score, 0.0) + mass

        for initial_server in starts:
            active = {(0, 0): 1.0 / len(starts)}
            tie_home = ATPFirstSetDoubleChanceModel._tiebreak_home_probability(
                home_point, away_point, initial_server
            )
            while active:
                following: Dict[Tuple[int, int], float] = {}
                for (home, away), mass in active.items():
                    if max(home, away) >= 6 and abs(home - away) >= 2:
                        add((home, away), mass)
                        continue
                    if home == away == 6:
                        add((7, 6), mass * tie_home)
                        add((6, 7), mass * (1.0 - tie_home))
                        continue

                    server = (initial_server + home + away) % 2
                    p_home = holds[0] if server == 0 else 1.0 - holds[1]
                    for score, probability in (
                        ((home + 1, away), p_home),
                        ((home, away + 1), 1.0 - p_home),
                    ):
                        following[score] = (
                            following.get(score, 0.0) + mass * probability
                        )
                active = following

        result = tuple(sorted(terminal.items()))
        if any(not math.isfinite(p) or p <= 0.0 for _, p in result):
            raise ValueError(
                "Serve probabilities are too extreme for reliable floating-point "
                "score enumeration."
            )
        return result

    @staticmethod
    def _fit_moments(
        scores: np.ndarray, reference: np.ndarray, target: np.ndarray
    ) -> np.ndarray:
        eps = np.finfo(float).eps
        scale = max(1.0, float(np.max(np.abs(scores))), float(np.max(abs(target))))
        geometry_tolerance = eps * scores.size * scale
        moment_tolerance = math.sqrt(eps) * scale

        hull = ConvexHull(scores)
        normals = hull.equations[:, :-1]
        offsets = hull.equations[:, -1]
        distances = normals @ target + offsets

        if np.any(distances > geometry_tolerance):
            raise ValueError(
                f"Expected games {target.tolist()} are outside the convex hull "
                "of legal completed first-set scores."
            )

        # Boundary targets admit mass only on their supporting face.
        active_faces = np.abs(distances) <= geometry_tolerance
        allowed = np.ones(len(scores), dtype=bool)
        if np.any(active_faces):
            face_distances = (
                scores @ normals[active_faces].T + offsets[active_faces]
            )
            allowed = np.all(
                np.abs(face_distances) <= geometry_tolerance, axis=1
            )
        if not np.any(allowed):
            raise ArithmeticError("Failed to identify the target's supporting face.")

        support = scores[allowed]
        prior = reference[allowed]
        origin = support[0]
        centered = support - origin
        _, singular_values, right_vectors = np.linalg.svd(
            centered, full_matrices=False
        )
        rank_tolerance = (
            eps * max(centered.shape) * singular_values[0]
            if singular_values.size and singular_values[0] > 0.0
            else 0.0
        )
        rank = int(np.sum(singular_values > rank_tolerance))

        if rank == 0:
            fitted = prior / math.fsum(prior.tolist())
        else:
            basis = right_vectors[:rank].T
            coordinates = centered @ basis
            desired = (target - origin) @ basis
            log_prior = np.log(prior)

            def probabilities(theta: np.ndarray) -> np.ndarray:
                logits = log_prior + coordinates @ theta
                return np.exp(logits - logsumexp(logits))

            def residual(theta: np.ndarray) -> np.ndarray:
                return probabilities(theta) @ coordinates - desired

            def jacobian(theta: np.ndarray) -> np.ndarray:
                probabilities_at_theta = probabilities(theta)
                mean = probabilities_at_theta @ coordinates
                deviations = coordinates - mean
                return (
                    deviations.T * probabilities_at_theta
                ) @ deviations

            fit = least_squares(
                residual,
                np.zeros(rank),
                jac=jacobian,
                ftol=eps ** 0.75,
                xtol=eps ** 0.75,
                gtol=eps ** 0.75,
            )
            fitted = probabilities(fit.x)

        result = np.zeros(len(scores), dtype=float)
        result[allowed] = fitted
        result /= math.fsum(result.tolist())

        if (
            not np.all(np.isfinite(result))
            or np.any(result < 0.0)
            or np.max(np.abs(result @ scores - target)) > moment_tolerance
        ):
            raise ArithmeticError(
                "Moment calibration did not meet numerical accuracy requirements."
            )
        return result

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        """Return the calibrated joint law for completed first-set game counts."""
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, required in (
            ("league", "ATP"),
            ("scope", "period_1"),
            ("family", "double_chance"),
        ):
            if key in game_context and game_context[key] != required:
                raise ValueError(f"{key} must be {required!r}.")

        if game_context.get("first_set_tiebreak_points", 7) != 7:
            raise ValueError("Only a standard seven-point first-set tiebreak is supported.")

        first_server = game_context.get("first_server", "unknown")
        if first_server not in ("home", "away", "unknown"):
            raise ValueError("first_server must be home, away, or unknown.")

        point_keys = ("home_serve_point_win", "away_serve_point_win")
        supplied = tuple(key in game_context for key in point_keys)
        if any(supplied) and not all(supplied):
            raise ValueError("Supply both players' serve-point probabilities.")

        if all(supplied):
            point_probabilities = tuple(
                self._finite(game_context[key], key) for key in point_keys
            )
            if any(not 0.0 < p < 1.0 for p in point_probabilities):
                raise ValueError("Serve-point probabilities must lie strictly in (0, 1).")
        else:
            # Symmetry under exchange of point winner, not an estimated ATP rate.
            point_probabilities = (1.0 / 2.0, 1.0 / 2.0)

        reference_items = self._reference_scores(
            point_probabilities[0], point_probabilities[1], first_server
        )
        scores = np.asarray([score for score, _ in reference_items], dtype=float)
        reference = np.asarray([p for _, p in reference_items], dtype=float)
        probabilities = self._fit_moments(
            scores, reference, self._target(game_context)
        )
        return {
            score: float(probability)
            for (score, _), probability in zip(reference_items, probabilities)
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        score_law = self.score_probabilities(game_context)
        home = math.fsum(
            mass for (home_games, away_games), mass in score_law.items()
            if home_games > away_games
        )
        away = math.fsum(
            mass for (home_games, away_games), mass in score_law.items()
            if away_games > home_games
        )
        total = home + away
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Invalid outcome probability normalization.")

        # Return mutually exclusive outcomes. The engine forms overlapping
        # double-chance contracts by summing their constituent outcomes.
        return DiscreteDistribution(
            probabilities={
                "home": home / total,
                "draw": 0.0,
                "away": away / total,
            }
        )