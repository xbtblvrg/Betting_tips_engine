from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAACollegeBaseballQuarter2ThreeWayModel(BaseCandidateModel):
    """
    Independent, bookmaker-free segment run model.

    Cold-start assumptions
    ----------------------
    The supplied segment means are 0.40 home runs and 0.30 away runs.
    No observed evidence identifies excess dispersion, dependence, or feature
    coefficients. Consequently, the default is independent Poisson scoring.
    No additional home-field advantage is applied.

    Baseball has no quarters. This class does not invent an inning mapping.
    Expected runs must refer to the provider's settlement definition of
    "quarter_2". Optional `inning_block` metadata must contain `start` and `end`;
    it documents that mapping but does not silently rescale segment means.

    Conditional model
    -----------------
    For side s, a segment intensity is

        m_s = expected_s * product(explicit exposure multipliers)
              * exp(sum_j beta_sj * (feature_j - center_sj)).

    Feature coefficients must be estimated externally on earlier observations.
    Missing fitted features cause an error rather than implicit imputation.
    Rest, weather, and raw attack/defense values have no invented coefficients.

    If an externally estimated conditional variance/mean ratio D_s exceeds one,
    represent residual intensity by a positive latent variable L_s. The moment
    constraints are E[L_s] = m_s and Var[L_s] = m_s * (D_s - 1). A gamma latent
    intensity satisfies those constraints and yields a negative-binomial count
    distribution with shape m_s / (D_s - 1). At D_s = 1 it becomes Poisson.

    Optional `shared_run_component_mean = c` explicitly specifies a common
    Poisson count Z added to both scores:

        H = X + Z, A = Y + Z.

    This gives Cov(H, A) = c and preserves the supplied marginal moments after
    adjusting residual moments. Z cancels from the margin. This is a particular
    dependence assumption, not a generic weather-frailty model; it defaults to
    zero and should be used only when externally supported.

    Numerical policy
    ----------------
    Probabilities are computed by summing one score distribution against the
    other distribution's CDF, PMF, and survival function. Omitted probability
    is bounded by `tail_probability_budget`; returned probabilities condition
    on the retained interval. Each outcome's absolute truncation error is at
    most the omitted mass, apart from floating-point arithmetic.

    `max_score_states` is a computational resource limit, not a statistical
    clamp. Exceeding it raises an error instead of changing model parameters.

    Context contract
    ----------------
    Optional positive dimensionless multipliers:
      segment_exposure_multiplier, tempo_multiplier,
      home_exposure_multiplier, away_exposure_multiplier,
      home.attack_multiplier, home.defense_multiplier,
      away.attack_multiplier, away.defense_multiplier.

    A defense multiplier above one means increased runs conceded. Explicit
    expected runs are before these multipliers; callers must avoid applying
    the same adjustment twice.

    Optional dispersion inputs:
      dispersion_index, home_dispersion_index, away_dispersion_index.

    Dotted feature paths may be supplied through `feature_coefficients`.
    Odds are intentionally excluded: execution policy can separately accept
    decimal odds >= 1.40 when probability * decimal_odds > 1.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_2_1108_073414",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_2",
        family: str = "three_way",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        feature_centers: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        tail_probability_budget: Optional[float] = None,
        max_score_states: int = 1 << 20,
    ) -> None:
        if (league, scope, family) != (
            "NCAA_BASEBALL",
            "quarter_2",
            "three_way",
        ):
            raise ValueError("This candidate is specific to its declared segment.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        if (
            isinstance(max_score_states, bool)
            or not isinstance(max_score_states, int)
            or max_score_states <= 0
        ):
            raise ValueError("max_score_states must be a positive integer.")

        self.max_score_states = max_score_states
        self.tail_probability_budget = self._number(
            math.sqrt(np.finfo(float).eps)
            if tail_probability_budget is None
            else tail_probability_budget,
            "tail_probability_budget",
        )
        if not 0 < self.tail_probability_budget < 1:
            raise ValueError("tail_probability_budget must lie strictly in (0, 1).")
        if 1.0 - self.tail_probability_budget / 2.0 == 1.0:
            raise ValueError("Requested tail budget is below float64 resolution.")

        self.feature_coefficients = self._copy_parameters(
            feature_coefficients, "feature_coefficients"
        )
        self.feature_centers = self._copy_parameters(
            feature_centers, "feature_centers"
        )
        for side in ("home", "away"):
            unused = (
                self.feature_centers[side].keys()
                - self.feature_coefficients[side].keys()
            )
            if unused:
                raise ValueError(f"Centers without coefficients for {side}: {unused}")

    @staticmethod
    def _number(value: Any, name: str) -> float:
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
    def _copy_parameters(
        cls,
        parameters: Optional[Mapping[str, Mapping[str, float]]],
        name: str,
    ) -> Dict[str, Dict[str, float]]:
        if parameters is None:
            return {"home": {}, "away": {}}
        if not isinstance(parameters, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        if set(parameters) - {"home", "away"}:
            raise ValueError(f"{name} supports only home and away.")

        result: Dict[str, Dict[str, float]] = {}
        for side in ("home", "away"):
            values = parameters.get(side, {})
            if not isinstance(values, Mapping):
                raise TypeError(f"{name}.{side} must be a mapping.")
            result[side] = {}
            for path, value in values.items():
                if not isinstance(path, str) or not path or any(
                    not part for part in path.split(".")
                ):
                    raise ValueError("Feature paths must be nonempty dotted strings.")
                result[side][path] = cls._number(value, f"{name}.{side}.{path}")
        return result

    @staticmethod
    def _lookup(
        context: Mapping[str, Any], path: str, *, required: bool
    ) -> Any:
        current: Any = context
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                if required:
                    raise ValueError(f"Required feature is missing: {path}")
                return None
            current = current[part]
        if current is None and required:
            raise ValueError(f"Required feature is null: {path}")
        return current

    def _expected_runs(self, context: Mapping[str, Any], side: str) -> float:
        baseline = {"home": 0.40, "away": 0.30}[side]
        expected = self._number(
            context.get(f"{side}_expected", baseline), f"{side}_expected"
        )
        if expected < 0:
            raise ValueError(f"{side}_expected must be nonnegative.")

        opponent = "away" if side == "home" else "home"
        log_adjustments = []
        for path in (
            "segment_exposure_multiplier",
            "tempo_multiplier",
            f"{side}_exposure_multiplier",
            f"{side}.attack_multiplier",
            f"{opponent}.defense_multiplier",
        ):
            raw = self._lookup(context, path, required=False)
            if raw is not None:
                multiplier = self._number(raw, path)
                if multiplier <= 0:
                    raise ValueError(f"{path} must be strictly positive.")
                log_adjustments.append(math.log(multiplier))

        for path, coefficient in self.feature_coefficients[side].items():
            value = self._number(
                self._lookup(context, path, required=True), path
            )
            center = self.feature_centers[side].get(path, 0.0)
            adjustment = coefficient * (value - center)
            if not math.isfinite(adjustment):
                raise ValueError(f"Feature adjustment overflow for {side}.{path}")
            log_adjustments.append(adjustment)

        if expected == 0:
            return 0.0

        try:
            mean = math.exp(math.fsum([math.log(expected), *log_adjustments]))
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"Adjusted {side} intensity is not representable.") from exc
        if not math.isfinite(mean) or mean <= 0:
            raise ValueError(f"Adjusted {side} intensity is not representable.")
        return mean

    def _dispersion(self, context: Mapping[str, Any], side: str) -> float:
        dispersion = self._number(
            context.get(
                f"{side}_dispersion_index",
                context.get("dispersion_index", 1.0),
            ),
            f"{side}_dispersion_index",
        )
        if dispersion < 1:
            raise ValueError(
                "This intensity-mixture model requires dispersion >= 1; "
                "underdispersion requires a different count process."
            )
        return dispersion

    @staticmethod
    def _residual_distribution(mean: float, dispersion: float, shared: float):
        residual_mean = mean - shared
        if residual_mean < 0:
            raise ValueError("Shared intensity cannot exceed either marginal mean.")
        if residual_mean == 0:
            if mean > 0 and dispersion != 1:
                raise ValueError(
                    "A zero residual mean cannot carry positive residual variance."
                )
            return poisson(mu=0.0)

        if dispersion == 1:
            return poisson(mu=residual_mean)

        excess_variance = mean * (dispersion - 1.0)
        if not math.isfinite(excess_variance) or excess_variance <= 0:
            raise ValueError("Excess variance is not representable.")

        relative_excess = excess_variance / residual_mean
        shape = residual_mean / relative_excess
        success_probability = 1.0 / (1.0 + relative_excess)
        if (
            not math.isfinite(shape)
            or shape <= 0
            or not 0 < success_probability < 1
        ):
            raise ValueError("Negative-binomial parameters are not representable.")
        return nbinom(n=shape, p=success_probability)

    def _support_interval(self, distribution) -> Tuple[int, int, float]:
        tail = self.tail_probability_budget / 2.0
        lower_quantile = float(distribution.ppf(tail))
        upper_quantile = float(distribution.isf(tail))
        if not (
            math.isfinite(lower_quantile)
            and math.isfinite(upper_quantile)
        ):
            raise ArithmeticError("Unable to obtain finite count quantiles.")

        lower = int(lower_quantile)
        upper = int(upper_quantile)
        if lower < 0 or upper < lower:
            raise ArithmeticError("Invalid count support interval.")
        if upper > 2 ** np.finfo(float).nmant:
            raise ArithmeticError("Count support exceeds exact float64 integers.")

        omitted = float(distribution.cdf(lower - 1)) + float(
            distribution.sf(upper)
        )
        if not math.isfinite(omitted) or omitted < 0:
            raise ArithmeticError("Invalid truncation probability.")
        if omitted > self.tail_probability_budget:
            raise ArithmeticError("Count quantiles failed the requested tail bound.")
        return lower, upper, omitted

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "NCAA_BASEBALL"),
            ("scope", "quarter_2"),
            ("family", "three_way"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Context {key} does not match this candidate.")

        if "inning_block" in game_context:
            block = game_context["inning_block"]
            if not isinstance(block, Mapping):
                raise ValueError("inning_block must contain integer start and end.")
            start, end = block.get("start"), block.get("end")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
            ):
                raise ValueError("Invalid provider-defined inning block.")

        home_mean = self._expected_runs(game_context, "home")
        away_mean = self._expected_runs(game_context, "away")
        shared = self._number(
            game_context.get("shared_run_component_mean", 0.0),
            "shared_run_component_mean",
        )
        if shared < 0 or shared > min(home_mean, away_mean):
            raise ValueError("Shared intensity must lie between zero and both means.")

        home = self._residual_distribution(
            home_mean, self._dispersion(game_context, "home"), shared
        )
        away = self._residual_distribution(
            away_mean, self._dispersion(game_context, "away"), shared
        )

        home_interval = self._support_interval(home)
        away_interval = self._support_interval(away)
        sum_home = (
            home_interval[1] - home_interval[0]
            <= away_interval[1] - away_interval[0]
        )
        summed, other = (home, away) if sum_home else (away, home)
        lower, upper, omitted = home_interval if sum_home else away_interval

        states = upper - lower + 1
        if states > self.max_score_states:
            raise ArithmeticError(
                f"Prediction requires {states} score states, exceeding the "
                f"configured resource limit {self.max_score_states}."
            )

        scores = np.arange(lower, upper + 1, dtype=np.int64)
        mass = np.asarray(summed.pmf(scores), dtype=float)
        win_given_score = np.asarray(other.cdf(scores - 1), dtype=float)
        tie_given_score = np.asarray(other.pmf(scores), dtype=float)
        loss_given_score = np.asarray(other.sf(scores), dtype=float)

        for values in (mass, win_given_score, tie_given_score, loss_given_score):
            if not np.all(np.isfinite(values)) or np.any(values < 0):
                raise ArithmeticError("Count solver returned invalid probabilities.")

        win = float(np.dot(mass, win_given_score))
        draw = float(np.dot(mass, tie_given_score))
        loss = float(np.dot(mass, loss_given_score))
        total = math.fsum((win, draw, loss))

        # Allow accumulated floating-point error, separately from tail error.
        rounding_budget = np.finfo(float).eps * states
        if (
            not math.isfinite(total)
            or total <= 0
            or abs(total - (1.0 - omitted))
            > self.tail_probability_budget + rounding_budget
        ):
            raise ArithmeticError("Probability mass failed numerical validation.")

        win, draw, loss = win / total, draw / total, loss / total
        p_home, p_away = (win, loss) if sum_home else (loss, win)

        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": draw, "away": p_away}
        )