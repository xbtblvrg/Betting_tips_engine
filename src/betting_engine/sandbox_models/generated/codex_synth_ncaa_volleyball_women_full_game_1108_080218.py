from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.special import expit, logit, roots_jacobi

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # Some sandbox runners inject this type into the execution namespace.
    if "DiscreteDistribution" not in globals():
        raise


class NCAAWomenVolleyballMoneylineModel(BaseCandidateModel):
    """
    Serving-state rally model with exact win-by-two set absorption.

    Calibration contract
    --------------------
    calibration may contain:
      home_sideout, away_sideout:
          Baseline receiving-team rally-win probabilities.
      home_coefficients, away_coefficients:
          Fitted logit coefficients keyed by FEATURE_NAMES.
      home_advantage:
          Fitted, preferably hierarchically shrunk, home-court logit effect.
          Added to home sideout and subtracted from away sideout.
      home_sideout_concentration, away_sideout_concentration:
          Optional Beta concentrations fitted from volleyball rally/set data.
          Omit to use fixed sideout probabilities.
      variation_scope:
          "match": latent sideout probabilities persist throughout the match.
          "set": latent probabilities are independently redrawn each set.

    game_context may override sideout probabilities and concentrations using
    the same keys. Direct sideout probabilities are treated as fully adjusted
    predictions; context coefficients are not applied to those overrides.

    Optional game_context["first_server_scenarios"] is a sequence of mappings:
      {"weight": positive_number,
       "home_serve_probabilities": five probabilities}
    Scenarios can encode correlated opening-server assignments across sets.
    Without this information, opening server is marginalized symmetrically
    and independently for each set.

    The supplied 1.60/1.20 anchors define centered covariates, not volleyball
    point intensities. They cannot identify sideout probabilities. Without
    calibration or direct sideout forecasts, the model deliberately returns
    a neutral forecast. Zero settled bets do not establish a calibration
    defect, profitable edge, or a volleyball dispersion parameter.

    Beta concentration must come from observed volleyball variation or a
    fitted hierarchical model. A Poisson variance/mean index is not used.
    Integration error is checked by increasing quadrature order; failure
    raises rather than silently returning an unconverged prediction.

    No bookmaker prices enter the forecast. The model is deterministic,
    performs no I/O, and assigns zero probability to a completed-match draw.
    """

    HOME_EXPECTED_REFERENCE = 1.60
    AWAY_EXPECTED_REFERENCE = 1.20

    FEATURE_NAMES = frozenset(
        {
            "home_expected_log_ratio",
            "away_expected_log_ratio",
            "expected_log_contrast",
            "rest_log_contrast",
            "home_attack",
            "home_defense",
            "away_attack",
            "away_defense",
            "home_attack_exposure",
            "home_defense_exposure",
            "away_attack_exposure",
            "away_defense_exposure",
            "tempo",
            "pace",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_volleyball_women_full_game_1108_080218",
        version: str = "1.0.0",
        league: str = "NCAA_VOLLEYBALL_WOMEN",
        scope: str = "full_game",
        family: str = "moneyline",
        *,
        calibration: Optional[Mapping[str, Any]] = None,
        integration_tolerance: float = 1e-8,
        initial_quadrature_order: int = 8,
        maximum_quadrature_order: int = 128,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != (
            "NCAA_VOLLEYBALL_WOMEN",
            "full_game",
            "moneyline",
        ):
            raise ValueError("This model supports only its declared segment.")

        self.integration_tolerance = self._positive(
            integration_tolerance, "integration_tolerance"
        )
        for name, value in (
            ("initial_quadrature_order", initial_quadrature_order),
            ("maximum_quadrature_order", maximum_quadrature_order),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer of at least two.")
        if maximum_quadrature_order < 2 * initial_quadrature_order:
            raise ValueError("Maximum order must allow a convergence comparison.")

        self.initial_quadrature_order = initial_quadrature_order
        self.maximum_quadrature_order = maximum_quadrature_order

        config = dict(calibration or {})
        self._home_baseline = self._probability(
            config.get("home_sideout", 0.5), "home_sideout", interior=True
        )
        self._away_baseline = self._probability(
            config.get("away_sideout", 0.5), "away_sideout", interior=True
        )
        self._home_advantage = self._finite(
            config.get("home_advantage", 0.0), "home_advantage"
        )
        self._home_coefficients = self._coefficients(
            config.get("home_coefficients", {})
        )
        self._away_coefficients = self._coefficients(
            config.get("away_coefficients", {})
        )
        self._home_concentration = self._concentration(
            config.get("home_sideout_concentration"),
            "home_sideout_concentration",
        )
        self._away_concentration = self._concentration(
            config.get("away_sideout_concentration"),
            "away_sideout_concentration",
        )
        self._variation_scope = config.get("variation_scope", "match")
        if self._variation_scope not in {"match", "set"}:
            raise ValueError("variation_scope must be 'match' or 'set'.")

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
            raise ValueError(f"{name} must belong to {interval}.")
        return result

    @classmethod
    def _concentration(cls, value: Any, name: str) -> Optional[float]:
        return None if value is None else cls._positive(value, name)

    @classmethod
    def _coefficients(cls, values: Mapping[str, Any]) -> Dict[str, float]:
        if not isinstance(values, Mapping):
            raise TypeError("Coefficients must be a mapping.")
        unknown = set(values).difference(cls.FEATURE_NAMES)
        if unknown:
            raise ValueError(f"Unsupported coefficient features: {sorted(unknown)}")
        return {name: cls._finite(value, name) for name, value in values.items()}

    @classmethod
    def _feature(cls, context: Mapping[str, Any], name: str) -> float:
        if name in {
            "home_expected_log_ratio",
            "away_expected_log_ratio",
            "expected_log_contrast",
        }:
            home = cls._positive(
                context.get("home_expected", cls.HOME_EXPECTED_REFERENCE),
                "home_expected",
            )
            away = cls._positive(
                context.get("away_expected", cls.AWAY_EXPECTED_REFERENCE),
                "away_expected",
            )
            home_log = math.log(home) - math.log(cls.HOME_EXPECTED_REFERENCE)
            away_log = math.log(away) - math.log(cls.AWAY_EXPECTED_REFERENCE)
            return {
                "home_expected_log_ratio": home_log,
                "away_expected_log_ratio": away_log,
                "expected_log_contrast": home_log - away_log,
            }[name]

        if name == "rest_log_contrast":
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("Fitted rest effects require both rest-day values.")
            home = cls._finite(context["home_rest_days"], "home_rest_days")
            away = cls._finite(context["away_rest_days"], "away_rest_days")
            if home < 0.0 or away < 0.0:
                raise ValueError("Rest days cannot be negative.")
            return math.log1p(home) - math.log1p(away)

        if name in context:
            return cls._finite(context[name], name)

        for side in ("home", "away"):
            prefix = side + "_"
            if name.startswith(prefix):
                nested = context.get(side)
                key = name[len(prefix):]
                if isinstance(nested, Mapping) and key in nested:
                    return cls._finite(nested[key], name)

        raise ValueError(f"Missing feature required by fitted calibration: {name}")

    def _sideout(self, context: Mapping[str, Any], side: str) -> float:
        key = side + "_sideout"
        if key in context:
            return self._probability(context[key], key, interior=True)

        home = side == "home"
        baseline = self._home_baseline if home else self._away_baseline
        coefficients = self._home_coefficients if home else self._away_coefficients
        advantage = self._home_advantage if home else -self._home_advantage
        linear_predictor = math.fsum(
            [float(logit(baseline)), advantage]
            + [
                coefficient * self._feature(context, name)
                for name, coefficient in coefficients.items()
                if coefficient != 0.0
            ]
        )
        result = float(expit(linear_predictor))
        # Reject floating-point saturation instead of imposing a probability cap.
        return self._probability(result, key, interior=True)

    @staticmethod
    def _deuce_probabilities(home_sideout: float, away_sideout: float) -> np.ndarray:
        """
        Exact absorption probabilities at a tied deuce score, by current server.

        The scalar p²/(p²+q²) formula applies only when rally-win probability
        is independent of the serving state. This solves the two-state chain.
        """
        x, y = home_sideout, away_sideout
        u, v = 1.0 - y, 1.0 - x

        home_absorption = u * u + y * v
        away_absorption = x * u + v * v
        transition_h_to_a = u * y
        transition_a_to_h = v * x

        denominator = math.fsum(
            (
                home_absorption * away_absorption,
                home_absorption * transition_a_to_h,
                transition_h_to_a * away_absorption,
            )
        )
        if denominator <= 0.0 or not math.isfinite(denominator):
            raise ArithmeticError("Deuce absorption is numerically unresolved.")

        home_server_win = (
            u * u * (away_absorption + transition_a_to_h)
            + transition_h_to_a * x * u
        ) / denominator
        away_server_win = (
            (home_absorption + transition_h_to_a) * x * u
            + transition_a_to_h * u * u
        ) / denominator
        return np.array([home_server_win, away_server_win], dtype=float)

    @staticmethod
    @lru_cache(maxsize=8192)
    def _set_probabilities(
        target: int, home_sideout: float, away_sideout: float
    ) -> Tuple[float, float]:
        """Return home set-win probabilities for home/away opening server."""
        deuce = NCAAWomenVolleyballMoneylineModel._deuce_probabilities(
            home_sideout, away_sideout
        )
        values = np.zeros((target + 1, target + 1, 2), dtype=float)
        values[target, :target, :] = 1.0

        p_home_on_home_serve = 1.0 - away_sideout
        p_home_on_away_serve = home_sideout

        for home_score in range(target - 1, -1, -1):
            for away_score in range(target - 1, -1, -1):
                if home_score == away_score == target - 1:
                    values[home_score, away_score] = deuce
                    continue

                # The rally winner serves next.
                after_home_win = values[home_score + 1, away_score, 0]
                after_away_win = values[home_score, away_score + 1, 1]
                values[home_score, away_score, 0] = (
                    p_home_on_home_serve * after_home_win
                    + away_sideout * after_away_win
                )
                values[home_score, away_score, 1] = (
                    p_home_on_away_serve * after_home_win
                    + (1.0 - home_sideout) * after_away_win
                )

        return float(values[0, 0, 0]), float(values[0, 0, 1])

    @staticmethod
    def _match_probability(set_probabilities: Sequence[float]) -> float:
        """Absorbing best-of-five recursion, retaining a distinct fifth set."""
        active = {(0, 0): 1.0}
        home_wins = []
        for probability in set_probabilities:
            following: Dict[Tuple[int, int], float] = {}
            for (home_sets, away_sets), mass in active.items():
                home_mass = mass * probability
                away_mass = mass * (1.0 - probability)

                if home_sets + 1 == 3:
                    home_wins.append(home_mass)
                else:
                    state = home_sets + 1, away_sets
                    following[state] = following.get(state, 0.0) + home_mass

                if away_sets + 1 < 3:
                    state = home_sets, away_sets + 1
                    following[state] = following.get(state, 0.0) + away_mass
            active = following
        if active:
            raise ArithmeticError("Best-of-five recursion did not absorb.")
        return math.fsum(home_wins)

    @classmethod
    def _server_scenarios(
        cls, context: Mapping[str, Any]
    ) -> Tuple[Tuple[float, Tuple[float, ...]], ...]:
        raw = context.get("first_server_scenarios")
        if raw is None:
            return ((1.0, (0.5,) * 5),)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
            raise ValueError("first_server_scenarios must be a nonempty sequence.")

        scenarios = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("Each first-server scenario must be a mapping.")
            weight = cls._positive(item["weight"], "scenario weight")
            servers = tuple(
                cls._probability(value, "opening-server probability")
                for value in item["home_serve_probabilities"]
            )
            if len(servers) != 5:
                raise ValueError("Each scenario must describe exactly five sets.")
            scenarios.append((weight, servers))

        scale = max(weight for weight, _ in scenarios)
        total = math.fsum(weight / scale for weight, _ in scenarios)
        return tuple(
            ((weight / scale) / total, servers) for weight, servers in scenarios
        )

    @staticmethod
    def _beta_quadrature(
        mean: float, concentration: Optional[float], order: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        if concentration is None:
            return np.array([mean]), np.array([1.0])

        alpha = mean * concentration
        beta = (1.0 - mean) * concentration
        jacobi_alpha, jacobi_beta = beta - 1.0, alpha - 1.0
        if jacobi_alpha <= -1.0 or jacobi_beta <= -1.0:
            raise ArithmeticError("Beta shape parameters exceed numerical resolution.")

        nodes, weights = roots_jacobi(order, jacobi_alpha, jacobi_beta)
        probabilities = (nodes + 1.0) / 2.0
        total = float(np.sum(weights))
        if (
            not np.all(np.isfinite(probabilities))
            or not np.all(np.isfinite(weights))
            or np.any(weights <= 0.0)
            or not math.isfinite(total)
            or total <= 0.0
            or np.any(probabilities <= 0.0)
            or np.any(probabilities >= 1.0)
        ):
            raise ArithmeticError("Beta quadrature could not be represented reliably.")
        return probabilities, weights / total

    def _integrate(
        self,
        home_mean: float,
        away_mean: float,
        home_concentration: Optional[float],
        away_concentration: Optional[float],
        scenarios: Tuple[Tuple[float, Tuple[float, ...]], ...],
        order: int,
    ) -> float:
        home_nodes, home_weights = self._beta_quadrature(
            home_mean, home_concentration, order
        )
        away_nodes, away_weights = self._beta_quadrature(
            away_mean, away_concentration, order
        )

        expected_set_probabilities = np.zeros((2, 2), dtype=float)
        match_terms = []
        for home_node, home_weight in zip(home_nodes, home_weights):
            for away_node, away_weight in zip(away_nodes, away_weights):
                weight = float(home_weight * away_weight)
                sets = np.array(
                    [
                        self._set_probabilities(25, float(home_node), float(away_node)),
                        self._set_probabilities(15, float(home_node), float(away_node)),
                    ],
                    dtype=float,
                )
                if self._variation_scope == "set":
                    expected_set_probabilities += weight * sets
                else:
                    match_terms.append(weight * self._average_match(sets, scenarios))

        if self._variation_scope == "set":
            return self._average_match(expected_set_probabilities, scenarios)
        return math.fsum(match_terms)

    @classmethod
    def _average_match(
        cls,
        sets: np.ndarray,
        scenarios: Tuple[Tuple[float, Tuple[float, ...]], ...],
    ) -> float:
        terms = []
        for scenario_weight, opening_servers in scenarios:
            probabilities = []
            for index, home_serves in enumerate(opening_servers):
                row = sets[int(index == 4)]
                probabilities.append(
                    home_serves * float(row[0])
                    + (1.0 - home_serves) * float(row[1])
                )
            terms.append(scenario_weight * cls._match_probability(probabilities))
        return math.fsum(terms)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "NCAA_VOLLEYBALL_WOMEN"),
            ("scope", "full_game"),
            ("family", "moneyline"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Incompatible context {key}: {game_context[key]!r}")

        home_mean = self._sideout(game_context, "home")
        away_mean = self._sideout(game_context, "away")
        home_concentration = self._concentration(
            game_context.get(
                "home_sideout_concentration", self._home_concentration
            ),
            "home_sideout_concentration",
        )
        away_concentration = self._concentration(
            game_context.get(
                "away_sideout_concentration", self._away_concentration
            ),
            "away_sideout_concentration",
        )
        scenarios = self._server_scenarios(game_context)

        order = self.initial_quadrature_order
        probability = self._integrate(
            home_mean,
            away_mean,
            home_concentration,
            away_concentration,
            scenarios,
            order,
        )

        if home_concentration is not None or away_concentration is not None:
            while order < self.maximum_quadrature_order:
                next_order = min(2 * order, self.maximum_quadrature_order)
                refined = self._integrate(
                    home_mean,
                    away_mean,
                    home_concentration,
                    away_concentration,
                    scenarios,
                    next_order,
                )
                if abs(refined - probability) <= self.integration_tolerance:
                    probability = refined
                    break
                probability, order = refined, next_order
            else:
                raise ArithmeticError(
                    "Latent-sideout integration did not converge; "
                    "increase maximum_quadrature_order or inspect calibration."
                )

        probability = self._probability(probability, "home match-win probability")
        return DiscreteDistribution(
            probabilities={
                "home": probability,
                "draw": 0.0,
                "away": 1.0 - probability,
            }
        )