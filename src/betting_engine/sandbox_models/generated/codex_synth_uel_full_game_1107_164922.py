from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.signal import convolve2d
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the execution environment.


class UELWinnerTotalModel(BaseCandidateModel):
    """
    Price winner/total contracts from one joint integer-score distribution.

    Cold-start identification
    -------------------------
    The supplied evidence identifies only marginal means (1.60, 1.20).
    It does not identify dependence, situational coefficients, or parameter
    uncertainty. Consequently, the default is the maximum-entropy-in-time
    counting-process baseline: independent Poisson goal counts. Nonzero
    dependence and feature effects require externally estimated parameters.

    Model
    -----
    H = X + Z, A = Y + Z, with independent components. Z is Poisson with
    calibrated intensity c; X and Y have means mu_h-c and mu_a-c.
    When posterior rate variance v is supplied, the corresponding private
    count is a Gamma-Poisson mixture with shape mean**2 / v. Otherwise it is
    Poisson. Thus:

        E[H] = mu_h                 E[A] = mu_a
        Var(H) = mu_h + v_h         Var(A) = mu_a + v_a
        Cov(H, A) = c

    A margin-preserving generalization of the Dixon-Coles adjustment moves
    probability among (0,0), (0,1), (1,0), and (1,1). In the independent
    Poisson limit it is exactly the usual Dixon-Coles correction. Its
    admissible range is derived from the actual cell probabilities.

    Expected-goal inputs represent full-game regulation-time goals, including
    stoppage time and excluding extra time and penalties. Winner and total
    events must both be settled from the returned score matrix.

    Configuration
    -------------
    `parameters` accepts:
      baseline_home, baseline_away
      rest_coefficient, state_coefficient, pace_coefficient
      travel_shrink_coefficient, rotation_shrink_coefficient
      shared_intensity, rho

    Missing coefficients and dependence parameters are zero: an explicit
    no-effect prior, not a fitted claim. Supply parameters fitted on historical
    regulation-time scorelines using chronological out-of-sample validation.

    Context
    -------
    home_expected / away_expected:
        Optional posterior mean goal projections.

    home / away:
        Optional dictionaries containing attack_log_offset,
        defense_log_offset, and attack_exposure / defense_exposure.
        Offsets are centered log effects; exposures are positive ratios.
        Raw unstandardized attack/defense ratings are deliberately not used.

    home_rest_days / away_rest_days:
        Used together when rest_coefficient is nonzero.
    state:
        Signed, centered aggregate-incentive feature.
    pace_log_offset:
        Centered log pace feature.
    tempo_multiplier:
        Positive multiplier, default unity.
    travel_distance / lineup_rotation:
        Nonnegative features in the units used to fit shrink coefficients.

    home_rate_variance / away_rate_variance:
        Nonnegative posterior variances of each private scoring intensity,
        expressed in final goal-rate units. These are epistemic variances,
        not observed goal-count variances.

    home_rate_samples / away_rate_samples:
        Alternative equal-weight posterior rate draws in final goal-rate
        units. Their empirical distribution supplies the rate variance.
        Do not pass unrelated forecast-model outputs as posterior draws.

    shared_intensity / rho:
        Optional match-specific calibrated dependence parameters.

    Numerical controls bound omitted probability, not odds or probabilities.
    No bookmaker prices enter the forecast. Cold-start stake restrictions and
    odds >= 1.40 eligibility belong in the portfolio/settlement layer.
    """

    _DEFAULT_PARAMETERS = {
        "baseline_home": 1.60,
        "baseline_away": 1.20,
        "rest_coefficient": 0.0,
        "state_coefficient": 0.0,
        "pace_coefficient": 0.0,
        "travel_shrink_coefficient": 0.0,
        "rotation_shrink_coefficient": 0.0,
        "shared_intensity": 0.0,
        "rho": 0.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1107_164922",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "winner_total",
        *,
        parameters: Optional[Mapping[str, float]] = None,
        tail_tolerance: float = 1e-10,
        max_score_cells: int = 1_000_000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        supplied = dict(parameters or {})
        unknown = set(supplied).difference(self._DEFAULT_PARAMETERS)
        if unknown:
            raise ValueError(f"Unknown parameters: {sorted(unknown)}")

        self.parameters = dict(self._DEFAULT_PARAMETERS)
        for key, value in supplied.items():
            self.parameters[key] = self._finite(value, key)

        for key in ("baseline_home", "baseline_away"):
            self._nonnegative(self.parameters[key], key)

        for key in (
            "travel_shrink_coefficient",
            "rotation_shrink_coefficient",
            "shared_intensity",
        ):
            self._nonnegative(self.parameters[key], key)

        self.tail_tolerance = self._finite(tail_tolerance, "tail_tolerance")
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")

        if (
            isinstance(max_score_cells, bool)
            or not isinstance(max_score_cells, int)
            or max_score_cells < 4
        ):
            raise ValueError("max_score_cells must be an integer of at least four.")
        self.max_score_cells = max_score_cells

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
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        value = context.get(side, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{side} must be a mapping.")
        return value

    def _expected_goals(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float]:
        p = self.parameters
        home = self._team(context, "home")
        away = self._team(context, "away")

        base_h = self._nonnegative(
            context.get("home_expected", p["baseline_home"]), "home_expected"
        )
        base_a = self._nonnegative(
            context.get("away_expected", p["baseline_away"]), "away_expected"
        )

        def team_effect(
            attacker: Mapping[str, Any],
            defender: Mapping[str, Any],
            label: str,
        ) -> float:
            attack = self._finite(
                attacker.get("attack_log_offset", 0.0),
                f"{label}.attack_log_offset",
            )
            defense = self._finite(
                defender.get("defense_log_offset", 0.0),
                f"{label}.opponent.defense_log_offset",
            )
            attack_exposure = self._positive(
                attacker.get("attack_exposure", 1.0),
                f"{label}.attack_exposure",
            )
            defense_exposure = self._positive(
                defender.get("defense_exposure", 1.0),
                f"{label}.opponent.defense_exposure",
            )
            return (
                attack
                - defense
                + math.log(attack_exposure)
                - math.log(defense_exposure)
            )

        rest_effect = 0.0
        if p["rest_coefficient"] != 0.0:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("Both rest-day inputs are required for a rest effect.")
            rest_h = self._nonnegative(context["home_rest_days"], "home_rest_days")
            rest_a = self._nonnegative(context["away_rest_days"], "away_rest_days")
            rest_effect = p["rest_coefficient"] * (rest_h - rest_a)

        state_effect = 0.0
        if p["state_coefficient"] != 0.0:
            if "state" not in context:
                raise ValueError("state is required for a nonzero state coefficient.")
            state_effect = p["state_coefficient"] * self._finite(
                context["state"], "state"
            )

        pace_effect = 0.0
        if p["pace_coefficient"] != 0.0:
            if "pace_log_offset" not in context:
                raise ValueError("pace_log_offset is required for a pace effect.")
            pace_effect = p["pace_coefficient"] * self._finite(
                context["pace_log_offset"], "pace_log_offset"
            )

        common = pace_effect + math.log(
            self._positive(context.get("tempo_multiplier", 1.0), "tempo_multiplier")
        )
        shift_h = team_effect(home, away, "home") + rest_effect + state_effect + common
        shift_a = team_effect(away, home, "away") - rest_effect - state_effect + common

        try:
            mu_h = 0.0 if base_h == 0.0 else math.exp(math.log(base_h) + shift_h)
            mu_a = 0.0 if base_a == 0.0 else math.exp(math.log(base_a) + shift_a)
        except OverflowError as exc:
            raise ValueError("Situational inputs produce overflowing goal rates.") from exc

        if not math.isfinite(mu_h + mu_a):
            raise ValueError("Situational inputs produce nonfinite goal rates.")
        if (base_h > 0.0 and mu_h == 0.0) or (base_a > 0.0 and mu_a == 0.0):
            raise ValueError("Situational inputs underflow a positive goal rate.")

        shrink_exponent = 0.0
        for coefficient, feature in (
            ("travel_shrink_coefficient", "travel_distance"),
            ("rotation_shrink_coefficient", "lineup_rotation"),
        ):
            if p[coefficient] != 0.0:
                if feature not in context:
                    raise ValueError(f"{feature} is required for {coefficient}.")
                shrink_exponent += p[coefficient] * self._nonnegative(
                    context[feature], feature
                )

        if not math.isfinite(shrink_exponent):
            raise ValueError("Travel/rotation shrinkage overflowed.")

        if shrink_exponent > 0.0 and mu_h > 0.0 and mu_a > 0.0:
            total = mu_h + mu_a
            log_ratio = (math.log(mu_h) - math.log(mu_a)) * math.exp(-shrink_exponent)
            # Stable logistic allocation preserves total expected goals.
            if log_ratio >= 0.0:
                ratio = math.exp(-log_ratio)
                mu_h, mu_a = total / (1.0 + ratio), total * ratio / (1.0 + ratio)
            else:
                ratio = math.exp(log_ratio)
                mu_h, mu_a = total * ratio / (1.0 + ratio), total / (1.0 + ratio)

        return mu_h, mu_a

    def _rate_variance(self, context: Mapping[str, Any], side: str) -> float:
        variance_key = f"{side}_rate_variance"
        samples_key = f"{side}_rate_samples"
        if variance_key in context and samples_key in context:
            raise ValueError(f"Supply {variance_key} or {samples_key}, not both.")
        if variance_key in context:
            return self._nonnegative(context[variance_key], variance_key)
        if samples_key not in context:
            return 0.0

        try:
            samples = np.asarray(context[samples_key], dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{samples_key} must contain numeric posterior draws.") from exc
        if (
            samples.ndim != 1
            or samples.size < 2
            or not np.all(np.isfinite(samples))
            or np.any(samples < 0.0)
        ):
            raise ValueError(
                f"{samples_key} requires at least two finite nonnegative draws."
            )
        with np.errstate(over="ignore", invalid="ignore"):
            variance = float(np.var(samples, ddof=0))
        return self._nonnegative(variance, variance_key)

    @staticmethod
    def _count_law(mean: float, variance: float) -> Any:
        if mean == 0.0:
            if variance != 0.0:
                raise ValueError("A nonnegative rate with zero mean has zero variance.")
            return poisson(mu=0.0)
        if variance == 0.0:
            return poisson(mu=mean)

        shape = mean * (mean / variance)
        probability = 1.0 / (1.0 + variance / mean)
        if (
            not math.isfinite(shape)
            or shape <= 0.0
            or not 0.0 < probability < 1.0
        ):
            raise ValueError(
                "Gamma-Poisson parameters exceed numerical precision; "
                "provide a representable rate variance."
            )
        return nbinom(n=shape, p=probability)

    def _support_limit(self, law: Any, tail_budget: float) -> int:
        quantile = float(law.isf(tail_budget))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ValueError("Cannot resolve count support at the requested tolerance.")
        limit = max(1, math.ceil(quantile))
        if limit + 1 > self.max_score_cells:
            raise ValueError("Required score support exceeds max_score_cells.")
        while float(law.sf(limit)) > tail_budget:
            limit += 1
            if limit + 1 > self.max_score_cells:
                raise ValueError("Required score support exceeds max_score_cells.")
        return limit

    def _score_matrix(self, context: Mapping[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._expected_goals(context)
        var_h = self._rate_variance(context, "home")
        var_a = self._rate_variance(context, "away")
        shared = self._nonnegative(
            context.get("shared_intensity", self.parameters["shared_intensity"]),
            "shared_intensity",
        )
        if shared > min(mu_h, mu_a):
            raise ValueError("shared_intensity cannot exceed either marginal mean.")

        private_h = self._count_law(mu_h - shared, var_h)
        private_a = self._count_law(mu_a - shared, var_a)

        # Marginals are private count + shared Poisson. Allocate the total
        # omitted-mass budget across all three independent components.
        component_budget = self.tail_tolerance / 3.0
        h_limit = self._support_limit(private_h, component_budget)
        a_limit = self._support_limit(private_a, component_budget)
        shared_law = poisson(mu=shared)
        z_limit = 0 if shared == 0.0 else self._support_limit(
            shared_law, component_budget
        )

        rows, columns = h_limit + z_limit + 1, a_limit + z_limit + 1
        if rows * columns > self.max_score_cells:
            raise ValueError(
                f"Required score matrix has {rows * columns} cells; "
                f"configured capacity is {self.max_score_cells}."
            )

        hp = np.asarray(private_h.pmf(np.arange(h_limit + 1)), dtype=float)
        ap = np.asarray(private_a.pmf(np.arange(a_limit + 1)), dtype=float)
        matrix = np.outer(hp, ap)

        if shared > 0.0:
            zp = np.asarray(shared_law.pmf(np.arange(z_limit + 1)), dtype=float)
            shared_kernel = np.diag(zp)
            matrix = convolve2d(matrix, shared_kernel, mode="full")

        if not np.all(np.isfinite(matrix)) or np.any(matrix < 0.0):
            raise ArithmeticError("Invalid probability mass from count-law evaluation.")

        rho = self._finite(context.get("rho", self.parameters["rho"]), "rho")
        if rho != 0.0:
            # The signed cycle preserves every row and column sum, hence
            # preserves both marginal count distributions and expected goals.
            delta = rho * mu_h * mu_a * float(matrix[0, 0])
            if not math.isfinite(delta):
                raise ValueError("The low-score correction is not finite.")
            lower = -min(float(matrix[0, 1]), float(matrix[1, 0]))
            upper = min(float(matrix[0, 0]), float(matrix[1, 1]))
            if not lower <= delta <= upper:
                raise ValueError(
                    "rho produces negative low-score probability: "
                    f"required correction is {delta}, admissible interval "
                    f"is [{lower}, {upper}]. Refit the dependence parameter."
                )
            matrix[0, 0] -= delta
            matrix[0, 1] += delta
            matrix[1, 0] += delta
            matrix[1, 1] -= delta

        mass = math.fsum(matrix.ravel())
        numerical_slack = (
            np.finfo(float).eps * matrix.size * max(1.0, abs(mass))
        )
        if (
            mass <= 0.0
            or not math.isfinite(mass)
            or abs(1.0 - mass) > self.tail_tolerance + numerical_slack
        ):
            raise ArithmeticError("Score support failed its probability-mass check.")

        # Conditioning on retained support changes any event probability by
        # at most the omitted mass, bounded above by tail_tolerance.
        matrix /= mass
        return matrix

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        for field, expected in (
            ("league", "UEL"),
            ("scope", "full_game"),
            ("family", "winner_total"),
        ):
            if field in game_context and game_context[field] != expected:
                raise ValueError(f"This model requires {field}={expected!r}.")

        matrix = self._score_matrix(game_context)
        scores = {
            (home_goals, away_goals): float(matrix[home_goals, away_goals])
            for home_goals, away_goals in np.ndindex(matrix.shape)
            if matrix[home_goals, away_goals] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)