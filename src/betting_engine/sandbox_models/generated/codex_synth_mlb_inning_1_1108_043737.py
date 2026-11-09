from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the execution environment, as specified.


class MLBFirstInningExactScoreModel(BaseCandidateModel):
    """
    Deterministic, bookmaker-independent first-inning exact-score model.

    Identification
    --------------
    Means alone do not identify scoreless probabilities, scoring bursts, or
    dependence between halves. With no additional evidence, use the supplied
    Poisson dispersion prior, rather than inventing lineup effects or treating
    the proposed 70–75% scoreless frequency as an observed statistic.

    When an independently estimated scoreless probability z is available,
    model positive runs geometrically:

        P(R=0) = z
        P(R=k) = (1-z) * q * (1-q)**(k-1), k >= 1
        q = (1-z) / mean

    Thus E[R] = mean exactly, and

        Var(R) / E[R] = 2/q - 1 - mean.

    The positive conditional distribution is the maximum-entropy distribution
    on positive integers with the required conditional mean. Its dispersion
    emerges from the supplied zero probability and mean.

    Optional base-out mechanics
    --------------------------
    game_context["home_half_inning"] and ["away_half_inning"] may contain:
      * "zero_probability": independently estimated final-situation P(R=0), or
      * "base_out_chain": a finite absorbing Markov chain.

    A chain contains:
      * "transient": array of shape (maximum_transition_runs + 1, n, n)
      * "absorbing": array of shape (maximum_transition_runs + 1, n)
      * "initial": probability vector of length n

    Array index r denotes runs scored on that transition. Transient states
    should encode bases, outs, and lineup position as needed; absorption means
    the third out. Arrays must describe the final pregame situation. Both
    half-innings complete, so there is no walk-off censoring in inning one.

    For transient transition matrices Q_r and absorption vectors a_r,

        P(R=0) = initial @ solve(I - Q_0, a_0).

    This computes zero-run probability exactly; it does not approximate scoring
    by counting baserunners. Positive-run shape is then the hurdle model above.
    Invalid combinations of mean and zero probability are rejected.

    If no zero probability is supplied, "dispersion_index" in the half-inning
    specification defaults to the supplied unobserved prior of 1.0. Values
    above one produce a moment-matched negative binomial. Underdispersion
    requires a separately justified model and is rejected.

    Situational features
    --------------------
    home_expected/away_expected are FINAL inning means when present.
    Otherwise the supplied baseline means are scaled by:
      * home_exposure/away_exposure, defaulting to the multiplicative identity;
      * own attack / attack_reference, when both are supplied;
      * opponent defense / defense_reference, when both are supplied;
      * tempo / tempo_reference and pace / pace_reference, when supplied;
      * exp(sum(beta * feature)) using externally fitted coefficients.

    Attack and defense must be run-production and run-concession indices,
    respectively. References make units explicit. Supplying an index without
    its reference is an error. Do not supply both tempo and pace when they
    measure the same exposure.

    feature_coefficients is {"home": {path: beta}, "away": {path: beta}}.
    Paths address numeric context fields, e.g. "home_rest_days",
    "away_rest_days", "weather.wind_speed", "weather.temp", "weather.is_dome".
    Features must already have the training-time centering/transformation.
    Coefficients must be estimated using strictly earlier, independent data.
    No unsupported rest, weather, venue, attack, or defense slope is invented.

    Dependence
    ----------
    Optional context["joint_zero_probability"] supplies a fitted P(0,0).
    Given marginal zero probabilities, this identifies the four zero/nonzero
    block masses. Conditional positive magnitudes remain independent. This
    is the maximum-entropy joint law under those constraints and avoids an
    unidentified Gaussian-copula parameter. Otherwise halves are independent.

    Numerical contract
    ------------------
    from_scores is expected to accept {(home_runs, away_runs): probability}.
    Infinite tails are truncated with a certified omitted-mass bound, then
    explicitly conditioned on retained support. Total-variation error relative
    to the infinite distribution is at most tail_tolerance. Its default is
    sqrt(machine epsilon), a numerical precision choice, not a betting clamp.

    Optional max_score_cells limits computational resources. Exceeding it
    raises an error instead of silently clipping scores or discarding tail mass.

    No odds influence forecasts. There is no evidence in the supplied cold
    start establishing a calibration defect, achievable ROI, or realized edge.
    """

    BASELINE_HOME = 0.69
    BASELINE_AWAY = 0.63
    DISPERSION_PRIOR = 1.0

    def __init__(
        self,
        model_id: str = "codex_synth_mlb_inning_1_1108_043737",
        version: str = "1.0.0",
        league: str = "MLB",
        scope: str = "inning_1",
        family: str = "exact_score",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("MLB", "inning_1", "exact_score"):
            raise ValueError("This model supports only MLB|inning_1|exact_score.")

        self.tail_tolerance = self._number(
            np.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else tail_tolerance,
            "tail_tolerance",
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

        supplied = {} if feature_coefficients is None else feature_coefficients
        if not isinstance(supplied, Mapping):
            raise TypeError("feature_coefficients must be a mapping.")
        if set(supplied) - {"home", "away"}:
            raise ValueError("Coefficient groups must be 'home' or 'away'.")

        self.feature_coefficients: Dict[str, Dict[str, float]] = {}
        for side in ("home", "away"):
            group = supplied.get(side, {})
            if not isinstance(group, Mapping):
                raise TypeError(f"Coefficients for {side} must be a mapping.")
            self.feature_coefficients[side] = {}
            for path, coefficient in group.items():
                if not isinstance(path, str) or not path or any(
                    not component for component in path.split(".")
                ):
                    raise ValueError("Feature paths must be nonempty dotted strings.")
                self.feature_coefficients[side][path] = self._number(
                    coefficient, f"coefficient[{side}][{path}]"
                )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _ratio(cls, source: Mapping, key: str) -> float:
        reference = f"{key}_reference"
        if key not in source and reference not in source:
            return 1.0
        if key not in source or reference not in source:
            raise ValueError(f"{key} and {reference} must be supplied together.")
        numerator = cls._number(source[key], key)
        denominator = cls._number(source[reference], reference)
        if numerator < 0.0 or denominator <= 0.0:
            raise ValueError(f"{key} must be nonnegative and its reference positive.")
        result = numerator / denominator
        if not math.isfinite(result):
            raise ValueError(f"{key} ratio overflowed.")
        return result

    @classmethod
    def _feature(cls, context: Mapping, path: str) -> float:
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing fitted feature: {path}")
            value = value[component]
        if isinstance(value, (bool, np.bool_)):
            return float(value)
        return cls._number(value, path)

    def _mean(self, context: Mapping, side: str) -> float:
        expected_key = f"{side}_expected"
        if expected_key in context:
            result = self._number(context[expected_key], expected_key)
            if result < 0.0:
                raise ValueError(f"{expected_key} must be nonnegative.")
            return result

        opponent = "away" if side == "home" else "home"
        own = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)
        exposure = self._number(context.get(f"{side}_exposure", 1.0), "exposure")
        if exposure < 0.0:
            raise ValueError("Exposure must be nonnegative.")

        factors = (
            exposure,
            self._ratio(own, "attack"),
            self._ratio(other, "defense"),
            self._ratio(context, "tempo"),
            self._ratio(context, "pace"),
        )
        linear_predictor = math.fsum(
            coefficient * self._feature(context, path)
            for path, coefficient in self.feature_coefficients[side].items()
        )
        if not math.isfinite(linear_predictor):
            raise ValueError("Situational linear predictor overflowed.")
        if any(factor == 0.0 for factor in factors):
            return 0.0

        baseline = self.BASELINE_HOME if side == "home" else self.BASELINE_AWAY
        log_mean = math.fsum(
            [math.log(baseline), linear_predictor]
            + [math.log(factor) for factor in factors]
        )
        try:
            result = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Situational mean overflowed.") from exc
        if not math.isfinite(result) or result == 0.0:
            raise ValueError("Situational mean is outside floating-point range.")
        return result

    @classmethod
    def _chain_zero_probability(cls, specification: Any) -> float:
        chain = cls._mapping(specification, "base_out_chain")
        try:
            q = np.asarray(chain["transient"], dtype=float)
            a = np.asarray(chain["absorbing"], dtype=float)
            initial = np.asarray(chain["initial"], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid base_out_chain arrays.") from exc

        if (
            q.ndim != 3
            or q.shape[0] == 0
            or q.shape[1] == 0
            or q.shape[1] != q.shape[2]
            or a.shape != q.shape[:2]
            or initial.shape != (q.shape[1],)
        ):
            raise ValueError("Incompatible base_out_chain array dimensions.")
        for array in (q, a, initial):
            if not np.all(np.isfinite(array)) or np.any(array < 0.0):
                raise ValueError("Chain entries must be finite and nonnegative.")

        numerical_tolerance = np.finfo(float).eps * max(q.size, a.size, initial.size)
        row_sums = q.sum(axis=(0, 2)) + a.sum(axis=0)
        if np.any(np.abs(row_sums - 1.0) > numerical_tolerance):
            raise ValueError("Each chain transition row must sum to one.")
        if abs(float(initial.sum()) - 1.0) > numerical_tolerance:
            raise ValueError("The initial state probabilities must sum to one.")

        total_q = q.sum(axis=0)
        spectral_radius = float(np.max(np.abs(np.linalg.eigvals(total_q))))
        if not spectral_radius < 1.0:
            raise ValueError("The chain must absorb almost surely from every state.")

        try:
            zero_by_state = np.linalg.solve(np.eye(q.shape[1]) - q[0], a[0])
        except np.linalg.LinAlgError as exc:
            raise ValueError("Zero-run chain system is singular.") from exc

        zero_probability = float(initial @ zero_by_state)
        if not 0.0 <= zero_probability <= 1.0:
            raise ArithmeticError("Computed chain zero probability is outside [0, 1].")
        return zero_probability

    def _marginal(
        self, mean: float, specification: Any
    ) -> Tuple[float, Any, Any]:
        """
        Return (P(0), positive-PMF evaluator, positive-tail quantile function).

        Positive PMFs are conditional on a nonzero score.
        """
        spec = self._mapping(specification, "half-inning specification")
        if "zero_probability" in spec and "base_out_chain" in spec:
            raise ValueError("Supply either zero_probability or base_out_chain.")

        zero: Optional[float] = None
        if "zero_probability" in spec:
            zero = self._probability(spec["zero_probability"], "zero_probability")
        elif "base_out_chain" in spec:
            zero = self._chain_zero_probability(spec["base_out_chain"])

        if mean == 0.0:
            if zero is not None and zero != 1.0:
                raise ValueError("A zero mean requires probability one at zero.")
            return 1.0, lambda k: np.zeros_like(k, dtype=float), lambda tolerance: 0

        if zero is not None:
            if "dispersion_index" in spec:
                raise ValueError(
                    "Hurdle dispersion is implied by the mean and zero probability."
                )
            positive_mass = 1.0 - zero
            if positive_mass <= 0.0 or positive_mass > mean:
                raise ValueError(
                    "Hurdle feasibility requires 0 < 1 - P(0) <= mean."
                )
            success = positive_mass / mean
            if success == 1.0:
                return (
                    zero,
                    lambda k: np.asarray(k == 1, dtype=float),
                    lambda tolerance: 1,
                )

            log_continuation = math.log1p(-success)

            def positive_pmf(k: np.ndarray) -> np.ndarray:
                return success * np.exp((k - 1) * log_continuation)

            def cutoff(tolerance: float) -> int:
                # Conditional positive tail beyond K is (1-success)**K.
                return max(1, math.ceil(math.log(tolerance) / log_continuation))

            return zero, positive_pmf, cutoff

        dispersion = self._number(
            spec.get("dispersion_index", self.DISPERSION_PRIOR),
            "dispersion_index",
        )
        if dispersion < 1.0:
            raise ValueError("Underdispersion needs an explicitly supported model.")
        if dispersion == 1.0:
            law = poisson(mu=mean)
        else:
            shape = mean / (dispersion - 1.0)
            if not math.isfinite(shape) or shape <= 0.0:
                raise ValueError("Negative-binomial shape is numerically invalid.")
            law = nbinom(n=shape, p=1.0 / dispersion)

        log_zero = float(law.logpmf(0))
        zero = math.exp(log_zero)
        positive_mass = -math.expm1(log_zero)
        if positive_mass <= 0.0 or zero == 1.0:
            raise ArithmeticError("Positive scoring mass is below numerical resolution.")

        def positive_pmf(k: np.ndarray) -> np.ndarray:
            return np.exp(law.logpmf(k) - math.log(positive_mass))

        def cutoff(tolerance: float) -> int:
            unconditional_tail = tolerance * positive_mass
            quantile = float(law.isf(unconditional_tail))
            if not math.isfinite(quantile):
                raise ArithmeticError("Cannot resolve the requested score-tail quantile.")
            result = max(1, math.ceil(quantile))
            while float(law.sf(result)) > unconditional_tail:
                result += 1
            return result

        return zero, positive_pmf, cutoff

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        home_mean = self._mean(context, "home")
        away_mean = self._mean(context, "away")

        home_zero, home_positive, home_cutoff = self._marginal(
            home_mean, context.get("home_half_inning", {})
        )
        away_zero, away_positive, away_cutoff = self._marginal(
            away_mean, context.get("away_half_inning", {})
        )

        if "joint_zero_probability" in context:
            both_zero = self._probability(
                context["joint_zero_probability"], "joint_zero_probability"
            )
            lower = max(0.0, home_zero + away_zero - 1.0)
            upper = min(home_zero, away_zero)
            if not lower <= both_zero <= upper:
                raise ValueError(
                    "joint_zero_probability violates marginal Frechet bounds."
                )
            blocks = np.array(
                [
                    [both_zero, away_zero - both_zero],
                    [home_zero - both_zero, 1.0 - home_zero - away_zero + both_zero],
                ],
                dtype=float,
            )
            # Rows here are away-zero/positive; transpose to home/away order.
            blocks = blocks.T
        else:
            blocks = np.outer(
                [home_zero, 1.0 - home_zero],
                [away_zero, 1.0 - away_zero],
            )

        if np.any(blocks < 0.0):
            raise ArithmeticError("Joint block masses lost numerical feasibility.")

        # Allocate truncation error across the two marginal positive tails.
        per_half_tolerance = self.tail_tolerance / 2.0
        home_max = home_cutoff(per_half_tolerance)
        away_max = away_cutoff(per_half_tolerance)
        cells = (home_max + 1) * (away_max + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise ValueError(
                f"Certified score support needs {cells} cells; "
                f"max_score_cells is {self.max_score_cells}."
            )

        hp = home_positive(np.arange(1, home_max + 1, dtype=float))
        ap = away_positive(np.arange(1, away_max + 1, dtype=float))
        scores_array = np.zeros((home_max + 1, away_max + 1), dtype=float)
        scores_array[0, 0] = blocks[0, 0]
        scores_array[0, 1:] = blocks[0, 1] * ap
        scores_array[1:, 0] = blocks[1, 0] * hp
        scores_array[1:, 1:] = blocks[1, 1] * np.outer(hp, ap)

        if not np.all(np.isfinite(scores_array)) or np.any(scores_array < 0.0):
            raise ArithmeticError("Score probabilities are not finite and nonnegative.")

        retained_mass = math.fsum(float(value) for value in scores_array.flat)
        rounding_tolerance = np.finfo(float).eps * cells
        if (
            retained_mass <= 0.0
            or retained_mass > 1.0 + rounding_tolerance
            or 1.0 - retained_mass > self.tail_tolerance + rounding_tolerance
        ):
            raise ArithmeticError("Score-support mass failed the truncation certificate.")

        scores = {
            (home_runs, away_runs): float(probability / retained_mass)
            for (home_runs, away_runs), probability in np.ndenumerate(scores_array)
            if probability > 0.0
        }
        return DiscreteDistribution.from_scores(scores)