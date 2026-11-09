from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import minimize
from scipy.special import expit, gammaln
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLExactScoreModel(BaseCandidateModel):
    """
    Regulation-time exact-score model, including stoppage time but excluding
    extra time and penalties.

    Cold start:
        Independent Poisson scores with means 1.60 and 1.20, unless overridden
        by pre-match expected-goals inputs and explicit exposure multipliers.
        The supplied dispersion index of one provides no evidence for an
        additional latent variance component.

    Learned dynamics, on normalized match time t in [0, 1]:
        q_home(t, h, a) =
            mu_home * exp(
                beta * t * tanh(a - h)
                + gamma * t * tanh(-aggregate_home - h + a)
            )

        The away intensity reverses the score and aggregate differentials.
        The aggregate term is enabled only for explicitly marked second legs.
        Bounded state responses ensure nonexplosion for finite coefficients.

    A positivity-constrained Dixon-Coles correction acts on the four lowest
    score cells. Its parameter is zero at cold start. The correction is
    normalized after application; with learned state dynamics it need not
    preserve the uncorrected marginal means.

    Feature contract:
        home_expected, away_expected:
            Pre-match expected goals; defaults are the supplied empirical means.
        home_attack_exposure, away_attack_exposure:
            Dimensionless multipliers, neutral value one.
        home_defense_exposure, away_defense_exposure:
            Dimensionless goals-conceded multipliers, neutral value one.
        pace, duration_exposure:
            Shared positive multipliers, neutral value one.
        home_rest_days, away_rest_days:
            Optional nonnegative values. Their log-rest difference receives a
            learned coefficient; missing either value disables this feature.
        is_second_leg:
            Boolean.
        aggregate_home:
            Home team's pre-match aggregate lead, required for second legs.

    Exposure fields may also appear as home/away dictionaries containing
    attack_exposure and defense_exposure. Raw attack/defense ratings are not
    interpreted as dimensionless multipliers.

    Do not supply exposure multipliers already incorporated into expected
    goals. Weather and venue are not assigned invented cold-start effects.

    fit() consumes match results, not selected wagers. Prior precisions must
    be supplied explicitly; no shrinkage strength is inferred from zero bets.
    Features must have been available before kickoff. Chronological validation
    and training/forecast separation remain the caller's responsibility.

    numerical_tolerance controls computational approximation, not probabilities
    or betting eligibility. max_cells is an optional computational resource
    budget. No bookmaker information enters score probabilities.

    Integration contract:
        DiscreteDistribution.from_scores accepts a mapping
        {(home_goals, away_goals): probability}.
    """

    PARAMETER_NAMES = (
        "home_log_adjustment",
        "away_log_adjustment",
        "rest_coefficient",
        "score_response",
        "aggregate_response",
        "dependence",
    )

    def __init__(
        self,
        model_id="codex_synth_ucl_full_game_1108_045013",
        version="1.0.0",
        league="UCL",
        scope="full_game",
        family="exact_score",
        *,
        numerical_tolerance: Optional[float] = None,
        max_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("UCL", "full_game", "exact_score"):
            raise ValueError("This candidate supports UCL|full_game|exact_score only.")

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else float(numerical_tolerance)
        )
        if not math.isfinite(tolerance) or not 0 < tolerance < 1:
            raise ValueError("numerical_tolerance must lie strictly between zero and one.")
        if max_cells is not None:
            if isinstance(max_cells, bool) or int(max_cells) != max_cells or max_cells < 4:
                raise ValueError("max_cells must be an integer of at least four.")

        self.numerical_tolerance = tolerance
        self.max_cells = None if max_cells is None else int(max_cells)
        self.parameters_ = np.zeros(len(self.PARAMETER_NAMES), dtype=float)
        self.training_match_count_ = 0
        self.fit_objective_ = None

    @staticmethod
    def _number(value: Any, name: str, *, positive=False, nonnegative=False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0:
            raise ValueError(f"{name} must be positive.")
        if nonnegative and result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _goal(cls, value: Any, name: str) -> int:
        result = cls._number(value, name, nonnegative=True)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    def _exposure(self, context: Mapping[str, Any], side: str, kind: str) -> float:
        key = f"{side}_{kind}_exposure"
        nested = context.get(side, {})
        if nested is None:
            nested = {}
        if not isinstance(nested, Mapping):
            raise ValueError(f"{side} must be a mapping when supplied.")
        value = context.get(key, nested.get(f"{kind}_exposure", 1.0))
        return self._number(value, key, positive=True)

    def _features(self, context: Mapping[str, Any]) -> Tuple[float, float, float, bool, float]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home = self._number(
            context.get("home_expected", 1.60), "home_expected", positive=True
        )
        away = self._number(
            context.get("away_expected", 1.20), "away_expected", positive=True
        )
        pace = self._number(context.get("pace", 1.0), "pace", positive=True)
        duration = self._number(
            context.get("duration_exposure", 1.0), "duration_exposure", positive=True
        )

        log_home = (
            math.log(home)
            + math.log(pace)
            + math.log(duration)
            + math.log(self._exposure(context, "home", "attack"))
            + math.log(self._exposure(context, "away", "defense"))
        )
        log_away = (
            math.log(away)
            + math.log(pace)
            + math.log(duration)
            + math.log(self._exposure(context, "away", "attack"))
            + math.log(self._exposure(context, "home", "defense"))
        )

        rest_values = {}
        for side in ("home", "away"):
            key = f"{side}_rest_days"
            if context.get(key) is not None:
                rest_values[side] = self._number(context[key], key, nonnegative=True)
        rest = (
            math.log1p(rest_values["home"]) - math.log1p(rest_values["away"])
            if len(rest_values) == 2
            else 0.0
        )

        second_leg = context.get("is_second_leg", False)
        if not isinstance(second_leg, (bool, np.bool_)):
            raise ValueError("is_second_leg must be boolean.")
        if second_leg and "aggregate_home" not in context:
            raise ValueError("Second legs require a pre-match aggregate_home value.")
        aggregate = (
            self._number(context["aggregate_home"], "aggregate_home")
            if second_leg
            else 0.0
        )
        return log_home, log_away, rest, bool(second_leg), aggregate

    @staticmethod
    def _dc_factors(mu_home: float, mu_away: float, dependence: float) -> np.ndarray:
        # Positivity interval:
        # max(-1/mu_home, -1/mu_away) < rho < min(1, 1/(mu_home*mu_away)).
        # This logistic parameterization maps dependence=0 exactly to rho=0.
        if dependence == 0:
            return np.ones((2, 2), dtype=float)

        lower = -1.0 / max(mu_home, mu_away)
        upper = math.exp(min(0.0, -math.log(mu_home) - math.log(mu_away)))
        offset = math.log(-lower) - math.log(upper)
        z = dependence + offset

        # Express each affine correction as a convex combination of its
        # endpoint values, avoiding cancellation near a positivity boundary.
        weight_lower = expit(-z)
        weight_upper = expit(z)
        endpoint_lower = np.array(
            [
                [1.0 - mu_home * mu_away * lower, 1.0 + mu_home * lower],
                [1.0 + mu_away * lower, 1.0 - lower],
            ],
            dtype=float,
        )
        endpoint_upper = np.array(
            [
                [1.0 - mu_home * mu_away * upper, 1.0 + mu_home * upper],
                [1.0 + mu_away * upper, 1.0 - upper],
            ],
            dtype=float,
        )
        factors = weight_lower * endpoint_lower + weight_upper * endpoint_upper
        rounding_budget = np.finfo(float).eps * (
            1.0 + np.abs(endpoint_lower) + np.abs(endpoint_upper)
        )
        if np.any(factors < -rounding_budget) or not np.all(np.isfinite(factors)):
            raise FloatingPointError("Dependence correction is numerically invalid.")
        return np.maximum(factors, 0.0)

    def _score_matrix(
        self,
        features: Tuple[float, float, float, bool, float],
        parameters: np.ndarray,
        minimum_score: Tuple[int, int] = (0, 0),
    ) -> np.ndarray:
        log_home, log_away, rest, second_leg, aggregate = features
        home_adjustment, away_adjustment, rest_coef, beta, gamma, dependence = parameters

        mu_home = math.exp(log_home + home_adjustment + rest_coef * rest)
        mu_away = math.exp(log_away + away_adjustment - rest_coef * rest)
        if not all(math.isfinite(x) and x > 0 for x in (mu_home, mu_away)):
            raise FloatingPointError("Expected-goal intensities are outside numeric range.")

        gamma = gamma if second_leg else 0.0
        maximum_multiplier = math.exp(abs(beta) + abs(gamma))
        bound_home = mu_home * maximum_multiplier
        bound_away = mu_away * maximum_multiplier
        if not all(math.isfinite(x) for x in (bound_home, bound_away)):
            raise FloatingPointError("Intensity domination bound overflowed.")

        factors = self._dc_factors(mu_home, mu_away, dependence)
        correction_bound = max(1.0, float(factors.max()))

        # Every path's side-specific intensity is bounded by bound_home/away.
        # Poisson survival bounds therefore control mass leaving the lattice.
        tail_budget = self.numerical_tolerance / correction_bound
        side_budget = tail_budget / 2.0
        if side_budget <= 0:
            raise FloatingPointError("Requested tail accuracy is not representable.")

        cutoffs = []
        for bound, required in zip((bound_home, bound_away), minimum_score):
            quantile = poisson.isf(side_budget, bound)
            if not np.isfinite(quantile):
                raise FloatingPointError("Cannot construct a finite score lattice.")
            cutoff = max(1, required, int(math.ceil(float(quantile))))
            while poisson.sf(cutoff, bound) > side_budget:
                cutoff += 1
            cutoffs.append(cutoff)

        home_max, away_max = cutoffs
        shape = (home_max + 1, away_max + 1)
        cell_count = shape[0] * shape[1]
        if self.max_cells is not None and cell_count > self.max_cells:
            raise RuntimeError(
                f"Required score lattice has {cell_count} cells, exceeding max_cells."
            )

        home_axis = np.arange(shape[0], dtype=float)
        away_axis = np.arange(shape[1], dtype=float)

        if beta == 0 and gamma == 0:
            home_mass = np.exp(
                -mu_home + home_axis * math.log(mu_home) - gammaln(home_axis + 1)
            )
            away_mass = np.exp(
                -mu_away + away_axis * math.log(mu_away) - gammaln(away_axis + 1)
            )
            probabilities = np.outer(home_mass, away_mass)
        else:
            differential = home_axis[:, None] - away_axis[None, :]
            home_pressure = -beta * np.tanh(differential)
            if second_leg:
                home_pressure -= gamma * np.tanh(aggregate + differential)

            initial = np.zeros(cell_count + 1, dtype=float)
            initial[0] = 1.0

            def forward_equations(t, state):
                mass = state[:-1].reshape(shape)
                home_flow = mass * mu_home * np.exp(t * home_pressure)
                away_flow = mass * mu_away * np.exp(-t * home_pressure)

                derivative = -home_flow - away_flow
                derivative[1:, :] += home_flow[:-1, :]
                derivative[:, 1:] += away_flow[:, :-1]

                result = np.empty_like(state)
                result[:-1] = derivative.ravel()
                result[-1] = home_flow[-1, :].sum() + away_flow[:, -1].sum()
                return result

            absolute_tolerance = tail_budget / (cell_count + 1)
            solution = solve_ivp(
                forward_equations,
                (0.0, 1.0),
                initial,
                method="DOP853",
                rtol=self.numerical_tolerance,
                atol=absolute_tolerance,
                t_eval=[1.0],
            )
            if not solution.success:
                raise RuntimeError(f"Score integration failed: {solution.message}")

            terminal = solution.y[:, -1]
            if not np.all(np.isfinite(terminal)):
                raise FloatingPointError("Score integration produced nonfinite mass.")

            negative_mass = float(-terminal[terminal < 0].sum())
            conservation_error = abs(float(terminal.sum()) - 1.0)
            error_budget = tail_budget + self.numerical_tolerance
            if negative_mass > error_budget or conservation_error > error_budget:
                raise FloatingPointError("Score integration failed probability checks.")

            probabilities = np.maximum(terminal[:-1], 0.0).reshape(shape)

        retained_mass = float(probabilities.sum())
        missing_mass = abs(1.0 - retained_mass)
        if missing_mass > tail_budget + self.numerical_tolerance:
            raise FloatingPointError("Score lattice lost more mass than its error budget.")

        probabilities[:2, :2] *= factors
        normalization = float(probabilities.sum())
        if not math.isfinite(normalization) or normalization <= 0:
            raise FloatingPointError("Corrected score distribution cannot be normalized.")

        # Conditioning the finite lattice can amplify truncation error if the
        # dependence correction leaves very little total mass.
        if correction_bound * missing_mass / normalization > (
            self.numerical_tolerance + tail_budget
        ):
            raise FloatingPointError(
                "Dependence correction amplifies truncation error; "
                "use a tighter numerical_tolerance."
            )

        return probabilities / normalization

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        matrix = self._score_matrix(self._features(game_context), self.parameters_)
        scores = {
            (home, away): float(matrix[home, away])
            for home in range(matrix.shape[0])
            for away in range(matrix.shape[1])
        }
        return DiscreteDistribution.from_scores(scores)

    def probability(
        self, game_context: Dict[str, Any], home_goals: int, away_goals: int
    ) -> float:
        score = (
            self._goal(home_goals, "home_goals"),
            self._goal(away_goals, "away_goals"),
        )
        matrix = self._score_matrix(
            self._features(game_context), self.parameters_, minimum_score=score
        )
        return float(matrix[score])

    def value_contract(
        self,
        game_context: Dict[str, Any],
        home_goals: int,
        away_goals: int,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        odds = self._number(decimal_odds, "decimal_odds", positive=True)
        probability = self.probability(game_context, home_goals, away_goals)
        expected_return = odds * probability - 1.0
        return {
            "probability": probability,
            "decimal_odds": odds,
            "expected_return_per_unit": expected_return,
            "eligible": odds >= 1.40 and expected_return > 0.0,
        }

    def fit(
        self,
        matches: Iterable[Mapping[str, Any]],
        *,
        prior_precision: Mapping[str, float],
        optimizer_options: Optional[Dict[str, Any]] = None,
    ):
        """
        Fit zero-centered Gaussian-prior MAP coefficients.

        Each match must contain:
            game_context: pre-match feature mapping
            home_goals: settled regulation-time home score
            away_goals: settled regulation-time away score

        prior_precision maps every PARAMETER_NAMES entry to a strictly positive
        precision, i.e. inverse prior variance. Select these values using
        domain information or an inner chronological validation procedure,
        never the evaluation period.

        Fits replace the previous coefficients atomically after convergence.
        An empty match collection restores the documented cold-start model.
        """
        if set(prior_precision) != set(self.PARAMETER_NAMES):
            raise ValueError("prior_precision must specify every parameter exactly once.")
        precisions = np.array(
            [
                self._number(prior_precision[name], name, positive=True)
                for name in self.PARAMETER_NAMES
            ],
            dtype=float,
        )

        observations = []
        for match in matches:
            if not isinstance(match, Mapping):
                raise TypeError("Each match must be a mapping.")
            features = self._features(match["game_context"])
            score = (
                self._goal(match["home_goals"], "home_goals"),
                self._goal(match["away_goals"], "away_goals"),
            )
            observations.append((features, score))

        if not observations:
            self.parameters_ = np.zeros(len(self.PARAMETER_NAMES), dtype=float)
            self.training_match_count_ = 0
            self.fit_objective_ = None
            return self

        def objective(parameters):
            if not np.all(np.isfinite(parameters)):
                return float("inf")
            penalty = float(np.dot(precisions, parameters * parameters) / 2.0)
            if not math.isfinite(penalty):
                return float("inf")
            loss = penalty
            try:
                for features, score in observations:
                    matrix = self._score_matrix(features, parameters, score)
                    probability = float(matrix[score])
                    if probability <= 0 or not math.isfinite(probability):
                        return float("inf")
                    loss -= math.log(probability)
            except (OverflowError, FloatingPointError, RuntimeError):
                return float("inf")
            return loss

        # Derivative-free optimization avoids differentiating through adaptive
        # lattice cutoffs and numerical forward-equation integration.
        result = minimize(
            objective,
            np.zeros(len(self.PARAMETER_NAMES), dtype=float),
            method="Powell",
            options={} if optimizer_options is None else dict(optimizer_options),
        )
        if not result.success or not np.isfinite(result.fun):
            raise RuntimeError(f"MAP fit failed; prior model retained: {result.message}")
        if not np.all(np.isfinite(result.x)):
            raise FloatingPointError("MAP fit returned nonfinite parameters.")

        fitted_parameters = np.asarray(result.x, dtype=float).copy()
        final_objective = objective(fitted_parameters)
        if not math.isfinite(final_objective):
            raise RuntimeError("Final MAP verification failed; prior model retained.")

        self.parameters_ = fitted_parameters
        self.training_match_count_ = len(observations)
        self.fit_objective_ = final_objective
        return self